from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Section(Base):
    __tablename__ = "sections"

    section_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("guideline_versions.version_id", ondelete="CASCADE"),
        nullable=False,
    )
    heading: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.section_id"), nullable=True
    )
    level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    order_index: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_suspect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    intro_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    heading_bbox: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_bboxes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    landing_chunks: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    version: Mapped["GuidelineVersion"] = relationship(
        "GuidelineVersion", back_populates="sections"
    )
    parent: Mapped["Section | None"] = relationship(
        "Section", remote_side="Section.section_id", back_populates="children"
    )
    children: Mapped[list["Section"]] = relationship(
        "Section", back_populates="parent", lazy="select"
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        "Chunk", back_populates="section", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<Section id={self.section_id} heading={self.heading!r} "
            f"level={self.level}>"
        )
