from sqlalchemy import BigInteger, ForeignKey, Identity, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Chunk(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("guideline_versions.version_id", ondelete="CASCADE"),
        nullable=False,
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("sections.section_id", ondelete="SET NULL"),
        nullable=True,
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    version: Mapped["GuidelineVersion"] = relationship(
        "GuidelineVersion", back_populates="chunks"
    )
    section: Mapped["Section | None"] = relationship(
        "Section", back_populates="chunks"
    )
    embedding: Mapped["ChunkEmbedding | None"] = relationship(
        "ChunkEmbedding", back_populates="chunk", uselist=False
    )

    def __repr__(self) -> str:
        return f"<Chunk id={self.chunk_id} section_id={self.section_id}>"
