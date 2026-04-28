"""
Node fields:
  node_id           — SHA-1 (12 hex) ổn định từ đường dẫn TOC đầy đủ
  title             — tiêu đề từ TOC
  page_start/end    — trang (1-indexed)
  content           — text thuần (chỉ node lá; parent = null)
  intro_content     — text giữa heading parent và heading child đầu tiên (parent có con; lá = null)
  match_score       — 1.0 nếu matched by ID; null nếu unmatched
  heading_bbox      — bbox dòng heading
  content_bboxes    — union bbox từng trang của nội dung
  landing_chunks    — [{id, type}]
  sections / subsections / ...
"""

from __future__ import annotations

import bisect
import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

OCR_MD_DIR     = Path("./data/02_ocr_markdown")
TOC_INPUT_DIR  = Path("./data/03_toc_json")
ADE_CHUNKS_DIR = Path("./data/06_ade_chunks")
OUTPUT_DIR     = Path("./data/04_chunked_json")

FILE_PAIRS: list[tuple[str, str, str, str]] = []  # rỗng = tự động detect

_PAGE_BREAK = "<!-- PAGE BREAK -->"
_CHILD_KEYS = (
    "chapters", "sections", "subsections",
    "subsubsections", "subsubsubsections", "subsubsubsubsections",
)

_RE_ANCHOR  = re.compile(r"<a\s+id='[^']*'>.*?</a>", re.DOTALL)
_RE_HEADING = re.compile(r"^#{1,6}\s*")
_RE_TOC_MARKER = re.compile(r"MUC\s*LUC|MỤC\s*LỤC|TABLE\s+OF\s+CONTENTS", re.IGNORECASE)

# Intra-chunk search window (chars). Đủ lớn để bao phủ 1 chunk dài nhất, nhưng không scan toàn file.
_INTRA_CHUNK_SEARCH_WINDOW = 60_000
# Điểm Dice tối thiểu để chấp nhận heading match khi tìm split char
_HEADING_MATCH_THRESHOLD = 0.55


# ── Auto file-pair detection ──────────────────────────────────────────────────

def get_file_pairs() -> list[tuple[str, str, str, str]]:
    if FILE_PAIRS:
        return FILE_PAIRS

    pairs: list[tuple[str, str, str, str]] = []
    TOC_INPUT_DIR.mkdir(parents=True, exist_ok=True)

    for toc_file in sorted(TOC_INPUT_DIR.glob("*_toc_structure.json")):
        stem = toc_file.stem
        if stem.endswith("_toc_structure"):
            stem = stem[: -len("_toc_structure")]
        base = stem[: -len("_ocr")] if stem.endswith("_ocr") else stem

        ocr_md = OCR_MD_DIR     / f"{stem}.md"
        ade_js = ADE_CHUNKS_DIR / f"{base}_ade_chunks.json"
        out_js = f"{base}_chunks.json"

        if not ocr_md.exists():
            logger.warning("OCR Markdown not found: %s", ocr_md)
            continue
        if not ade_js.exists():
            logger.warning("ADE chunks not found: %s", ade_js)
            continue

        pairs.append((toc_file.name, ocr_md.name, ade_js.name, out_js))
        logger.info(
            "Paired: %s + %s + %s → %s",
            toc_file.name, ocr_md.name, ade_js.name, out_js,
        )

    return pairs


# ── Page-break index ──────────────────────────────────────────────────────────

def build_page_breaks(ocr_md: str) -> list[int]:
    return [m.start() for m in re.finditer(re.escape(_PAGE_BREAK), ocr_md)]


def page_at(char_pos: int, breaks: list[int]) -> int:
    return bisect.bisect_right(breaks, char_pos) + 1


def _find_body_start(text: str) -> int:
    pages = text.split(_PAGE_BREAK)
    offset = 0
    toc_seen = False
    for page in pages:
        if _RE_TOC_MARKER.search(page):
            toc_seen = True
        elif toc_seen and page.strip():
            return offset
        offset += len(page) + len(_PAGE_BREAK)
    return 0


# ── ADE offset map ────────────────────────────────────────────────────────────

def build_ade_offset_map(ocr_md: str, ade_chunks: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for ch in ade_chunks:
        anchor = f"<a id='{ch['id']}'></a>"
        pos    = ocr_md.find(anchor)
        enriched.append({**ch, "start_char": pos, "_anchor_len": len(anchor)})

    for i, ch in enumerate(enriched):
        nxt = next(
            (enriched[j]["start_char"] for j in range(i + 1, len(enriched))
             if enriched[j]["start_char"] >= 0),
            len(ocr_md),
        )
        ch["end_char"] = nxt

    found = sum(1 for c in enriched if c["start_char"] >= 0)
    logger.info("ADE offset map: %d/%d chunks có anchor", found, len(enriched))
    return enriched


def build_id_to_idx(ade_enriched: list[dict]) -> dict[str, int]:
    """Build {chunk_id: list_index} lookup — O(1) match theo heading_chunk_id."""
    return {ch["id"]: i for i, ch in enumerate(ade_enriched) if ch.get("id")}


# ── Intra-chunk heading finder (xử lý ADE merged chunks) ─────────────────────

def _find_next_heading_char(
    ocr_md: str,
    title: str,
    search_from: int,
    min_advance: int = 1,
) -> int | None:

    a_words = set(re.sub(r"[^\w\s]", " ", title.lower()).split())
    if not a_words:
        return None

    search_start = search_from + min_advance
    window       = ocr_md[search_start: search_start + _INTRA_CHUNK_SEARCH_WINDOW]
    lines        = window.splitlines(keepends=True)

    offset     = 0
    best_pos   = None
    best_score = 0.0

    for line in lines:
        clean = _RE_HEADING.sub("", line).strip()
        if clean:
            b_words = set(re.sub(r"[^\w\s]", " ", clean.lower()).split())
            if b_words:
                inter = a_words & b_words
                score = 2 * len(inter) / (len(a_words) + len(b_words))
                if score >= _HEADING_MATCH_THRESHOLD and score > best_score:
                    best_score = score
                    best_pos   = search_start + offset
        offset += len(line)

    return best_pos


# ── TOC chunk assignment (ID-based, với same-chunk split) ────────────────────

def _compute_sibling_ends(
    nodes: list[dict],
    parent_end: int,
    ade_enriched: list[dict],
    ocr_md: str,
) -> None:

    for i, node in enumerate(nodes):
        cur_idx = node.get("_chunk_idx")
        nxt_idx = nodes[i + 1].get("_chunk_idx") if i + 1 < len(nodes) else None
        node["_chunk_end"] = nxt_idx if nxt_idx is not None else parent_end

        # ── Same-chunk sibling detection ─────────────────────────────────────
        if cur_idx is not None and nxt_idx is not None and cur_idx == nxt_idx:
            chunk_start = ade_enriched[cur_idx].get("start_char", -1)
            nxt_title   = nodes[i + 1].get("title", "")
            if chunk_start >= 0 and nxt_title:
                split = _find_next_heading_char(ocr_md, nxt_title, chunk_start)
                if split is not None and split > chunk_start:
                    node["_end_char_override"] = split
                    logger.info(
                        "  [SHARED_CHUNK/sibling] %-40s ← split @char %d (id=%.8s)",
                        node["title"][:40], split, ade_enriched[cur_idx].get("id", ""),
                    )
                else:
                    logger.warning(
                        "  [SHARED_CHUNK/no-split] %-40s  next=%-40s",
                        node["title"][:40], nxt_title[:40],
                    )


def _infer_from_children(node: dict) -> None:
    child_idxs, child_ends = [], []
    for key in _CHILD_KEYS:
        for ch in node.get(key, []):
            if ch.get("_chunk_idx") is not None:
                child_idxs.append(ch["_chunk_idx"])
            if ch.get("_chunk_end") is not None:
                child_ends.append(ch["_chunk_end"])
    if child_idxs and node.get("_chunk_idx") is None:
        node["_chunk_idx"] = min(child_idxs)
    if child_ends and node.get("_chunk_end") is None:
        node["_chunk_end"] = max(child_ends)


def _infer_recursive(node: dict) -> None:
    for key in _CHILD_KEYS:
        for child in node.get(key, []):
            _infer_recursive(child)
    _infer_from_children(node)


def _process_level(
    nodes: list[dict],
    ade_enriched: list[dict],
    id_to_idx: dict[str, int],
    p_start: int,
    p_end: int,
    ocr_md: str,
) -> None:
    """Gán _chunk_idx cho mỗi node dựa trên heading_chunk_id; đệ quy vào children."""
    for node in nodes:
        cid = node.get("heading_chunk_id")
        if cid:
            raw_idx = id_to_idx.get(cid)
            if raw_idx is not None and p_start <= raw_idx < p_end:
                node["_chunk_idx"]   = raw_idx
                node["_match_score"] = 1.0
            else:
                node["_chunk_idx"]   = None
                node["_match_score"] = 0.0
                if raw_idx is not None:
                    logger.warning(
                        "  [OUT_OF_RANGE] %-55s  idx=%d range=[%d,%d)",
                        node["title"][:55], raw_idx, p_start, p_end,
                    )
                else:
                    logger.warning(
                        "  [ID_NOT_FOUND] %-55s  id=%.8s…", node["title"][:55], cid,
                    )
        else:
            node["_chunk_idx"]   = None
            node["_match_score"] = 0.0
            logger.warning("  [NO_CHUNK_ID]  %-55s", node["title"][:55])

    _compute_sibling_ends(nodes, p_end, ade_enriched, ocr_md)

    for node in nodes:
        idx = node.get("_chunk_idx")
        end = node.get("_chunk_end")
        children = [ch for k in _CHILD_KEYS for ch in node.get(k, [])]
        if not children:
            continue
        # When this node has no matched chunk (e.g. top-level chapter with no
        # heading_chunk_id), fall back to the inherited parent range so children
        # are still processed and not silently skipped.
        child_start = idx if idx is not None else p_start
        child_end   = end if end is not None else p_end
        _process_level(children, ade_enriched, id_to_idx, child_start, child_end, ocr_md)


def _assign_all(
    toc_root: dict,
    ade_enriched: list[dict],
    id_to_idx: dict[str, int],
    ocr_md: str,
) -> None:
    top = [ch for k in _CHILD_KEYS for ch in toc_root.get(k, [])]
    if not top:
        return
    _process_level(top, ade_enriched, id_to_idx, 0, len(ade_enriched), ocr_md)
    for node in top:
        _infer_recursive(node)


# ── Content extraction ────────────────────────────────────────────────────────

def _find_heading_end(text: str, toc_title: str) -> int:
    a_words = set(re.sub(r"[^\w\s]", " ", toc_title.lower()).split())
    if not a_words:
        return 0

    lines = text.splitlines(keepends=True)
    best_i, best_score = -1, 0.0

    for i, line in enumerate(lines):
        clean = _RE_HEADING.sub("", line).strip()
        if not clean:
            continue
        b_words = set(re.sub(r"[^\w\s]", " ", clean.lower()).split())
        if not b_words:
            continue
        inter = a_words & b_words
        score = 2 * len(inter) / (len(a_words) + len(b_words))  # Dice
        if score > best_score:
            best_score, best_i = score, i

    if best_score < 0.5 or best_i < 0:
        return 0
    return sum(len(lines[j]) for j in range(best_i + 1))


def _extract_content(ocr_md: str, toc_title: str, start: int, end: int) -> str | None:
    if start < 0 or end <= start:
        return None
    stripped = _RE_ANCHOR.sub("", ocr_md[start:end]).replace(_PAGE_BREAK, "")
    content  = stripped[_find_heading_end(stripped, toc_title):].strip()
    return content or None


# ── BBox helpers ──────────────────────────────────────────────────────────────

def _union_bboxes_by_page(bboxes: list[dict]) -> list[dict]:
    by_page: dict[int, list[dict]] = {}
    for b in bboxes:
        by_page.setdefault(b["page"], []).append(b)
    return [
        {
            "page":   p,
            "left":   round(min(b["left"]   for b in pg), 6),
            "top":    round(min(b["top"]    for b in pg), 6),
            "right":  round(max(b["right"]  for b in pg), 6),
            "bottom": round(max(b["bottom"] for b in pg), 6),
        }
        for p, pg in sorted(by_page.items())
    ]


# ── Node ID ───────────────────────────────────────────────────────────────────

def _make_node_id(path: str) -> str:
    """SHA-1 (12 hex chars) từ đường dẫn TOC đầy đủ. Ổn định qua nhiều lần chạy."""
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


# ── First-child start char (Bug 1 fix + shared-chunk parent/child) ────────────

def _get_first_child_start_char(
    toc_node: dict,
    ade_enriched: list[dict],
    ocr_md: str,
) -> int | None:

    parent_idx = toc_node.get("_chunk_idx")

    for key in _CHILD_KEYS:
        for child in toc_node.get(key, []):
            idx = child.get("_chunk_idx")
            if idx is None or idx >= len(ade_enriched):
                continue
            sc = ade_enriched[idx]["start_char"]
            if sc < 0:
                continue

            # Parent và child cùng chunk → tìm intra-chunk heading position
            if parent_idx is not None and parent_idx == idx:
                child_title = child.get("title", "")
                split = _find_next_heading_char(ocr_md, child_title, sc)
                if split is not None and split > sc:
                    logger.info(
                        "  [SHARED_CHUNK/parent-child] %-40s ← child heading @char %d",
                        toc_node["title"][:40], split,
                    )
                    return split
                # Không tìm được split → không có intro_content (các heading sát nhau)
                return None

            return sc  # Normal: first child ở chunk khác

    return None


# ── Build output node ─────────────────────────────────────────────────────────

def _build_chunk_node(
    toc_node: dict,
    ade_enriched: list[dict],
    ocr_md: str,
    page_breaks: list[int],
    path: str = "",
) -> dict:
    h_idx: int | None = toc_node.get("_chunk_idx")
    s_end: int | None = toc_node.get("_chunk_end")

    # ── Char offsets ──────────────────────────────────────────────────────────
    start_char: int | None = (
        ade_enriched[h_idx]["start_char"]
        if h_idx is not None and h_idx < len(ade_enriched)
           and ade_enriched[h_idx]["start_char"] >= 0
        else None
    )

    if s_end is not None:
        ec = (
            ade_enriched[s_end]["start_char"]
            if s_end < len(ade_enriched) else len(ocr_md)
        )
        end_char: int | None = ec if ec >= 0 else len(ocr_md)
    else:
        end_char = None

    # Shared-chunk sibling override: khi s_end == h_idx (zero-length range),
    # dùng char position của next sibling's heading để tách nội dung.
    end_char_override = toc_node.get("_end_char_override")
    if end_char_override is not None:
        end_char = end_char_override

    # ── Page numbers ──────────────────────────────────────────────────────────
    page_start = page_at(start_char, page_breaks) if start_char is not None else None
    page_end   = page_at(end_char - 1, page_breaks) if end_char else None

    # ── Heading bbox ──────────────────────────────────────────────────────────
    heading_bbox: dict | None = None
    if h_idx is not None and h_idx < len(ade_enriched):
        bbs = ade_enriched[h_idx].get("bboxes", [])
        heading_bbox = bbs[0] if bbs else None

    # ── Same-chunk detection (for content_bboxes & landing_chunks) ────────────
    # s_end <= h_idx nghĩa là content nằm trong chính h_idx (hoặc đã override)
    same_chunk = (
        h_idx is not None and s_end is not None and s_end <= h_idx
    ) or (
        h_idx is not None and end_char_override is not None
        and end_char_override <= (
            ade_enriched[h_idx]["end_char"]
            if h_idx < len(ade_enriched) else end_char_override + 1
        )
    )

    # ── Content bboxes (union per page, skip marginalia) ──────────────────────
    content_bboxes: list[dict] = []
    if h_idx is not None and s_end is not None:
        if same_chunk:
            # Content nằm trong h_idx chunk → dùng h_idx làm content bbox
            raw = [
                b for b in ade_enriched[h_idx].get("bboxes", [])
            ] if ade_enriched[h_idx].get("type") != "marginalia" else []
        else:
            raw = [
                b
                for i in range(h_idx + 1, min(s_end, len(ade_enriched)))
                if ade_enriched[i].get("type") != "marginalia"
                for b in ade_enriched[i].get("bboxes", [])
            ]
        content_bboxes = _union_bboxes_by_page(raw)

    # ── Landing chunks ────────────────────────────────────────────────────────
    landing_chunks: list[dict] = []
    if h_idx is not None and s_end is not None:
        if same_chunk:
            # Chỉ h_idx vì content nằm trong chunk đó
            lc_range = range(h_idx, h_idx + 1)
        else:
            lc_range = range(h_idx, min(s_end, len(ade_enriched)))
        _ID_TYPES = {"table", "figure"}
        landing_chunks = [
            {"id": ade_enriched[i]["id"], "type": ade_enriched[i].get("type", "text")}
            for i in lc_range
            if ade_enriched[i].get("id")
            and ade_enriched[i].get("type", "text") in _ID_TYPES
        ]

    # ── Node ID ───────────────────────────────────────────────────────────────
    node_path = f"{path}/{toc_node['title']}" if path else toc_node["title"]
    node_id   = _make_node_id(node_path)

    # ── Content vs intro_content ──────────────────────────────────────────────
    # Leaf node   : content = text từ heading → end; intro_content = null
    # Parent node : content = null; intro_content = text từ heading → first child heading
    #   • Normal  : first_child_sc > start_char ← OK
    #   • Shared  : dùng intra-chunk split từ _get_first_child_start_char
    has_children    = any(toc_node.get(k) for k in _CHILD_KEYS)
    content: str | None       = None
    intro_content: str | None = None

    if start_char is not None:
        if has_children:
            first_child_sc = _get_first_child_start_char(toc_node, ade_enriched, ocr_md)
            if first_child_sc is not None and first_child_sc > start_char:
                intro_content = _extract_content(
                    ocr_md, toc_node["title"], start_char, first_child_sc,
                )
        elif end_char is not None:
            content = _extract_content(ocr_md, toc_node["title"], start_char, end_char)

    node_out: dict = {
        "node_id":        node_id,
        "title":          toc_node["title"],
        "page_start":     page_start,
        "page_end":       page_end,
        "content":        content,
        "intro_content":  intro_content,
        "match_score":    round(toc_node.get("_match_score") or 0.0, 4) or None,
        "heading_bbox":   heading_bbox,
        "content_bboxes": content_bboxes,
        "landing_chunks": landing_chunks,
    }

    # Recurse children
    for key in _CHILD_KEYS:
        if key not in toc_node:
            continue
        node_out[key] = [
            _build_chunk_node(child, ade_enriched, ocr_md, page_breaks, node_path)
            for child in toc_node[key]
            if child.get("title")
        ]

    return node_out


# ── Core processor ─────────────────────────────────────────────────────────────

def process(toc_path: Path, ocr_md_path: Path, ade_path: Path, out_path: Path) -> None:
    for p in [toc_path, ocr_md_path, ade_path]:
        if not p.exists():
            logger.error("File not found: %s", p)
            return

    logger.info("=" * 60)
    logger.info("Processing: %s", toc_path.name)

    toc_data   = json.loads(toc_path.read_text(encoding="utf-8"))
    ocr_md     = ocr_md_path.read_text(encoding="utf-8")
    ade_chunks = json.loads(ade_path.read_text(encoding="utf-8"))

    logger.info("OCR MD: %d chars | ADE chunks: %d", len(ocr_md), len(ade_chunks))

    page_breaks  = build_page_breaks(ocr_md)
    ade_enriched = build_ade_offset_map(ocr_md, ade_chunks)
    id_to_idx    = build_id_to_idx(ade_enriched)

    toc_copy = json.loads(json.dumps(toc_data, ensure_ascii=False))
    _assign_all(toc_copy, ade_enriched, id_to_idx, ocr_md)

    top_chunks = [
        _build_chunk_node(child, ade_enriched, ocr_md, page_breaks)
        for key in _CHILD_KEYS
        for child in toc_copy.get(key, [])
        if child.get("title")
    ]

    def _count(nodes: list[dict]) -> tuple[int, int]:
        m = u = 0
        for n in nodes:
            if n.get("heading_bbox") or n.get("page_start"):
                m += 1
            else:
                u += 1
            for k in _CHILD_KEYS:
                m2, u2 = _count(n.get(k, []))
                m += m2
                u += u2
        return m, u

    matched, unmatched = _count(top_chunks)
    logger.info("Match: %d ok, %d unmatched", matched, unmatched)

    meta_keys = ["title", "publisher", "decision_number", "specialty", "date",
                 "isbn_electronic", "isbn_print", "total_pages", "source_file"]
    result = {k: toc_data.get(k) for k in meta_keys}
    result["source_file"] = result["source_file"] or ocr_md_path.name
    result["chapters"]    = top_chunks

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote → %s", out_path.name)


# ── Service class (API) ────────────────────────────────────────────────────────

class BBoxChunkingService:
    def __init__(self, markdown_service: Any | None = None) -> None:
        self.markdown_service = markdown_service

    @staticmethod
    def build_chunk_payload(
        ocr_md_text: str | None = None,
        ade_chunks: list[dict] | None = None,
        toc_data: dict | None = None,
        *,
        clean_text: str | None = None,
        toc: dict | None = None,
        score_threshold: float | None = None,
    ) -> dict:
        if ocr_md_text is None:
            ocr_md_text = clean_text or ""
        if toc_data is None:
            toc_data = toc or {}
        if ade_chunks is None:
            raise ValueError("ade_chunks is required for bbox chunking pipeline.")

        page_breaks  = build_page_breaks(ocr_md_text)
        ade_enriched = build_ade_offset_map(ocr_md_text, ade_chunks)
        id_to_idx    = build_id_to_idx(ade_enriched)
        toc_copy     = json.loads(json.dumps(toc_data, ensure_ascii=False))
        _assign_all(toc_copy, ade_enriched, id_to_idx, ocr_md_text)

        top_chunks = [
            _build_chunk_node(child, ade_enriched, ocr_md_text, page_breaks)
            for key in _CHILD_KEYS
            for child in toc_copy.get(key, [])
            if child.get("title")
        ]
        meta_keys = ["title", "publisher", "decision_number", "specialty", "date",
                     "isbn_electronic", "isbn_print", "total_pages", "source_file"]
        result = {k: toc_copy.get(k) for k in meta_keys}
        result["chapters"] = top_chunks
        return result


class FuzzyChunkingService(BBoxChunkingService):
    @staticmethod
    def _match_score(expected: str, candidate: str) -> float:
        a_words = set(re.sub(r"[^\w\s]", " ", expected.lower()).split())
        b_words = set(re.sub(r"[^\w\s]", " ", candidate.lower()).split())
        if not a_words or not b_words:
            return 0.0
        inter = a_words & b_words
        return 2 * len(inter) / (len(a_words) + len(b_words))


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    pairs = get_file_pairs()
    if not pairs:
        logger.info(
            "Không tìm thấy cặp file nào. Kiểm tra: %s / %s / %s",
            TOC_INPUT_DIR, OCR_MD_DIR, ADE_CHUNKS_DIR,
        )
        return

    for toc_s, md_s, ade_s, out_s in pairs:
        process(
            toc_path    = Path(toc_s) if Path(toc_s).is_absolute() else TOC_INPUT_DIR  / toc_s,
            ocr_md_path = Path(md_s)  if Path(md_s).is_absolute()  else OCR_MD_DIR     / md_s,
            ade_path    = Path(ade_s) if Path(ade_s).is_absolute() else ADE_CHUNKS_DIR / ade_s,
            out_path    = OUTPUT_DIR / out_s,
        )


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as e:
        logger.error("%s", e)
        sys.exit(1)
    except Exception:
        logger.exception("Unhandled error")
        sys.exit(1)
