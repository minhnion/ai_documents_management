from sqlalchemy import BigInteger, ForeignKey, Identity, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.sql_types import HALFVEC
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
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("sections.section_id", ondelete="SET NULL"),
        nullable=True,
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[object | None] = mapped_column(HALFVEC(3072), nullable=True)

    version: Mapped["GuidelineVersion"] = relationship(
        "GuidelineVersion", back_populates="chunks"
    )
    section: Mapped["Section | None"] = relationship(
        "Section", back_populates="chunks"
    )
    owner: Mapped["User"] = relationship("User", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Chunk id={self.chunk_id} section_id={self.section_id}>"
