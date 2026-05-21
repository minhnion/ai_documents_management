from sqlalchemy import BigInteger, ForeignKey, Identity, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Guideline(Base):
    __tablename__ = "guidelines"

    guideline_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    ten_benh: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)
    chuyen_khoa: Mapped[str | None] = mapped_column(Text, nullable=True)
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

    owner: Mapped["User"] = relationship(
        "User", foreign_keys=[owner_user_id], lazy="selectin"
    )
    created_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[created_by_user_id], lazy="selectin"
    )
    versions: Mapped[list["GuidelineVersion"]] = relationship(
        "GuidelineVersion", back_populates="guideline", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Guideline id={self.guideline_id} title={self.title!r}>"
