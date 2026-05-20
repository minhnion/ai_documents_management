from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('admin', 'health_department', 'hospital', 'doctor')",
            name="ck_users_role",
        ),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="health_department",
        server_default=text("'health_department'"),
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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

    parent: Mapped["User | None"] = relationship(
        "User",
        remote_side=[user_id],
        foreign_keys=[parent_id],
        back_populates="children",
        lazy="selectin",
    )
    children: Mapped[list["User"]] = relationship(
        "User",
        foreign_keys=[parent_id],
        back_populates="parent",
        lazy="select",
    )
    created_by: Mapped["User | None"] = relationship(
        "User",
        remote_side=[user_id],
        foreign_keys=[created_by_user_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<User id={self.user_id} email={self.email!r} "
            f"role={self.role!r}>"
        )
