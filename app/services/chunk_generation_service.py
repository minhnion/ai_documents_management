from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from openai import OpenAI
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestException, UnprocessableEntityException
from app.models.chunk import Chunk
from app.models.section import Section
from app.services.pipeline.chunk_prompts import (
    CHUNK_ABSTRACT_SYSTEM_PROMPT,
    build_chunk_abstract_user_prompt,
)

_PAGE_BREAK_RE = re.compile(r"<!--\s*PAGE(?:_| )BREAK\s*-->", re.IGNORECASE)
_STANDALONE_PAGE_NUMBER_RE = re.compile(r"(?m)^\s*\d+\s*$")
_BLANK_LINE_RE = re.compile(r"\n{3,}")


@dataclass(slots=True)
class SectionSnapshot:
    section_id: int
    parent_id: int | None
    heading: str | None
    content: str | None
    section_path: str | None
    order_index: int | None
    children: list["SectionSnapshot"] = field(default_factory=list)


@dataclass(slots=True)
class PreparedChunk:
    section_id: int
    text: str


class ChunkGenerationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def rebuild_chunks_for_version(self, version_id: int) -> dict[str, int]:
        max_chars = self._validate_chunk_settings()

        await self.db.execute(delete(Chunk).where(Chunk.version_id == version_id))
        await self.db.flush()

        sections = await self._load_section_snapshots(version_id)
        if not sections:
            return {"chunk_count": 0}

        roots = self._build_section_tree(sections)
        prepared_chunks = self._prepare_chunk_specs(roots, max_chars=max_chars)
        if not prepared_chunks:
            return {"chunk_count": 0}

        chunk_rows = await self._persist_chunk_rows(
            version_id=version_id,
            prepared_chunks=prepared_chunks,
        )
        return {"chunk_count": len(chunk_rows)}

    def _validate_chunk_settings(self) -> int:
        if not settings.OPENAI_API_KEY.strip():
            raise BadRequestException("OPENAI_API_KEY is required for chunk abstract/embedding pipeline.")
        if not settings.OPENAI_MODEL_NAME.strip():
            raise BadRequestException("OPENAI_MODEL_NAME is required for chunk abstract pipeline.")
        if not settings.OPENAI_EMBEDDING_MODEL_NAME.strip():
            raise BadRequestException("OPENAI_EMBEDDING_MODEL_NAME is required for chunk embedding pipeline.")
        max_chars = int(settings.CHUNK_MAX_CHARS)
        if max_chars <= 0:
            raise BadRequestException("CHUNK_MAX_CHARS must be greater than 0.")
        return max_chars

    async def _load_section_snapshots(self, version_id: int) -> list[SectionSnapshot]:
        rows = (
            await self.db.execute(
                select(Section).where(Section.version_id == version_id)
            )
        ).scalars().all()
        return [
            SectionSnapshot(
                section_id=int(section.section_id),
                parent_id=section.parent_id,
                heading=section.heading,
                content=section.content,
                section_path=section.section_path,
                order_index=section.order_index,
            )
            for section in rows
        ]

    def _build_section_tree(
        self,
        sections: Sequence[SectionSnapshot],
    ) -> list[SectionSnapshot]:
        node_map = {section.section_id: section for section in sections}
        roots: list[SectionSnapshot] = []
        for section in sections:
            if section.parent_id is None:
                roots.append(section)
                continue
            parent = node_map.get(section.parent_id)
            if parent is None:
                roots.append(section)
                continue
            parent.children.append(section)

        def sort_key(node: SectionSnapshot) -> tuple[tuple[int, ...], int, int]:
            return (
                self._section_path_sort_key(node.section_path),
                int(node.order_index or 0),
                int(node.section_id),
            )

        def sort_children(nodes: list[SectionSnapshot]) -> list[SectionSnapshot]:
            nodes.sort(key=sort_key)
            for node in nodes:
                sort_children(node.children)
            return nodes

        return sort_children(roots)

    def _prepare_chunk_specs(
        self,
        roots: Sequence[SectionSnapshot],
        *,
        max_chars: int,
    ) -> list[PreparedChunk]:
        prepared: list[PreparedChunk] = []
        for root in roots:
            self._collect_subtree_chunks(
                node=root,
                ancestor_blocks=[],
                max_chars=max_chars,
                prepared=prepared,
            )
        return prepared

    def _collect_subtree_chunks(
        self,
        *,
        node: SectionSnapshot,
        ancestor_blocks: list[str],
        max_chars: int,
        prepared: list[PreparedChunk],
    ) -> None:
        own_block = self._build_section_block(node)
        next_ancestor_blocks = list(ancestor_blocks)
        if own_block:
            next_ancestor_blocks.append(own_block)

        if not node.children:
            if own_block:
                prepared.append(
                    PreparedChunk(
                        section_id=node.section_id,
                        text=self._join_context_blocks([*ancestor_blocks, own_block]),
                    )
                )
            return

        subtree_block = self._build_subtree_block(node)
        if subtree_block and len(subtree_block) <= max_chars:
            prepared.append(
                PreparedChunk(
                    section_id=node.section_id,
                    text=self._join_context_blocks([*ancestor_blocks, subtree_block]),
                )
            )
            return

        for child in node.children:
            self._collect_subtree_chunks(
                node=child,
                ancestor_blocks=next_ancestor_blocks,
                max_chars=max_chars,
                prepared=prepared,
            )

    def _build_subtree_block(self, node: SectionSnapshot) -> str:
        blocks: list[str] = []
        own_block = self._build_section_block(node)
        if own_block:
            blocks.append(own_block)

        for child in node.children:
            child_block = self._build_subtree_block(child)
            if child_block:
                blocks.append(child_block)

        return self._join_context_blocks(blocks)

    def _build_section_block(self, node: SectionSnapshot) -> str:
        parts: list[str] = []
        heading = self._normalize_text(node.heading)
        content = self._normalize_text(node.content)
        if heading:
            parts.append(heading)
        if content:
            parts.append(content)
        return "\n\n".join(parts).strip()

    def _normalize_text(self, value: str | None) -> str:
        if value is None:
            return ""
        text_value = value.strip()
        if not text_value:
            return ""
        text_value = _PAGE_BREAK_RE.sub("\n", text_value)
        text_value = _STANDALONE_PAGE_NUMBER_RE.sub("", text_value)
        text_value = _BLANK_LINE_RE.sub("\n\n", text_value)
        return text_value.strip()

    def _join_context_blocks(self, blocks: Iterable[str]) -> str:
        return "\n\n".join(block.strip() for block in blocks if block and block.strip()).strip()

    def _split_text(self, text_value: str, *, max_chars: int) -> list[str]:
        normalized = text_value.strip()
        if not normalized:
            return []
        if len(normalized) <= max_chars:
            return [normalized]

        paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_length = 0

        for paragraph in paragraphs:
            paragraph_length = len(paragraph)
            separator = 2 if current else 0
            if current and current_length + separator + paragraph_length > max_chars:
                chunks.append("\n\n".join(current).strip())
                current = []
                current_length = 0

            if paragraph_length > max_chars:
                if current:
                    chunks.append("\n\n".join(current).strip())
                    current = []
                    current_length = 0
                chunks.extend(self._hard_split_text(paragraph, max_chars=max_chars))
                continue

            current.append(paragraph)
            current_length += paragraph_length + (2 if len(current) > 1 else 0)

        if current:
            chunks.append("\n\n".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    def _hard_split_text(self, text_value: str, *, max_chars: int) -> list[str]:
        chunks: list[str] = []
        cursor = 0
        while cursor < len(text_value):
            next_cursor = min(cursor + max_chars, len(text_value))
            chunks.append(text_value[cursor:next_cursor].strip())
            cursor = next_cursor
        return [chunk for chunk in chunks if chunk]

    def _section_path_sort_key(self, section_path: str | None) -> tuple[int, ...]:
        if not section_path:
            return (10**9,)
        parts = []
        for item in section_path.split("."):
            try:
                parts.append(int(item))
            except ValueError:
                parts.append(10**9)
        return tuple(parts or [10**9])

    async def _persist_chunk_rows(
        self,
        *,
        version_id: int,
        prepared_chunks: Sequence[PreparedChunk],
    ) -> list[Chunk]:
        abstracts = await self._build_chunk_abstracts(prepared_chunks)
        chunk_rows: list[Chunk] = []
        for prepared, abstract in zip(prepared_chunks, abstracts):
            chunk = Chunk(
                version_id=version_id,
                section_id=prepared.section_id,
                text=prepared.text,
                text_abstract=abstract,
                embedding=None,
            )
            self.db.add(chunk)
            chunk_rows.append(chunk)
        await self.db.flush()

        embeddings = await self._build_embeddings(abstracts)
        await self._persist_embeddings(chunk_rows, embeddings)
        return chunk_rows

    async def _build_chunk_abstracts(
        self,
        prepared_chunks: Sequence[PreparedChunk],
    ) -> list[str]:
        abstracts: list[str] = []
        for prepared in prepared_chunks:
            abstract = await asyncio.to_thread(
                self._summarize_chunk_text_sync,
                prepared.text,
            )
            abstracts.append(abstract)
        return abstracts

    def _summarize_chunk_text_sync(self, text_value: str) -> str:
        client = self._build_openai_client()
        user_prompt = build_chunk_abstract_user_prompt(text_value)
        try:
            response = client.responses.create(
                model=settings.OPENAI_MODEL_NAME.strip(),
                input=[
                    {"role": "system", "content": CHUNK_ABSTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_output_tokens=800,
            )
            summary = (response.output_text or "").strip()
            if summary:
                return summary
        except Exception:
            pass

        try:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL_NAME.strip(),
                messages=[
                    {"role": "system", "content": CHUNK_ABSTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=800,
            )
            message = response.choices[0].message.content if response.choices else None
            summary = (message or "").strip()
            if summary:
                return summary
        except Exception as exc:
            raise UnprocessableEntityException(
                f"Chunk abstract generation failed: {exc}"
            ) from exc

        raise UnprocessableEntityException("Chunk abstract generation returned empty output.")

    async def _build_embeddings(self, abstracts: Sequence[str]) -> list[list[float]]:
        if not abstracts:
            return []
        return await asyncio.to_thread(self._build_embeddings_sync, list(abstracts))

    def _build_embeddings_sync(self, abstracts: list[str]) -> list[list[float]]:
        client = self._build_openai_client()
        try:
            response = client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL_NAME.strip(),
                input=abstracts,
            )
        except Exception as exc:
            raise UnprocessableEntityException(
                f"Chunk embedding generation failed: {exc}"
            ) from exc

        embeddings: list[list[float]] = []
        for item in response.data:
            embedding = getattr(item, "embedding", None)
            if not embedding:
                raise UnprocessableEntityException(
                    "Chunk embedding generation returned empty embedding."
                )
            embeddings.append([float(value) for value in embedding])
        return embeddings

    async def _persist_embeddings(
        self,
        chunk_rows: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        if len(chunk_rows) != len(embeddings):
            raise UnprocessableEntityException(
                "Chunk embedding count mismatch during persistence."
            )

        update_stmt = text(
            """
            UPDATE chunks
            SET embedding = CAST(:embedding AS halfvec(3072))
            WHERE chunk_id = :chunk_id
            """
        )
        for chunk_row, embedding in zip(chunk_rows, embeddings):
            await self.db.execute(
                update_stmt,
                {
                    "chunk_id": int(chunk_row.chunk_id),
                    "embedding": self._format_halfvec_literal(embedding),
                },
            )
        await self.db.flush()

    def _format_halfvec_literal(self, embedding: Sequence[float]) -> str:
        return json.dumps(
            [float(value) for value in embedding],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def _build_openai_client(self) -> OpenAI:
        api_key = settings.OPENAI_API_KEY.strip()
        base_url = settings.OPENAI_API_URL.strip().rstrip("/")
        if not api_key:
            raise BadRequestException("OPENAI_API_KEY is required for chunk generation.")
        kwargs: dict[str, str] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAI(**kwargs)
