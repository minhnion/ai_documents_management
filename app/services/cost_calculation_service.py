"""Pricing configuration and immutable usage-ledger accounting.

The service deliberately calculates from normalized units.  A provider can be
replaced without changing the reporting contract: OCR is billed by pages and
requests, LLM by input/cache/output tokens, and Embedding by input tokens and
requests.  Each event stores the exact rate snapshot used for its calculation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.cost import CostModelProfile, CostRateCard, CostTrackingSetting, CostUsageEvent
from app.models.user import User


@dataclass(frozen=True, slots=True)
class OcrUsage:
    input_chars: int = 0
    output_chars: int = 0
    pages: int = 0


@dataclass(frozen=True, slots=True)
class VlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class UsageEventInput:
    idempotency_key: str
    model_type: str
    operation: str
    document_id: int | None = None
    version_id: int | None = None
    job_type: str | None = None
    source_job_id: int | str | None = None
    actor_user_id: int | None = None
    account_id: int | None = None
    group_id: int | None = None
    occurred_at: datetime | None = None
    status: str = "succeeded"
    usage_source: str = "estimated"
    request_sequence: int = 1
    attempt_number: int = 1
    page_count: int = 0
    request_count: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    output_chars: int = 0
    compute_seconds: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class CalculatedCost:
    cost_usd: Decimal
    pricing_status: str
    missing_units: tuple[str, ...] = field(default_factory=tuple)
    rate_snapshot: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class CostCalculationService:
    OCR_MODEL_TYPE = "ocr"
    LLM_MODEL_TYPE = "llm"
    EMBEDDING_MODEL_TYPE = "embedding"
    MODEL_TYPES = (OCR_MODEL_TYPE, LLM_MODEL_TYPE, EMBEDDING_MODEL_TYPE)

    UNIT_SPECS: dict[str, tuple[tuple[str, str, int], ...]] = {
        OCR_MODEL_TYPE: (
            ("page", "Trang xử lý", 1000),
            ("request", "Lần gọi", 1000),
            ("output_char", "Ký tự đầu ra", 1_000_000),
        ),
        LLM_MODEL_TYPE: (
            ("input_token", "Token đầu vào", 1_000_000),
            ("cached_input_token", "Token đầu vào cache", 1_000_000),
            ("output_token", "Token đầu ra", 1_000_000),
            ("request", "Lần gọi", 1000),
        ),
        EMBEDDING_MODEL_TYPE: (
            ("input_token", "Token đầu vào", 1_000_000),
            ("request", "Lần gọi", 1000),
        ),
    }
    DISPLAY_NAMES = {"ocr": "OCR", "llm": "LLM", "embedding": "Embedding"}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_pricing(self) -> dict[str, Any]:
        await self._ensure_catalog()
        now = datetime.now(timezone.utc)
        profiles = {
            row.model_type: row
            for row in (await self.db.execute(select(CostModelProfile).where(CostModelProfile.is_active.is_(True)))).scalars().all()
        }
        rates = await self._get_active_rates(as_of=now)
        models = []
        for model_type in self.MODEL_TYPES:
            profile = profiles.get(model_type)
            model_rates = []
            for unit, label, divisor in self.UNIT_SPECS[model_type]:
                row = rates.get((model_type, unit))
                model_rates.append({
                    "unit": unit,
                    "label": label,
                    "unit_divisor": int(row.unit_divisor if row else divisor),
                    "price_usd": row.price_usd if row else Decimal("0"),
                })
            models.append({
                "model_type": model_type,
                "display_name": profile.display_name if profile else self.DISPLAY_NAMES[model_type],
                "billing_mode": profile.billing_mode if profile else "usage",
                "rates": model_rates,
            })
        return {"models": models}

    async def update_pricing(self, *, models: list[Mapping[str, Any]]) -> dict[str, Any]:
        await self._ensure_catalog()
        by_type = {str(item["model_type"]): item for item in models}
        if set(by_type) != set(self.MODEL_TYPES):
            raise ValueError("Pricing must include exactly OCR, LLM and Embedding.")

        now = datetime.now(timezone.utc)
        for model_type in self.MODEL_TYPES:
            expected_units = {unit for unit, _, _ in self.UNIT_SPECS[model_type]}
            submitted = {str(rate["unit"]): rate for rate in by_type[model_type].get("rates", [])}
            if set(submitted) != expected_units:
                raise ValueError(f"Invalid pricing units for {model_type}.")
            await self.db.execute(
                update(CostRateCard)
                .where(
                    CostRateCard.model_type == model_type,
                    CostRateCard.effective_to.is_(None),
                )
                .values(effective_to=now)
            )
            for unit, _, default_divisor in self.UNIT_SPECS[model_type]:
                rate = submitted[unit]
                divisor = int(rate.get("unit_divisor") or default_divisor)
                price = Decimal(str(rate.get("price_usd") or "0"))
                if divisor <= 0 or price < 0:
                    raise ValueError("Unit divisor must be positive and price cannot be negative.")
                self.db.add(CostRateCard(
                    model_type=model_type,
                    unit=unit,
                    unit_divisor=divisor,
                    price_usd=price,
                    effective_from=now,
                ))
        await self.db.flush()
        return await self.get_pricing()

    async def record_usage_event(self, usage: UsageEventInput) -> CostUsageEvent:
        if usage.model_type not in self.MODEL_TYPES:
            raise ValueError(f"Unsupported model type: {usage.model_type}")
        existing = (
            await self.db.execute(
                select(CostUsageEvent).where(CostUsageEvent.idempotency_key == usage.idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        occurred_at = usage.occurred_at or datetime.now(timezone.utc)
        rates = await self._get_active_rates(as_of=occurred_at)
        quantities = self.quantities_for_usage(usage)
        calculated = self.calculate_cost(usage.model_type, quantities, rates)
        group_id = usage.group_id if usage.group_id is not None else await self._resolve_group_id(usage.account_id)
        source_job_id: int | None = None
        if usage.source_job_id is not None:
            try:
                source_job_id = int(usage.source_job_id)
            except (TypeError, ValueError):
                source_job_id = None

        event = CostUsageEvent(
            idempotency_key=usage.idempotency_key,
            document_id=usage.document_id,
            version_id=usage.version_id,
            job_type=usage.job_type,
            source_job_id=source_job_id,
            actor_user_id=usage.actor_user_id,
            account_id=usage.account_id,
            group_id=group_id,
            occurred_at=occurred_at,
            model_type=usage.model_type,
            operation=usage.operation,
            status=usage.status,
            usage_source=usage.usage_source,
            request_sequence=max(1, int(usage.request_sequence)),
            attempt_number=max(1, int(usage.attempt_number)),
            page_count=max(0, int(usage.page_count)),
            request_count=max(0, int(usage.request_count)),
            input_tokens=max(0, int(usage.input_tokens)),
            cached_input_tokens=max(0, min(int(usage.input_tokens), int(usage.cached_input_tokens))),
            output_tokens=max(0, int(usage.output_tokens)),
            output_chars=max(0, int(usage.output_chars)),
            compute_seconds=max(Decimal("0"), Decimal(str(usage.compute_seconds))),
            rate_snapshot=list(calculated.rate_snapshot),
            pricing_status=calculated.pricing_status,
            missing_units=list(calculated.missing_units),
            cost_usd=calculated.cost_usd,
        )
        self.db.add(event)
        await self.db.flush()
        return event

    @classmethod
    async def record_usage_isolated(cls, usage: UsageEventInput) -> None:
        """Persist accounting independently from a processing transaction.

        A failed ingestion can roll back its document transaction; the provider
        request has still happened, so the ledger must survive that rollback.
        """
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            service = cls(session)
            await service.record_usage_event(usage)
            await session.commit()

    @classmethod
    def quantities_for_usage(cls, usage: UsageEventInput) -> dict[str, int | Decimal]:
        input_tokens = max(0, int(usage.input_tokens))
        cached_tokens = max(0, min(input_tokens, int(usage.cached_input_tokens)))
        quantities: dict[str, int | Decimal] = {
            "page": max(0, int(usage.page_count)),
            "request": max(0, int(usage.request_count)),
            "input_token": input_tokens - cached_tokens,
            "cached_input_token": cached_tokens,
            "output_token": max(0, int(usage.output_tokens)),
            "output_char": max(0, int(usage.output_chars)),
            "compute_second": max(Decimal("0"), Decimal(str(usage.compute_seconds))),
        }
        return quantities

    @classmethod
    def calculate_cost(
        cls,
        model_type: str,
        quantities: Mapping[str, int | Decimal],
        rates: Mapping[tuple[str, str], Any],
    ) -> CalculatedCost:
        total = Decimal("0")
        missing: list[str] = []
        snapshot: list[dict[str, Any]] = []
        for unit, _, default_divisor in cls.UNIT_SPECS.get(model_type, ()):
            quantity = Decimal(str(quantities.get(unit, 0) or 0))
            if quantity <= 0:
                continue
            rate = rates.get((model_type, unit))
            if rate is None:
                missing.append(unit)
                continue
            divisor = int(rate.unit_divisor or default_divisor)
            price = Decimal(str(rate.price_usd or 0))
            total += quantity * price / Decimal(divisor)
            snapshot.append({
                "unit": unit,
                "quantity": str(quantity),
                "unit_divisor": divisor,
                "price_usd": str(price),
            })
        if missing and total == 0:
            status = "unpriced"
        elif missing:
            status = "partial"
        else:
            status = "priced"
        return CalculatedCost(total.quantize(Decimal("0.000000000001")), status, tuple(missing), tuple(snapshot))

    async def get_statistics(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        account_id: int | None = None,
        group_id: int | None = None,
        model_type: str | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        tracking_started_at = await self._get_tracking_started_at()
        filters = self._build_event_filters(tracking_started_at, date_from, date_to, account_id, group_id, model_type, operation)
        total_cost = func.coalesce(func.sum(CostUsageEvent.cost_usd), 0)
        ocr_cost = func.coalesce(func.sum(case((CostUsageEvent.model_type == "ocr", CostUsageEvent.cost_usd), else_=0)), 0)
        llm_cost = func.coalesce(func.sum(case((CostUsageEvent.model_type == "llm", CostUsageEvent.cost_usd), else_=0)), 0)
        embedding_cost = func.coalesce(func.sum(case((CostUsageEvent.model_type == "embedding", CostUsageEvent.cost_usd), else_=0)), 0)
        row = (
            await self.db.execute(
                select(
                    total_cost.label("total_cost"),
                    ocr_cost.label("ocr_cost"),
                    llm_cost.label("llm_cost"),
                    embedding_cost.label("embedding_cost"),
                    func.count(func.distinct(CostUsageEvent.document_id)).label("documents_processed"),
                    func.count(CostUsageEvent.event_id).label("usage_events"),
                    func.coalesce(func.sum(CostUsageEvent.page_count), 0).label("ocr_pages"),
                    func.coalesce(func.sum(CostUsageEvent.input_tokens * case((CostUsageEvent.model_type == "llm", 1), else_=0)), 0).label("llm_input_tokens"),
                    func.coalesce(func.sum(CostUsageEvent.cached_input_tokens * case((CostUsageEvent.model_type == "llm", 1), else_=0)), 0).label("llm_cached_input_tokens"),
                    func.coalesce(func.sum(CostUsageEvent.output_tokens * case((CostUsageEvent.model_type == "llm", 1), else_=0)), 0).label("llm_output_tokens"),
                    func.coalesce(func.sum(CostUsageEvent.input_tokens * case((CostUsageEvent.model_type == "embedding", 1), else_=0)), 0).label("embedding_input_tokens"),
                ).where(*filters)
            )
        ).one()
        period = func.to_char(CostUsageEvent.occurred_at, "YYYY-MM-DD")
        points = (
            await self.db.execute(
                select(period.label("period"), ocr_cost.label("ocr_cost"), llm_cost.label("llm_cost"), embedding_cost.label("embedding_cost"), total_cost.label("total_cost"))
                .where(*filters).group_by(period).order_by(period.asc())
            )
        ).all()
        return {
            "date_from": date_from,
            "date_to": date_to,
            "summary": {
                "total_cost": row.total_cost,
                "ocr_cost": row.ocr_cost,
                "llm_cost": row.llm_cost,
                "embedding_cost": row.embedding_cost,
                "documents_processed": int(row.documents_processed or 0),
                "usage_events": int(row.usage_events or 0),
                "ocr_pages": int(row.ocr_pages or 0),
                "llm_input_tokens": int(row.llm_input_tokens or 0),
                "llm_cached_input_tokens": int(row.llm_cached_input_tokens or 0),
                "llm_output_tokens": int(row.llm_output_tokens or 0),
                "embedding_input_tokens": int(row.embedding_input_tokens or 0),
            },
            "timeseries": [
                {"period": item.period, "ocr_cost": item.ocr_cost, "llm_cost": item.llm_cost, "embedding_cost": item.embedding_cost, "total_cost": item.total_cost}
                for item in points
            ],
            "filters": await self.get_filter_options(),
        }

    async def list_history(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        date_from: date | None = None,
        date_to: date | None = None,
        account_id: int | None = None,
        group_id: int | None = None,
        model_type: str | None = None,
        operation: str | None = None,
    ) -> tuple[list[CostUsageEvent], int]:
        tracking_started_at = await self._get_tracking_started_at()
        filters = self._build_event_filters(tracking_started_at, date_from, date_to, account_id, group_id, model_type, operation)
        total = int((await self.db.execute(select(func.count()).select_from(CostUsageEvent).where(*filters))).scalar_one() or 0)
        stmt = (
            select(CostUsageEvent)
            .options(selectinload(CostUsageEvent.account), selectinload(CostUsageEvent.group))
            .where(*filters)
            .order_by(CostUsageEvent.occurred_at.desc(), CostUsageEvent.event_id.desc())
            .offset((max(page, 1) - 1) * page_size).limit(page_size)
        )
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_filter_options(self) -> dict[str, list[User]]:
        users = list((await self.db.execute(select(User).order_by(User.role.asc(), User.full_name.asc().nullslast(), User.email.asc()))).scalars().all())
        group_ids = {int(user.parent_id) for user in users if user.parent_id is not None}
        return {"accounts": users, "groups": [user for user in users if int(user.user_id) in group_ids]}

    async def _ensure_catalog(self) -> None:
        existing = {row.model_type for row in (await self.db.execute(select(CostModelProfile))).scalars().all()}
        for model_type in self.MODEL_TYPES:
            if model_type not in existing:
                self.db.add(CostModelProfile(model_type=model_type, display_name=self.DISPLAY_NAMES[model_type], billing_mode="usage"))
        await self.db.flush()
        active_units = {(row.model_type, row.unit) for row in (await self.db.execute(select(CostRateCard).where(CostRateCard.effective_to.is_(None)))).scalars().all()}
        now = datetime.now(timezone.utc)
        for model_type, specs in self.UNIT_SPECS.items():
            for unit, _, divisor in specs:
                if (model_type, unit) not in active_units:
                    self.db.add(CostRateCard(model_type=model_type, unit=unit, unit_divisor=divisor, price_usd=Decimal("0"), effective_from=now))
        await self.db.flush()

    async def _get_active_rates(self, *, as_of: datetime) -> dict[tuple[str, str], CostRateCard]:
        rows = list((await self.db.execute(select(CostRateCard).where(CostRateCard.effective_from <= as_of, (CostRateCard.effective_to.is_(None) | (CostRateCard.effective_to > as_of))))).scalars().all())
        selected: dict[tuple[str, str], CostRateCard] = {}
        for row in sorted(rows, key=lambda item: item.effective_from):
            selected[(row.model_type, row.unit)] = row
        return selected

    async def _resolve_group_id(self, account_id: int | None) -> int | None:
        if account_id is None:
            return None
        current = await self.db.get(User, account_id)
        if current is None:
            return account_id
        while current.parent_id is not None:
            parent = await self.db.get(User, int(current.parent_id))
            if parent is None or parent.role == "admin":
                break
            current = parent
        return int(current.user_id)

    async def _get_tracking_started_at(self) -> datetime | None:
        return (
            await self.db.execute(
                select(CostTrackingSetting.tracking_started_at).where(CostTrackingSetting.setting_id == 1)
            )
        ).scalar_one_or_none()

    @staticmethod
    def _build_event_filters(tracking_started_at, date_from, date_to, account_id, group_id, model_type, operation) -> list[Any]:
        filters: list[Any] = []
        if tracking_started_at is not None:
            filters.append(CostUsageEvent.occurred_at >= tracking_started_at)
        if date_from is not None:
            filters.append(CostUsageEvent.occurred_at >= date_from)
        if date_to is not None:
            filters.append(CostUsageEvent.occurred_at < date_to + timedelta(days=1))
        if account_id is not None:
            filters.append(CostUsageEvent.account_id == account_id)
        if group_id is not None:
            filters.append(CostUsageEvent.group_id == group_id)
        if model_type is not None:
            filters.append(CostUsageEvent.model_type == model_type)
        if operation is not None:
            filters.append(CostUsageEvent.operation == operation)
        return filters
