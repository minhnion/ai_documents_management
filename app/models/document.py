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
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_mode_used: Mapped[str | None] = mapped_column(String(30), nullable=True)

    version: Mapped["GuidelineVersion"] = relationship(
        "GuidelineVersion", back_populates="documents"
    )
    owner: Mapped["User"] = relationship(
        "User", foreign_keys=[owner_user_id], lazy="selectin"
    )
    created_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[created_by_user_id], lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.document_id} version_id={self.version_id}>"
