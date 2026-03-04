from sqlalchemy import BigInteger, ForeignKey, Identity, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("guideline_versions.version_id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    version: Mapped["GuidelineVersion"] = relationship(
        "GuidelineVersion", back_populates="documents"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.document_id} version_id={self.version_id}>"
