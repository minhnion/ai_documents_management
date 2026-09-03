import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Đảm bảo import app.
sys.path.insert(0, str(ROOT))

from app.services.pipeline.parse_models import ParseResult
from app.services.pipeline.toc_builder_service import TocBuilderService
from app.services.pipeline.build_chunks import ChunkDocumentBuilder, ChunkConfig

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def norm_title(t: str) -> str:
    import re
    t = re.sub(r"[*#`\[\]]", " ", t or "")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def analyze_chunk(payload: dict) -> dict:
    from collections import Counter
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

        child_keys = ["sections", "subsections", "subsubsections", "subsubsubsections", "subsubsubsubsections"]
        has_children = any(node.get(k) for k in child_keys)
        content = node.get("content") or ""

        if content and node.get("title"):
            if norm_title(content[:150]).startswith(norm_title(node["title"])[:150]):
                stats["content_starts_with_title"] += 1

        if siblings and idx + 1 < len(siblings) and content:
            next_title = siblings[idx + 1].get("title", "")
            if next_title and norm_title(next_title) and norm_title(next_title) in norm_title(content[-500:]):
                stats["content_overlaps_next"] += 1

        for child_key in child_keys:
            children = node.get(child_key, []) or []
            if children and content:
                child_title = children[0].get("title", "")
                if child_title and norm_title(child_title) and norm_title(child_title) in norm_title(content):
                    stats["content_contains_child_title"] += 1
                    break

        if not has_children and not content and not node.get("unmatched_reason"):
            stats["empty_leaf"] += 1

        for child_key in child_keys:
            children = node.get(child_key, []) or []
            for i, child in enumerate(children):
                walk(child, children, i)

    for chapter in payload.get("chapters", []):
        walk(chapter)
    return stats


async def run_toc(guideline_id: int, version_id: int, *, also_chunk: bool = True) -> None:
    artifact_dir = ROOT / "uploads" / "guidelines" / str(guideline_id) / str(version_id) / "pipeline"
    dpt3_path = artifact_dir / "dpt3_ocr.json"
    if not dpt3_path.exists():
        print(f"Không tìm thấy {dpt3_path}")
        raise SystemExit(1)

    parse_result = ParseResult.load(dpt3_path)
    service = TocBuilderService()
    toc = await service.build_toc(
        parse_result=parse_result,
        artifact_dir=artifact_dir,
        source_filename="source.pdf",
    )
    print("TOC built:", artifact_dir / "toc_structure.json")
    print("Usage events:", json.dumps(service.last_usage_events, ensure_ascii=False, indent=2))

    if also_chunk:
        builder = ChunkDocumentBuilder(ChunkConfig())
        payload = builder.build(artifact_dir / "toc_structure.json", dpt3_path)
        stats = analyze_chunk(payload)
        (artifact_dir / "chunks_dev.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Chunks built:", artifact_dir / "chunks_dev.json")
        print("Chunk stats:", json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/rebuild_toc.py <guideline_id> <version_id>")
        raise SystemExit(1)
    asyncio.run(run_toc(int(sys.argv[1]), int(sys.argv[2])))
