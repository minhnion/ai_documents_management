from __future__ import annotations

import argparse
import bisect
import sys
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# sys.path patch để bare-import các module cùng thư mục pipeline hoạt động khi load dưới dạng package
_PIPELINE_DIR = Path(__file__).resolve().parent
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

from parse_models import (
    MEDIA_TYPES, NOISE_TYPES, PAGE_BREAK, LeafElement, ParseResult, TableCellElement,
    flatten_leaves, index_table_cells,
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DEPTH_CHILD_KEYS: dict[int, str] = {
    1: "sections", 2: "subsections", 3: "subsubsections",
    4: "subsubsubsections", 5: "subsubsubsubsections",
}
METADATA_KEYS = [
    "title", "publisher", "decision_number", "specialty", "date",
    "isbn_electronic", "isbn_print", "total_pages", "source_file", "chapters",
]


@dataclass
class TocConfig:
    input_dir: Path = Path("./data/06_ade_chunks")
    output_dir: Path = Path("./data/03_toc_json")
    files: tuple[str, ...] = ()
    model: str = field(default_factory=lambda: os.environ.get("TOC_MODEL", "gpt-5.1"))
    scan_pages: int = 20
    min_depth_short: int = 3
    min_depth_long: int = 4
    depth_page_threshold: int = 99
    chunk_pages: int = 20
    landmark_batch_size: int = 100
    landmark_overlap: int = 10
    window_buffer: int = 25
    max_user_chars: int = 200_000
    subgroup_size: int = 8
    max_window: int = 600
    edge_ratio: float = 0.15
    expand_factor: int = 2
    max_expand_tries: int = 2
    deep_inherit_cutoff: int = 4
    anchor_block_char_limit: int = 3000
    anchor_titles_per_call: int = 12
    anchor_batch_char_budget: int = 12000
    anchor_groups_per_batch: int = 6
    heading_chunk_chars: int = 16000
    heading_chunk_overlap: int = 400
    heading_window_chars: int = 16000
    heading_window_overlap: int = 1
    level_batch_size: int = 60
    level_batch_overlap: int = 6
    gap_batch_char_budget: int = 60_000
    verify_radius: int = 5

    def min_depth_for(self, total_pages: int) -> int:
        return self.min_depth_long if total_pages >= self.depth_page_threshold else self.min_depth_short


class JsonRepair:
    @staticmethod
    def parse(text: str) -> dict:
        s = text.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s)
            s = re.sub(r"\s*```$", "", s).strip()
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        fixed, repaired = JsonRepair._close(s)
        try:
            result = json.loads(fixed)
            if repaired:
                logger.warning("JSON response truncated — recovered via state-machine")
            return result
        except json.JSONDecodeError:
            pass
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            raise ValueError("No JSON object found in response")
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        fixed, repaired = JsonRepair._close(candidate)
        try:
            result = json.loads(fixed)
            if repaired:
                logger.warning("JSON response truncated — recovered from regex candidate")
            return result
        except json.JSONDecodeError:
            for end in range(len(s) - 1, max(len(s) - 8000, 0), -1):
                if s[end] != "}":
                    continue
                try:
                    result = json.loads(s[:end + 1])
                    logger.warning("JSON truncated — trimmed to last complete object at char %d", end)
                    return result
                except json.JSONDecodeError:
                    continue
            raise ValueError("Could not parse or recover truncated JSON response")

    @staticmethod
    def _scan_state(s: str) -> tuple[bool, list[str]]:
        in_str = escaped = False
        stack: list[str] = []
        for ch in s:
            if escaped:
                escaped = False
                continue
            if in_str:
                if ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in ("{", "["):
                    stack.append(ch)
                elif ch == "}" and stack and stack[-1] == "{":
                    stack.pop()
                elif ch == "]" and stack and stack[-1] == "[":
                    stack.pop()
        return in_str, stack

    @staticmethod
    def _close(candidate: str) -> tuple[str, bool]:
        s = re.sub(r",\s*$", "", candidate.rstrip())
        in_str, stack = JsonRepair._scan_state(s)
        repaired = in_str or bool(stack)
        if in_str:
            last_comma = s.rfind(",")
            last_open = max(s.rfind("{"), s.rfind("["))
            cut = last_comma if last_comma > last_open else (last_open + 1 if last_open >= 0 else -1)
            s = re.sub(r",\s*$", "", s[:cut].rstrip()) if cut > 0 else s + '"'
            in_str, stack = JsonRepair._scan_state(s)
        return s + "".join("}" if c == "{" else "]" for c in reversed(stack)), repaired


class OpenAiJsonCaller:
    def __init__(self, client: OpenAI, model: str) -> None:
        self._client = client
        self._model = model

    def call(self, system: str, user: str) -> dict:
        response = self._client.responses.create(
            model=self._model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_object"}},
            temperature=0.0,
            max_output_tokens=32000,
        )
        self._last_response = response
        return JsonRepair.parse(response.output_text or "")

    def call_structured(self, system: str, user: str, schema: dict, name: str) -> dict:
        response = self._client.responses.create(
            model=self._model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
            temperature=0.0,
            max_output_tokens=32000,
        )
        self._last_response = response
        return JsonRepair.parse(response.output_text or "")


_RE_MD_HEADING_PREFIX = re.compile(r"^#+\s*")
_RE_BOLD_MARK = re.compile(r"\*{2,}")
_RE_LEAD_NUM = re.compile(r"^(\d+(?:\.\d+)*)\.?(?:\s+|$)")
_RE_LEAD_ALPHA = re.compile(r"^([A-Za-z])\.(?:\s+|$)")
_RE_NUM_LABEL_ANY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?=[.)\s]|$)")
# Bỏ phần tiêu đề lặp lại ở cuối, ví dụ "Heading 1 Heading 1" → "Heading 1"
_RE_DUP_TITLE = re.compile(r"(\b\S+(?:\s+\S+){0,3})\s+\1\s*$")
_RE_PLACEHOLDER_TITLE = re.compile(
    r"^\s*(?:nội\s+dung|nội\s+dung\s+chương|nội\s+dung\s+chính|mục\s+lục|table\s+of\s+contents?|contents?)\s*$",
    re.IGNORECASE,
)
_MAX_POSITION = 1 << 30


@dataclass
class GapCandidate:
    container: list
    depth: int
    parent_title: str | None
    before: dict | None
    after: dict
    missing: tuple[int, ...]


@dataclass
class LabelInfo:
    kind: str
    numeric: tuple[int, ...] | None
    letter: str | None
    raw_prefix: str


@dataclass
class HeadingSpan:
    title: str
    element_id: str
    leaf_index: int
    rank: int
    is_divider: bool = False
    label: LabelInfo | None = None


class TocTree:
    _MAX_GAP_SIZE = 5

    @staticmethod
    def norm_title(t: str) -> str:
        t = _RE_MD_HEADING_PREFIX.sub("", t)
        t = _RE_BOLD_MARK.sub("", t)
        t = re.sub(r"\s+", " ", t).strip()
        m = _RE_DUP_TITLE.search(t)
        if m:
            prefix = t[: m.start()].strip()
            t = f"{prefix} {m.group(1)}".strip() if prefix else m.group(1).strip()
        # Bỏ dấu câu thừa ở cuối tiêu đề để so khớp text chính xác hơn
        t = t.rstrip(":.,;")
        return t

    @staticmethod
    def normalize_nodes(items: list, depth: int) -> list:
        if not isinstance(items, list):
            return []
        child_key = DEPTH_CHILD_KEYS.get(depth)
        out = []
        for item in items:
            if isinstance(item, str):
                clean = TocTree.norm_title(item)
                if clean:
                    out.append({"title": clean})
                continue
            if not isinstance(item, dict):
                continue
            node: dict = {"title": TocTree.norm_title(str(item.get("title", "")))}
            if "heading_element_id" in item:
                node["heading_element_id"] = item["heading_element_id"]
            if child_key:
                node[child_key] = TocTree.normalize_nodes(item.get(child_key, []), depth + 1)
            out.append(node)
        return out

    @staticmethod
    def depth(nodes: list, node_depth: int = 1) -> int:
        child_key = DEPTH_CHILD_KEYS.get(node_depth)
        if not child_key:
            return node_depth
        max_d = node_depth
        for node in nodes:
            if isinstance(node, dict) and node.get(child_key):
                max_d = max(max_d, TocTree.depth(node[child_key], node_depth + 1))
        return max_d

    @staticmethod
    def count_nodes(nodes: list, depth: int = 1) -> int:
        child_key = DEPTH_CHILD_KEYS.get(depth)
        count = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            count += 1
            if child_key:
                count += TocTree.count_nodes(node.get(child_key, []), depth + 1)
        return count

    @staticmethod
    def is_shallow(chapters: list, min_depth: int) -> bool:
        return not chapters or TocTree.depth(chapters) < min_depth

    @staticmethod
    def flatten_refs(nodes: list, path: str = "") -> list[tuple[str, dict]]:
        result = []
        for node in nodes:
            if not isinstance(node, dict) or not node.get("title"):
                continue
            full_path = f"{path}/{node['title']}" if path else node["title"]
            result.append((full_path, node))
            for key in DEPTH_CHILD_KEYS.values():
                if node.get(key):
                    result.extend(TocTree.flatten_refs(node[key], full_path))
        return result

    @staticmethod
    def _insert_ordered(nodes: list, node: dict) -> None:
        prefix = TocTree._numeric_order(node.get("title", ""))
        if prefix is not None:
            for i, existing in enumerate(nodes):
                if not isinstance(existing, dict):
                    continue
                existing_prefix = TocTree._numeric_order(existing.get("title", ""))
                if existing_prefix is not None and existing_prefix > prefix:
                    nodes.insert(i, node)
                    return
        nodes.append(node)

    @staticmethod
    def _strip_leading_label(title: str) -> tuple[str | None, str | None, str]:
        m = _RE_LEAD_NUM.match(title)
        if m:
            return "num", m.group(1), title[m.end():]
        m = _RE_LEAD_ALPHA.match(title)
        if m:
            return "alpha", m.group(1), title[m.end():]
        return None, None, title

    @staticmethod
    def dedup_key(title: str) -> str:
        _, _, remainder = TocTree._strip_leading_label(TocTree.norm_title(title))
        remainder = remainder.strip().rstrip(":.,;").strip()
        return re.sub(r"\s+", " ", remainder.lower())

    @staticmethod
    def _numeric_order(title: str) -> tuple[int, ...] | None:
        kind, value, _ = TocTree._strip_leading_label(TocTree.norm_title(title))
        if kind != "num":
            return None
        return tuple(int(x) for x in value.split("."))

    @staticmethod
    def _declared_orders(*titles: str | None) -> set[tuple[int, ...]]:
        return {
            tuple(int(x) for x in m.group(1).split("."))
            for title in titles if title
            for m in _RE_NUM_LABEL_ANY.finditer(TocTree.norm_title(title))
        }

    @staticmethod
    def _undeclared(family: tuple[int, ...], values, declared: set[tuple[int, ...]]) -> tuple[int, ...]:
        return tuple(v for v in values if family + (v,) not in declared)

    @staticmethod
    def _family(order: tuple[int, ...]) -> tuple[int, ...]:
        return order[:-1]

    @staticmethod
    def _walk_sibling_groups(nodes: list, depth: int = 1, parent: dict | None = None):
        yield nodes, depth, parent
        child_key = DEPTH_CHILD_KEYS.get(depth)
        if not child_key:
            return
        for node in nodes:
            if isinstance(node, dict) and node.get(child_key):
                yield from TocTree._walk_sibling_groups(node[child_key], depth + 1, node)

    @staticmethod
    def find_gap_candidates(chapters: list) -> list[GapCandidate]:
        candidates: list[GapCandidate] = []
        for nodes, depth, parent in TocTree._walk_sibling_groups(chapters):
            by_family: dict[tuple[int, ...], list[tuple[int, dict]]] = {}
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                order = TocTree._numeric_order(n.get("title", ""))
                if order:
                    by_family.setdefault(TocTree._family(order), []).append((order[-1], n))
            for family, members in by_family.items():
                if len(members) < 2:
                    continue
                members.sort(key=lambda m: m[0])
                parent_title = parent.get("title") if parent else None
                if 1 < members[0][0] <= TocTree._MAX_GAP_SIZE + 1:
                    missing = TocTree._undeclared(
                        family, range(1, members[0][0]), TocTree._declared_orders(parent_title)
                    )
                    if missing:
                        candidates.append(GapCandidate(
                            container=nodes, depth=depth, parent_title=parent_title,
                            before=parent, after=members[0][1], missing=missing,
                        ))
                for i in range(len(members) - 1):
                    v0, n0 = members[i]
                    v1, n1 = members[i + 1]
                    if not 1 < v1 - v0 <= TocTree._MAX_GAP_SIZE + 1:
                        continue
                    missing = TocTree._undeclared(
                        family, range(v0 + 1, v1),
                        TocTree._declared_orders(parent_title, n0.get("title")),
                    )
                    if missing:
                        candidates.append(GapCandidate(
                            container=nodes, depth=depth, parent_title=parent_title,
                            before=n0, after=n1, missing=missing,
                        ))
        return candidates

    @staticmethod
    def apply_gap_fill(gap: GapCandidate, title: str, heading_element_id: str) -> None:
        node: dict = {"title": title, "heading_element_id": heading_element_id}
        child_key = DEPTH_CHILD_KEYS.get(gap.depth)
        if child_key:
            node[child_key] = []
        TocTree._insert_ordered(gap.container, node)


_METADATA_SCHEMA = """\
| Trường            | Nguồn                                                          |
|-------------------|----------------------------------------------------------------|
| title             | Tên đầy đủ tài liệu, thường ở trang bìa hoặc đầu file.        |
| publisher         | Cơ quan ban hành (Bộ Y tế, Bệnh viện, Hội Y học…).            |
| decision_number   | Số quyết định dạng "XXXX/QĐ-YYY" (ví dụ: "2855/QĐ-BYT").      |
| specialty         | Chuyên khoa (Tim mạch, Nội tiết, Hô hấp, Truyền nhiễm…).      |
| date              | Ngày ban hành ISO 8601: "YYYY-MM-DD".                          |
| isbn_electronic   | ISBN điện tử nếu có, ngược lại null.                           |
| isbn_print        | ISBN in nếu có, ngược lại null.                                |
| total_pages       | Tổng số trang (số nguyên), tìm ở cuối file hoặc trang bìa.    |
| source_file       | Tên file Markdown (đã cung cấp, điền vào đây).                |"""

_STRUCTURE_RULES = """\
CẤU TRÚC PHÂN CẤP (lồng nhau):
  chapters → sections → subsections → subsubsections → subsubsubsections
  Mỗi node chỉ có "title" và key mảng con tương ứng. Mảng con rỗng thì để [].

NHẬN DIỆN TIÊU ĐỀ (Tiếng Việt):
  - Cấp 1 (chapters): "Tài liệu X", "Phần X", hoặc các mục lớn nhất không có cha.
  - Cấp 2 (sections): "Chương X", "Bước X", "Mục X", "I, II, III", tiêu đề in đậm dưới chapter.
  - Cấp 3+ (subsections…): đánh số thập phân (2.1, 2.1.1…).
  - Phụ lục có số → lồng dưới chapter tương ứng. Phụ lục không số → chapter riêng.

  SUY CẤP KHI MỤC LỤC KHÔNG THỤT LỀ (bảng phẳng — trường hợp PHỔ BIẾN NHẤT):
    Rất nhiều MỤC LỤC không thể hiện cấp bằng thụt lề, và các mục lớn KHÔNG mang nhãn
    (tên bài / tên bệnh / tên quy trình / tên kỹ thuật). Khi đó dùng chính DÃY ĐÁNH SỐ để suy cấp:
    • ĐỘ SÂU THEO SỐ CẤP CỦA NHÃN: "N." và "M." (cùng một cấp số) LUÔN NGANG HÀNG; "N.M" là con của
      "N."; "N.M.K" là con của "N.M". Sau một dãy dài "N.1, N.2, N.3…" mà gặp "(N+1)." thì "(N+1)."
      là ANH EM của "N.", TUYỆT ĐỐI KHÔNG phải con của "N." hay của "N.M".
    • Các mục đánh số (1., 2., 3.… hoặc I., II.…) thuộc về mục KHÔNG-SỐ gần nhất phía TRÊN.
    • Khi dãy số đó kết thúc, một mục KHÔNG-SỐ mới xuất hiện, rồi dãy số lại BẮT ĐẦU LẠI TỪ 1
      → mục không-số mới NGANG HÀNG với mục không-số trước, KHÔNG phải con của nó.
    • Chỉ lồng sâu hơn khi có bằng chứng rõ: nhãn cấp (PHẦN/CHƯƠNG/BÀI), số thập phân nhiều cấp
      (N.M dưới N), hoặc ngữ nghĩa cho thấy mục sau là một BỘ PHẬN của mục trước.
    Sơ đồ (placeholder):
      "<Tên bài A>"  "1. …" "2. …" "3. …"  "<Tên bài B>"  "1. …" "2. …"
      → A và B NGANG HÀNG; mỗi dãy 1./2./3. là con của bài đứng ngay trên nó.
  - Loại bỏ: số trang, dòng chân trang, tên tác giả, đoạn văn bản nội dung.
  - GHÉP TIÊU ĐỀ NHIỀU DÒNG: Trong body text, nếu "PHẦN X", "CHƯƠNG X", hoặc "TÀI LIỆU X" nằm trên
    một dòng riêng và ngay dòng sau là phần nội dung của tên chương (không có anchor, không có
    số trang, không phải heading mới) → Ghép thành 1 node duy nhất.
    Ví dụ (sơ đồ, placeholder): "PHẦN <N>" / "<nhan đề>" → 1 node "PHẦN <N> <nhan đề>"
    (áp dụng tương tự cho nhãn "CHƯƠNG <N>", "TÀI LIỆU <N>")."""

_PROMPT_PHASE1 = f"""\
Bạn là hệ thống trích xuất cấu trúc tài liệu y tế. Trả về DUY NHẤT một JSON hợp lệ, không markdown, không giải thích.

OUTPUT SCHEMA:
{{
  "title": "...", "publisher": "...", "decision_number": "...", "specialty": "...",
  "date": "YYYY-MM-DD", "isbn_electronic": null, "isbn_print": null, "total_pages": 0, "source_file": "...",
  "chapters": [{{"title": "...", "sections": [{{"title": "...", "subsections": [{{"title": "...", "subsubsections": [{{"title": "...", "subsubsubsections": []}}]}}]}}]}}]
}}

METADATA – trích xuất từ văn bản, không tìm thấy → null:
{_METADATA_SCHEMA}

MỤC LỤC (key "chapters") — HAI TRƯỜNG HỢP:

TRƯỜNG HỢP 1 — TÌM THẤY PHẦN MỤC LỤC/TABLE OF CONTENTS:
  - CHỈ dùng các dòng/hàng nằm BÊN TRONG phần MỤC LỤC đó.
  - TUYỆT ĐỐI KHÔNG suy luận thêm mục con từ nội dung chương, tiêu đề body, hay bất kỳ phần nào khác của văn bản.
  - TUYỆT ĐỐI KHÔNG thêm bất kỳ mục nào không xuất hiện trong MỤC LỤC.
  - Nếu MỤC LỤC chỉ có 2 cấp → chỉ trả về 2 cấp, không tự thêm cấp 3.
  - Kết quả nông (ít sections) là ĐÚNG nếu MỤC LỤC gốc nông — hệ thống sẽ tự bổ sung ở bước tiếp theo.

  ⚠ MỤC LỤC PHÂN TRANG — CỰC KỲ QUAN TRỌNG:
  Bảng MỤC LỤC trong PDF OCR thường bị TÁCH thành nhiều trang vật lý,
  phân cách bởi {PAGE_BREAK}. Các trang tiếp theo không có tiêu đề "MỤC LỤC"
  nhưng ĐƯỢC ĐÁNH NHÃN [MỤC LỤC - tiếp theo, trang N] bởi hệ thống.
  - Đọc VÀ SỬ DỤNG tất cả các trang mang nhãn [MỤC LỤC - tiếp theo, trang N].
  - Ghép nội dung toàn bộ các trang đó vào cùng một bảng MỤC LỤC thống nhất.
  - TUYỆT ĐỐI KHÔNG bỏ qua bất kỳ hàng nào trong các trang tiếp theo này.

  ⚠ TIÊU ĐỀ NHIỀU DÒNG — HAI QUY TẮC GHÉP CHÍNH XÁC:

  QUY TẮC 1 — NHẬN DIỆN "IDENTIFIER TRẦN" (quan trọng nhất):
  Một dòng MỤC LỤC là "identifier trần" khi nội dung của nó (sau khi bỏ dấu chấm dẫn và số trang)
  CHỈ còn đúng "PHẦN X" hoặc "CHƯƠNG X" (X là số La Mã hoặc Ả rập) — KHÔNG có bất kỳ chữ mô
  tả nào đi kèm.
  Ví dụ: "PHẦN <N>........<tr>" → bỏ dấu chấm dẫn và số trang → còn "PHẦN <N>" → là identifier trần.
  Đối lập: "PHẦN <N>. <nhan đề>... <tr>" → sau khi bỏ số trang vẫn còn nhan đề → KHÔNG phải identifier trần.
  Khi gặp identifier trần: GHÉP ngay với dòng liền sau (dòng đó là phần mô tả của tiêu đề).
  Kết quả: 1 node duy nhất = "PHẦN <N> [dòng liền sau]" hoặc "CHƯƠNG <N> [dòng liền sau]".
  Ví dụ (sơ đồ, placeholder):
    "PHẦN <N>.......<tr>"  (identifier trần) ─┐ → title = "PHẦN <N> <nhan đề>"
    "<nhan đề>....<tr>"                        ─┘

  QUY TẮC 2 — DÒNG THIẾU SỐ TRANG (tổng quát, áp dụng cho MỌI kiểu viết hoa/thường):
  Mọi mục thật trong MỤC LỤC đều kèm số trang. Một dòng KHÔNG có số trang riêng là tiêu đề bị
  cắt giữa chừng khi OCR xuống dòng → GHÉP với dòng NGAY SAU (dòng mang số trang), BẤT KỂ dòng
  sau viết HOA hay viết thường.
  Ví dụ (sơ đồ, placeholder):
    "<N.M.K> <nửa đầu nhan đề>"      (KHÔNG có số trang) ─┐ → title = "<N.M.K> <nửa đầu nhan đề> <nửa sau>"
    "<nửa sau nhan đề>....<tr>"      (có số trang)       ─┘

  QUY TẮC 3 — DÒNG BẮT ĐẦU BẰNG CHỮ THƯỜNG:
  Một dòng MỤC LỤC bắt đầu bằng ký tự CHỮ THƯỜNG là phần TIẾP THEO của tiêu đề dòng ngay trên —
  GHÉP vào dòng trên.

  Ba quy tắc này có thể kết hợp cho tiêu đề nhiều dòng:
    "PHẦN <N>......<tr>"     (identifier trần → ghép với dòng sau)
    "<Nhan đề>"              (thiếu số trang → còn tiếp)
    "<phần tiếp>....<tr>"    (có số trang → kết thúc tiêu đề)
    → title cuối = "PHẦN <N> <Nhan đề> <phần tiếp>"

  ⚠ LƯU Ý: KHÔNG ghép 2 dòng chỉ vì chúng CÙNG số trang — chỉ ghép theo 3 quy tắc trên.
  Ví dụ "<Bước/Mục X>...<tr>" và "A. <nhan đề>...<tr>" có cùng trang nhưng là các mục KHÁC NHAU
  (A. không phải identifier trần, CÓ số trang riêng, không bắt đầu chữ thường).

{_STRUCTURE_RULES}"""

_PHASE2_READING_RULES = f"""\
══════════════════════════════════════════════
FORMAT OCR (LandingAI DPT-3)
══════════════════════════════════════════════
  • {PAGE_BREAK} = ngắt trang vật lý, KHÔNG phải ranh giới cấu trúc.
  • Heading có thể ở dạng Markdown (#, ##, ###), in đậm (**...**), hoặc văn bản thường có nhãn đánh số / ALL CAPS.
  • Chữ hoa hoặc vị trí đầu trang KHÔNG tự quyết định là heading — căn cứ VAI TRÒ CẤU TRÚC + định dạng + nhãn.
  • Số trang đứng riêng ("123", hoặc "<tên tài liệu> / 123") → bỏ.

══════════════════════════════════════════════
ĐỊNH NGHĨA — HEADING vs NỘI DUNG (phân biệt cốt lõi)
══════════════════════════════════════════════
HEADING = dòng TIÊU ĐỀ đặt tên cho một mục và MỞ ĐẦU khối nội dung thuộc mục đó (đoạn văn, danh sách, hoặc
  các tiểu mục con bên dưới). Là NHAN ĐỀ ngắn gọn, không phải câu văn.
KHÔNG phải heading (là NỘI DUNG — để nguyên trong thân mục cha, KHÔNG liệt kê):
  • Câu hoặc đoạn văn xuôi.
  • MỤC LIỆT KÊ NỘI DUNG: các dòng (đánh số 1./2., chữ cái a./b., hoặc gạch đầu dòng) liệt kê SONG SONG các ý —
    thường mở đầu bằng một câu dẫn kết thúc bằng ":"; mỗi mục là một mệnh đề/câu (hay kết thúc bằng ";" hoặc ",");
    và KHÔNG mục nào mở ra khối con riêng.
  • Chú thích hình/bảng/biểu đồ; tiêu đề chạy (header/footer lặp giữa các trang); số trang; câu hỏi lượng giá.

══════════════════════════════════════════════
QUY TRÌNH QUYẾT ĐỊNH cho MỖI DÒNG (xét theo THỨ TỰ, dừng ở bước khớp)
══════════════════════════════════════════════
DẠNG NHÃN cấu trúc: "CHƯƠNG/PHẦN/MỤC/BƯỚC/BÀI <N>"; số La Mã (I., II.); chữ cái HOA/THƯỜNG kèm "." hoặc ")"
(A., a., B), c)...); số Ả-rập một hoặc nhiều cấp kèm "." hoặc ")" (N, N.M, N.M.K).

B1. LOẠI TRỪ TRƯỚC (KHÔNG bao giờ là heading, kể cả khi in đậm, có # hay mang nhãn đánh số):
    chú thích hình/bảng/biểu đồ/sơ đồ (mở đầu bằng "Hình/Bảng/Biểu đồ/Sơ đồ/Ảnh" + số); số trang;
    tiêu đề chạy (header/footer lặp giữa các trang); câu hỏi lượng giá; thẻ HTML / anchor;
    DÒNG LIỆT KÊ CHỈ MỤC — khối gồm NHIỀU dòng liên tiếp dạng «<nhan đề> + dấu chấm dẫn hoặc khoảng
    trắng + SỐ TRANG», không dòng nào mở ra khối nội dung bên dưới (bảng MỤC LỤC, DANH MỤC BẢNG /
    HÌNH / CHỮ VIẾT TẮT, chỉ mục cuối sách). Cả khối đó trả `titles: []` — dù MỖI dòng trông y hệt
    một heading và thoả B2/B3, chúng chỉ TRỎ TỚI heading ở nơi khác, không phải heading tại chỗ.
B2. TÍN HIỆU ĐỊNH DẠNG: dòng mang #/##/### hoặc in đậm **...**, HOẶC là dòng NGẮN viết HOA TOÀN BỘ (ALL CAPS)
    đứng riêng — VÀ đóng vai nhan đề (KHÔNG phải một mục trong dãy liệt kê song song) → HEADING.
B3. DÒNG CÓ NHÃN cấu trúc: là HEADING nếu đóng vai NHAN ĐỀ; KHÔNG phải heading nếu là MỤC LIỆT KÊ NỘI DUNG.
    → HEADING: nhan đề NGẮN (thường ≤ ~12 từ, gọn trong một dòng), thuộc HỆ ĐÁNH SỐ nhất quán, MỞ ĐẦU đoạn
      văn/tiểu mục riêng bên dưới. Nhãn viết THƯỜNG (a., a)) xét Y HỆT chữ HOA — KHÔNG loại chỉ vì viết thường.
    → LIỆT KÊ NỘI DUNG (loại): đứng ngay sau câu dẫn kết thúc ":"; là câu/mệnh đề DÀI mô tả; kết thúc bằng ";"
      hoặc ","; nhiều mục cùng nhóm là câu song song, KHÔNG mục nào mở khối con.
    → Nhan đề tự thân kết thúc bằng ":" KHÔNG phải câu dẫn — vẫn là heading nếu ngắn và mở khối con.
B4. Còn lại (câu/đoạn văn xuôi, không nhãn, không định dạng heading) → KHÔNG phải heading.

  Sơ đồ (placeholder — KHÔNG phải nội dung thật):
    HEADING:          "## N.M <nhan đề ngắn>"  →  đoạn văn / tiểu mục của nó.
    LIỆT KÊ NỘI DUNG: "<câu dẫn ...:>"  →  "1. <mệnh đề dài ...>;"  "2. <mệnh đề dài ...>;"  (song song, không con)

══════════════════════════════════════════════
GHÉP TIÊU ĐỀ NHIỀU DÒNG
══════════════════════════════════════════════
  • NHÃN TRẦN: nhãn cấu trúc không kèm nhan đề ("CHƯƠNG <N>", "PHẦN <N>", "MỤC <N>"...) rồi dòng kế là nhan đề →
    GHÉP thành MỘT heading — KỂ CẢ khi dòng nhan đề mang dấu #/## hoặc in đậm.
  • DÒNG NỐI TIẾP: dòng bắt đầu bằng chữ thường và không mang nhãn cấu trúc → là phần tiếp của heading dòng trên
    → ghép vào heading trên.
  • KHÔNG GHÉP 2 HEADING RIÊNG (quan trọng): nếu dòng kế TỰ MANG NHÃN cấu trúc riêng (số N/N.M, chữ cái, La Mã,
    "CHƯƠNG"/"PHẦN"/"MỤC"...) thì đó là HEADING RIÊNG — liệt kê TÁCH BIỆT, TUYỆT ĐỐI không nối vào heading trên.
    Chỉ ghép khi dòng kế KHÔNG có nhãn của riêng nó. Sơ đồ: "IV. <nhan đề>" rồi "4.1. <nhan đề>" → HAI heading.

══════════════════════════════════════════════
TÍNH ĐẦY ĐỦ & CHUẨN HÓA
══════════════════════════════════════════════
  • Một khối có 0, 1 hoặc NHIỀU heading. Heading thường RUN-IN (nhan đề rồi nội dung ngay bên dưới) — vẫn là
    heading, PHẢI liệt kê. Trong một hệ đánh số lồng nhau, liệt kê ĐỦ MỌI cấp có mặt (không dừng giữa chừng).
  • DÃY ĐÁNH SỐ TRẢI NHIỀU KHỐI: các khối được cho theo ĐÚNG thứ tự đọc, và một dãy heading đánh số
    (1., 2., 3.… / I., II.… / A., B.…) thường trải qua NHIỀU khối liên tiếp — mỗi khối chỉ chứa một
    hoặc vài mục của dãy. Nếu bạn liệt kê "2." ở một khối thì "1." của CÙNG dãy phải nằm ở khối đó
    hoặc một khối TRƯỚC ĐÓ: hãy soát lại và ĐỪNG BỎ SÓT mục ĐẦU DÃY, kể cả khi nó đứng ngay sau một
    nhan đề bài hoặc một mục tóm tắt.
  • Chép NGUYÊN VĂN dòng tiêu đề (giữ số, dấu câu, hoa/thường, dấu tiếng Việt); KHÔNG dịch/viết lại/đánh số lại.
  • KHÔNG thêm tiêu đề không có trong văn bản."""

_PROMPT_PHASE3_SYS = (
    "Bạn là hệ thống mapping TOC heading → phần tử (element) trong tài liệu y tế OCR. "
    'Trả về DUY NHẤT JSON hợp lệ: {"mappings": [{"toc_idx": int, "element_id": str_or_null}]}\n\n'
    "ĐẶC ĐIỂM QUAN TRỌNG CỦA ELEMENT TRONG TÀI LIỆU NÀY:\n"
    "• Nhiều tiêu đề cấp 2 (BƯỚC X, MỤC X, I/II/III...) không xuất hiện như element text riêng — "
    "chúng nằm BÊN TRONG một table/figure element, thường là cell đầu tiên hoặc header của bảng.\n"
    "• Preview element được hiển thị đầy đủ. Hãy scan TOÀN BỘ nội dung, không chỉ phần mở đầu.\n"
    "• Nếu tiêu đề cần tìm xuất hiện ở giữa hoặc cuối preview của một element → element đó là kết quả đúng.\n"
    "• SỐ LA MÃ vs SỐ THƯỜNG: Mục lục có thể dùng số thường (Phần 4, Chương 3) trong khi nội dung thực tế "
    "dùng số La Mã (PHẦN IV, CHƯƠNG III) hoặc ngược lại. Hãy nhận diện linh hoạt: Phần 4 = PHẦN IV, "
    "Chương 2 = CHƯƠNG II, v.v. Đây KHÔNG phải là chương khác — hãy map vào element có tiêu đề tương đương."
)
_PROMPT_LANDMARK_SYS = (
    "Bạn là hệ thống định vị chương cấp 1 trong tài liệu y tế OCR. "
    'Trả về DUY NHẤT JSON hợp lệ: {"mappings": [{"toc_idx": int, "element_id": str_or_null}]}\n\n'
    "QUY TẮC QUAN TRỌNG:\n"
    "• Đây chỉ là MỘT ĐOẠN của tài liệu — nếu heading không có trong đoạn này thì null là bình thường.\n"
    "• Chỉ tìm trong ĐOẠN THỰC ĐƯỢC CUNG CẤP — KHÔNG phải bảng mục lục đầu sách.\n"
    "• Nếu tìm thấy heading chính xác → gán element_id đó.\n"
    "• Nếu heading chương không xuất hiện là element text riêng — hãy tìm element gần nhất "
    "có nội dung đầu chương đó (element đầu tiên của phần nội dung mới, được in đậm hoặc tiêu đề).\n"
    "• SỐ LA MÃ = SỐ THƯỜNG: Phần 4 = PHẦN IV, Chương 3 = CHƯƠNG III. Nhận diện linh hoạt.\n"
    "• TIÊU ĐỀ NHIỀU DÒNG TRONG MỘT ELEMENT: nhiều chapter heading có dạng 2 dòng trong cùng 1 element OCR — "
    "nhãn trần ('PHẦN <N>', 'CHƯƠNG <N>') ở dòng trên, nhan đề ở dòng dưới. Nếu TOC node là title đã ghép "
    "('PHẦN <N> <nhan đề>') → map vào element chứa cả 2 dòng; nếu TOC chỉ có nhãn trần → chọn element chứa nhãn "
    "đó (có thể cùng element với nhan đề).\n"
    "• Heading đôi khi bị OCR tách thành 2 element liên tiếp ngắn (element A: nhãn 'CHƯƠNG <N>', element B: nhan "
    "đề) → chọn element_id của element ĐẦU TIÊN chứa nhãn 'CHƯƠNG <N>'.\n"
    "• Heading có thể nằm BÊN TRONG table/figure element — scan toàn bộ nội dung, không chỉ đầu element.\n"
    "• GIÁ TRỊ element_id phải CHÍNH XÁC lấy sau 'id=' trong danh sách. Sai format → null."
)
_PROMPT_PHASE3_USER_TMPL = """\
TOC NODES (toc_idx — title):
{toc_list}

ELEMENTS (mỗi dòng gồm: số thứ tự | id=... | nội dung):
{element_list}

NHIỆM VỤ: Với mỗi toc_idx, tìm element có TEXT khớp tốt nhất với tiêu đề đó.

QUY TẮC:
1. Element phải là nơi heading XUẤT HIỆN TRONG NỘI DUNG THỰC của tài liệu — KHÔNG phải bảng mục lục đầu sách.
2. Khớp dựa trên số mục (5.3, CHƯƠNG 4…) VÀ tiêu đề. Số mục (CHƯƠNG 4) phải khớp CHÍNH XÁC. Không gán nhầm sang chương khác.
3. Nếu tiêu đề bị ngắt dòng hoặc phân tách thành nhiều element liên tiếp (ví dụ element 1: "CHƯƠNG 7", element 2: "Tiêu đề"), hãy chọn element_id của phần ĐẦU TIÊN (element 1 chứa "CHƯƠNG 7"). Chấp nhận tiêu đề bị cắt cụt.
4. Nhiều toc_idx có thể được gán cùng một element_id khi nhiều tiêu đề nằm trong cùng một element (thường gặp khi heading bị gộp vào element kề trước).
5. GIÁ TRỊ element_id trong kết quả phải CHÍNH XÁC lấy sau 'id=' trên dòng ELEMENTS.
   KHÔNG đưa số thứ tự vào trước id. Sai format → trả null.
6. HEADING TRONG TABLE/FIGURE: Tiêu đề cấp 2 (BƯỚC X, MỤC X, I/II/III...) thường nằm BÊN TRONG
   một element dạng table hoặc figure — không phải element text riêng. Tiêu đề có thể ở giữa hoặc
   cuối preview, không nhất thiết ở đầu. Scan TOÀN BỘ nội dung preview của mỗi element.
   Khi tìm thấy tiêu đề khớp bên trong một table element → đó là element đúng, hãy gán nó.
7. Thà để null hơn là assign sai element — không đoán mò khi không thấy text khớp rõ ràng."""

_PROMPT_ANCHOR_SYS = (
    "Bạn là hệ thống định vị CHÍNH XÁC điểm bắt đầu của từng tiêu đề bên trong MỘT khối văn bản "
    "OCR chứa NHIỀU tiêu đề dính liền nhau (không tách thành element riêng — ví dụ nhiều mục "
    "\"BƯỚC X\", \"A.\", \"B.\" nằm chung trong một ô bảng hoặc một khối figure). "
    'Trả về DUY NHẤT JSON hợp lệ: {"anchors": [{"toc_idx": int, "anchor": str_or_null}]}\n\n'
    "QUY TẮC BẮT BUỘC:\n"
    "1. anchor PHẢI là chuỗi VERBATIM — chép lại NGUYÊN VĂN 100% khoảng 6-10 từ đầu tiên của "
    "tiêu đề đó ĐÚNG NHƯ NÓ XUẤT HIỆN trong VĂN BẢN GỐC (giữ nguyên dấu câu, khoảng trắng, "
    "hoa/thường, dấu tiếng Việt). TUYỆT ĐỐI KHÔNG diễn giải, sửa lỗi chính tả, dịch, hay rút gọn — "
    "nếu không chép được chính xác nguyên văn thì trả null cho mục đó.\n"
    "2. anchor PHẢI bắt đầu từ CHỮ ĐỌC ĐƯỢC của chính tiêu đề đó — KHÔNG được bắt đầu bằng thẻ "
    "HTML (như <tr>, <td>, <table>...) hay ký tự markdown thuần (như dấu **). Nếu văn bản gốc có "
    "thẻ HTML ngay trước tiêu đề, hãy BỎ QUA thẻ đó và bắt đầu anchor từ chữ đầu tiên đọc được.\n"
    "3. anchor PHẢI đủ dài và ĐẶC TRƯNG để chỉ xuất hiện ĐÚNG MỘT LẦN trong văn bản gốc — "
    "tránh chọn cụm từ chung chung hoặc lặp lại nhiều nơi (ví dụ chỉ số thứ tự \"1.\" đứng một mình).\n"
    "4. Các tiêu đề trong danh sách được cho theo ĐÚNG THỨ TỰ xuất hiện trong văn bản — "
    "anchor của tiêu đề sau PHẢI nằm SAU vị trí anchor của tiêu đề trước.\n"
    "5. Nếu một tiêu đề thực sự KHÔNG xuất hiện trong văn bản gốc → anchor = null. "
    "Thà để null hơn là bịa hoặc đoán sai vị trí.\n"
    "6. KHÔNG lấy nhầm sang câu/đoạn khác chỉ vì có từ giống nhau — phải đúng là điểm bắt đầu "
    "thật sự của CHÍNH tiêu đề đó, không phải một chỗ nhắc lại hay tham chiếu chéo tới nó."
)
_PROMPT_ANCHOR_USER_TMPL = """\
VĂN BẢN GỐC (nguyên văn, có thể chứa nhiều tiêu đề dính liền nhau):
---
{block_text}
---

CÁC TIÊU ĐỀ CẦN ĐỊNH VỊ, theo đúng thứ tự xuất hiện (toc_idx — title):
{title_list}

Với mỗi toc_idx, trả về anchor là 6-10 từ đầu tiên VERBATIM của tiêu đề đó, chép nguyên văn từ VĂN BẢN GỐC ở trên."""

_PROMPT_ANCHOR_MULTI_SYS = (
    "Bạn là hệ thống định vị CHÍNH XÁC điểm bắt đầu của từng tiêu đề bên trong NHIỀU khối văn bản "
    "OCR độc lập, mỗi khối chứa nhiều tiêu đề dính liền nhau (không tách thành element riêng — "
    "ví dụ nhiều mục \"BƯỚC X\", \"A.\", \"B.\" nằm chung trong một ô bảng hoặc một khối figure). "
    "Các khối HOÀN TOÀN ĐỘC LẬP với nhau — KHÔNG được lấy anchor của khối này gán cho khối khác.\n"
    'Trả về DUY NHẤT JSON hợp lệ: {"anchors": [{"block_idx": int, "toc_idx": int, "anchor": str_or_null}]}\n\n'
    "QUY TẮC BẮT BUỘC:\n"
    "1. anchor PHẢI là chuỗi VERBATIM — chép lại NGUYÊN VĂN 100% khoảng 6-10 từ đầu tiên của "
    "tiêu đề đó ĐÚNG NHƯ NÓ XUẤT HIỆN trong văn bản của ĐÚNG block_idx tương ứng (giữ nguyên dấu câu, "
    "khoảng trắng, hoa/thường, dấu tiếng Việt). TUYỆT ĐỐI KHÔNG diễn giải, sửa lỗi chính tả, dịch, hay "
    "rút gọn — nếu không chép được chính xác nguyên văn thì trả null cho mục đó.\n"
    "2. anchor PHẢI bắt đầu từ CHỮ ĐỌC ĐƯỢC của chính tiêu đề đó — KHÔNG được bắt đầu bằng thẻ "
    "HTML (như <tr>, <td>, <table>...) hay ký tự markdown thuần (như dấu **). Nếu văn bản gốc có "
    "thẻ HTML ngay trước tiêu đề, hãy BỎ QUA thẻ đó và bắt đầu anchor từ chữ đầu tiên đọc được.\n"
    "3. anchor PHẢI đủ dài và ĐẶC TRƯNG để chỉ xuất hiện ĐÚNG MỘT LẦN trong văn bản của khối đó — "
    "tránh chọn cụm từ chung chung hoặc lặp lại nhiều nơi.\n"
    "4. Trong CÙNG một khối, các tiêu đề được cho theo ĐÚNG THỨ TỰ xuất hiện trong văn bản — "
    "anchor của tiêu đề sau PHẢI nằm SAU vị trí anchor của tiêu đề trước, TRONG CÙNG khối đó.\n"
    "5. Nếu một tiêu đề thực sự KHÔNG xuất hiện trong văn bản của khối tương ứng → anchor = null. "
    "Thà để null hơn là bịa hoặc đoán sai vị trí, hoặc lấy nhầm từ khối khác.\n"
    "6. KHÔNG lấy nhầm sang câu/đoạn khác hay khối khác chỉ vì có từ giống nhau — phải đúng là điểm "
    "bắt đầu thật sự của CHÍNH tiêu đề đó trong ĐÚNG khối của nó.\n"
    "7. Trả về đủ tất cả (block_idx, toc_idx) đã được liệt kê ở mỗi khối."
)
_PROMPT_ANCHOR_MULTI_BLOCK_TMPL = """\
=== KHỐI {block_idx} ===
VĂN BẢN GỐC (nguyên văn):
---
{block_text}
---
Tiêu đề cần định vị trong KHỐI {block_idx}, theo đúng thứ tự xuất hiện (toc_idx — title):
{title_list}"""

_PROMPT_HEADING_DETECT_SYS = (
    "Bạn là hệ thống NHẬN DIỆN TIÊU ĐỀ (heading) trong tài liệu OCR (LandingAI DPT-3 format).\n\n"
    f"{_PHASE2_READING_RULES}\n\n"
    "══════════════════════════════════════════════\n"
    "NHIỆM VỤ (CHỈ NHẬN DIỆN — KHÔNG DỰNG CÂY, KHÔNG PHÂN CẤP)\n"
    "══════════════════════════════════════════════\n"
    "Đầu vào là NHIỀU KHỐI văn bản liên tiếp, mỗi khối là 1 phần tử OCR, đánh số [i].\n"
    "Với MỖI khối [i], liệt kê NGUYÊN VĂN các heading khối đó chứa theo đúng thứ tự xuất hiện, áp dụng "
    "ĐỊNH NGHĨA và QUY TẮC QUYẾT ĐỊNH ở trên.\n"
    "• Loại khỏi kết quả mọi thứ KHÔNG phải heading theo ĐỊNH NGHĨA (câu/đoạn nội dung; mục liệt kê nội dung; "
    "số trang; chú thích hình/bảng; tiêu đề chạy) — kể cả khi in đậm.\n"
    "• Áp dụng GHÉP TIÊU ĐỀ NHIỀU DÒNG và TÍNH ĐẦY ĐỦ (liệt kê ĐỦ mọi heading run-in/lồng nhau trong khối).\n"
    "• KHÔNG suy diễn/không thêm tiêu đề không có trong khối; KHÔNG phân cấp; KHÔNG đánh số lại.\n"
    'Trả về DUY NHẤT JSON: {"blocks": [{"i": int, "titles": [str, ...]}]} — đủ mọi khối đã cho (khối '
    "không có heading → titles = [])."
)
_HEADING_DETECT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "i": {"type": "integer"},
                    "titles": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["i", "titles"],
            },
        }
    },
    "required": ["blocks"],
}
_PROMPT_CHAPTERS_SYS = (
    "Cho DANH SÁCH HEADING theo đúng thứ tự đọc của MỘT tài liệu y tế (tài liệu này KHÔNG đánh nhãn "
    "CHƯƠNG/PHẦN rõ ràng). Nhiệm vụ: chọn những heading là MỤC CẤP CAO NHẤT của tài liệu — các mục "
    "chia tài liệu thành những phần lớn nhất, không phụ thuộc mục nào khác.\n\n"
    "QUY TẮC:\n"
    "• Dựa vào ngữ nghĩa + định dạng: mục cấp cao thường là chủ đề lớn, ALL CAPS, mở đầu một mảng nội "
    "dung lớn của tài liệu.\n"
    "• KHÔNG chọn mục con chi tiết (đánh số thập phân nhiều cấp như '1.2.3', tiểu mục).\n"
    "• Các mục được chọn phải theo đúng thứ tự đọc và bao phủ toàn bộ tài liệu.\n"
    'Trả về DUY NHẤT JSON: {"chapter_indices": [int, ...]} — các chỉ số [i] của heading cấp cao nhất.'
)
_CHAPTERS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"chapter_indices": {"type": "array", "items": {"type": "integer"}}},
    "required": ["chapter_indices"],
}
_PROMPT_LEVELS_SYS = (
    "Bạn gán CẤP (level) cho từng heading trong MỘT chương tài liệu y tế, theo đúng thứ tự đọc. Chương là "
    "cấp 1; mọi heading dưới đây là con cháu nên level ≥ 2.\n\n"
    "Phần MẠCH ĐANG MỞ liệt kê các mảng cấp trên đang mở NGAY TRƯỚC lô này (kèm cấp) — dùng làm MỐC để giữ "
    "cấp NHẤT QUÁN, không gán lại từ đầu mỗi lô.\n\n"
    "CĂN CỨ (ưu tiên từ trên xuống):\n"
    "1. SỐ THỨ TỰ là ĐỊNH NGHĨA phân cấp: 'N.M.K' là con của 'N.M' (mỗi dấu chấm = sâu 1 cấp). Mục đánh số "
    "ĐẦU DÃY ('1.', 'I.', 'A.' — chưa có anh em cùng dãy phía trước) đặt LÀM CON của mảng đang mở gần nhất "
    "phía trên, TUYỆT ĐỐI không nhảy lên cấp chương.\n"
    "2. CHUỖI SONG SONG: các heading cùng MẪU hoặc cùng VAI (ví dụ dạng '<nhãn> <n>: …' lặp lại, hay một "
    "loạt chủ đề đồng hạng) là NGANG HÀNG → gán CÙNG một cấp.\n"
    "3. Heading đánh dấu «trang riêng» (đứng một mình trên 1 trang) là MẢNG cấp cao, thường ngay dưới chương "
    "→ cấp thấp (số nhỏ).\n"
    "4. Heading KHÔNG số còn lại: cấp = cấp của MẢNG-cha ngữ nghĩa gần nhất + 1. Nếu nó MỞ một mảng nội dung "
    "riêng (bên dưới có tóm tắt / dãy số / bảng con) → là MẢNG (cấp nông hơn); nếu chỉ là nhãn nội dung rời "
    "→ để cùng cấp các mục nội dung ngang hàng.\n"
    "5. Chữ HOA / in đậm gợi ý cấp cao hơn văn xuôi thường, nhưng KHÔNG tự quyết cấp.\n\n"
    "RÀNG BUỘC: cây hợp lệ — con chỉ sâu hơn cha ĐÚNG 1 cấp, KHÔNG nhảy cấp.\n"
    'Trả về DUY NHẤT JSON: {"levels": [{"idx": int, "level": int}]} — đủ mọi idx, level nguyên ≥ 2.'
)
_LEVELS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "levels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"idx": {"type": "integer"}, "level": {"type": "integer"}},
                "required": ["idx", "level"],
            },
        }
    },
    "required": ["levels"],
}
_PROMPT_GAP_SYS = (
    "Bạn tìm tiêu đề bị thiếu trong 1 danh sách đánh số của cây mục lục tài liệu y tế, dựa trên văn "
    "bản gốc được cung cấp cho từng khối.\n"
    'Trả về DUY NHẤT JSON hợp lệ: {"blocks": [{"i": int, "found": [{"number": int, "quote": str_or_null}]}]}\n\n'
    "QUY TẮC BẮT BUỘC:\n"
    "1. quote PHẢI là NGUYÊN VĂN 100% toàn bộ dòng tiêu đề đó, chép chính xác từ văn bản gốc (giữ "
    "nguyên dấu câu, hoa/thường, dấu tiếng Việt) — không diễn giải, không tự đặt tên.\n"
    "2. CHỈ trả tiêu đề nếu chắc chắn đó là 1 heading thật (không phải câu văn nội dung, không phải "
    "mục liệt kê thông thường) và số thứ tự của nó khớp đúng với số đang cần tìm.\n"
    "3. Không tìm thấy tiêu đề phù hợp cho 1 số → quote = null cho số đó. Thà null còn hơn đoán sai.\n"
    "4. Mỗi khối độc lập — không lấy tiêu đề của khối này gán cho khối khác."
)
_PROMPT_GAP_BLOCK_TMPL = """\
=== KHỐI {block_idx} ===
Văn bản gốc:
---
{window_text}
---
Mục cha: '{parent_title}'. Mục liền trước: '{before_title}'. Mục liền sau: '{after_title}'.
Cần tìm tiêu đề cho các số còn thiếu: {numbers}."""
class TocPrompts:
    PHASE1 = _PROMPT_PHASE1
    HEADING_DETECT_SYS = _PROMPT_HEADING_DETECT_SYS
    CHAPTERS_SYS = _PROMPT_CHAPTERS_SYS
    LEVELS_SYS = _PROMPT_LEVELS_SYS
    PHASE3_SYS = _PROMPT_PHASE3_SYS
    PHASE3_USER_TMPL = _PROMPT_PHASE3_USER_TMPL
    LANDMARK_SYS = _PROMPT_LANDMARK_SYS
    ANCHOR_SYS = _PROMPT_ANCHOR_SYS
    ANCHOR_USER_TMPL = _PROMPT_ANCHOR_USER_TMPL
    ANCHOR_MULTI_SYS = _PROMPT_ANCHOR_MULTI_SYS
    ANCHOR_MULTI_BLOCK_TMPL = _PROMPT_ANCHOR_MULTI_BLOCK_TMPL
    GAP_SYS = _PROMPT_GAP_SYS
    GAP_BLOCK_TMPL = _PROMPT_GAP_BLOCK_TMPL


_RE_ANCHOR_STRIP_LEAD = re.compile(r"^(\s*<a\s+[^>]+>\s*</a>\s*)+", re.IGNORECASE)
_RE_TOC_MARKER = re.compile(r"MUC\s*LUC|MỤC\s*LỤC|TABLE\s+OF\s+CONTENTS", re.IGNORECASE)
_RE_TABLE_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_RE_TABLE_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_RE_ANY_TAG = re.compile(r"<[^>]+>")
_RE_LEADING_FOLIO = re.compile(r"^(?:[ \t]*\d{1,4}[ \t]*\n)+")
_RE_INDEX_ENTRY = re.compile(r"^(\S.{3,199}?)[.…\s|]+(\d{1,4})$")


class DocumentLines:
    @staticmethod
    def of(text: str) -> list[str]:
        body = _RE_ANCHOR_STRIP_LEAD.sub("", text.lstrip()).lstrip()
        body = _RE_LEADING_FOLIO.sub("", body)
        body = _RE_TABLE_ROW.sub(DocumentLines._row_to_line, body)
        body = _RE_ANY_TAG.sub(" ", body)
        return [line.strip() for line in body.splitlines() if line.strip()]

    @staticmethod
    def _row_to_line(match: re.Match) -> str:
        cells = [_RE_ANY_TAG.sub(" ", c).strip() for c in _RE_TABLE_CELL.findall(match.group(1))]
        return "\n" + " | ".join(c for c in cells if c) + "\n"


@dataclass(frozen=True)
class IndexSignature:
    entries: int
    lines: int

    @property
    def ratio(self) -> float:
        return self.entries / self.lines if self.lines else 0.0


class IndexListingDetector:
    _MIN_TITLE_ALPHA = 6
    _PAGE_MIN_ENTRIES = 3
    _PAGE_MIN_RATIO = 0.5
    _BLOCK_MIN_ENTRIES = 6
    _BLOCK_MIN_RATIO = 0.7

    @classmethod
    def signature(cls, text: str) -> IndexSignature:
        lines = DocumentLines.of(text)
        return IndexSignature(sum(1 for line in lines if cls._is_entry(line)), len(lines))

    @classmethod
    def _is_entry(cls, line: str) -> bool:
        match = _RE_INDEX_ENTRY.match(line)
        if not match:
            return False
        title = match.group(1).strip(" .…|")
        alpha = sum(c.isalpha() for c in title)
        return alpha >= cls._MIN_TITLE_ALPHA and alpha >= sum(c.isdigit() for c in title)

    @classmethod
    def is_continuation_page(cls, text: str) -> bool:
        signature = cls.signature(text)
        return signature.entries >= cls._PAGE_MIN_ENTRIES and signature.ratio >= cls._PAGE_MIN_RATIO

    @classmethod
    def is_index_block(cls, text: str) -> bool:
        signature = cls.signature(text)
        return signature.entries >= cls._BLOCK_MIN_ENTRIES and signature.ratio >= cls._BLOCK_MIN_RATIO


@dataclass(frozen=True)
class TocScan:
    text: str
    start: int | None
    end: int | None

    @property
    def found(self) -> bool:
        return self.start is not None

    @property
    def merged_pages(self) -> int:
        if self.start is None or self.end is None:
            return 0
        return self.end - self.start


class PageTextScanner:
    _MAX_CONTINUATION_PAGES = 15

    def __init__(self, config: TocConfig) -> None:
        self._config = config

    @staticmethod
    def get_pages(text: str, n: int) -> str:
        if n <= 0:
            return text
        parts = text.split(PAGE_BREAK)
        return PAGE_BREAK.join(parts[:n]) if len(parts) > n else text

    @staticmethod
    def is_toc_continuation(page_text: str) -> bool:
        return IndexListingDetector.is_continuation_page(page_text)

    def scan_for_phase1(self, text: str) -> TocScan:
        n_pages = self._config.scan_pages
        pages = text.split(PAGE_BREAK)
        total = len(pages)
        toc_start = next((i for i in range(min(n_pages, total)) if _RE_TOC_MARKER.search(pages[i])), None)
        if toc_start is None:
            return TocScan(self.get_pages(text, n_pages), None, None)

        toc_end = toc_start
        for j in range(toc_start + 1, min(toc_start + self._MAX_CONTINUATION_PAGES, total)):
            if not self.is_toc_continuation(pages[j]):
                break
            toc_end = j

        base_pages = list(pages[:min(n_pages, total)])
        for k in range(toc_start + 1, toc_end + 1):
            labeled = f"\n[MỤC LỤC - tiếp theo, trang {k + 1}]\n" + pages[k]
            if k < len(base_pages):
                base_pages[k] = labeled
            else:
                base_pages.append(labeled)
        if toc_end > toc_start:
            logger.info(
                "  Phase 1: MỤC LỤC trang %d–%d (%d trang tiếp theo được ghép)",
                toc_start + 1, toc_end + 1, toc_end - toc_start,
            )
        return TocScan(PAGE_BREAK.join(base_pages), toc_start, toc_end)


class Phase1Builder:
    def __init__(self, caller: OpenAiJsonCaller, scanner: PageTextScanner) -> None:
        self._caller = caller
        self._scanner = scanner

    def run(self, text: str, filename: str) -> tuple[dict, TocScan]:
        scan = self._scanner.scan_for_phase1(text)
        user = f"source_file = {filename}\n\nNội dung văn bản {self._label(scan)}:\n{scan.text}"
        logger.info("  Phase 1: %d chars ...", len(scan.text))
        try:
            return self._caller.call(TocPrompts.PHASE1, user), scan
        except Exception as e:
            logger.warning("  Phase 1 failed: %s", e)
            return {"chapters": [], "source_file": filename}, scan

    @staticmethod
    def _label(scan: TocScan) -> str:
        if not scan.found:
            return "(trang đầu, KHÔNG tìm thấy MỤC LỤC)"
        if scan.merged_pages:
            return f"(trang đầu + MỤC LỤC {scan.merged_pages + 1} trang đã ghép và đánh nhãn đầy đủ)"
        return "(trang đầu + MỤC LỤC 1 trang, không có trang tiếp theo)"


_RE_TAG_STRIP = re.compile(r"<[^>]+>")


class LeafElementSummary:
    @staticmethod
    def build(
        result: ParseResult, leaves: list[LeafElement], toc_end_page: int | None, chunk_chars: int,
        table_cells_by_leaf: dict[str, list[TableCellElement]] | None = None, chunk_overlap: int = 0,
    ) -> list[dict]:
        out = []
        counter = 0
        for leaf in leaves:
            if leaf.type in NOISE_TYPES:
                continue
            if toc_end_page is not None and leaf.page <= toc_end_page:
                continue
            if IndexListingDetector.is_index_block(result.markdown[leaf.span[0]:leaf.span[1]]):
                continue
            cells = table_cells_by_leaf.get(leaf.id) if table_cells_by_leaf and leaf.type == "table" else None
            if chunk_overlap > 0:
                pieces = LeafElementSummary._chunks(LeafElementSummary.full_text(result, leaf, cells), chunk_chars, chunk_overlap)
            elif cells:
                pieces = [LeafElementSummary._table_preview(result, cells, chunk_chars)]
            else:
                pieces = [LeafElementSummary._leaf_text(result, leaf)[:chunk_chars]]
            for piece in pieces:
                out.append({"i": counter, "id": leaf.id, "t": piece, "type": leaf.type, "page": leaf.page})
                counter += 1
        return out

    @staticmethod
    def full_text(result: ParseResult, leaf: LeafElement, cells: list[TableCellElement] | None) -> str:
        return LeafElementSummary._table_text(result, cells) if cells else LeafElementSummary._leaf_text(result, leaf)

    @staticmethod
    def _leaf_text(result: ParseResult, leaf: LeafElement) -> str:
        return re.sub(r"\s+", " ", _RE_TAG_STRIP.sub(" ", result.markdown[leaf.span[0]:leaf.span[1]])).strip()

    @staticmethod
    def _table_text(result: ParseResult, cells: list[TableCellElement]) -> str:
        parts = []
        for cell in cells:
            text = re.sub(r"\s+", " ", _RE_TAG_STRIP.sub(" ", result.markdown[cell.span[0]:cell.span[1]])).strip()
            if text:
                parts.append(text)
        return " | ".join(parts)

    @staticmethod
    def _chunks(text: str, size: int, overlap: int) -> list[str]:
        if len(text) <= size:
            return [text]
        step = max(1, size - overlap)
        pieces: list[str] = []
        start = 0
        while start < len(text):
            pieces.append(text[start:start + size])
            if start + size >= len(text):
                break
            start += step
        return pieces

    @staticmethod
    def _table_preview(result: ParseResult, cells: list[TableCellElement], limit: int) -> str:
        texts = []
        for cell in cells:
            text = _RE_TAG_STRIP.sub(" ", result.markdown[cell.span[0]:cell.span[1]])
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                texts.append(text)
        if not texts:
            return ""
        per_cell = max(1, limit // len(texts))
        return " | ".join(t[:per_cell] for t in texts)[:limit]

    @staticmethod
    def toc_end_leaf_page(result: ParseResult, toc_end_md_page: int | None) -> int | None:
        if toc_end_md_page is None:
            return None
        breaks = [m.start() for m in re.finditer(re.escape(PAGE_BREAK), result.markdown)]
        cutoff_char = breaks[toc_end_md_page] if toc_end_md_page < len(breaks) else len(result.markdown)
        candidates = [p.page for p in result.structure.children if p.type == "page" and p.span and p.span[0] < cutoff_char]
        return max(candidates) if candidates else toc_end_md_page


class ElementText:
    def __init__(self, result: ParseResult, leaves: list[LeafElement],
                 table_cells_by_leaf: dict[str, list[TableCellElement]]) -> None:
        self._result = result
        self._leaf_by_id = {leaf.id: leaf for leaf in leaves if leaf.id}
        self._cells = table_cells_by_leaf
        self._cache: dict[str, str] = {}

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", _RE_MD_NOISE.sub("", text)).strip().lower()

    def haystack(self, element_id: str) -> str:
        if element_id not in self._cache:
            leaf = self._leaf_by_id.get(element_id)
            cells = self._cells.get(element_id) if leaf is not None and leaf.type == "table" else None
            body = LeafElementSummary.full_text(self._result, leaf, cells) if leaf is not None else ""
            self._cache[element_id] = self.normalize(body)
        return self._cache[element_id]

    def contains(self, element_id: str, title: str) -> bool:
        needle = self.normalize(TocTree.norm_title(title))
        return bool(needle) and needle in self.haystack(element_id)


def _lis_nondecreasing(anchors: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if len(anchors) <= 1:
        return list(anchors)
    tails: list[int] = []
    tail_idx: list[int] = []
    parent = [-1] * len(anchors)
    for i, (_, pos) in enumerate(anchors):
        lo, hi = 0, len(tails)
        while lo < hi:
            mid = (lo + hi) // 2
            if tails[mid] <= pos:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(tails):
            tails.append(pos)
            tail_idx.append(i)
        else:
            tails[lo] = pos
            tail_idx[lo] = i
        parent[i] = tail_idx[lo - 1] if lo > 0 else -1
    result: list[tuple[int, int]] = []
    idx = tail_idx[-1]
    while idx >= 0:
        result.append(anchors[idx])
        idx = parent[idx]
    result.reverse()
    return result


class HeadingElementMatcher:
    def __init__(self, caller: OpenAiJsonCaller, config: TocConfig) -> None:
        self._caller = caller
        self._config = config

    def match(self, chapters: list, result: ParseResult, toc_end_md_page: int | None) -> None:
        flat_refs = TocTree.flatten_refs(chapters)
        if not flat_refs:
            return
        leaves = flatten_leaves(result)
        toc_end_leaf_page = LeafElementSummary.toc_end_leaf_page(result, toc_end_md_page)
        table_cells_by_leaf = index_table_cells(result)
        summary = LeafElementSummary.build(result, leaves, toc_end_leaf_page, 1500, table_cells_by_leaf=table_cells_by_leaf)
        # Không dùng figure làm heading element vì chunk figure sẽ bị loại bỏ → content rỗng
        summary = [e for e in summary if e.get("type") != "figure"]
        if not summary:
            return

        valid_ids = {e["id"] for e in summary}
        id_pos = {e["id"]: j for j, e in enumerate(summary)}
        n_elem = len(summary)
        n_toc = len(flat_refs)
        logger.info("  Phase 3: %d TOC nodes, %d elements", n_toc, n_elem)

        landmarks = self._phase3_landmark_pass(flat_refs, summary, valid_ids)
        initial_landmarks = dict(landmarks)
        self._phase3_bounded_pass(flat_refs, summary, valid_ids, id_pos, landmarks, n_elem)
        self._phase3_order_check(flat_refs, id_pos, initial_landmarks)
        self._phase3_orphan_pass(flat_refs, summary, valid_ids, id_pos, landmarks, n_elem)
        self._phase3_order_check(flat_refs, id_pos, initial_landmarks)
        text = ElementText(result, leaves, table_cells_by_leaf)
        self._phase3_verify_pass(flat_refs, summary, id_pos, text)
        n_text_search = self._phase3_text_search_fallbacks(flat_refs, summary, valid_ids, id_pos, text)
        if n_text_search:
            logger.info("  Phase 3 [TextSearch]: %d node matched by text search", n_text_search)
        self._phase3_cascade_inherit(flat_refs)
        self._phase3_deterministic_fallbacks(flat_refs, id_pos)

        matched = sum(1 for _, n in flat_refs if n.get("heading_element_id"))
        logger.info("  Phase 3: %d/%d matched after all passes", matched, n_toc)

    def _phase3_verify_pass(self, flat_refs: list[tuple], summary: list[dict], id_pos: dict[str, int], text: ElementText) -> None:
        radius = self._config.verify_radius
        fixed = 0
        for _, node in flat_refs:
            eid = node.get("heading_element_id")
            if eid not in id_pos or text.contains(eid, node.get("title", "")):
                continue
            here = id_pos[eid]
            near = range(max(0, here - radius), min(len(summary), here + radius + 1))
            better = [j for j in near if text.contains(summary[j]["id"], node.get("title", ""))]
            if better:
                node["heading_element_id"] = summary[min(better, key=lambda j: abs(j - here))]["id"]
                fixed += 1
        if fixed:
            logger.info("  Phase 3 [Verify]: sửa %d node có element không chứa tiêu đề", fixed)

    def _phase3_landmark_pass(self, flat_refs: list[tuple], summary: list[dict], valid_ids: set[str]) -> dict[int, int]:
        chapter_indices = [i for i, (path, node) in enumerate(flat_refs) if "/" not in path and not node.get("heading_element_id")]
        if not chapter_indices:
            return {}
        id_pos = {e["id"]: j for j, e in enumerate(summary)}
        chapter_list = "\n".join(f"[{i}] {flat_refs[i][0]}" for i in chapter_indices)
        step = self._config.landmark_batch_size - self._config.landmark_overlap
        n_elem = len(summary)
        raw_hits: dict[int, list[int]] = {i: [] for i in chapter_indices}

        for b_start in range(0, n_elem, step):
            b_end = min(b_start + self._config.landmark_batch_size, n_elem)
            window = summary[b_start:b_end]
            element_list = "\n".join(f"{e['i']:4d} | id={e['id']} | {e['t']}" for e in window)
            user = (
                f"DANH SÁCH CHƯƠNG CẤP 1 CẦN TÌM (toc_idx — title):\n{chapter_list}\n\n"
                f"ĐOẠN TÀI LIỆU [{b_start}–{b_end - 1}]:\n{element_list}\n\n"
                "NHIỆM VỤ: rà soát toàn bộ đoạn trên. Với mỗi toc_idx: nếu thấy heading chương → ghi "
                "element_id; không thấy → null. Trả về đủ tất cả toc_idx."
            )
            try:
                result = self._caller.call(TocPrompts.LANDMARK_SYS, user)
                for m in result.get("mappings", []):
                    idx, eid = m.get("toc_idx"), self._phase3_sanitize(m.get("element_id"), valid_ids)
                    if idx in chapter_indices and eid and eid in id_pos:
                        raw_hits[idx].append(id_pos[eid])
            except Exception as e:
                logger.warning("    Landmark batch [%d:%d] failed: %s", b_start, b_end, e)

        deduped = {idx: min(pos) for idx, pos in raw_hits.items() if pos}
        valid_pairs = _lis_nondecreasing(sorted(deduped.items()))
        landmarks: dict[int, int] = {}
        for idx, pos in valid_pairs:
            flat_refs[idx][1]["heading_element_id"] = summary[pos]["id"]
            landmarks[idx] = pos
        logger.info("  Phase 3 [Landmark]: %d/%d chapters mapped", len(landmarks), len(chapter_indices))
        return landmarks

    def _phase3_window_for(
        self, toc_s: int, toc_e: int, landmarks: dict[int, int], n_elem: int, buffer: int | None = None
    ) -> tuple[int, int]:
        if not landmarks:
            return 0, n_elem
        buffer = self._config.window_buffer if buffer is None else buffer
        sorted_lm = sorted(landmarks.items())
        prev_pos = max((pos for t, pos in sorted_lm if t <= toc_s), default=0)
        next_pos = min((pos for t, pos in sorted_lm if t >= toc_e), default=n_elem)
        prev_prev_pos = max((pos for t, pos in sorted_lm if t < toc_s), default=0)
        mid_prev = (prev_prev_pos + prev_pos) // 2 if prev_prev_pos > 0 else 0
        win_s = max(mid_prev, prev_pos - buffer)
        win_e = min(n_elem, next_pos + buffer)
        if win_s >= win_e:
            win_s, win_e = max(0, prev_pos - buffer), min(n_elem, prev_pos + buffer * 4)
        return win_s, win_e

    def _phase3_bounded_pass(self, flat_refs, summary, valid_ids, id_pos, landmarks, n_elem) -> None:
        chapter_starts = sorted(i for i, (path, _) in enumerate(flat_refs) if "/" not in path) or [0]
        n_toc = len(flat_refs)
        ranges = [
            (s, chapter_starts[i + 1] if i + 1 < len(chapter_starts) else n_toc)
            for i, s in enumerate(chapter_starts)
        ]
        for chap_s, chap_e in ranges:
            pending = [
                i for i in range(chap_s, chap_e)
                if not flat_refs[i][1].get("heading_element_id")
                and flat_refs[i][0].count("/") < self._config.deep_inherit_cutoff
            ]
            if not pending:
                continue
            win_s, win_e = self._phase3_window_for(chap_s, chap_e, landmarks, n_elem)
            group_size = self._config.subgroup_size if len(pending) <= 50 else min(self._config.subgroup_size * 2, 16)
            for k in range(0, len(pending), group_size):
                batch = pending[k:k + group_size]
                self._phase3_resolve_batch(flat_refs, summary, valid_ids, id_pos, landmarks, batch, win_s, win_e, n_elem)

    def _phase3_resolve_batch(self, flat_refs, summary, valid_ids, id_pos, landmarks, batch, win_s, win_e, n_elem, buffer=None) -> None:
        batch = [i for i in batch if not flat_refs[i][1].get("heading_element_id")]
        if not batch:
            return
        b_win_s, b_win_e = self._phase3_window_for(batch[0], batch[-1] + 1, landmarks, n_elem, buffer)
        win_s, win_e = max(win_s, b_win_s), min(win_e, b_win_e)
        if win_e - win_s > self._config.max_window:
            win_e = win_s + self._config.max_window
        window = summary[win_s:win_e]

        user = self._phase3_build_user(flat_refs, batch, window)
        shrink = max(10, len(window) // 5)
        while len(user) > self._config.max_user_chars and len(window) > shrink:
            window = window[:max(shrink, len(window) - shrink)]
            user = self._phase3_build_user(flat_refs, batch, window)

        try:
            result = self._caller.call(TocPrompts.PHASE3_SYS, user)
            mapped_positions = self._phase3_apply(result.get("mappings", []), flat_refs, valid_ids, id_pos, win_s, win_e)
            for i in batch:
                eid = flat_refs[i][1].get("heading_element_id")
                if eid and eid in id_pos and i not in landmarks:
                    landmarks[i] = id_pos[eid]

            still_null = [i for i in batch if not flat_refs[i][1].get("heading_element_id")]
            if still_null and mapped_positions:
                self._phase3_expand_and_retry(flat_refs, summary, valid_ids, id_pos, landmarks, still_null, win_s, win_e, n_elem, mapped_positions)
        except Exception as e:
            logger.warning("    Batch [%d:%d] failed: %s", win_s, win_e, e)

    def _phase3_expand_and_retry(self, flat_refs, summary, valid_ids, id_pos, landmarks, still_null, win_s, win_e, n_elem, mapped_positions) -> None:
        win_size = max(1, win_e - win_s)
        edge_size = max(5, int(win_size * self._config.edge_ratio))
        near_left = any(p < win_s + edge_size for p in mapped_positions)
        near_right = any(p > win_e - 1 - edge_size for p in mapped_positions)
        if not (near_left or near_right):
            return
        expansion = win_size * (self._config.expand_factor - 1)
        exp_s = max(0, win_s - expansion) if near_left else win_s
        exp_e = min(n_elem, win_e + expansion) if near_right else win_e
        if exp_e - exp_s > self._config.max_window:
            center = (min(mapped_positions) + max(mapped_positions)) // 2
            half = self._config.max_window // 2
            exp_s = max(0, center - half)
            exp_e = min(n_elem, exp_s + self._config.max_window)

        window = summary[exp_s:exp_e]
        user = self._phase3_build_user(flat_refs, still_null, window)
        tries = 0
        shrink = max(10, len(window) // 5)
        while len(user) > self._config.max_user_chars and len(window) > shrink and tries < self._config.max_expand_tries:
            window = window[:max(shrink, len(window) - shrink)]
            user = self._phase3_build_user(flat_refs, still_null, window)
            tries += 1
        try:
            result = self._caller.call(TocPrompts.PHASE3_SYS, user)
            self._phase3_apply(result.get("mappings", []), flat_refs, valid_ids, id_pos, exp_s, exp_e)
            for i in still_null:
                eid = flat_refs[i][1].get("heading_element_id")
                if eid and eid in id_pos and i not in landmarks:
                    landmarks[i] = id_pos[eid]
        except Exception as e:
            logger.warning("    Expand retry failed: %s", e)

    def _phase3_orphan_pass(self, flat_refs, summary, valid_ids, id_pos, landmarks, n_elem) -> None:
        orphans = [
            i for i, (path, node) in enumerate(flat_refs)
            if not node.get("heading_element_id") and path.count("/") < self._config.deep_inherit_cutoff
        ]
        if not orphans:
            return
        logger.info("  Phase 3 [Orphan]: %d nodes still null", len(orphans))
        for k in range(0, len(orphans), self._config.subgroup_size):
            sub = orphans[k:k + self._config.subgroup_size]
            buffer = self._config.window_buffer * 2
            win_s, win_e = self._phase3_window_for(sub[0], sub[-1] + 1, landmarks, n_elem, buffer)
            self._phase3_resolve_batch(
                flat_refs, summary, valid_ids, id_pos, landmarks, sub, win_s, win_e, n_elem, buffer
            )

    def _phase3_build_user(self, flat_refs: list[tuple], batch: list[int], window: list[dict]) -> str:
        toc_list = "\n".join(f"[{i}] {flat_refs[i][0]}" for i in batch)
        element_list = "\n".join(f"{e['i']:4d} | id={e['id']} | {e['t']}" for e in window)
        return TocPrompts.PHASE3_USER_TMPL.format(toc_list=toc_list, element_list=element_list)

    def _phase3_apply(self, mappings: list[dict], flat_refs: list[tuple], valid_ids: set[str], id_pos: dict[str, int], win_s: int, win_e: int) -> list[int]:
        mapped_positions: list[int] = []
        for m in mappings:
            idx = m.get("toc_idx")
            if not isinstance(idx, int) or not (0 <= idx < len(flat_refs)) or flat_refs[idx][1].get("heading_element_id"):
                continue
            eid = self._phase3_sanitize(m.get("element_id"), valid_ids)
            if eid and eid in id_pos:
                pos = id_pos[eid]
                if (win_s - 3) <= pos < (win_e + 3):
                    flat_refs[idx][1]["heading_element_id"] = eid
                    mapped_positions.append(pos)
        return mapped_positions

    @staticmethod
    def _phase3_sanitize(eid, valid_ids: set[str]) -> str | None:
        if isinstance(eid, str) and eid in valid_ids:
            return eid
        return None

    @staticmethod
    def _phase3_order_check(flat_refs: list[tuple], id_pos: dict[str, int], landmarks: dict[int, int]) -> None:
        lm_idxs = set(landmarks.keys())
        assigned = [
            (i, id_pos[node.get("heading_element_id")])
            for i, (_, node) in enumerate(flat_refs)
            if node.get("heading_element_id") and node["heading_element_id"] in id_pos and i not in lm_idxs
        ]
        if len(assigned) < 2:
            return
        valid_set = {i for i, _ in _lis_nondecreasing(assigned)}
        for i, _ in assigned:
            if i not in valid_set:
                del flat_refs[i][1]["heading_element_id"]

    @staticmethod
    def _phase3_cascade_inherit(flat_refs: list[tuple]) -> None:
        path_to_node = {p: n for p, n in flat_refs}
        if not flat_refs:
            return
        max_depth = max(p.count("/") for p, _ in flat_refs)
        for depth in range(4, max_depth + 1):
            for path, node in flat_refs:
                if node.get("heading_element_id") or path.count("/") != depth:
                    continue
                parent = path_to_node.get(path.rsplit("/", 1)[0])
                if parent and parent.get("heading_element_id"):
                    node["heading_element_id"] = parent["heading_element_id"]

    @staticmethod
    def _phase3_text_search_fallbacks(
        flat_refs: list[tuple], summary: list[dict], valid_ids: set[str], id_pos: dict[str, int], text: ElementText,
    ) -> int:
        """Tìm heading element cho các node chưa match bằng cách so khớp text thuần túy, không gọi LLM."""
        matched = 0
        n_elem = len(summary)
        path_to_node = {p: n for p, n in flat_refs}
        for i, (path, node) in enumerate(flat_refs):
            if node.get("heading_element_id"):
                continue
            title = node.get("title", "")
            if _RE_PLACEHOLDER_TITLE.search(title) or len(TocTree.norm_title(title)) < 6:
                continue
            prev_pos = 0
            next_pos = n_elem
            for j in range(i - 1, -1, -1):
                eid = flat_refs[j][1].get("heading_element_id")
                if eid and eid in id_pos:
                    prev_pos = max(prev_pos, id_pos[eid] + 1)
                    break
            for j in range(i + 1, len(flat_refs)):
                eid = flat_refs[j][1].get("heading_element_id")
                if eid and eid in id_pos:
                    next_pos = min(next_pos, id_pos[eid])
                    break
            if "/" in path:
                parent_path = path.rsplit("/", 1)[0]
                parent = path_to_node.get(parent_path)
                if parent and parent.get("heading_element_id") in id_pos:
                    parent_pos = id_pos[parent["heading_element_id"]]
                    if parent_pos + 1 > prev_pos:
                        prev_pos = parent_pos + 1
                child_prefix = path + "/"
                child_positions = [
                    id_pos[n["heading_element_id"]]
                    for p, n in flat_refs
                    if p.startswith(child_prefix) and n.get("heading_element_id") in id_pos
                ]
                if child_positions:
                    min_child = min(child_positions)
                    if min_child < next_pos:
                        next_pos = min_child
            if prev_pos >= next_pos:
                continue
            for j in range(prev_pos, next_pos):
                e = summary[j]
                if e["id"] not in valid_ids:
                    continue
                if text.contains(e["id"], title):
                    node["heading_element_id"] = e["id"]
                    matched += 1
                    break
        return matched

    @staticmethod
    def _phase3_deterministic_fallbacks(flat_refs: list[tuple], id_pos: dict[str, int]) -> None:
        for i, (_, node) in enumerate(flat_refs):
            if node.get("heading_element_id"):
                continue
            for j in range(i - 1, -1, -1):
                pred = flat_refs[j][1].get("heading_element_id")
                if pred:
                    node["heading_element_id"] = pred
                    break

        for i, (path, node) in enumerate(flat_refs):
            if node.get("heading_element_id"):
                continue
            prefix = path + "/"
            desc = [
                (id_pos[fnode["heading_element_id"]], fnode["heading_element_id"])
                for fpath, fnode in flat_refs
                if fpath.startswith(prefix) and fnode.get("heading_element_id") in id_pos
            ]
            if desc:
                node["heading_element_id"] = min(desc, key=lambda x: x[0])[1]

        for path, node in flat_refs:
            if node.get("heading_element_id") or "/" not in path:
                continue
            parent = next((n for p, n in flat_refs if p == path.rsplit("/", 1)[0]), None)
            if parent and parent.get("heading_element_id"):
                node["heading_element_id"] = parent["heading_element_id"]


class GapFillResolver:
    def __init__(self, caller: OpenAiJsonCaller, config: TocConfig) -> None:
        self._caller = caller
        self._config = config

    def resolve(self, chapters: list, result: ParseResult, leaves: list[LeafElement]) -> None:
        candidates = TocTree.find_gap_candidates(chapters)
        if not candidates:
            return
        id_to_leaf = {leaf.id: leaf for leaf in leaves if leaf.id}
        windows = self._bound(candidates, id_to_leaf, len(result.markdown))
        if not windows:
            return
        proposals = self._propose(windows, result.markdown)
        starts = [leaf.span[0] for leaf in leaves]
        applied = self._apply(proposals, result.markdown, leaves, starts)
        if applied:
            logger.info("  GapFill: thêm %d node còn thiếu", applied)

    @staticmethod
    def _bound(
        candidates: list[GapCandidate], id_to_leaf: dict[str, LeafElement], doc_end: int
    ) -> list[tuple[GapCandidate, int, int]]:
        windows: list[tuple[GapCandidate, int, int]] = []
        for c in candidates:
            floor = 0 if c.before is None else GapFillResolver._position(c.before, id_to_leaf)
            ceiling = GapFillResolver._position(c.after, id_to_leaf)
            if floor is None or ceiling is None or floor >= ceiling:
                continue
            windows.append((c, floor, min(ceiling, doc_end)))
        return windows

    @staticmethod
    def _position(node: dict, id_to_leaf: dict[str, LeafElement]) -> int | None:
        leaf = id_to_leaf.get(node.get("heading_element_id"))
        return leaf.span[0] if leaf else None

    def _propose(
        self, windows: list[tuple[GapCandidate, int, int]], markdown: str
    ) -> list[tuple[GapCandidate, int, int, list[tuple[int, str | None]]]]:
        found_by_window: dict[int, list[tuple[int, str | None]]] = {}
        for batch in self._batches(windows):
            user = "\n\n".join(self._block(local, windows[g], markdown) for local, g in enumerate(batch))
            try:
                response = self._caller.call(TocPrompts.GAP_SYS, user)
            except Exception as e:
                logger.warning("  GapFill: LLM call failed: %s", e)
                continue
            for block in response.get("blocks", []):
                if not isinstance(block, dict) or not isinstance(block.get("i"), int):
                    continue
                local = block["i"]
                if not 0 <= local < len(batch):
                    continue
                found_by_window[batch[local]] = [
                    (item.get("number"), item.get("quote"))
                    for item in block.get("found", [])
                    if isinstance(item, dict) and isinstance(item.get("number"), int)
                ]
        return [(c, floor, ceiling, found_by_window.get(i, [])) for i, (c, floor, ceiling) in enumerate(windows)]

    def _batches(self, windows: list[tuple[GapCandidate, int, int]]) -> list[list[int]]:
        batches: list[list[int]] = []
        current: list[int] = []
        used = 0
        for i, (_, floor, ceiling) in enumerate(windows):
            cost = ceiling - floor
            if current and used + cost > self._config.gap_batch_char_budget:
                batches.append(current)
                current, used = [], 0
            current.append(i)
            used += cost
        if current:
            batches.append(current)
        return batches

    @staticmethod
    def _block(block_idx: int, window: tuple[GapCandidate, int, int], markdown: str) -> str:
        c, floor, ceiling = window
        return TocPrompts.GAP_BLOCK_TMPL.format(
            block_idx=block_idx, window_text=markdown[floor:ceiling],
            parent_title=c.parent_title or "(gốc)",
            before_title=c.before.get("title", "(đầu tài liệu)") if c.before else "(đầu tài liệu)",
            after_title=c.after.get("title", ""),
            numbers=", ".join(str(n) for n in c.missing),
        )

    @staticmethod
    def _apply(
        proposals: list[tuple[GapCandidate, int, int, list[tuple[int, str | None]]]],
        markdown: str, leaves: list[LeafElement], starts: list[int],
    ) -> int:
        applied = 0
        for candidate, floor, ceiling, found in proposals:
            window_text = markdown[floor:ceiling]
            for _, quote in found:
                if not quote or window_text.count(quote) != 1:
                    continue
                leaf = GapFillResolver._leaf_at(leaves, starts, floor + window_text.find(quote))
                if leaf is None or leaf.type in MEDIA_TYPES:
                    # Không điền heading vào figure/table vì chunk media bị loại/sẽ rỗng
                    continue
                title = TocTree.norm_title(quote)
                if not title:
                    continue
                TocTree.apply_gap_fill(candidate, title, leaf.id)
                applied += 1
        return applied

    @staticmethod
    def _leaf_at(leaves: list[LeafElement], starts: list[int], pos: int) -> LeafElement | None:
        idx = bisect.bisect_right(starts, pos) - 1
        if idx < 0:
            return None
        leaf = leaves[idx]
        return leaf if leaf.span[0] <= pos < leaf.span[1] else None


class IntraElementAnchorResolver:
    def __init__(self, caller: OpenAiJsonCaller, config: TocConfig) -> None:
        self._caller = caller
        self._config = config

    def resolve(self, chapters: list, result: ParseResult, leaves: list[LeafElement]) -> None:
        shared_groups = self._phase4_collect_shared_groups(chapters, leaves, result)
        if not shared_groups:
            return
        total_nodes = sum(len(nodes) for nodes, _ in shared_groups)

        call_ready, needs_windowing = self._phase4_partition_by_call_limits(shared_groups)

        resolved = 0
        n_calls = 0
        for call_batch in self._phase4_pack_into_call_batches(call_ready):
            resolved += self._phase4_resolve_call_batch(call_batch)
            n_calls += 1
        for nodes, block_text in needs_windowing:
            group_resolved, group_calls = self._phase4_resolve_by_progressive_windows(nodes, block_text)
            resolved += group_resolved
            n_calls += group_calls

        logger.info(
            "  Phase 4 [Anchor]: %d/%d shared-element TOC nodes resolved to verbatim anchor "
            "(%d groups [%d needing windowed sub-batches], %d LLM calls)",
            resolved, total_nodes, len(shared_groups), len(needs_windowing), n_calls,
        )

    @staticmethod
    def _phase4_collect_shared_groups(
        chapters: list, leaves: list[LeafElement], result: ParseResult
    ) -> list[tuple[list[dict], str]]:
        flat_refs = TocTree.flatten_refs(chapters)
        leaf_by_id = {leaf.id: leaf for leaf in leaves if leaf.id}
        by_element: dict[str, list[dict]] = {}
        for _, node in flat_refs:
            eid = node.get("heading_element_id")
            if eid:
                by_element.setdefault(eid, []).append(node)

        groups: list[tuple[list[dict], str]] = []
        for eid, nodes in by_element.items():
            if len(nodes) < 2:
                continue
            leaf = leaf_by_id.get(eid)
            if leaf is None or leaf.span is None:
                continue
            groups.append((nodes, result.markdown[leaf.span[0]:leaf.span[1]]))
        return groups

    def _phase4_partition_by_call_limits(
        self, groups: list[tuple[list[dict], str]]
    ) -> tuple[list[tuple[list[dict], str]], list[tuple[list[dict], str]]]:
        call_ready, needs_windowing = [], []
        for nodes, block_text in groups:
            fits = (
                len(nodes) <= self._config.anchor_titles_per_call
                and len(block_text) <= self._config.anchor_block_char_limit
            )
            (call_ready if fits else needs_windowing).append((nodes, block_text))
        return call_ready, needs_windowing

    def _phase4_pack_into_call_batches(
        self, groups: list[tuple[list[dict], str]]
    ) -> list[list[tuple[list[dict], str]]]:
        batches: list[list[tuple[list[dict], str]]] = []
        current: list[tuple[list[dict], str]] = []
        chars_used = 0
        for nodes, block_text in groups:
            cost = len(block_text) + sum(len(n.get("title", "")) for n in nodes) + 60
            batch_full = len(current) >= self._config.anchor_groups_per_batch
            over_budget = current and chars_used + cost > self._config.anchor_batch_char_budget
            if current and (batch_full or over_budget):
                batches.append(current)
                current, chars_used = [], 0
            current.append((nodes, block_text))
            chars_used += cost
        if current:
            batches.append(current)
        return batches

    def _phase4_resolve_call_batch(self, call_batch: list[tuple[list[dict], str]]) -> int:
        if len(call_batch) == 1:
            nodes, block_text = call_batch[0]
            return self._phase4_call_llm_for_one_group(nodes, block_text)

        block_prompts = []
        for block_idx, (nodes, block_text) in enumerate(call_batch):
            title_list = "\n".join(f"[{j}] {n.get('title', '')}" for j, n in enumerate(nodes))
            block_prompts.append(
                TocPrompts.ANCHOR_MULTI_BLOCK_TMPL.format(block_idx=block_idx, block_text=block_text, title_list=title_list)
            )
        user = "\n\n".join(block_prompts)
        try:
            out = self._caller.call(TocPrompts.ANCHOR_MULTI_SYS, user)
        except Exception as e:
            logger.warning("    Anchor batch (%d groups) failed: %s", len(call_batch), e)
            return 0

        mappings_by_block: dict[int, list[dict]] = {}
        for m in out.get("anchors", []):
            block_idx = m.get("block_idx")
            if isinstance(block_idx, int) and 0 <= block_idx < len(call_batch):
                mappings_by_block.setdefault(block_idx, []).append(m)

        resolved = 0
        for block_idx, (nodes, block_text) in enumerate(call_batch):
            resolved += self._phase4_verify_and_apply(nodes, block_text, mappings_by_block.get(block_idx, []))
        return resolved

    def _phase4_call_llm_for_one_group(self, nodes: list[dict], block_text: str) -> int:
        title_list = "\n".join(f"[{j}] {n.get('title', '')}" for j, n in enumerate(nodes))
        user = TocPrompts.ANCHOR_USER_TMPL.format(block_text=block_text, title_list=title_list)
        try:
            out = self._caller.call(TocPrompts.ANCHOR_SYS, user)
        except Exception as e:
            logger.warning("    Anchor resolution failed: %s", e)
            return 0
        return self._phase4_verify_and_apply(nodes, block_text, out.get("anchors", []))

    def _phase4_resolve_by_progressive_windows(self, nodes: list[dict], block_text: str) -> tuple[int, int]:
        resolved = 0
        calls = 0
        floor = 0
        window_size = self._config.anchor_titles_per_call
        for k in range(0, len(nodes), window_size):
            window_nodes = nodes[k:k + window_size]
            window_text = block_text[floor:floor + self._config.anchor_block_char_limit]
            if not window_text:
                break
            title_list = "\n".join(f"[{j}] {n.get('title', '')}" for j, n in enumerate(window_nodes))
            user = TocPrompts.ANCHOR_USER_TMPL.format(block_text=window_text, title_list=title_list)
            calls += 1
            try:
                out = self._caller.call(TocPrompts.ANCHOR_SYS, user)
            except Exception as e:
                logger.warning("    Anchor window failed: %s", e)
                continue
            n_resolved, last_match_end = self._phase4_verify_and_apply(
                window_nodes, window_text, out.get("anchors", []), return_last_match_end=True
            )
            resolved += n_resolved
            if last_match_end is not None:
                floor += last_match_end
        return resolved, calls

    @staticmethod
    def _phase4_verify_and_apply(
        nodes: list[dict], block_text: str, llm_mappings: list[dict], return_last_match_end: bool = False
    ):
        positions: dict[int, tuple[str, int]] = {}
        for m in llm_mappings:
            j, anchor = m.get("toc_idx"), m.get("anchor")
            if not isinstance(j, int) or not (0 <= j < len(nodes)) or not isinstance(anchor, str):
                continue
            anchor = anchor.strip()
            if anchor and block_text.count(anchor) == 1:
                positions[j] = (anchor, block_text.find(anchor))

        ordered = sorted(positions.items())
        valid_js = {j for j, _ in _lis_nondecreasing([(j, pos) for j, (_, pos) in ordered])}
        last_match_end = None
        for j, (anchor, pos) in positions.items():
            if j in valid_js:
                nodes[j]["heading_anchor"] = anchor
                last_match_end = max(last_match_end or 0, pos + len(anchor))
        if return_last_match_end:
            return len(valid_js), last_match_end
        return len(valid_js)


_RE_LBL_CHAPTER = re.compile(r"^\s*(?:CHƯƠNG|CHUONG|PHẦN|PHAN|CHAPTER|PART|BÀI|BAI)\s+(\d+|[IVXLCDM]+)\b", re.IGNORECASE)
_RE_LBL_DECIMAL = re.compile(r"^\s*(\d+(?:\.\d+)*)(?=[.)\s]|$)")
_RE_LBL_ROMAN = re.compile(r"^\s*([IVXLCDM]+)[.)](?=\s|$)")
_RE_LBL_LETTER = re.compile(r"^\s*([A-Za-zĐ])[.)](?=\s|$)")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_RE_MD_NOISE = re.compile(r"[#*`]")
_RE_CAPTION = re.compile(r"^(?:Hình|Bảng|Biểu\s*đồ|Sơ\s*đồ|Ảnh|Figure|Table)\s+\d", re.IGNORECASE)
_RE_EMBED_NUM = re.compile(r"(?<=\s)(\d+(?:\.\d+)*)[.)]\s+(?=\S)")
_RE_TOPLEVEL_PART = re.compile(
    r"^\s*(?:TÀI LIỆU THAM KHẢO|TAI LIEU THAM KHAO|PHỤ LỤC|PHU LUC|"
    r"LỜI (?:GIỚI THIỆU|NÓI ĐẦU|CẢM ƠN|TỰA)|LOI (?:GIOI THIEU|NOI DAU|CAM ON|TUA)|"
    r"REFERENCES|BIBLIOGRAPHY|APPENDI(?:X|CES)|ACKNOWLEDG(?:E)?MENTS?|FOREWORD|PREFACE)\b",
    re.IGNORECASE,
)
_RE_PART_SUBNUM = re.compile(r"\s*\d+\.\d+")


class HeadingExtractor:
    def __init__(self, caller: OpenAiJsonCaller, config: TocConfig) -> None:
        self._caller = caller
        self._config = config

    def extract(self, result: ParseResult, toc_end: int | None) -> list[HeadingSpan]:
        leaves = flatten_leaves(result)
        cells = index_table_cells(result)
        toc_end_page = LeafElementSummary.toc_end_leaf_page(result, toc_end)
        summary = [
            block for block in LeafElementSummary.build(
                result, leaves, toc_end_page, self._config.heading_chunk_chars, cells,
                chunk_overlap=self._config.heading_chunk_overlap,
            )
            if block["type"] != "figure"
        ]
        if not summary:
            return []
        leaf_by_id = {leaf.id: leaf for leaf in leaves}
        page_counts = self._page_counts(leaves)
        text = ElementText(result, leaves, cells)
        spans: list[HeadingSpan] = []
        seen: set[tuple[str, str]] = set()
        for window in self._windows(summary):
            detected = self._detect(window)
            for block in window:
                leaf = leaf_by_id.get(block["id"])
                if leaf is None:
                    continue
                haystack = text.haystack(leaf.id)
                for raw in detected.get(block["i"], []):
                    cleaned = self._clean(str(raw))
                    if not cleaned:
                        continue
                    for title in self._split_stacked(cleaned):
                        if _RE_CAPTION.match(title):
                            continue
                        pos = haystack.find(title.lower())
                        if pos < 0:
                            continue
                        key = (leaf.id, title.lower())
                        if key in seen:
                            continue
                        seen.add(key)
                        divider = self._is_divider(leaf, page_counts)
                        spans.append(HeadingSpan(title=title, element_id=leaf.id, leaf_index=leaf.index,
                                                 rank=pos, is_divider=divider))
        spans.sort(key=lambda s: (s.leaf_index, s.rank))
        logger.info("  Phase A [HeadingExtractor]: %d heading từ %d phần tử thân bài", len(spans), len(summary))
        return spans

    def _windows(self, summary: list[dict]) -> list[list[dict]]:
        windows: list[list[dict]] = []
        n = len(summary)
        budget = self._config.heading_window_chars
        overlap = self._config.heading_window_overlap
        i = 0
        while i < n:
            window: list[dict] = []
            size = 0
            j = i
            while j < n and (not window or size + len(summary[j]["t"]) <= budget):
                window.append(summary[j])
                size += len(summary[j]["t"]) + 16
                j += 1
            windows.append(window)
            if j >= n:
                break
            i = max(j - overlap, i + 1)
        return windows

    def _detect(self, window: list[dict]) -> dict[int, list[str]]:
        listing = "\n".join(f"[{block['i']}] {block['t']}" for block in window)
        try:
            result = self._caller.call_structured(
                TocPrompts.HEADING_DETECT_SYS, f"CÁC KHỐI VĂN BẢN LIÊN TIẾP:\n{listing}",
                _HEADING_DETECT_SCHEMA, "heading_blocks",
            )
        except Exception as exc:
            logger.warning("  Phase A: LLM call failed: %s", exc)
            return {}
        out: dict[int, list[str]] = {}
        for block in result.get("blocks", []):
            if isinstance(block, dict) and isinstance(block.get("i"), int) and isinstance(block.get("titles"), list):
                out[block["i"]] = [t for t in block["titles"] if isinstance(t, str)]
        return out

    @staticmethod
    def _clean(raw: str) -> str:
        return re.sub(r"\s+", " ", _RE_MD_NOISE.sub("", TocTree.norm_title(raw))).strip()

    @staticmethod
    def _split_stacked(title: str) -> list[str]:
        lead = TocTree._numeric_order(title)
        if lead is None:
            return [title]
        for m in _RE_EMBED_NUM.finditer(title):
            if m.start() == 0:
                continue
            try:
                embedded = tuple(int(x) for x in m.group(1).split("."))
            except ValueError:
                continue
            if len(embedded) > len(lead) and embedded[: len(lead)] == lead:
                head, tail = title[: m.start()].strip(), title[m.start():].strip()
                if head and tail:
                    return [head] + HeadingExtractor._split_stacked(tail)
        return [title]

    @staticmethod
    def _page_counts(leaves: list[LeafElement]) -> dict[int, int]:
        counts: dict[int, int] = {}
        for leaf in leaves:
            if leaf.type in NOISE_TYPES:
                continue
            counts[leaf.page] = counts.get(leaf.page, 0) + 1
        return counts

    @staticmethod
    def _is_divider(leaf: LeafElement, page_counts: dict[int, int]) -> bool:
        return page_counts.get(leaf.page, 0) == 1


class HierarchyAssembler:
    _MAX_LEVEL = 6

    def __init__(self, caller: OpenAiJsonCaller, config: TocConfig) -> None:
        self._caller = caller
        self._config = config

    def assemble(self, headings: list[HeadingSpan]) -> list:
        if not headings:
            return []
        for heading in headings:
            heading.label = self._parse_label(heading.title)
        chapters = [self._build_chapter(chap, members) for chap, members in self._segment_chapters(headings)]
        logger.info("  Phase B [HierarchyAssembler]: %d chương, %d node", len(chapters), TocTree.count_nodes(chapters))
        return chapters

    def _segment_chapters(self, headings: list[HeadingSpan]) -> list[tuple[HeadingSpan, list[HeadingSpan]]]:
        markers = {i for i, h in enumerate(headings) if h.label and h.label.kind == "chapter"}
        if markers:
            boundaries = markers | {i for i, h in enumerate(headings) if self._is_toplevel_part(h)}
        else:
            boundaries = set(self._detect_chapters(headings))
        boundaries = sorted(boundaries | {0})
        segments: list[tuple[HeadingSpan, list[HeadingSpan]]] = []
        for k, start in enumerate(boundaries):
            end = boundaries[k + 1] if k + 1 < len(boundaries) else len(headings)
            segments.append((headings[start], headings[start + 1:end]))
        return segments

    @staticmethod
    def _is_toplevel_part(heading: HeadingSpan) -> bool:
        if not (heading.label and heading.label.kind == "bare"):
            return False
        text = TocTree.norm_title(heading.title)
        m = _RE_TOPLEVEL_PART.match(text)
        return bool(m) and not _RE_PART_SUBNUM.match(text[m.end():])

    def _detect_chapters(self, headings: list[HeadingSpan]) -> list[int]:
        listing = "\n".join(f"[{i}] {h.title}" for i, h in enumerate(headings))
        try:
            result = self._caller.call_structured(
                TocPrompts.CHAPTERS_SYS, f"DANH SÁCH HEADING (theo thứ tự đọc):\n{listing}",
                _CHAPTERS_SCHEMA, "chapter_indices",
            )
        except Exception as exc:
            logger.warning("  Phase B: chapter detection failed: %s", exc)
            return []
        return sorted({i for i in result.get("chapter_indices", []) if isinstance(i, int) and 0 <= i < len(headings)})

    def _build_chapter(self, chapter: HeadingSpan, members: list[HeadingSpan]) -> dict:
        title = chapter.title
        if members and self._is_titleless_marker(chapter) and members[0].label and members[0].label.kind == "bare":
            title = f"{title} {members[0].title}".strip()
            members = members[1:]
        root = {"title": title, "heading_element_id": chapter.element_id, DEPTH_CHILD_KEYS[1]: []}
        if not members:
            return root
        levels = self._llm_levels(chapter, members)
        node_level = {id(root): 1}
        parent_of: dict[int, dict | None] = {id(root): None}
        open_path = {1: root}
        numbered: dict[tuple[int, ...], dict] = {}
        for i, heading in enumerate(members):
            key = heading.label.numeric if (heading.label and heading.label.numeric and heading.label.kind in ("decimal", "roman")) else None
            parent = self._numbered_parent(key, numbered, parent_of, {id(n) for n in open_path.values()})
            if parent is None:
                target = max(2, min(levels[i], self._MAX_LEVEL))
                plevel = target - 1
                while plevel >= 1 and plevel not in open_path:
                    plevel -= 1
                parent = open_path.get(plevel, root)
            while parent is not root and node_level[id(parent)] > 5:
                parent = parent_of[id(parent)]
            parent_level = node_level[id(parent)]
            level = min(parent_level + 1, self._MAX_LEVEL)
            node = {"title": heading.title, "heading_element_id": heading.element_id}
            child_key = DEPTH_CHILD_KEYS.get(level)
            if child_key:
                node[child_key] = []
            parent.setdefault(DEPTH_CHILD_KEYS[parent_level], []).append(node)
            node_level[id(node)] = level
            parent_of[id(node)] = parent
            open_path = {}
            cur: dict | None = node
            while cur is not None:
                open_path[node_level[id(cur)]] = cur
                cur = parent_of[id(cur)]
            if key:
                numbered[key] = node
        return root

    @staticmethod
    def _is_titleless_marker(heading: HeadingSpan) -> bool:
        if not heading.label or heading.label.kind != "chapter":
            return False
        text = TocTree.norm_title(heading.title)
        m = _RE_LBL_CHAPTER.match(text)
        return bool(m) and not any(c.isalpha() for c in text[m.end():])

    @staticmethod
    def _numbered_parent(key, numbered, parent_of, opened):
        if not key:
            return None
        for depth in range(len(key) - 1, 0, -1):
            ancestor = numbered.get(key[:depth])
            if ancestor is not None and id(ancestor) in opened:
                return ancestor
        if key[-1] > 1:
            sibling = numbered.get(key[:-1] + (key[-1] - 1,))
            if sibling is not None and id(parent_of[id(sibling)]) in opened:
                return parent_of[id(sibling)]
        return None

    def _llm_levels(self, chapter: HeadingSpan, members: list[HeadingSpan]) -> list[int]:
        n = len(members)
        levels = [2] * n
        for s, e in self._level_batches(n):
            spine = self._open_spine(members, levels, s)
            for local, level in self._call_levels(chapter, members, s, e, spine).items():
                gi = s + local
                if s <= gi < e:
                    levels[gi] = level
        return levels

    def _level_batches(self, n: int) -> list[tuple[int, int]]:
        size = self._config.level_batch_size
        overlap = self._config.level_batch_overlap
        if n <= size:
            return [(0, n)]
        batches: list[tuple[int, int]] = []
        s = 0
        while s < n:
            e = min(s + size, n)
            batches.append((s, e))
            if e >= n:
                break
            s = max(e - overlap, s + 1)
        return batches

    @staticmethod
    def _open_spine(members: list[HeadingSpan], levels: list[int], upto: int) -> list[tuple[str, int]]:
        stack: list[tuple[str, int]] = []
        for j in range(upto):
            while stack and stack[-1][1] >= levels[j]:
                stack.pop()
            stack.append((members[j].title, levels[j]))
        return stack

    def _call_levels(self, chapter: HeadingSpan, members: list[HeadingSpan], s: int, e: int,
                     spine: list[tuple[str, int]]) -> dict[int, int]:
        context = "\n".join(f"  cấp {lv}: {title}" for title, lv in spine) or "  (đầu chương)"
        listing = "\n".join(
            f"[{i - s}]{' «trang riêng»' if members[i].is_divider else ''} {members[i].title}"
            for i in range(s, e)
        )
        user = (
            f"CHƯƠNG (cấp 1): {chapter.title}\n\n"
            f"MẠCH ĐANG MỞ (mảng cấp trên đang mở ngay trước lô này):\n{context}\n\n"
            f"CÁC HEADING (theo thứ tự đọc; «trang riêng» = heading đứng một mình trên 1 trang):\n{listing}"
        )
        try:
            result = self._caller.call_structured(TocPrompts.LEVELS_SYS, user, _LEVELS_SCHEMA, "heading_levels")
        except Exception as exc:
            logger.warning("  Phase B: level assignment failed: %s", exc)
            return {}
        out: dict[int, int] = {}
        for item in result.get("levels", []):
            if isinstance(item, dict) and isinstance(item.get("idx"), int) and isinstance(item.get("level"), int):
                out[item["idx"]] = item["level"]
        return out

    @staticmethod
    def _parse_label(title: str) -> LabelInfo:
        text = TocTree.norm_title(title)
        m = _RE_LBL_CHAPTER.match(text)
        if m:
            return LabelInfo("chapter", (HierarchyAssembler._ordinal(m.group(1)),), None, m.group(0).strip())
        m = _RE_LBL_DECIMAL.match(text)
        if m:
            return LabelInfo("decimal", tuple(int(x) for x in m.group(1).split(".")), None, m.group(1))
        m = _RE_LBL_ROMAN.match(text)
        if m:
            return LabelInfo("roman", (HierarchyAssembler._roman(m.group(1)),), None, m.group(1))
        m = _RE_LBL_LETTER.match(text)
        if m:
            return LabelInfo("letter", None, m.group(1).upper(), m.group(1))
        return LabelInfo("bare", None, None, "")

    @staticmethod
    def _ordinal(token: str) -> int:
        return int(token) if token.isdigit() else HierarchyAssembler._roman(token)

    @staticmethod
    def _roman(token: str) -> int:
        total = 0
        prev = 0
        for ch in reversed(token.upper()):
            value = _ROMAN_VALUES.get(ch, 0)
            total += value if value >= prev else -value
            prev = max(prev, value)
        return total


class DuplicateCollapser:
    def collapse(self, chapters: list, leaves: list[LeafElement]) -> None:
        position = {leaf.id: leaf.index for leaf in leaves if leaf.id}
        entries: list[tuple[dict, list]] = []

        def walk(nodes: list) -> None:
            for node in nodes:
                entries.append((node, nodes))
                for key in DEPTH_CHILD_KEYS.values():
                    if node.get(key):
                        walk(node[key])

        walk(chapters)
        sequence = [
            (i, position[node["heading_element_id"]])
            for i, (node, _) in enumerate(entries)
            if node.get("heading_element_id") in position
        ]
        in_order = {i for i, _ in _lis_nondecreasing(sequence)}
        groups: dict[tuple[int, str, str], list[int]] = {}
        for i, (node, container) in enumerate(entries):
            eid = node.get("heading_element_id")
            if eid:
                key = (id(container), TocTree.norm_title(node.get("title", "")).lower(), eid)
                groups.setdefault(key, []).append(i)

        removed = 0
        for idxs in groups.values():
            if len(idxs) < 2:
                continue
            keep = next((i for i in idxs if i in in_order), idxs[0])
            for i in idxs:
                if i == keep:
                    continue
                node, container = entries[i]
                if any(node.get(k) for k in DEPTH_CHILD_KEYS.values()):
                    continue
                for pos, sibling in enumerate(container):
                    if sibling is node:
                        container.pop(pos)
                        removed += 1
                        break
        if removed:
            logger.info("  DuplicateCollapser: gỡ %d node trùng vật lý (cùng element_id + title)", removed)

        removed_sib = self._dedup_siblings(chapters, position)
        if removed_sib:
            logger.info("  DuplicateCollapser: gỡ %d node trùng title cùng cha (OCR nhân đôi qua ranh giới element)", removed_sib)

    @staticmethod
    def _dedup_siblings(nodes: list, position: dict) -> int:
        by_title: dict[str, list[dict]] = {}
        for node in nodes:
            by_title.setdefault(TocTree.norm_title(node.get("title", "")).lower(), []).append(node)
        drop: set[int] = set()
        for group in by_title.values():
            if len(group) < 2:
                continue
            with_children = [n for n in group if any(n.get(k) for k in DEPTH_CHILD_KEYS.values())]
            if len(with_children) > 1:
                continue
            keep = with_children[0] if with_children else group[0]
            for member in group:
                if member is not keep and DuplicateCollapser._reading_adjacent(member, keep, nodes, position):
                    drop.add(id(member))
        removed = 0
        if drop:
            kept = [n for n in nodes if id(n) not in drop]
            removed = len(nodes) - len(kept)
            nodes[:] = kept
        for node in nodes:
            for key in DEPTH_CHILD_KEYS.values():
                if node.get(key):
                    removed += DuplicateCollapser._dedup_siblings(node[key], position)
        return removed

    @staticmethod
    def _reading_adjacent(a: dict, b: dict, siblings: list, position: dict) -> bool:
        pa = position.get(a.get("heading_element_id"))
        pb = position.get(b.get("heading_element_id"))
        if pa is None or pb is None:
            return False
        lo, hi = sorted((pa, pb))
        for other in siblings:
            if other is a or other is b:
                continue
            po = position.get(other.get("heading_element_id"))
            if po is not None and lo < po < hi:
                return False
        return True


class ReadingOrderNormalizer:
    def normalize(self, chapters: list, leaves: list[LeafElement]) -> None:
        position = {leaf.id: leaf.index for leaf in leaves if leaf.id}
        moved = self._sort(chapters, position)
        if moved:
            logger.info("  ReadingOrderNormalizer: đưa %d node về đúng thứ tự đọc", moved)

    def _sort(self, nodes: list, position: dict) -> int:
        keyed = [(position.get(n.get("heading_element_id"), _MAX_POSITION), i, n) for i, n in enumerate(nodes)]
        ordered = sorted(keyed)
        moved = sum(1 for original, (_, _, node) in zip(nodes, ordered) if original is not node)
        nodes[:] = [n for _, _, n in ordered]
        for node in nodes:
            for key in DEPTH_CHILD_KEYS.values():
                if node.get(key):
                    moved += self._sort(node[key], position)
        return moved


class IntegrityChecker:
    def check(self, chapters: list, leaves: list[LeafElement]) -> None:
        position = {leaf.id: leaf.index for leaf in leaves if leaf.id}
        flat = TocTree.flatten_refs(chapters)
        matched = sum(1 for _, node in flat if node.get("heading_element_id"))
        sequence = [
            (i, position[node["heading_element_id"]])
            for i, (_, node) in enumerate(flat)
            if node.get("heading_element_id") in position
        ]
        keep = {i for i, _ in _lis_nondecreasing(sequence)}
        violations = [flat[i][0] for i, _ in sequence if i not in keep]
        logger.info(
            "  Phase E [IntegrityChecker]: %d/%d node có heading_element_id, %d node lệch thứ tự vị trí",
            matched, len(flat), len(violations),
        )
        for path in violations[:20]:
            logger.warning("    lệch thứ tự vị trí (rà soát): %s", path)


class TocBuilder:
    def __init__(self, config: TocConfig, client: OpenAI) -> None:
        self._config = config
        caller = OpenAiJsonCaller(client, config.model)
        self._scanner = PageTextScanner(config)
        self._phase1 = Phase1Builder(caller, self._scanner)
        self._extractor = HeadingExtractor(caller, config)
        self._assembler = HierarchyAssembler(caller, config)
        self._matcher = HeadingElementMatcher(caller, config)
        self._gap_resolver = GapFillResolver(caller, config)
        self._anchor_resolver = IntraElementAnchorResolver(caller, config)
        self._collapser = DuplicateCollapser()
        self._normalizer = ReadingOrderNormalizer()
        self._integrity = IntegrityChecker()

    def build(self, json_path: Path) -> dict:
        result = ParseResult.load(json_path)
        filename = json_path.name
        logger.info("Processing: %s", filename)

        raw_toc, scan = self._phase1.run(result.markdown, filename)
        toc = self._ensure_schema(raw_toc, filename)
        if not toc.get("total_pages"):
            toc["total_pages"] = result.metadata.page_count
        logger.info(
            "  Phase 0 [Profiler]: MỤC LỤC=%s (%d trang), %d chương, độ sâu %d",
            scan.found, scan.merged_pages + 1 if scan.found else 0,
            len(toc["chapters"]), TocTree.depth(toc["chapters"]) if toc["chapters"] else 0,
        )

        min_depth = self._config.min_depth_for(toc["total_pages"] or 0)
        if scan.found and not TocTree.is_shallow(toc["chapters"], min_depth):
            logger.info("  MỤC LỤC đủ sâu (≥%d cấp) → dùng trực tiếp, định vị bằng Phase 3", min_depth)
            self._matcher.match(toc["chapters"], result, scan.end)
        else:
            logger.info("  MỤC LỤC nông/không có → dựng cây bằng Phase A + Phase B")
            headings = self._extractor.extract(result, scan.end)
            toc["chapters"] = TocTree.normalize_nodes(self._assembler.assemble(headings), depth=1)

        leaves = flatten_leaves(result)
        self._gap_resolver.resolve(toc["chapters"], result, leaves)
        self._anchor_resolver.resolve(toc["chapters"], result, leaves)
        self._collapser.collapse(toc["chapters"], leaves)
        self._normalizer.normalize(toc["chapters"], leaves)
        self._integrity.check(toc["chapters"], leaves)
        return toc

    @staticmethod
    def _ensure_schema(toc: dict, filename: str) -> dict:
        for k in METADATA_KEYS:
            toc.setdefault(k, None)
        toc["source_file"] = filename
        toc["chapters"] = TocTree.normalize_nodes(toc.get("chapters", []), depth=1)
        if toc.get("total_pages") is not None:
            try:
                toc["total_pages"] = int(toc["total_pages"])
            except (ValueError, TypeError):
                toc["total_pages"] = None
        return toc


class TocBuilderPipeline:
    def __init__(self, config: TocConfig, client: OpenAI) -> None:
        self._config = config
        self._builder = TocBuilder(config, client)

    def run(self) -> None:
        json_paths = self._discover()
        if not json_paths:
            logger.info("Không có file nào trong %s", self._config.input_dir)
            return
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        for path in json_paths:
            try:
                self._process_one(path)
            except Exception:
                logger.exception("Lỗi khi xử lý %s", path.name)

    def _discover(self) -> list[Path]:
        if self._config.files:
            return [self._config.input_dir / name for name in self._config.files]
        self._config.input_dir.mkdir(parents=True, exist_ok=True)
        return sorted(self._config.input_dir.glob("*_dpt3.json"))

    def _process_one(self, path: Path) -> None:
        stem = path.stem[: -len("_dpt3")] if path.stem.endswith("_dpt3") else path.stem
        out_path = self._config.output_dir / f"{stem}_toc_structure.json"
        toc = self._builder.build(path)
        out_path.write_text(json.dumps(toc, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("  Saved → %s", out_path.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trích xuất cây TOC từ file DPT-3 JSON.")
    parser.add_argument("--input-dir", default=str(TocConfig().input_dir))
    parser.add_argument("--output-dir", default=str(TocConfig().output_dir))
    parser.add_argument("--files", nargs="*")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip().strip("\"'")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY in .env")

    config = TocConfig(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        files=tuple(args.files) if args.files else (),
    )
    if args.model:
        config.model = args.model

    client = OpenAI(api_key=api_key)
    TocBuilderPipeline(config, client).run()


if __name__ == "__main__":
    main()
