from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Identity, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class GuidelineVersion(Base):
    __tablename__ = "guideline_versions"

    version_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    guideline_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guidelines.guideline_id", ondelete="CASCADE"), nullable=False
    )
    version_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Relationships
    guideline: Mapped["Guideline"] = relationship(
        "Guideline", back_populates="versions"
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="version", lazy="select"
    )
    sections: Mapped[list["Section"]] = relationship(
        "Section", back_populates="version", lazy="select"
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        "Chunk", back_populates="version", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<GuidelineVersion id={self.version_id} "
            f"label={self.version_label!r} status={self.status!r}>"
        )
