from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    organization_id: Mapped[int] = mapped_column(
        BigInteger, Identity(), primary_key=True
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="organization", lazy="select"
    )
    guidelines: Mapped[list["Guideline"]] = relationship(
        "Guideline", back_populates="organization", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.organization_id} slug={self.slug!r}>"
