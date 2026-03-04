from sqlalchemy import BigInteger, ForeignKey, Identity, Integer, SmallInteger, Text
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
    section_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sections.section_id"), nullable=True
    )
    level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    order_index: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

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
