from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

import httpx
from dotenv import load_dotenv
from pypdf import PdfReader, PdfWriter

from parse_models import ElementGrounding, GroundingBox, GroundingPart, ParseMetadata, ParseResult, StructureNode

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _load_environment() -> None:
    here = Path(__file__).resolve().parent
    for candidate in (here / ".env", here.parent / "flow 2 api in ocr only" / ".env", Path.cwd() / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
    load_dotenv(override=False)


_load_environment()


@dataclass
class LandingOcrConfig:
    pdf_dir: Path = Path("./data/01_raw_pdf")
    markdown_dir: Path = Path("./data/02_ocr_markdown")
    output_dir: Path = Path("./data/06_ade_chunks")
    pdf_files: tuple[str, ...] = ()

    max_pages_per_call: int = 50
    max_part_size_bytes: int = 45 * 1024 * 1024
    hard_size_limit_bytes: int = 50 * 1024 * 1024

    request_timeout_seconds: float = 600.0
    max_retries: int = 3
    retry_backoff_seconds: float = 5.0
    inter_call_delay_seconds: float = 5.0

    api_key: str = field(default_factory=lambda: os.environ.get("VISION_AGENT_API_KEY", ""))
    environment: str = field(default_factory=lambda: os.environ.get("LANDINGAI_ADE_ENVIRONMENT", "production"))
    model: str = field(default_factory=lambda: os.environ.get("DPT3_MODEL", "dpt-3-pro-latest"))

    def __post_init__(self) -> None:
        if not self.api_key:
            raise EnvironmentError("VISION_AGENT_API_KEY chưa được set trong .env")

    @property
    def base_url(self) -> str:
        return "https://api.ade.eu-west-1.landing.ai" if self.environment == "eu" else "https://api.ade.landing.ai"

    @property
    def parse_url(self) -> str:
        return f"{self.base_url}/v2/parse"


class LandingOcrApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class ApiResponseAdapter:
    @classmethod
    def to_parse_result(cls, raw: dict) -> ParseResult:
        structure = cls._node(raw["structure"])
        grounding: dict[str, ElementGrounding] = {}
        cls._collect_grounding(raw["structure"], grounding)
        meta = raw.get("metadata") or {}
        billing = meta.get("billing") or {}
        metadata = ParseMetadata(
            job_id=meta.get("job_id", ""),
            filename="",
            version=meta.get("model_version", ""),
            page_count=meta.get("page_count", 0),
            failed_pages=list(meta.get("failed_pages") or []),
            duration_ms=meta.get("duration_ms", 0),
            markdown_chars=meta.get("output_markdown_chars", 0),
            credit_usage=billing.get("total_credits", 0.0),
        )
        return ParseResult(markdown=raw["markdown"], structure=structure, grounding=grounding, metadata=metadata)

    @classmethod
    def _node(cls, raw: dict) -> StructureNode:
        g = raw.get("grounding")
        return StructureNode(
            type=raw["type"],
            id=raw.get("id"),
            span=(g["range"]["start"], g["range"]["end"]) if g else None,
            page=g["page"] - 1 if g else None,
            width=1.0 if g else None,
            height=1.0 if g else None,
            status=raw.get("status"),
            reason=raw.get("reason"),
            row=raw.get("row"),
            col=raw.get("col"),
            rowspan=raw.get("rowspan"),
            colspan=raw.get("colspan"),
            children=[cls._node(c) for c in raw.get("children", [])],
        )

    @classmethod
    def _collect_grounding(cls, raw: dict, out: dict[str, ElementGrounding]) -> None:
        node_id, g = raw.get("id"), raw.get("grounding")
        if node_id and g:
            out[node_id] = ElementGrounding(
                page=g["page"] - 1,
                span=(g["range"]["start"], g["range"]["end"]),
                box=cls._box(g["box"]),
                parts=[
                    GroundingPart(span=(p["range"]["start"], p["range"]["end"]), box=cls._box(p["box"]))
                    for p in (raw.get("atomic_grounding") or [])
                ],
            )
        for child in raw.get("children", []):
            cls._collect_grounding(child, out)

    @staticmethod
    def _box(raw: dict) -> GroundingBox:
        return GroundingBox(left=raw["xmin"], top=raw["ymin"], right=raw["xmax"], bottom=raw["ymax"])


class LandingOcrClient:
    def __init__(self, config: LandingOcrConfig) -> None:
        self._config = config
        self._http = httpx.Client(timeout=httpx.Timeout(config.request_timeout_seconds))

    def __enter__(self) -> "LandingOcrClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def parse_file(self, pdf_path: Path) -> ParseResult:
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        data = {"model": self._config.model}

        last_error: Exception | None = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                with pdf_path.open("rb") as fh:
                    files = {"document": (pdf_path.name, fh, "application/pdf")}
                    response = self._http.post(self._config.parse_url, headers=headers, data=data, files=files)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("Lỗi kết nối tới DPT-3 (lần %d/%d): %s", attempt, self._config.max_retries, exc)
                time.sleep(self._config.retry_backoff_seconds * attempt)
                continue

            if response.status_code in (200, 206):
                result = ApiResponseAdapter.to_parse_result(response.json())
                if response.status_code == 206 or result.metadata.failed_pages:
                    logger.warning(
                        "  Partial success cho %s — trang lỗi: %s", pdf_path.name, result.metadata.failed_pages
                    )
                return result

            if response.status_code == 429 or response.status_code >= 500:
                last_error = LandingOcrApiError(response.status_code, response.text[:500])
                logger.warning(
                    "  Retry sau lỗi HTTP %d (lần %d/%d)", response.status_code, attempt, self._config.max_retries
                )
                time.sleep(self._config.retry_backoff_seconds * attempt)
                continue

            raise LandingOcrApiError(response.status_code, response.text[:500])

        raise last_error if last_error is not None else LandingOcrApiError(0, "unknown error")


class PdfSplitter:
    def __init__(self, max_part_bytes: int) -> None:
        self._max_part_bytes = max_part_bytes

    def split(self, pdf_path: Path, workdir: Path) -> list[Path]:
        reader = PdfReader(str(pdf_path))
        n_pages = len(reader.pages)
        file_size = pdf_path.stat().st_size
        avg_page_bytes = max(file_size // max(n_pages, 1), 1)
        pages_per_part = max(1, self._max_part_bytes // avg_page_bytes)

        parts: list[Path] = []
        start = 0
        part_index = 0
        while start < n_pages:
            end = min(start + pages_per_part, n_pages)
            part_path = workdir / f"part_{part_index:03d}.pdf"
            self._write_part(reader, start, end, part_path)
            while part_path.stat().st_size > self._max_part_bytes and end - start > 1:
                end -= max(1, (end - start) // 4)
                self._write_part(reader, start, end, part_path)
            parts.append(part_path)
            start = end
            part_index += 1
        return parts

    @staticmethod
    def _write_part(reader: PdfReader, start: int, end: int, out_path: Path) -> None:
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        with open(out_path, "wb") as fh:
            writer.write(fh)


class ParseResultMerger:
    _PART_SEPARATOR = "\n\n\n<!-- PAGE BREAK -->\n\n"

    @classmethod
    def merge(cls, parts: list[tuple[ParseResult, int]]) -> ParseResult:
        if len(parts) == 1 and parts[0][1] == 0:
            return parts[0][0]

        markdown_segments: list[str] = []
        merged_pages: list[StructureNode] = []
        merged_grounding: dict[str, ElementGrounding] = {}
        char_offset = 0
        credit_usage = 0.0
        duration_ms = 0
        failed_pages: list[int] = []
        markdown_chars = 0
        job_ids: list[str] = []
        filename = ""
        version = ""

        for part_index, (result, page_offset) in enumerate(parts):
            renamed = cls._rename_ids(result, f"p{part_index}_")
            shifted = cls._shift(renamed, char_offset, page_offset)

            markdown_segments.append(shifted.markdown)
            merged_pages.extend(shifted.structure.children)
            merged_grounding.update(shifted.grounding)

            char_offset += len(shifted.markdown) + len(cls._PART_SEPARATOR)
            credit_usage += shifted.metadata.credit_usage
            duration_ms += shifted.metadata.duration_ms
            failed_pages.extend(shifted.metadata.failed_pages)
            markdown_chars += shifted.metadata.markdown_chars
            job_ids.append(shifted.metadata.job_id)
            filename = filename or shifted.metadata.filename
            version = version or shifted.metadata.version

        page_count = max((p.page for p in merged_pages if p.page is not None), default=-1) + 1
        metadata = ParseMetadata(
            job_id="+".join(job_ids),
            filename=filename,
            version=version,
            page_count=page_count,
            failed_pages=sorted(set(failed_pages)),
            duration_ms=duration_ms,
            markdown_chars=markdown_chars,
            credit_usage=credit_usage,
        )
        structure = StructureNode(type="document", children=merged_pages)
        return ParseResult(
            markdown=cls._PART_SEPARATOR.join(markdown_segments),
            structure=structure,
            grounding=merged_grounding,
            metadata=metadata,
        )

    @staticmethod
    def _rename_ids(result: ParseResult, prefix: str) -> ParseResult:
        def rename_node(node: StructureNode) -> StructureNode:
            new_id = f"{prefix}{node.id}" if node.id is not None else None
            return replace(node, id=new_id, children=[rename_node(c) for c in node.children])

        new_structure = rename_node(result.structure)
        new_grounding = {f"{prefix}{k}": v for k, v in result.grounding.items()}
        return replace(result, structure=new_structure, grounding=new_grounding)

    @staticmethod
    def _shift(result: ParseResult, char_offset: int, page_offset: int) -> ParseResult:
        def shift_span(span: tuple[int, int] | None) -> tuple[int, int] | None:
            if span is None:
                return None
            return (span[0] + char_offset, span[1] + char_offset)

        def shift_node(node: StructureNode) -> StructureNode:
            new_page = node.page + page_offset if node.type == "page" and node.page is not None else node.page
            return replace(
                node,
                span=shift_span(node.span),
                page=new_page,
                children=[shift_node(c) for c in node.children],
            )

        new_structure = shift_node(result.structure)
        new_grounding = {
            k: ElementGrounding(
                page=g.page + page_offset,
                span=shift_span(g.span),
                box=g.box,
                parts=[GroundingPart(span=shift_span(p.span), box=p.box) for p in g.parts],
            )
            for k, g in result.grounding.items()
        }
        new_metadata = replace(
            result.metadata,
            failed_pages=[p + page_offset for p in result.metadata.failed_pages],
        )
        return replace(result, structure=new_structure, grounding=new_grounding, metadata=new_metadata)


class LandingOcrPipeline:
    def __init__(self, config: LandingOcrConfig, client: LandingOcrClient) -> None:
        self._config = config
        self._client = client
        self._splitter = PdfSplitter(config.max_part_size_bytes)

    def run(self) -> None:
        pdf_paths = self._discover_pdfs()
        if not pdf_paths:
            logger.info("Không có file PDF nào trong %s", self._config.pdf_dir)
            return

        for pdf_path in pdf_paths:
            try:
                self._process_one(pdf_path)
            except Exception:
                logger.exception("Lỗi khi xử lý %s", pdf_path.name)

        logger.info("\nDone  md → %s  |  json → %s", self._config.markdown_dir, self._config.output_dir)

    def _discover_pdfs(self) -> list[Path]:
        if self._config.pdf_files:
            return [self._config.pdf_dir / name for name in self._config.pdf_files]
        self._config.pdf_dir.mkdir(parents=True, exist_ok=True)
        return sorted(self._config.pdf_dir.glob("*.pdf"))

    def _process_one(self, pdf_path: Path) -> None:
        out_md = self._config.markdown_dir / f"{pdf_path.stem}_ocr.md"
        out_json = self._config.output_dir / f"{pdf_path.stem}_dpt3.json"

        n_pages = self._page_count(pdf_path)
        logger.info("[%s] %d trang (DPT-3 · %s)", pdf_path.name, n_pages, self._config.model)

        result = self._parse_document(pdf_path)

        self._config.markdown_dir.mkdir(parents=True, exist_ok=True)
        out_md.write_text(result.markdown, encoding="utf-8")

        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(
            "  → %s (%d trang, %.2f credits)",
            pdf_path.name, result.metadata.page_count, result.metadata.credit_usage,
        )

    def _parse_document(self, pdf_path: Path) -> ParseResult:
        file_size = pdf_path.stat().st_size
        if file_size > self._config.hard_size_limit_bytes:
            logger.info("  %.1f MB > giới hạn 50 MiB → tách file vật lý", file_size / (1024 * 1024))
            return self._parse_via_physical_split(pdf_path)

        page_count = self._page_count(pdf_path)
        if page_count > self._config.max_pages_per_call:
            logger.info("  %d trang > %d → chia batch bằng file vật lý", page_count, self._config.max_pages_per_call)
            return self._parse_via_page_batches(pdf_path, page_count)

        return self._client.parse_file(pdf_path)

    def _parse_via_page_batches(self, pdf_path: Path, page_count: int) -> ParseResult:
        reader = PdfReader(str(pdf_path))
        batches = list(self._batches(page_count, self._config.max_pages_per_call))
        with tempfile.TemporaryDirectory(prefix="dpt3_pages_") as tmp:
            tmp_dir = Path(tmp)
            parts: list[tuple[ParseResult, int]] = []
            for i, pages in enumerate(batches):
                logger.info("  batch %d/%d — trang %d-%d", i + 1, len(batches), pages[0] + 1, pages[-1] + 1)
                part_path = tmp_dir / f"pages_{i:03d}.pdf"
                PdfSplitter._write_part(reader, pages[0], pages[-1] + 1, part_path)
                result = self._client.parse_file(part_path)
                parts.append((result, pages[0]))
                if i < len(batches) - 1:
                    time.sleep(self._config.inter_call_delay_seconds)
        return ParseResultMerger.merge(parts)

    def _parse_via_physical_split(self, pdf_path: Path) -> ParseResult:
        with tempfile.TemporaryDirectory(prefix="dpt3_split_") as tmp:
            tmp_dir = Path(tmp)
            part_paths = self._splitter.split(pdf_path, tmp_dir)
            parts: list[tuple[ParseResult, int]] = []
            page_offset = 0
            for i, part_path in enumerate(part_paths):
                part_page_count = self._page_count(part_path)
                logger.info(
                    "  phần %d/%d (%d trang, %.1f MB)",
                    i + 1, len(part_paths), part_page_count, part_path.stat().st_size / (1024 * 1024),
                )
                if part_page_count > self._config.max_pages_per_call:
                    part_result = self._parse_via_page_batches(part_path, part_page_count)
                else:
                    part_result = self._client.parse_file(part_path)
                parts.append((part_result, page_offset))
                page_offset += part_page_count
                if i < len(part_paths) - 1:
                    time.sleep(self._config.inter_call_delay_seconds)
        return ParseResultMerger.merge(parts)

    @staticmethod
    def _batches(n: int, size: int) -> Iterator[list[int]]:
        for start in range(0, n, size):
            yield list(range(start, min(start + size, n)))

    @staticmethod
    def _page_count(pdf_path: Path) -> int:
        return len(PdfReader(str(pdf_path)).pages)


def main() -> None:
    config = LandingOcrConfig()
    with LandingOcrClient(config) as client:
        LandingOcrPipeline(config, client).run()


if __name__ == "__main__":
    main()
