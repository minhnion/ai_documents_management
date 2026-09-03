"""Chạy build_toc với model rẻ + track token/cost.

Usage:
    set TOC_MODEL=gpt-4o-mini
    python scripts/run_toc_tracked.py "d:/TTDN/dpt3/dpt3/06_ade_chunks/document-18_dpt3.json" "d:/TTDN/dpt3/dpt3/03_toc_json_test_cheap"
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Add pipeline dir to sys.path for the old build_toc module layout
_PIPELINE_DIR = Path(__file__).resolve().parent.parent / "app" / "services" / "pipeline"
if str(_PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_DIR))

# import after sys.path patch
from toc_builder_service import _build_toc_sync


def _main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python run_toc_tracked.py <dpt3_json_path> <output_dir> [model]")
        sys.exit(1)

    dpt3_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else os.environ.get("TOC_MODEL", "gpt-5.1")
    output_path = output_dir / f"{dpt3_path.stem}_toc_structure.json"

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY chưa được set")

    usage_events: list[dict] = []
    toc = _build_toc_sync(
        dpt3_path,
        api_key=api_key,
        model=model,
        output_path=output_path,
        usage_events=usage_events,
    )

    total_in = sum(e["input_tokens"] for e in usage_events)
    total_out = sum(e["output_tokens"] for e in usage_events)
    total_calls = len(usage_events)

    # rough pricing (current OpenAI API list prices, no cached discount)
    if "gpt-4o-mini" in model:
        in_price, out_price = 0.15, 0.60  # $/M tokens
    elif "gpt-4o" in model:
        in_price, out_price = 2.50, 10.00
    else:
        in_price, out_price = 2.50, 10.00  # fallback

    cost = (total_in / 1_000_000) * in_price + (total_out / 1_000_000) * out_price

    summary = {
        "model": model,
        "calls": total_calls,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "estimated_cost_usd": round(cost, 4),
    }
    (output_dir / "_usage_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Model: {model}")
    print(f"LLM calls: {total_calls}")
    print(f"Input tokens: {total_in}")
    print(f"Output tokens: {total_out}")
    print(f"Estimated cost: ${cost:.4f}")
    print(f"TOC saved: {output_path}")


if __name__ == "__main__":
    _main()
