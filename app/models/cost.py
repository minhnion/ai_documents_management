from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ModelPricing(Base):
    __tablename__ = "model_pricing"

    pricing_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    model_type: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    input_char_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    output_char_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    page_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    input_token_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    output_token_price: Mapped[Decimal] = mapped_column(
        Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0")
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


class DocumentCostHistory(Base):
    __tablename__ = "document_cost_history"

    cost_history_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    account_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    group_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    ocr_input_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    ocr_output_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    ocr_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    ocr_cost: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"), server_default=text("0"))
    vlm_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    vlm_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    vlm_cost: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"), server_default=text("0"))
    total_cost: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False, default=Decimal("0"), server_default=text("0"))

    document: Mapped["Document"] = relationship("Document", lazy="selectin")
    user: Mapped["User | None"] = relationship("User", foreign_keys=[user_id], lazy="selectin")
    account: Mapped["User | None"] = relationship("User", foreign_keys=[account_id], lazy="selectin")
    group: Mapped["User | None"] = relationship("User", foreign_keys=[group_id], lazy="selectin")
