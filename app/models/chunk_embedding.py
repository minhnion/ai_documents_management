from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chunks.chunk_id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=True
    )

    # Relationships
    chunk: Mapped["Chunk"] = relationship("Chunk", back_populates="embedding")

    def __repr__(self) -> str:
        return (
            f"<ChunkEmbedding chunk_id={self.chunk_id} "
            f"model={self.embedding_model!r}>"
        )
