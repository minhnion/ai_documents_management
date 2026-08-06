from datetime import datetime
from decimal import Decimal

from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Integer, JSON, Numeric, String, func, text
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


class CostModelProfile(Base):
    """The three billable model slots exposed by the product.

    Provider/model identifiers intentionally stay out of this public contract. The
    backend may associate them with a slot later when a self-hosted model is used.
    """

    __tablename__ = "cost_model_profiles"

    model_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    model_type: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    billing_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="usage", server_default=text("'usage'"))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CostTrackingSetting(Base):
    __tablename__ = "cost_tracking_settings"

    setting_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tracking_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CostRateCard(Base):
    """Versioned price for one normalized billing unit."""

    __tablename__ = "cost_rate_cards"

    rate_id: Mapped[int] = mapped_column("rate_card_id", BigInteger, Identity(), primary_key=True)
    model_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    unit: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    unit_divisor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))
    price_usd: Mapped[Decimal] = mapped_column(Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CostUsageEvent(Base):
    """Immutable usage ledger row for one actual model request or batch."""

    __tablename__ = "cost_usage_events"

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column("event_key", String(255), nullable=False, unique=True, index=True)
    document_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("documents.document_id", ondelete="SET NULL"), nullable=True, index=True)
    version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("guideline_versions.version_id", ondelete="SET NULL"), nullable=True, index=True)
    job_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_job_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    model_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="succeeded", server_default=text("'succeeded'"), index=True)
    usage_source: Mapped[str] = mapped_column(String(30), nullable=False, default="estimated", server_default=text("'estimated'"))
    request_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    page_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    request_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    output_chars: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    compute_seconds: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False, default=Decimal("0"), server_default=text("0"))
    rate_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, server_default=text("'{}'::json"))
    pricing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="priced", server_default=text("'priced'"), index=True)
    missing_units: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, server_default=text("'[]'::json"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(20, 12), nullable=False, default=Decimal("0"), server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    document: Mapped["Document | None"] = relationship("Document", lazy="selectin")
    actor: Mapped["User | None"] = relationship("User", foreign_keys=[actor_user_id], lazy="selectin")
    account: Mapped["User | None"] = relationship("User", foreign_keys=[account_id], lazy="selectin")
    group: Mapped["User | None"] = relationship("User", foreign_keys=[group_id], lazy="selectin")
