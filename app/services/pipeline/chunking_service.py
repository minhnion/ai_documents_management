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
    """Bỏ qua phần mục lục đầu file để tránh match nhầm vào bảng TOC."""
    sentinel = "<!-- PAGE_BREAK -->"
    pos      = 0
    in_toc   = False

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not in_toc:
            plain = _RE_HTML_TAG.sub("", stripped).strip()
            if _RE_TOC_HEADER.match(stripped) or _RE_TOC_HEADER.match(plain):
                in_toc = True
            pos += len(line)
            continue
        if (not stripped or stripped == sentinel
                or stripped.startswith("<") or _RE_PURE_NUM.match(stripped)):
            pos += len(line)
            continue
        return pos
    return 0

# ──────────────────────────────────────────────────────────────────────────────
# FUZZY MATCHING LOGIC
# ──────────────────────────────────────────────────────────────────────────────

def _normalize(title: str) -> str:
    """Chuẩn hóa để so khớp: không dấu, viết thường, loại bỏ nhãn 'Bước/Phần'."""
    s = title.strip()
    s = re.sub(
        r"^(?:ph[a\u1ea7n]|phan|b[\u01b0\u01a1\u01b0\u01a1c]+|buoc|"
        r"ch[\u01b0\u01a1\u01a1u]ng|chuong)\s+\S+\s*[\.\:\-\)]?\s*",
        "", s, flags=re.IGNORECASE,
    )
    s = re.sub(r"^[0-9]+(?:\.[0-9]+)*[\.\:\)\-]?\s*", "", s)
    s = re.sub(r"^[A-Za-z][\.\)\-]\s*", "", s)
    s = re.sub(r"\s*\(.*", "", s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())

def _word_set(text: str) -> set:
    return {w for w in _normalize(text).split() if len(w) > 2}

def _effective_inter(toc_word_list: List[str], cand_words: set) -> int:
    """
    Đếm số toc_words được cover bởi cand_words, kể cả chữ viết tắt y tế.
    Chỉ dùng kết quả acronym expansion khi nó cover TẤT CẢ toc_words.
    """
    toc_set     = set(toc_word_list)
    direct      = toc_set & cand_words
    direct_cnt  = len(direct)

    matched   = set(direct)
    unmatched = [w for w in toc_word_list if w not in matched]

    for cw in cand_words:
        if cw in toc_set or len(cw) < 2:
            continue
        for start in range(len(unmatched)):
            for length in range(2, len(unmatched) - start + 1):
                subseq = unmatched[start : start + length]
                if (len(cw) == length
                        and all(cw[i] == subseq[i][0] for i in range(length))):
                    matched.update(subseq)
                    for w in subseq:
                        try: unmatched.remove(w)
                        except ValueError: pass
                    break

    expanded_cnt = len(matched)

    if expanded_cnt == len(toc_word_list):
        return expanded_cnt
    return direct_cnt


def _match_score(toc_title: str, candidate: str) -> float:
    toc_norm   = _normalize(toc_title)
    cand_norm  = _normalize(candidate)
    toc_list   = [w for w in toc_norm.split()  if len(w) > 2]
    cand_words = {w for w in cand_norm.split() if len(w) > 2}
    if not toc_list or not cand_words:
        return 0.0

    eff_inter = _effective_inter(toc_list, cand_words)
    forward   = eff_inter / len(toc_list)

    if len(toc_list) <= 3:
        backward = min(1.0, eff_inter / len(cand_words))
        if backward < 0.30:
            if cand_norm.startswith(toc_norm):
                return forward
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

def _extract_candidates(text: str, body_start: int) -> List[Tuple[int, int, str]]:
    """Trích xuất các vị trí nghi ngờ là tiêu đề từ text và bảng HTML."""
    seen:       set = set()
    candidates: List[Tuple[int, int, str]] = []

    def _add(s: int, e: int, t: str, key=None) -> None:
        k = key if key is not None else s
        if k not in seen:
            seen.add(k)
            candidates.append((s, e, t))

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
        cleaned = _clean_candidate(txt)
        if cleaned:
            _add(line_start, pos, cleaned)

    # Nguồn 2: Text bên trong các ô bảng HTML
    body_text = text[body_start:]
    base      = body_start
    _RE_SUBLABEL_SEP     = re.compile(r'[\.\-\:]\s+[A-Z]\.\s')
    _RE_CELL_HEADING_END = re.compile(r"\s+\d+\.\s")

    for cell_m in _RE_TD_TEXT.finditer(body_text):
        cstart = base + cell_m.start()
        cend   = base + cell_m.end()
        cell_t = _RE_HTML_TAG.sub("", cell_m.group(1)).replace("\n", " ")

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

    # Nguồn 3: Tiêu đề bị ngắt dòng do Page Break → merge 2 dòng liền kề
    line_list: List[Tuple[int, int, str]] = []
    pos2 = body_start
    for raw in text[body_start:].splitlines(keepends=True):
        stripped = raw.strip()
        if stripped and not stripped.startswith("<") and stripped != "<!-- PAGE_BREAK -->":
            md_m = _RE_MD_HDG.match(stripped)
            txt  = md_m.group(1).strip() if md_m else stripped
            cleaned = _clean_candidate(txt)
            if cleaned:
                line_list.append((pos2, pos2 + len(raw), cleaned))
        pos2 += len(raw)

    for i in range(len(line_list) - 1):
        s1, e1, t1 = line_list[i]
        s2, e2, t2 = line_list[i + 1]
        if len(t1) < 90 and len(t2) < 90:
            merged = t1 + " " + t2
            if len(merged) <= MAX_HEADING_LEN:
                _add(s1, e2, merged, key=(s1, e2))

    candidates.sort(key=lambda x: x[0])
    return candidates

# ──────────────────────────────────────────────────────────────────────────────
# HIERARCHICAL MATCHING
# ──────────────────────────────────────────────────────────────────────────────

def _match_unordered(nodes: List[Dict], candidates: List[Tuple[int, int, str]], r_start: int, r_end: int) -> None:
    """Matching không thứ tự cho cấp cao nhất (Chapter)."""
    in_range = [(s, e, t) for s, e, t in candidates if r_start <= s < r_end]
    pairs = []
    for node in nodes:
        title = node.get("title", "")
        for cand in in_range:
            score = _match_score(title, cand[2])
            if score >= MIN_MATCH_SCORE:
                pairs.append((score, cand[0], node, cand))
    pairs.sort(key=lambda x: (-x[0], x[1]))
    assigned_nodes, assigned_cands = set(), set()
    for score, _cpos, node, cand in pairs:
        if id(node) not in assigned_nodes and cand[0] not in assigned_cands:
            node["_match_start"]   = cand[0]
            node["_match_end"]     = cand[1]
            node["_match_heading"] = cand[2]
            node["_match_score"]   = round(score, 3)
            assigned_nodes.add(id(node))
            assigned_cands.add(cand[0])
    for node in nodes:
        if "_match_start" not in node:
            node["_match_start"] = node["_match_end"] = node["_match_heading"] = node["_match_score"] = None

def _match_ordered(nodes: List[Dict], candidates: List[Tuple[int, int, str]], r_start: int, r_end: int) -> None:
    """Matching có thứ tự (Monotonic) cho các cấp bên dưới."""
    in_range    = [(s, e, t) for s, e, t in candidates if r_start <= s < r_end]
    search_from = r_start
    for node in nodes:
        title, best_score, best_cand = node.get("title", ""), 0.0, None
        for cand in in_range:
            if cand[0] < search_from:
                continue
            score = _match_score(title, cand[2])
            if score > best_score:
                best_score = score
                best_cand  = cand
        if best_score >= MIN_MATCH_SCORE and best_cand:
            node["_match_start"]   = best_cand[0]
            node["_match_end"]     = best_cand[1]
            node["_match_heading"] = best_cand[2]
            node["_match_score"]   = round(best_score, 3)
            search_from = best_cand[1]
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

    candidates = _extract_candidates(clean_text, body_start)
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
        candidates = _extract_candidates(prepared_text, body_start)
        _assign_all(toc_copy, candidates, len(prepared_text))
        page_map = _build_page_map(prepared_text)

        top_chunks = []
        for key in _CHILD_KEYS:
            for child in toc_copy.get(key, []):
                if child.get("title"):
                    top_chunks.append(_build_chunk_node(child, prepared_text, page_map))

        return {
            "title":           toc_copy.get("title", None),
            "publisher":       toc_copy.get("publisher", None),
            "decision_number": toc_copy.get("decision_number", None),
            "specialty":       toc_copy.get("specialty", None),
            "date":            toc_copy.get("date", None),
            "isbn_electronic": toc_copy.get("isbn_electronic", None),
            "isbn_print":      toc_copy.get("isbn_print", None),
            "total_pages":     toc_copy.get("total_pages", None),
            "source_file":     toc_copy.get("source_file", None),
            "chapters":        top_chunks,
        }
    finally:
        MIN_MATCH_SCORE = previous_threshold


class FuzzyChunkingService:
    def __init__(self, markdown_service=None) -> None:
        self._markdown_service = markdown_service

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
