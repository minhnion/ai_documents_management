from pathlib import Path
import bisect
import re, json, sys, unicodedata
from typing import Any, Dict, List, Optional, Tuple
import logging

from app.core.exceptions import UnprocessableEntityException

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ==============================================================================
# CẤU HÌNH ĐẦU VÀO / ĐẦU RA
# ==============================================================================

# 1. ĐẦU VÀO:
#   - Thư mục chứa các file Markdown OCR (Tạo từ Bước 1)
MD_INPUT_DIR  = Path("./data/02_ocr_markdown")
#   - Thư mục chứa JSON Cấu trúc Mục lục (Tạo từ Bước 2)
TOC_INPUT_DIR = Path("./data/03_toc_json")

# 2. ĐẦU RA: Thư mục lưu kết quả Chunking JSON (File _chunks.json)
OUTPUT_DIR    = Path("./data/04_chunked_json")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Danh sách các cặp file cần chạy (Để rỗng [] nếu muốn tự động nối file dựa trên tên)
FILE_PAIRS: List[Tuple[str, str, str]] = []

def get_file_pairs():
    if FILE_PAIRS:
        return FILE_PAIRS

    pairs = []
    if not MD_INPUT_DIR.exists() or not TOC_INPUT_DIR.exists():
        MD_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        TOC_INPUT_DIR.mkdir(parents=True, exist_ok=True)
        logging.info(f"[*] Đã tạo thư mục đầu vào. Copy Markdown vào {MD_INPUT_DIR} và JSON vào {TOC_INPUT_DIR}")
        return pairs

    # Tự động match file markdown với file json dựa trên tiền tố tên
    for md_file in MD_INPUT_DIR.glob("*.md"):
        stem = md_file.stem
        if stem.endswith(".extraction"):
            stem = stem[:-len(".extraction")]
        if stem.endswith("_ocr"):
            stem = stem[:-len("_ocr")]

        toc_file = TOC_INPUT_DIR / f"{stem}_toc_structure.json"

        # Thử một pattern tên khác nếu không tìm thấy
        if not toc_file.exists():
             toc_file = TOC_INPUT_DIR / f"{md_file.stem}_toc_structure.json"

        if toc_file.exists():
            out_name = f"{stem}_chunks.json"
            pairs.append((md_file.name, toc_file.name, out_name))
        else:
            logging.warning(f"Không tìm thấy file TOC tương ứng cho {md_file.name}")

    return pairs

MIN_MATCH_SCORE:  float = 0.65
MAX_HEADING_LEN:  int   = 250
DEBUG_MATCH_INFO: bool  = False

try:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────────────
# PATTERNS
# ──────────────────────────────────────────────────────────────────────────────

_RE_ANCHOR     = re.compile(r"<a\s[^>]*></a>", re.IGNORECASE)
_RE_PAGE_BREAK = re.compile(r"<!--\s*PAGE\s*BREAK\s*-->", re.IGNORECASE)
_RE_HTML_CMT   = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_TOC_HEADER = re.compile(
    r"^\s*(?:M\u1ee4C\s*L\u1ee4C|MUC\s*LUC|TABLE\s+OF\s+CONTENTS|CONTENTS)\s*$",
    re.IGNORECASE,
)
_RE_PURE_NUM   = re.compile(r"^\s*[\d\s,\.\-/]+\s*$")
_RE_LIST_ITEM  = re.compile(r"^\s*[-+\u2022\u2192>|]")
_RE_MD_HDG     = re.compile(r"^#{1,6}\s+(.*)")
_RE_TD_TEXT    = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.DOTALL | re.IGNORECASE)
_RE_HTML_TAG   = re.compile(r"<[^>]+>")
_RE_BOLD_STRIP = re.compile(r"^\*{1,2}|^\_{1,2}|\*{1,2}$|\_{1,2}$")

_RE_HTML_NON_TABLE = re.compile(
    r"<(?!/?(?:table|tr|td|th)\b)(?!::)[^>]*>",
    re.IGNORECASE,
)

# Pattern phát hiện MỤC LỤC — dùng trong _find_body_start
_RE_TOC_MARKER = re.compile(
    r"MUC\s*LUC|M\u1ee4C\s*L\u1ee4C|TABLE\s+OF\s+CONTENTS",
    re.IGNORECASE,
)

# ── BUG 2 FIX: Pattern trích section number ────────────────────────────────
_RE_SECTION_NUM = re.compile(r'^(\d+(?:\.\d+)*)[\.\):\-]\s')

def _extract_section_num(text: str) -> Optional[str]:
    """Trích section number từ đầu chuỗi. Ví dụ: '4.3.2. Giai đoạn...' → '4.3.2'"""
    m = _RE_SECTION_NUM.match(text.strip())
    return m.group(1) if m else None

def _section_num_compatible(toc_num: Optional[str], cand_num: Optional[str]) -> bool:
    """True nếu section number tương thích (cùng số hoặc một trong hai là None)."""
    if toc_num is None or cand_num is None:
        return True
    return toc_num == cand_num
# ─────────────────────────────────────────────────────────────────────────────

_RE_FOOTER_PAGE_TITLE = re.compile(
    r"^\s*\d{1,4}\s*[|]\s*[A-ZÀ-Ỵ]",
    re.IGNORECASE,
)

_RE_FOOTER_PAGE_TITLE2 = re.compile(
    r"^\s*\d{1,4}\s*[-–:]\s*(BÀI|CHƯƠNG|PHẦN|MỤC)\b",
    re.IGNORECASE,
)

def _is_footer_line(s: str) -> bool:
    x = s.strip()
    if _RE_PURE_NUM.match(x):
        return True
    if _RE_FOOTER_PAGE_TITLE.match(x):
        return True
    if _RE_FOOTER_PAGE_TITLE2.match(x):
        return True
    return False

_CHILD_KEYS = (
    "chapters", "sections", "subsections",
    "subsubsections", "subsubsubsections", "children",
)

# ──────────────────────────────────────────────────────────────────────────────
# CONTENT CLEANUP
# ──────────────────────────────────────────────────────────────────────────────

def _clean_content(content: Optional[str]) -> Optional[str]:
    if content is None:
        return None
    content = _RE_HTML_NON_TABLE.sub("", content)
    content = content.replace("<!-- PAGE_BREAK -->", "")
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content.strip() or None


# ──────────────────────────────────────────────────────────────────────────────
# PAGE MAP
# ──────────────────────────────────────────────────────────────────────────────

def _build_page_map(clean_text: str, *_args, **_kwargs) -> List[int]:
    """Trả về danh sách vị trí sentinel PAGE_BREAK đã sắp xếp."""
    sentinel  = "<!-- PAGE_BREAK -->"
    positions: List[int] = []
    pos = 0
    while True:
        idx = clean_text.find(sentinel, pos)
        if idx == -1:
            break
        positions.append(idx)
        pos = idx + 1
    logging.info("Page map: %d sentinels → %d pages total", len(positions), len(positions) + 1)
    return positions


def _page_at(sentinel_positions: List[int], char_pos: Optional[int]) -> Optional[int]:
    if char_pos is None:
        return None
    return bisect.bisect_right(sentinel_positions, char_pos) + 1

# ──────────────────────────────────────────────────────────────────────────────
# PREPROCESSING
# ──────────────────────────────────────────────────────────────────────────────

def _preprocess(text: str) -> str:
    text = _RE_ANCHOR.sub("", text)
    text = re.sub(r"<!--(?!\s*PAGE\s*BREAK\s*-->).*?-->", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = _RE_PAGE_BREAK.sub("<!-- PAGE_BREAK -->", text)
    return text

# ──────────────────────────────────────────────────────────────────────────────
# BODY-START DETECTION
# ──────────────────────────────────────────────────────────────────────────────

def _find_body_start(text: str) -> int:

    sentinel = "<!-- PAGE_BREAK -->"
    pages    = text.split(sentinel)

    # ── Bước 1: Tìm toc_start ────────────────────────────────────────────
    toc_start: Optional[int] = None
    for i, page in enumerate(pages[:40]):          # chỉ scan 40 trang đầu
        if _RE_TOC_MARKER.search(page):
            toc_start = i
            break

    if toc_start is None:
        return 0                                   # không có MỤC LỤC → body = đầu file

    # ── Bước 2a: Tìm continuation bằng <table (Landing AI format) ───────
    toc_end = toc_start
    for j in range(toc_start + 1, min(toc_start + 15, len(pages))):
        stripped = _RE_ANCHOR.sub("", pages[j].lstrip()).lstrip()
        if stripped.startswith("<table"):
            toc_end = j
        else:
            break


    if toc_end == toc_start:
        seen_strong = False
        for j in range(toc_start, min(toc_start + 10, len(pages))):
            lines    = [ln.strip() for ln in pages[j].splitlines() if ln.strip()]
            trailing = 0
            for ln in reversed(lines):
                if _RE_PURE_NUM.match(ln) and len(ln) <= 5:   # số trang: 1–5 ký tự
                    trailing += 1
                else:
                    break
            if trailing >= 2:
                seen_strong = True
                toc_end = j             # trang MỤC LỤC xác nhận mạnh
            elif trailing >= 1 and seen_strong:
                toc_end = j             # trang continuation sau khi đã confirm
            else:
                break                   # không đủ điều kiện → body bắt đầu

    # ── Bước 3: Tính char position của page[toc_end + 1] ─────────────────
    body_page_idx = toc_end + 1
    if body_page_idx >= len(pages):
        return 0

    # Ghép lại các pages trước đó + sentinel → lấy độ dài
    body_start = len(sentinel.join(pages[:body_page_idx])) + len(sentinel)
    logging.info(
        "_find_body_start: toc_start=%d  toc_end=%d  body_page=%d  body_start=%d",
        toc_start, toc_end, body_page_idx, body_start,
    )
    return body_start

# ──────────────────────────────────────────────────────────────────────────────
# FUZZY MATCHING LOGIC
# ──────────────────────────────────────────────────────────────────────────────

def _normalize(title: str) -> str:
    """Chuẩn hóa TOC title: không dấu, viết thường, loại bỏ nhãn 'Bước/Phần' và nội dung trong ngoặc."""
    s = title.strip()
    s = re.sub(
        r"^(?:ph[a\u1ea7n]|phan|b[\u01b0\u01a1\u01b0\u01a1c]+|buoc|"
        r"ch[\u01b0\u01a1\u01a1u]ng|chuong)\s+\S+\s*[\.:\-\)]?\s*",
        "", s, flags=re.IGNORECASE,
    )
    s = re.sub(r"^[0-9]+(?:\.[0-9]+)*[\.:\)\-]?\s*", "", s)
    s = re.sub(r"^[A-Za-z][\.\)\-]\s*", "", s)
    s = re.sub(r"\s*\(.*", "", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())

def _normalize_cand(title: str) -> str:
    s = title.strip()
    s = re.sub(
        r"^(?:ph[a\u1ea7n]|phan|b[\u01b0\u01a1\u01b0\u01a1c]+|buoc|"
        r"ch[\u01b0\u01a1\u01a1u]ng|chuong)\s+\S+\s*[\.:\-\)]?\s*",
        "", s, flags=re.IGNORECASE,
    )
    s = re.sub(r"^[0-9]+(?:\.[0-9]+)*[\.:\)\-]?\s*", "", s)
    s = re.sub(r"^[A-Za-z][\.\)\-]\s*", "", s)
    # KHÔNG strip nội dung trong ngoặc — khác _normalize
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())

def _word_set(text: str) -> set:
    return {w for w in _normalize(text).split() if len(w) > 2}

def _effective_inter(toc_word_list: List[str], cand_words: set, cand_word_list: Optional[List[str]] = None) -> int:
    """
    Đếm số toc_words được cover bởi cand_words, kể cả chữ viết tắt y tế.
    Đếm theo list (không phải set) để xử lý đúng từ trùng lặp sau normalize
    (ví dụ: "Đau đầu" → ["đau","đau"] phải trả về 2, không phải 1).
    Chỉ dùng kết quả acronym expansion khi nó cover TẤT CẢ toc_words.

    Hỗ trợ 2 chiều acronym:
    - Forward  (cw ∈ cand_words → subseq của toc unmatched):
        ví dụ cand="tha", toc=["tang","huyet","ap"] → tha[0]=t,h,a ✓
    - Reverse  (tw ∈ toc unmatched → subseq của cand_word_list):
        ví dụ toc="bptnmt", cand=["benh","phoi","tac","nghen","man","tinh"] → b,p,t,n,m,t ✓
        (cần khi tiêu đề TOC dùng viết tắt nhưng candidate dùng từ đầy đủ)
    """

    direct_cnt  = sum(1 for w in toc_word_list if w in cand_words)
    matched_set = {w for w in toc_word_list if w in cand_words}
    unmatched   = [w for w in toc_word_list if w not in cand_words]

    # --- Forward acronym: cand_word là viết tắt, toc_words là dạng đầy đủ ---
    for cw in cand_words:
        if cw in matched_set or len(cw) < 2:
            continue
        for start in range(len(unmatched)):
            for length in range(2, len(unmatched) - start + 1):
                subseq = unmatched[start : start + length]
                if len(cw) == length:
                    match_count = sum(1 for i in range(length) if cw[i] == subseq[i][0])
                    # Cho phép sai 1 ký tự đối với acronym dài (>= 4 ký tự)
                    if match_count == length or (length >= 4 and match_count >= length - 1):
                        matched_set.update(subseq)
                        for w in subseq:
                            try: unmatched.remove(w)
                            except ValueError: pass
                        break

    # --- Reverse acronym: toc_word là viết tắt, cand_word_list là dạng đầy đủ ---
    # Ví dụ: TOC có "bptnmt" nhưng candidate có "benh phoi tac nghen man tinh"
    if cand_word_list and unmatched:
        for tw in list(unmatched):          # list() vì unmatched bị modify bên trong
            if len(tw) < 4:
                continue
            n = len(tw)
            for start in range(len(cand_word_list)):
                if start + n > len(cand_word_list):
                    continue
                subseq = cand_word_list[start : start + n]
                mc = sum(1 for i in range(n) if tw[i] == subseq[i][0])
                if mc == n or (n >= 4 and mc >= n - 1):
                    try: unmatched.remove(tw)
                    except ValueError: pass
                    break

    # expanded_cnt = direct + những từ match bằng acronym expansion
    expanded_cnt = direct_cnt + (len(toc_word_list) - len(unmatched) - direct_cnt)

    if expanded_cnt == len(toc_word_list):
        return expanded_cnt
    return direct_cnt


def _match_score(toc_title: str, candidate: str) -> float:
    toc_norm   = _normalize(toc_title)
    cand_norm  = _normalize_cand(candidate)   # FIX: dùng normalize riêng cho candidate
    toc_list   = [w for w in toc_norm.split()  if len(w) > 2]
    cand_wlist = [w for w in cand_norm.split() if len(w) > 2]  # ordered — for reverse acronym
    cand_words = set(cand_wlist)                                # set  — for O(1) lookup

    # FIX 2a: nếu toc_list rỗng (tên quá ngắn như "Ho"), thử dùng tất cả từ không lọc
    if not toc_list:
        toc_list   = [w for w in toc_norm.split() if w]
        cand_wlist = [w for w in cand_norm.split() if w]
        cand_words = set(cand_wlist)
    if not toc_list or not cand_words:
        return 0.0

    eff_inter = _effective_inter(toc_list, cand_words, cand_wlist)
    forward   = eff_inter / len(toc_list)

    if len(toc_list) <= 3:
        backward = min(1.0, eff_inter / len(cand_words))
        threshold = 0.50 if len(toc_list) <= 2 else 0.30
        if backward < threshold:
            # Candidate có quá nhiều từ thừa so với tiêu đề ngắn → phạt điểm
            return forward * backward

        return forward

    backward = min(1.0, eff_inter / len(cand_words))
    if forward + backward == 0:
        return 0.0
    f1 = 2 * forward * backward / (forward + backward)
    return (f1 + forward) / 2 if forward == 1.0 else f1

# ──────────────────────────────────────────────────────────────────────────────
# HEADING CANDIDATE EXTRACTION
# ──────────────────────────────────────────────────────────────────────────────

def _clean_candidate(raw: str) -> Optional[str]:
    s = raw.strip()
    if not s or _RE_PURE_NUM.match(s) or _RE_LIST_ITEM.match(s):
        return None
    if re.search(r"\.{3,}", s):
        return None
    s = re.sub(r"\s+\d+\s*$", "", s).strip()
    s = _RE_BOLD_STRIP.sub("", s).strip()
    if not s or len(s) > MAX_HEADING_LEN:
        return None
    return s

def _extract_candidates(text: str, body_start: int, total_pages: int = 0, num_sections: int = 1) -> List[Tuple[int, int, str]]:
    """Trích xuất các vị trí nghi ngờ là tiêu đề từ text và bảng HTML."""
    seen:       set = set()
    raw_candidates: List[Tuple[int, int, str]] = []

    def _add(s: int, e: int, t: str, key=None) -> None:
        k = key if key is not None else s
        if k not in seen:
            seen.add(k)
            raw_candidates.append((s, e, t))

    # Nguồn 1: Các dòng text đơn lẻ
    pos = body_start
    for raw in text[body_start:].splitlines(keepends=True):
        line_start = pos
        pos += len(raw)
        stripped = raw.strip()
        if not stripped or stripped == "<!-- PAGE_BREAK -->" or stripped.startswith("<"):
            continue
        md_m = _RE_MD_HDG.match(stripped)
        txt  = md_m.group(1).strip() if md_m else stripped

        if _is_footer_line(txt):
            continue

        cleaned = _clean_candidate(txt)
        if cleaned:
            _add(line_start, pos, cleaned)

            colon_idx = txt.find(': ')
            if 10 < colon_idx < 60 and len(txt) > colon_idx + 20:
                prefix_txt = txt[:colon_idx].strip()
                cleaned_pfx = _clean_candidate(prefix_txt)
                if cleaned_pfx and cleaned_pfx != cleaned:
                    # Tính offset tuyệt đối: txt đã strip → cộng thêm leading whitespace
                    leading_ws = len(raw) - len(raw.lstrip())
                    pfx_end = line_start + leading_ws + colon_idx + 1  # include ':'
                    _add(line_start, pfx_end, cleaned_pfx, key=(line_start, 'colon_prefix'))

    # Nguồn 2: Text bên trong các ô bảng HTML
    body_text = text[body_start:]
    base      = body_start
    _RE_SUBLABEL_SEP     = re.compile(r'[.\-:]\s+[A-Z]\.\s')
    _RE_CELL_HEADING_END = re.compile(r"\s+\d+\.\s")

    for cell_m in _RE_TD_TEXT.finditer(body_text):
        cstart = base + cell_m.start()
        cend   = base + cell_m.end()
        cell_t = _RE_HTML_TAG.sub("", cell_m.group(1)).replace("\n", " ")

        if _is_footer_line(cell_t):
            continue

        cleaned = _clean_candidate(cell_t)
        if cleaned:
            _add(cstart, cend, cleaned)
        elif len(cell_t.strip()) > MAX_HEADING_LEN:
            split_m = _RE_CELL_HEADING_END.search(cell_t)
            if split_m:
                head_part = cell_t[:split_m.start()].strip()
                cleaned_head = _clean_candidate(head_part)
                if cleaned_head:
                    _add(cstart, cstart + split_m.start(), cleaned_head,
                         key=(cstart, "cellhead"))

        m_split = _RE_SUBLABEL_SEP.search(cell_t)
        if m_split:
            prefix = cell_t[:m_split.start() + 1].strip()
            cleaned_prefix = _clean_candidate(prefix)
            if cleaned_prefix:
                _add(cstart, cstart + m_split.start() + 1, cleaned_prefix, key=(cstart, "prefix"))

            suffix = cell_t[m_split.start() + 1:].strip()
            cleaned_suffix = _clean_candidate(suffix)
            if cleaned_suffix:
                _add(cstart + m_split.start() + 1, cend, cleaned_suffix, key=(cstart, "suffix"))

    # Nguồn 3: Tiêu đề bị ngắt dòng do Page Break → merge các dòng liền kề
    line_list: List[Tuple[int, int, str, bool]] = []
    pos2 = body_start
    for raw in text[body_start:].splitlines(keepends=True):
        stripped = raw.strip()
        if stripped and not stripped.startswith("<") and stripped != "<!-- PAGE_BREAK -->":
            md_m = _RE_MD_HDG.match(stripped)
            is_md_hdg = bool(md_m)
            txt  = md_m.group(1).strip() if md_m else stripped
            if not _is_footer_line(txt):
                cleaned = _clean_candidate(txt)
                if cleaned:
                    line_list.append((pos2, pos2 + len(raw), cleaned, is_md_hdg))
        pos2 += len(raw)

    for i in range(len(line_list) - 1):
        s1, e1, t1, is_hdg1 = line_list[i]
        s2, e2, t2, is_hdg2 = line_list[i + 1]
        if len(t1) < 90 and len(t2) < 90:
            merged = t1 + " " + t2
            if len(merged) <= MAX_HEADING_LEN:
                _add(s1, e2, merged, key=(s1, e2))

    # Ghép 3 dòng liên tiếp (bất kể có ### hay không) để bắt các tiêu đề bị vỡ quá nhỏ
    for i in range(len(line_list) - 2):
        s1, e1, t1, is_hdg1 = line_list[i]
        s2, e2, t2, is_hdg2 = line_list[i + 1]
        s3, e3, t3, is_hdg3 = line_list[i + 2]
        if len(t1) < 90 and len(t2) < 90 and len(t3) < 90:
            merged = t1 + " " + t2 + " " + t3
            if len(merged) <= MAX_HEADING_LEN:
                _add(s1, e3, merged, key=(s1, e3))

    # Nối 3 dòng ### liên tiếp trở lên
    i = 0
    while i < len(line_list):
        if line_list[i][3]: # Nếu là thẻ md heading
            j = i + 1
            merged_text = line_list[i][2]
            s_start = line_list[i][0]
            e_end = line_list[i][1]
            while j < len(line_list) and line_list[j][3] and len(line_list[j][2]) < 90:
                merged_text += " " + line_list[j][2]
                e_end = line_list[j][1]
                j += 1

            if j - i >= 2:
                if len(merged_text) <= MAX_HEADING_LEN:
                    _add(s_start, e_end, merged_text, key=(s_start, e_end))
            i = j
        else:
            i += 1

    raw_candidates.sort(key=lambda x: x[0])

    # Lọc các candidate có tính chất lặp lại (Footer/Header)
    if total_pages > 0 and num_sections > 0:
        short_threshold = max(4, total_pages // num_sections)
        long_threshold = 4
        from collections import Counter
        counts = Counter(t for _, _, t in raw_candidates)

        candidates = []
        for s, e, t in raw_candidates:
            freq = counts[t]
            if len(t) > 25 and freq >= long_threshold:
                continue
            if len(t) <= 25 and freq >= short_threshold:
                continue
            candidates.append((s, e, t))
        return candidates
    else:
        return raw_candidates

# ──────────────────────────────────────────────────────────────────────────────
# HIERARCHICAL MATCHING
# ──────────────────────────────────────────────────────────────────────────────

def _match_unordered(nodes: List[Dict], candidates: List[Tuple[int, int, str]], r_start: int, r_end: int) -> None:
    """Matching không thứ tự cho cấp cao nhất (Chapter).

    BUG 2 FIX: Bỏ qua candidate khi section number mâu thuẫn với TOC node.
    """
    in_range = [(s, e, t) for s, e, t in candidates if r_start <= s < r_end]
    pairs = []
    for node in nodes:
        title = node.get("title", "")
        toc_num = _extract_section_num(title)
        for cand in in_range:
            cand_num = _extract_section_num(cand[2])
            # BUG 2 FIX: chặn khi section number không tương thích
            if not _section_num_compatible(toc_num, cand_num):
                continue
            score = _match_score(title, cand[2])
            if score >= MIN_MATCH_SCORE:
                pairs.append((score, cand[0], node, cand))
    pairs.sort(key=lambda x: (-x[0], x[1]))
    assigned_nodes: set = set()
    assigned_ranges: List[Tuple[int, int]] = []  # (start, end) của các candidate đã gán

    def _overlaps(cs: int, ce: int) -> bool:
        """Kiểm tra xem [cs, ce) có chồng lấp với bất kỳ range đã gán không."""
        return any(cs < ae and ce > as_ for as_, ae in assigned_ranges)

    for score, _cpos, node, cand in pairs:
        cstart, cend = cand[0], cand[1]
        if id(node) not in assigned_nodes and not _overlaps(cstart, cend):
            node["_match_start"]   = cstart
            node["_match_end"]     = cend
            node["_match_heading"] = cand[2]
            node["_match_score"]   = round(score, 3)
            assigned_nodes.add(id(node))
            assigned_ranges.append((cstart, cend))
    for node in nodes:
        if "_match_start" not in node:
            node["_match_start"] = node["_match_end"] = node["_match_heading"] = node["_match_score"] = None


    prev_end = r_start
    for node in nodes:  # duyệt theo thứ tự TOC
        ms = node.get("_match_start")
        if ms is None:
            continue
        if ms >= prev_end:
            prev_end = node.get("_match_end") or ms + 1
            continue
        # node này vi phạm thứ tự → tìm candidate tốt hơn sau prev_end
        title = node.get("title", "")
        best_score, best_cand = 0.0, None
        for cand in in_range:
            if cand[0] < prev_end:
                continue
            # kiểm tra không chồng lấp với node đã gán khác
            if _overlaps(cand[0], cand[1]):
                # nếu chính node này đang chiếm range đó thì bỏ qua check
                if (cand[0], cand[1]) != (node.get("_match_start"), node.get("_match_end")):
                    continue
            score = _match_score(title, cand[2])
            if score > best_score:
                best_score = score
                best_cand  = cand
        if best_score >= MIN_MATCH_SCORE and best_cand:
            # gỡ range cũ khỏi assigned_ranges
            old_range = (node["_match_start"], node["_match_end"])
            if old_range in assigned_ranges:
                assigned_ranges.remove(old_range)
            node["_match_start"]   = best_cand[0]
            node["_match_end"]     = best_cand[1]
            node["_match_heading"] = best_cand[2]
            node["_match_score"]   = round(best_score, 3)
            assigned_ranges.append((best_cand[0], best_cand[1]))
            prev_end = best_cand[1]
        else:
            # không tìm được → mark unmatched để tránh làm hỏng range của siblings
            old_range = (node["_match_start"], node["_match_end"])
            if old_range in assigned_ranges:
                assigned_ranges.remove(old_range)
            node["_match_start"] = node["_match_end"] = node["_match_heading"] = node["_match_score"] = None

def _match_ordered(nodes: List[Dict], candidates: List[Tuple[int, int, str]], r_start: int, r_end: int) -> None:
    """Matching có thứ tự (Monotonic) cho các cấp bên dưới.

    BUG 2 FIX: Bỏ qua candidate khi section number mâu thuẫn với TOC node.
    """
    in_range    = [(s, e, t) for s, e, t in candidates if r_start <= s < r_end]
    search_from = r_start
    for node in nodes:
        title = node.get("title", "")
        toc_num = _extract_section_num(title)
        best_score, best_cand = 0.0, None
        for cand in in_range:
            if cand[0] < search_from:
                continue
            # BUG 2 FIX: chặn khi section number không tương thích
            cand_num = _extract_section_num(cand[2])
            if not _section_num_compatible(toc_num, cand_num):
                continue
            score = _match_score(title, cand[2])
            if score > best_score:
                best_score = score
                best_cand  = cand
        if best_score >= MIN_MATCH_SCORE and best_cand:
            # FIX: Bắt trường hợp heading bị OCR tách thành 2 dòng liên tiếp.
            # Ví dụ: "PHÁ THAI BẰNG PHƯƠNG PHÁP HÚT CHÂN KHÔNG" (840142-840183)
            #        "(BƠM HÚT 1 VAN) VỚI THAI DƯỚI 7 TUẦN"    (840183-840220) ← best_cand
            # Điều kiện: có candidate kết thúc ĐÚNG TẠI best_cand[0] (adjacent, không overlap)
            # và merged text cũng score >= MIN_MATCH_SCORE → anchor match_start về đầu thật.
            anchor_start = best_cand[0]
            for cand in in_range:
                if cand[0] >= best_cand[0] or cand[0] < search_from:
                    continue
                if cand[1] != best_cand[0]:          # phải kết thúc ĐÚNG tại best_cand[0]
                    continue
                merged = cand[2] + " " + best_cand[2]
                if _match_score(title, merged) >= MIN_MATCH_SCORE:
                    anchor_start = cand[0]
                    break
            node["_match_start"]   = anchor_start
            node["_match_end"]     = best_cand[1]
            node["_match_heading"] = best_cand[2]
            node["_match_score"]   = round(best_score, 3)
            search_from = best_cand[1]  # advance bằng end của best (không phải anchor)
        else:
            node["_match_start"] = node["_match_end"] = node["_match_heading"] = node["_match_score"] = None

def _compute_sibling_ends(nodes: List[Dict], parent_end: int) -> None:
    matched = sorted(
        [(n["_match_start"], n) for n in nodes if n.get("_match_start") is not None],
        key=lambda x: x[0],
    )
    for i, (start, node) in enumerate(matched):
        node["_end_char"] = matched[i + 1][0] if i + 1 < len(matched) else parent_end
    for node in nodes:
        if node.get("_match_start") is None:
            node["_end_char"] = None

def _infer_from_children(node: Dict) -> None:
    """Nếu tiêu đề cha không tìm thấy, lấy dải char dựa trên các tiêu đề con."""
    if node.get("_match_start") is not None:
        return
    child_starts, child_ends = [], []
    for key in _CHILD_KEYS:
        for child in node.get(key, []):
            _infer_from_children(child)
            if child.get("_match_start") is not None:
                child_starts.append(child["_match_start"])
            if child.get("_end_char") is not None:
                child_ends.append(child["_end_char"])
    node["_match_start"] = min(child_starts) if child_starts else None
    node["_end_char"]    = max(child_ends)   if child_ends   else None

def _process_level(nodes: List[Dict], candidates: List, p_start: int, p_end: int, ordered: bool) -> None:
    if ordered:
        _match_ordered(nodes, candidates, p_start, p_end)
    else:
        _match_unordered(nodes, candidates, p_start, p_end)
    _compute_sibling_ends(nodes, p_end)
    for node in nodes:
        nstart, nend = node.get("_match_start"), node.get("_end_char")
        if nstart is None or nend is None:
            continue
        child_nodes = []
        for key in _CHILD_KEYS:
            child_nodes.extend(node.get(key, []))
        if child_nodes:
            _process_level(child_nodes, candidates, nstart, nend, ordered=True)

def _assign_all(toc_root: Dict, candidates: List, text_len: int) -> None:
    top_nodes = []
    for key in _CHILD_KEYS:
        top_nodes.extend(toc_root.get(key, []))
    if not top_nodes:
        return
    _process_level(top_nodes, candidates, 0, text_len, ordered=False)
    for node in top_nodes:
        _infer_from_children(node)
        for key in _CHILD_KEYS:
            for child in node.get(key, []):
                _infer_from_children(child)

# ──────────────────────────────────────────────────────────────────────────────
# CHUNK TREE BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def _build_chunk_node(toc_node: Dict, text: str, page_map: List[int]) -> Dict:
    start     = toc_node.get("_match_start")
    match_end = toc_node.get("_match_end")
    node_end  = toc_node.get("_end_char")

    child_nodes_all = []
    for key in _CHILD_KEYS:
        child_nodes_all.extend(toc_node.get(key, []))

    if start is not None and node_end is not None:
        content_start = match_end if match_end is not None else start
        child_starts  = [c["_match_start"] for c in child_nodes_all if c.get("_match_start") is not None]
        content_end   = min(child_starts) if child_starts else node_end
        raw_content   = text[content_start:content_end].strip()
    else:
        raw_content = None

    cleaned_content = _clean_content(raw_content)

    chunk: Dict = {
        "title":       toc_node["title"],
        "start_char":  start,
        "end_char":    node_end,
        "page_start":  _page_at(page_map, start),
        "page_end":    _page_at(page_map, (node_end - 1) if node_end else node_end),
        "content":     cleaned_content,
        "match_score": toc_node.get("_match_score"),
    }

    for key in _CHILD_KEYS:
        if key not in toc_node:
            continue
        children = toc_node[key]
        if children:
            chunk[key] = [_build_chunk_node(child, text, page_map) for child in children if child.get("title")]
        else:
            chunk[key] = []

    return chunk

# ──────────────────────────────────────────────────────────────────────────────
# CORE ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def process_pair(md_path: Path, toc_path: Path, out_path: Path) -> None:
    if not md_path.exists() or not toc_path.exists():
        logging.warning("MD/TOC file missing for: %s", md_path.name)
        return

    raw_text = md_path.read_text(encoding="utf-8", errors="ignore")
    toc_data = json.loads(toc_path.read_text(encoding="utf-8", errors="ignore"))

    clean_text = _preprocess(raw_text)

    body_start = _find_body_start(clean_text)
    logging.info("Body starts at char %d in %s", body_start, md_path.name)

    total_pages = toc_data.get("total_pages", 0)
    num_sections = sum(len(toc_data.get(k, [])) for k in _CHILD_KEYS)
    if num_sections == 0:
        num_sections = 1

    candidates = _extract_candidates(clean_text, body_start, total_pages, num_sections)
    logging.info("Extracted %d heading candidates", len(candidates))

    _assign_all(toc_data, candidates, len(clean_text))

    page_map = _build_page_map(clean_text)

    top_chunks = []
    for key in _CHILD_KEYS:
        for child in toc_data.get(key, []):
            if child.get("title"):
                top_chunks.append(_build_chunk_node(child, clean_text, page_map))

    result: Dict = {
        "title":           toc_data.get("title",           None),
        "publisher":       toc_data.get("publisher",       None),
        "decision_number": toc_data.get("decision_number", None),
        "specialty":       toc_data.get("specialty",       None),
        "date":            toc_data.get("date",            None),
        "isbn_electronic": toc_data.get("isbn_electronic", None),
        "isbn_print":      toc_data.get("isbn_print",      None),
        "total_pages":     toc_data.get("total_pages",     None),
        "source_file":     toc_data.get("source_file",     md_path.name),
        "chapters":        top_chunks,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote %d top-level chapters → %s", len(top_chunks), out_path.name)


def build_chunk_payload_from_text(clean_text: str, toc_data: Dict[str, Any], min_match_score: float | None = None) -> Dict[str, Any]:
    global MIN_MATCH_SCORE
    previous_threshold = MIN_MATCH_SCORE
    if min_match_score is not None:
        MIN_MATCH_SCORE = float(min_match_score)

    try:
        toc_copy = json.loads(json.dumps(toc_data, ensure_ascii=False))
        prepared_text = _preprocess(clean_text)
        body_start = _find_body_start(prepared_text)

        total_pages = toc_copy.get("total_pages") or 0
        num_sections = sum(len(toc_copy.get(k, [])) for k in _CHILD_KEYS)
        if num_sections == 0:
            num_sections = 1

        candidates = _extract_candidates(prepared_text, body_start, total_pages, num_sections)
        _assign_all(toc_copy, candidates, len(prepared_text))
        page_map = _build_page_map(prepared_text)

        top_chunks = []
        for key in _CHILD_KEYS:
            for child in toc_copy.get(key, []):
                if child.get("title"):
                    top_chunks.append(_build_chunk_node(child, prepared_text, page_map))

        return {
            "title":           toc_copy.get("title",           None),
            "publisher":       toc_copy.get("publisher",       None),
            "decision_number": toc_copy.get("decision_number", None),
            "specialty":       toc_copy.get("specialty",       None),
            "date":            toc_copy.get("date",            None),
            "isbn_electronic": toc_copy.get("isbn_electronic", None),
            "isbn_print":      toc_copy.get("isbn_print",      None),
            "total_pages":     toc_copy.get("total_pages",     None),
            "source_file":     toc_copy.get("source_file",     None),
            "chapters":        top_chunks,
        }
    finally:
        MIN_MATCH_SCORE = previous_threshold


class FuzzyChunkingService:
    def __init__(self, markdown_service=None) -> None:
        self._markdown_service = markdown_service

    def _match_score(self, toc_title: str, candidate: str) -> float:
        return _match_score(toc_title, candidate)

    def build_chunk_payload(self, clean_text: str, toc: dict[str, Any], score_threshold: float) -> dict[str, Any]:
        try:
            return build_chunk_payload_from_text(
                clean_text=clean_text,
                toc_data=toc,
                min_match_score=score_threshold,
            )
        except Exception as exc:
            raise UnprocessableEntityException(f"Chunking failed: {exc}") from exc


def main() -> None:
    pairs = get_file_pairs()
    if not pairs:
        logging.info("Không có cặp file nào để xử lý.")
        return

    for md_s, toc_s, out_s in pairs:
        md_p  = MD_INPUT_DIR / md_s  if not Path(md_s).is_absolute()  else Path(md_s)
        toc_p = TOC_INPUT_DIR / toc_s if not Path(toc_s).is_absolute() else Path(toc_s)
        process_pair(md_p, toc_p, OUTPUT_DIR / out_s)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.exception("Unhandled error: %s", e)
        sys.exit(1)