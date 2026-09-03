#!/usr/bin/env python
"""Re-chunk offline từ artifact dpt3_ocr.json + toc_structure.json. Không gọi API.

Usage (từ repo root):
    python scripts/rechunk_offline.py [--top N] [--save] [uploads/guidelines/<g>/<v>/pipeline]

Nếu không truyền path, tự tìm các cặp artifact trong uploads/guidelines.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

# Để import app.services.pipeline.build_chunks
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.pipeline.build_chunks import ChunkDocumentBuilder, ChunkConfig


def norm_title(t: str) -> str:
    t = re.sub(r"[*#`\[\]]", " ", t or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def collect_nodes(node: dict, out: list[dict], path: str = "") -> None:
    title = node.get("title", "")
    p = f"{path}/{title}" if path else title
    out.append({"path": p, "node": node})
    for key in ["sections", "subsections", "subsubsections", "subsubsubsections", "subsubsubsubsections"]:
        for child in node.get(key, []) or []:
            collect_nodes(child, out, p)


def analyze_chunk(payload: dict) -> dict:
    stats = {
        "total_nodes": 0,
        "matched": 0,
        "unmatched": 0,
        "empty_leaf": 0,
        "content_starts_with_title": 0,
        "content_overlaps_next": 0,
        "content_contains_child_title": 0,
        "reasons": Counter(),
    }

    def walk(node: dict, siblings: list[dict] | None = None, idx: int = 0) -> None:
        stats["total_nodes"] += 1
        if node.get("unmatched_reason"):
            stats["unmatched"] += 1
            stats["reasons"][node["unmatched_reason"]] += 1
        else:
            stats["matched"] += 1

        has_children = any(node.get(k) for k in ["sections", "subsections", "subsubsections", "subsubsubsections", "subsubsubsubsections"])
        content = node.get("content") or ""

        # content bắt đầu bằng chính tiêu đề của node -> _content_start chưa nhảy qua heading
        if content and node.get("title"):
            if norm_title(content[:150]).startswith(norm_title(node["title"])[:150]):
                stats["content_starts_with_title"] += 1

        # content chứa tiêu đề của node kế tiếp (cha nuốt con / anh em lấn nhau)
        if siblings and idx + 1 < len(siblings) and content:
            next_title = siblings[idx + 1].get("title", "")
            if next_title and norm_title(next_title) and norm_title(next_title) in norm_title(content[-500:]):
                stats["content_overlaps_next"] += 1

        # content chứa tiêu đề của con đầu tiên
        for child_key in ["sections", "subsections", "subsubsections", "subsubsubsections", "subsubsubsubsections"]:
            children = node.get(child_key, []) or []
            if children and content:
                child_title = children[0].get("title", "")
                if child_title and norm_title(child_title) and norm_title(child_title) in norm_title(content):
                    stats["content_contains_child_title"] += 1
                    break

        # leaf rỗng
        if not has_children and not content and not node.get("unmatched_reason"):
            stats["empty_leaf"] += 1

        for child_key in ["sections", "subsections", "subsubsections", "subsubsubsections", "subsubsubsubsections"]:
            children = node.get(child_key, []) or []
            for i, child in enumerate(children):
                walk(child, children, i)

    for chapter in payload.get("chapters", []):
        walk(chapter)
    return stats


def find_pairs(root: Path, top_n: int | None = None) -> list[tuple[Path, Path, Path]]:
    pairs = []
    for toc_path in sorted(root.rglob("toc_structure.json")):
        dpt3_path = toc_path.parent / "dpt3_ocr.json"
        if dpt3_path.exists():
            pairs.append((toc_path, dpt3_path, toc_path.parent / "chunks_dev.json"))
    if top_n:
        # ưu tiên file nhỏ
        pairs = sorted(pairs, key=lambda x: x[1].stat().st_size)[:top_n]
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=None, help="Chỉ chạy trên N file dpt3_ocr.json nhỏ nhất")
    parser.add_argument("--largest", type=int, default=None, help="Chỉ chạy trên N file dpt3_ocr.json lớn nhất")
    parser.add_argument("--save", action="store_true", help="Lưu chunks_dev.json cạnh toc_structure.json")
    parser.add_argument("--dir", type=Path, default=ROOT / "uploads" / "guidelines", help="Thư mục tìm artifact")
    args = parser.parse_args()
    args.dir = args.dir.resolve()

    pairs = find_pairs(args.dir, args.top)
    if args.largest:
        pairs = sorted(pairs, key=lambda x: x[1].stat().st_size, reverse=True)[:args.largest]
    if not pairs:
        print("Không tìm thấy cặp toc_structure.json + dpt3_ocr.json trong", args.dir)
        return 1

    builder = ChunkDocumentBuilder(ChunkConfig())
    summary: list[dict] = []

    print(f"Found {len(pairs)} pair(s) to rechunk")
    for toc_path, dpt3_path, out_path in pairs:
        try:
            t0 = time.perf_counter()
            payload = builder.build(toc_path, dpt3_path)
            elapsed = time.perf_counter() - t0
            if args.save:
                out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            stats = analyze_chunk(payload)
            row = {
                "guideline_version": str(toc_path.parent.relative_to(ROOT)),
                "elapsed_s": round(elapsed, 3),
                "dpt3_size_kb": round(dpt3_path.stat().st_size / 1024, 1),
                **stats,
                "reasons": dict(stats["reasons"]),
            }
            summary.append(row)
            print(
                f"{row['guideline_version']:40s} nodes={stats['total_nodes']:3d} "
                f"matched={stats['matched']:3d} unmatched={stats['unmatched']:3d} "
                f"empty={stats['empty_leaf']:3d} overlap_next={stats['content_overlaps_next']:3d} "
                f"starts_with_title={stats['content_starts_with_title']:3d} "
                f"contains_child_title={stats['content_contains_child_title']:3d}"
            )
        except Exception as exc:
            print(f"FAIL {toc_path}: {exc}")

    if summary:
        out = ROOT / "rechunk_baseline.json"
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nBaseline summary saved to {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
