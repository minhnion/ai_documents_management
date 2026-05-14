from __future__ import annotations

import bisect
import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_PAGE_BREAK = "<!-- PAGE BREAK -->"
_CHILD_KEYS = (
    "chapters", "sections", "subsections",
    "subsubsections", "subsubsubsections", "subsubsubsubsections",
)

_RE_ANCHOR       = re.compile(r"<a\s+id='[^']*'>.*?</a>", re.DOTALL)
_RE_HEADING      = re.compile(r"^#{1,6}\s*")
_RE_NONWORD      = re.compile(r"[^\w\s]")
_RE_HTML         = re.compile(r"<[^>]+>")
_RE_ALPHA_PREFIX = re.compile(r"^([A-Za-z])\.", re.UNICODE)
_RE_NUM_PREFIX   = re.compile(r"^(\d[\d.]*)")

_INTRA_CHUNK_SEARCH_WINDOW = 60_000
_HEADING_MATCH_THRESHOLD   = 0.55


# ── Text similarity helpers ──────────────────────────────────────────────────

def _words(text: str) -> set[str]:
    return set(_RE_NONWORD.sub(" ", text.lower()).split())


def _words_truncated(text: str, max_w: int) -> set[str]:
    return set(_RE_NONWORD.sub(" ", text.lower()).split()[:max_w])


def _dice(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


# ── Page / position helpers ──────────────────────────────────────────────────

def build_page_breaks(ocr_md: str) -> list[int]:
    return [m.start() for m in re.finditer(re.escape(_PAGE_BREAK), ocr_md)]


def page_at(char_pos: int, breaks: list[int]) -> int:
    return bisect.bisect_right(breaks, char_pos) + 1


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
    logger.info("ADE offset map: %d/%d chunks anchored", found, len(enriched))
    return enriched


def build_id_to_idx(ade_enriched: list[dict]) -> dict[str, int]:
    return {ch["id"]: i for i, ch in enumerate(ade_enriched) if ch.get("id")}


def _first_anchored_start(ade_enriched: list[dict], from_idx: int, fallback: int) -> int:
    for i in range(from_idx, len(ade_enriched)):
        sc = ade_enriched[i]["start_char"]
        if sc >= 0:
            return sc
    return fallback


# ── Heading search ───────────────────────────────────────────────────────────

def _find_next_heading_char(
    ocr_md: str,
    title: str,
    search_from: int,
    search_end: int | None = None,
    min_advance: int = 1,
) -> int | None:
    a_words = _words(title)
    if not a_words:
        return None

    search_start = search_from + min_advance
    end_limit    = search_end if search_end is not None else search_start + _INTRA_CHUNK_SEARCH_WINDOW
    window       = ocr_md[search_start:end_limit]
    lines        = window.splitlines(keepends=True)

    num_m        = _RE_NUM_PREFIX.match(title.strip())
    num_prefix   = num_m.group(1).rstrip(".").lower() if num_m else None
    num_re       = re.compile(re.escape(num_prefix) + r"(?:[.\s\u00a0]|$)") if num_prefix else None
    max_b_for_boost = max(len(a_words) * 2 + 2, 8)

    def _is_standalone(pos: int) -> bool:
        if pos == 0:
            return False
        p = line[pos - 1]
        return not (p.isdigit() or (p == "." and pos >= 2 and line[pos - 2].isdigit()))

    offset, best_pos, best_score = 0, None, 0.0

    for line in lines:
        clean = _RE_HTML.sub(" ", line)
        clean = _RE_HEADING.sub("", clean).strip()
        if clean:
            b     = _words(clean)
            score = _dice(a_words, b)

            if score >= _HEADING_MATCH_THRESHOLD and score > best_score:
                best_score, best_pos = score, search_start + offset

            if (num_re
                    and num_re.match(clean.lower())
                    and score >= _HEADING_MATCH_THRESHOLD
                    and len(b) <= max_b_for_boost
                    and best_score < 0.9):
                best_score, best_pos = 0.9, search_start + offset

            if num_re and not num_re.match(clean.lower()):
                for m in num_re.finditer(line):
                    if not _is_standalone(m.start()):
                        continue
                    sub = _RE_HTML.sub(" ", line[m.start():])
                    sub = _RE_HEADING.sub("", sub).strip()
                    if not sub:
                        continue
                    sub_b     = _words(sub)
                    sub_score = _dice(a_words, sub_b)
                    if sub_score >= _HEADING_MATCH_THRESHOLD and sub_score > best_score:
                        best_score, best_pos = sub_score, search_start + offset + m.start()
                    if (num_re.match(sub.lower())
                            and sub_score >= _HEADING_MATCH_THRESHOLD
                            and len(sub_b) <= max_b_for_boost
                            and best_score < 0.9):
                        best_score, best_pos = 0.9, search_start + offset + m.start()

        offset += len(line)

    return best_pos


def _refine_start_char(
    ocr_md: str,
    title: str,
    chunk_start: int,
    chunk_end: int | None,
    min_delta: int = 30,
    min_score: float = 0.55,
) -> int | None:
    a_words = _words(title)
    if not a_words:
        return None

    max_b     = max(len(a_words) * 2, 10)
    end_limit = chunk_end if chunk_end is not None else chunk_start + _INTRA_CHUNK_SEARCH_WINDOW
    window    = ocr_md[chunk_start:end_limit]
    lines     = window.splitlines(keepends=True)

    title_stripped = _RE_HEADING.sub("", title).strip()
    alpha_m        = _RE_ALPHA_PREFIX.match(title_stripped)
    num_m          = _RE_NUM_PREFIX.match(title_stripped) if not alpha_m else None
    title_prefix: str | None = None
    prefix_type:  str | None = None
    if alpha_m:
        title_prefix = alpha_m.group(1).upper()
        prefix_type  = "alpha"
    elif num_m:
        title_prefix = num_m.group(1).rstrip(".")
        prefix_type  = "num"

    offset, best_pos, best_score = 0, None, 0.0

    for line in lines:
        clean = _RE_HTML.sub(" ", line)
        clean = _RE_HEADING.sub("", clean).strip()
        if clean:
            if prefix_type == "alpha":
                cand_m = _RE_ALPHA_PREFIX.match(clean)
                if cand_m and cand_m.group(1).upper() != title_prefix:
                    offset += len(line)
                    continue
                if not cand_m and _RE_NUM_PREFIX.match(clean):
                    offset += len(line)
                    continue
            elif prefix_type == "num":
                cand_num = _RE_NUM_PREFIX.match(clean)
                if cand_num and cand_num.group(1).rstrip(".") != title_prefix:
                    offset += len(line)
                    continue

            b     = _words_truncated(clean, max_b)
            score = _dice(a_words, b)
            if score >= min_score and score > best_score:
                best_score, best_pos = score, chunk_start + offset
        offset += len(line)

    if best_pos is None or (best_pos - chunk_start) < min_delta:
        return None
    return best_pos


# ── Sibling / tree assignment ────────────────────────────────────────────────

def _compute_sibling_ends(
    nodes: list[dict],
    parent_end: int,
    ade_enriched: list[dict],
    ocr_md: str,
    boundary_title: str | None = None,
) -> None:
    last_split_by_chunk: dict[int, int] = {}

    for i, node in enumerate(nodes):
        cur_idx   = node.get("_chunk_idx")
        nxt_idx   = nodes[i + 1].get("_chunk_idx") if i + 1 < len(nodes) else None
        valid_nxt = nxt_idx is not None and (cur_idx is None or nxt_idx > cur_idx)
        node["_chunk_end"] = nxt_idx if valid_nxt else parent_end

        if cur_idx is not None and nxt_idx is not None and cur_idx == nxt_idx:
            chunk_start = ade_enriched[cur_idx].get("start_char", -1)
            nxt_title   = nodes[i + 1].get("title", "")
            if chunk_start >= 0 and nxt_title:
                search_from = last_split_by_chunk.get(cur_idx, chunk_start)
                chunk_end   = ade_enriched[cur_idx].get("end_char")
                split = _find_next_heading_char(ocr_md, nxt_title, search_from, search_end=chunk_end)
                if split is not None and split > search_from:
                    node["_end_char_override"]           = split
                    nodes[i + 1]["_start_char_override"] = split
                    last_split_by_chunk[cur_idx]         = split
                    logger.info(
                        "  [SHARED_CHUNK/sibling] %-40s ← split @char %d (id=%.8s)",
                        node["title"][:40], split, ade_enriched[cur_idx].get("id", ""),
                    )
                else:
                    logger.warning(
                        "  [SHARED_CHUNK/no-split] %-40s  next=%-40s",
                        node["title"][:40], nxt_title[:40],
                    )

        elif (
            not valid_nxt
            and cur_idx is not None
            and cur_idx == parent_end
            and boundary_title
        ):
            chunk_start = ade_enriched[cur_idx].get("start_char", -1) if cur_idx < len(ade_enriched) else -1
            chunk_end   = ade_enriched[cur_idx].get("end_char")        if cur_idx < len(ade_enriched) else None
            if chunk_start >= 0:
                search_from = last_split_by_chunk.get(cur_idx, chunk_start)
                split = _find_next_heading_char(
                    ocr_md, boundary_title, search_from, search_end=chunk_end
                )
                if split is not None and split > search_from:
                    node["_end_char_override"]   = split
                    last_split_by_chunk[cur_idx] = split
                    logger.info(
                        "  [CROSS_LEVEL/boundary] %-40s ← boundary '%.30s' @char %d",
                        node["title"][:40], boundary_title, split,
                    )
                else:
                    logger.warning(
                        "  [CROSS_LEVEL/no-split] %-40s  boundary='%.30s'",
                        node["title"][:40], boundary_title,
                    )

        elif (
            not valid_nxt
            and cur_idx is None
            and boundary_title
            and 0 < parent_end <= len(ade_enriched)
        ):
            ref_idx     = parent_end - 1
            chunk_start = ade_enriched[ref_idx].get("start_char", -1)
            chunk_end   = ade_enriched[ref_idx].get("end_char")
            if chunk_start >= 0:
                search_from = last_split_by_chunk.get(ref_idx, chunk_start)
                split = _find_next_heading_char(
                    ocr_md, boundary_title, search_from, search_end=chunk_end
                )
                if split is not None and split > search_from:
                    node["_end_char_override"]    = split
                    last_split_by_chunk[ref_idx]  = split
                    logger.info(
                        "  [CROSS_LEVEL/unmatched] %-40s ← boundary '%.30s' @char %d",
                        node["title"][:40], boundary_title, split,
                    )
                else:
                    logger.warning(
                        "  [CROSS_LEVEL/unmatched-no-split] %-40s  boundary='%.30s'",
                        node["title"][:40], boundary_title,
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
    boundary_title: str | None = None,
) -> None:
    for node in nodes:
        cid = node.get("heading_chunk_id")
        if cid:
            raw_idx = id_to_idx.get(cid)
            if raw_idx is not None and p_start <= raw_idx <= p_end:
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

    _compute_sibling_ends(nodes, p_end, ade_enriched, ocr_md, boundary_title=boundary_title)

    prev_chunk_end = p_start

    for i, node in enumerate(nodes):
        idx      = node.get("_chunk_idx")
        end      = node.get("_chunk_end")
        children = [ch for k in _CHILD_KEYS for ch in node.get(k, [])]

        if idx is not None:
            prev_chunk_end = idx

        if not children:
            continue

        child_start = idx if idx is not None else prev_chunk_end
        child_end   = end if end is not None else p_end

        child_boundary: str | None = None
        if i + 1 < len(nodes):
            nxt     = nodes[i + 1]
            nxt_idx = nxt.get("_chunk_idx")
            if nxt_idx is not None and nxt_idx == child_end:
                child_boundary = nxt.get("title") or ""
            elif nxt.get("title"):
                child_boundary = nxt.get("title")
        elif boundary_title is not None:
            child_boundary = boundary_title

        _process_level(
            children, ade_enriched, id_to_idx, child_start, child_end, ocr_md,
            boundary_title=child_boundary,
        )


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


# ── Content extraction ───────────────────────────────────────────────────────

def _find_heading_end(text: str, toc_title: str) -> int:
    a_words = _words(toc_title)
    if not a_words:
        return 0

    max_b    = max(len(a_words) * 2, 10)
    lines    = text.splitlines(keepends=True)
    best_i, best_score = -1, 0.0

    for i, line in enumerate(lines):
        clean = _RE_HTML.sub(" ", line)
        clean = _RE_HEADING.sub("", clean).strip()
        if not clean:
            continue
        b = _words_truncated(clean, max_b)
        score = _dice(a_words, b)
        if score > best_score:
            best_score, best_i = score, i

    if best_score < 0.5 or best_i < 0:
        return 0

    heading_end = sum(len(lines[j]) for j in range(best_i + 1))
    total_len   = sum(len(l) for l in lines)

    if total_len > 0 and heading_end / total_len > 0.70 and best_score < 0.65:
        return 0

    best_line       = lines[best_i]
    best_line_clean = _RE_HTML.sub(" ", best_line)
    best_line_clean = _RE_HEADING.sub("", best_line_clean).strip()
    colon_pos = best_line_clean.find(":")
    if colon_pos > 0:
        after_colon     = best_line_clean[colon_pos + 1:].strip()
        pre_colon_words = _words(best_line_clean[:colon_pos])
        if after_colon and len(pre_colon_words & a_words) >= max(1, len(a_words) // 2):
            prefix_end = sum(len(lines[j]) for j in range(best_i))
            raw_line   = lines[best_i]
            raw_colon  = raw_line.find(":")
            if raw_colon >= 0:
                inline_cut = prefix_end + raw_colon + 1
                if inline_cut < heading_end:
                    return inline_cut

    return heading_end


def _extract_content(ocr_md: str, toc_title: str, start: int, end: int) -> str | None:
    if start < 0 or end <= start:
        return None
    stripped = _RE_ANCHOR.sub("", ocr_md[start:end]).replace(_PAGE_BREAK, "")
    content  = stripped[_find_heading_end(stripped, toc_title):].strip()
    return content or None


# ── BBox aggregation ─────────────────────────────────────────────────────────

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


def _make_node_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


# ── Child helpers ────────────────────────────────────────────────────────────

def _get_first_child_start_char(
    toc_node: dict,
    ade_enriched: list[dict],
    ocr_md: str,
    parent_start_char: int | None = None,
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

            if parent_idx is not None and parent_idx == idx:
                chunk_end   = ade_enriched[idx].get("end_char")
                search_from = (
                    parent_start_char
                    if parent_start_char is not None and parent_start_char >= sc
                    else sc
                )
                split = _find_next_heading_char(
                    ocr_md, child.get("title", ""), search_from, search_end=chunk_end
                )
                if split is not None and split > search_from:
                    logger.info(
                        "  [SHARED_CHUNK/parent-child] %-40s ← child heading @char %d",
                        toc_node["title"][:40], split,
                    )
                    return split
                return None

            return sc

    return None


def _get_first_child_chunk_idx(toc_node: dict) -> int | None:
    for key in _CHILD_KEYS:
        for child in toc_node.get(key, []):
            idx = child.get("_chunk_idx")
            if idx is not None:
                return idx
    return None


# ── Node builder ─────────────────────────────────────────────────────────────

def _build_chunk_node(
    toc_node: dict,
    ade_enriched: list[dict],
    ocr_md: str,
    page_breaks: list[int],
    path: str = "",
    min_start_char: int | None = None,
    boundary_title: str | None = None,
) -> dict:
    h_idx: int | None = toc_node.get("_chunk_idx")
    s_end: int | None = toc_node.get("_chunk_end")

    start_char_override: int | None = toc_node.get("_start_char_override")

    start_char: int | None = (
        ade_enriched[h_idx]["start_char"]
        if h_idx is not None and h_idx < len(ade_enriched)
           and ade_enriched[h_idx]["start_char"] >= 0
        else None
    )

    if start_char_override is not None:
        logger.info(
            "  [START_OVERRIDE] %-40s  using precomputed char %d",
            toc_node["title"][:40], start_char_override,
        )
        start_char = start_char_override
    elif start_char is not None and h_idx is not None and h_idx < len(ade_enriched):
        refined = _refine_start_char(
            ocr_md, toc_node["title"], start_char,
            ade_enriched[h_idx].get("end_char"),
        )
        if refined is not None:
            logger.info(
                "  [START_REFINED] %-40s  %d → %d (Δ%d)",
                toc_node["title"][:40], start_char, refined, refined - start_char,
            )
            start_char = refined

    if min_start_char is not None and start_char is not None and start_char < min_start_char:
        logger.info(
            "  [START_CLAMPED] %-40s  %d → %d (parent floor)",
            toc_node["title"][:40], start_char, min_start_char,
        )
        start_char = min_start_char

    if s_end is not None:
        end_char: int | None = _first_anchored_start(ade_enriched, s_end, len(ocr_md))
    else:
        end_char = None

    end_char_override = toc_node.get("_end_char_override")
    if end_char_override is not None:
        end_char = end_char_override

    if (
        h_idx is not None and s_end is not None
        and h_idx == s_end
        and end_char_override is None
        and start_char is not None and end_char is not None
        and end_char <= start_char
        and h_idx < len(ade_enriched)
    ):
        chunk_actual_end = ade_enriched[h_idx].get("end_char", start_char)
        if chunk_actual_end > start_char:
            expand_end = chunk_actual_end
            if boundary_title:
                bpos = _find_next_heading_char(
                    ocr_md, boundary_title, start_char, search_end=chunk_actual_end
                )
                if bpos is not None and bpos > start_char:
                    expand_end = bpos
            chunk_raw = _RE_ANCHOR.sub("", ocr_md[start_char:expand_end]).replace(_PAGE_BREAK, "")
            if _find_heading_end(chunk_raw, toc_node["title"]) > 0:
                end_char = expand_end
                logger.info(
                    "  [SAME_IDX_EXPAND] %-40s end_char %d → %d",
                    toc_node["title"][:40], start_char, end_char,
                )

    if start_char is not None and end_char is not None and end_char <= start_char:
        logger.warning(
            "  [INVERTED_RANGE]  %-40s  start=%d end=%d",
            toc_node["title"][:40], start_char, end_char,
        )
        end_char = None

    page_start = page_at(start_char, page_breaks) if start_char is not None else None
    page_end   = page_at(end_char - 1, page_breaks) if end_char else None

    heading_bbox: dict | None = None
    if h_idx is not None and h_idx < len(ade_enriched):
        bbs = ade_enriched[h_idx].get("bboxes", [])
        heading_bbox = bbs[0] if bbs else None

    has_children = any(toc_node.get(k) for k in _CHILD_KEYS)
    if has_children:
        fci = _get_first_child_chunk_idx(toc_node)
        effective_s_end = fci if fci is not None else s_end
    else:
        effective_s_end = s_end

    same_chunk = (
        h_idx is not None and effective_s_end is not None and effective_s_end <= h_idx
    ) or (
        h_idx is not None and end_char_override is not None
        and end_char_override <= (
            ade_enriched[h_idx]["end_char"]
            if h_idx < len(ade_enriched) else end_char_override + 1
        )
    )

    content_bboxes: list[dict] = []
    if h_idx is not None and effective_s_end is not None:
        if same_chunk:
            raw = (
                list(ade_enriched[h_idx].get("bboxes", []))
                if ade_enriched[h_idx].get("type") != "marginalia" else []
            )
        else:
            raw = [
                b
                for i in range(h_idx + 1, min(effective_s_end, len(ade_enriched)))
                if ade_enriched[i].get("type") != "marginalia"
                for b in ade_enriched[i].get("bboxes", [])
            ]
            if not raw and ade_enriched[h_idx].get("type") != "marginalia":
                raw = list(ade_enriched[h_idx].get("bboxes", []))
        content_bboxes = _union_bboxes_by_page(raw)

    landing_chunks: list[dict] = []
    if h_idx is not None and effective_s_end is not None:
        lc_range = (
            range(h_idx, h_idx + 1)
            if same_chunk
            else range(h_idx, min(effective_s_end, len(ade_enriched)))
        )
        _ID_TYPES = {"table", "figure"}
        landing_chunks = [
            {"id": ade_enriched[i]["id"], "type": ade_enriched[i].get("type", "text")}
            for i in lc_range
            if ade_enriched[i].get("id") and ade_enriched[i].get("type", "text") in _ID_TYPES
        ]

    node_path = f"{path}/{toc_node['title']}" if path else toc_node["title"]
    node_id   = _make_node_id(node_path)

    content: str | None = None
    if start_char is not None:
        if has_children:
            first_child_sc = _get_first_child_start_char(
                toc_node, ade_enriched, ocr_md, parent_start_char=start_char
            )
            if first_child_sc is not None and first_child_sc > start_char:
                content = _extract_content(ocr_md, toc_node["title"], start_char, first_child_sc)
        elif end_char is not None:
            content = _extract_content(ocr_md, toc_node["title"], start_char, end_char)

    node_out: dict = {
        "node_id":        node_id,
        "title":          toc_node["title"],
        "page_start":     page_start,
        "page_end":       page_end,
        "content":        content,
        "match_score":    round(toc_node.get("_match_score") or 0.0, 4) or None,
        "heading_bbox":   heading_bbox,
        "content_bboxes": content_bboxes,
        "landing_chunks": landing_chunks,
    }

    for key in _CHILD_KEYS:
        if key not in toc_node:
            continue
        siblings = toc_node[key]
        built: list[dict] = []
        for ci, child in enumerate(siblings):
            if not child.get("title"):
                continue
            child_boundary = siblings[ci + 1].get("title") if ci + 1 < len(siblings) else boundary_title
            built.append(
                _build_chunk_node(
                    child, ade_enriched, ocr_md, page_breaks, node_path,
                    min_start_char=start_char,
                    boundary_title=child_boundary,
                )
            )
        node_out[key] = built

    return node_out


# ── Public service class ─────────────────────────────────────────────────────

import json


class BBoxChunkingService:
    """Pure in-memory chunking service.

    Mirrors the pattern of TocBuilderService / LandingAIOcrService:
    no disk I/O, no blocking network calls, no CLI scaffolding.
    Called directly (synchronously) by DocumentIngestionPipelineService._chunk_with_fuzzy_matching().
    """

    @staticmethod
    def build_chunk_payload(
        ocr_md_text: str,
        ade_chunks: list[dict[str, Any]],
        toc_data: dict[str, Any],
    ) -> dict[str, Any]:
        page_breaks  = build_page_breaks(ocr_md_text)
        ade_enriched = build_ade_offset_map(ocr_md_text, ade_chunks)
        id_to_idx    = build_id_to_idx(ade_enriched)

        toc_copy = json.loads(json.dumps(toc_data, ensure_ascii=False))
        _assign_all(toc_copy, ade_enriched, id_to_idx, ocr_md_text)

        top_level_nodes = [
            child
            for key in _CHILD_KEYS
            for child in toc_copy.get(key, [])
            if child.get("title")
        ]
        top_chunks = [
            _build_chunk_node(
                child, ade_enriched, ocr_md_text, page_breaks,
                boundary_title=(
                    top_level_nodes[ci + 1].get("title")
                    if ci + 1 < len(top_level_nodes) else None
                ),
            )
            for ci, child in enumerate(top_level_nodes)
        ]

        _META_KEYS = (
            "title", "publisher", "decision_number", "specialty", "date",
            "isbn_electronic", "isbn_print", "total_pages", "source_file",
        )
        result = {k: toc_copy.get(k) for k in _META_KEYS}
        result["chapters"] = top_chunks
        return result