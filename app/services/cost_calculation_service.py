from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.cost import DocumentCostHistory, ModelPricing
from app.models.document import Document
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


class CostCalculationService:
    OCR_MODEL_TYPE = "ocr"
    VLM_MODEL_TYPE = "vlm"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_pricing(self) -> dict[str, dict[str, Decimal]]:
        rows = await self._get_pricing_rows()
        ocr = rows[self.OCR_MODEL_TYPE]
        vlm = rows[self.VLM_MODEL_TYPE]
        return {
            "ocr": {
                "input_char_price": ocr.input_char_price,
                "output_char_price": ocr.output_char_price,
                "page_price": ocr.page_price,
            },
            "vlm": {
                "input_token_price": vlm.input_token_price,
                "output_token_price": vlm.output_token_price,
            },
        }

    async def update_pricing(
        self,
        *,
        ocr_input_char_price: Decimal,
        ocr_output_char_price: Decimal,
        ocr_page_price: Decimal,
        vlm_input_token_price: Decimal,
        vlm_output_token_price: Decimal,
    ) -> dict[str, dict[str, Decimal]]:
        rows = await self._get_pricing_rows()
        ocr = rows[self.OCR_MODEL_TYPE]
        vlm = rows[self.VLM_MODEL_TYPE]
        ocr.input_char_price = ocr_input_char_price
        ocr.output_char_price = ocr_output_char_price
        ocr.page_price = ocr_page_price
        vlm.input_token_price = vlm_input_token_price
        vlm.output_token_price = vlm_output_token_price
        await self.db.flush()
        return await self.get_pricing()

    async def record_document_cost(
        self,
        *,
        document: Document,
        ocr_usage: OcrUsage | None = None,
        vlm_usage: VlmUsage | None = None,
    ) -> DocumentCostHistory:
        ocr_usage = ocr_usage or OcrUsage()
        vlm_usage = vlm_usage or VlmUsage()
        rows = await self._get_pricing_rows()
        ocr_pricing = rows[self.OCR_MODEL_TYPE]
        vlm_pricing = rows[self.VLM_MODEL_TYPE]

        ocr_cost = (
            Decimal(ocr_usage.input_chars) * ocr_pricing.input_char_price
            + Decimal(ocr_usage.output_chars) * ocr_pricing.output_char_price
            + Decimal(ocr_usage.pages) * ocr_pricing.page_price
        )
        vlm_cost = (
            Decimal(vlm_usage.input_tokens) * vlm_pricing.input_token_price
            + Decimal(vlm_usage.output_tokens) * vlm_pricing.output_token_price
        )

        account_id = int(document.owner_user_id) if document.owner_user_id else None
        group_id = await self._resolve_group_id(account_id)
        history = DocumentCostHistory(
            document_id=int(document.document_id),
            user_id=int(document.created_by_user_id) if document.created_by_user_id else account_id,
            account_id=account_id,
            group_id=group_id,
            ocr_input_chars=max(0, int(ocr_usage.input_chars)),
            ocr_output_chars=max(0, int(ocr_usage.output_chars)),
            ocr_pages=max(0, int(ocr_usage.pages)),
            ocr_cost=ocr_cost,
            vlm_input_tokens=max(0, int(vlm_usage.input_tokens)),
            vlm_output_tokens=max(0, int(vlm_usage.output_tokens)),
            vlm_cost=vlm_cost,
            total_cost=ocr_cost + vlm_cost,
        )
        self.db.add(history)
        await self.db.flush()
        return history

    async def get_statistics(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        account_id: int | None = None,
        group_id: int | None = None,
    ) -> dict[str, object]:
        filters = self._build_history_filters(
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            group_id=group_id,
        )
        sum_ocr = func.coalesce(func.sum(DocumentCostHistory.ocr_cost), 0)
        sum_vlm = func.coalesce(func.sum(DocumentCostHistory.vlm_cost), 0)
        sum_total = func.coalesce(func.sum(DocumentCostHistory.total_cost), 0)
        summary_row = (
            await self.db.execute(
                select(
                    sum_ocr.label("ocr_cost"),
                    sum_vlm.label("vlm_cost"),
                    sum_total.label("total_cost"),
                    func.count(func.distinct(DocumentCostHistory.document_id)).label("documents_processed"),
                ).where(*filters)
            )
        ).one()

        period_expr = func.to_char(DocumentCostHistory.created_at, "YYYY-MM-DD")
        ts_rows = (
            await self.db.execute(
                select(
                    period_expr.label("period"),
                    sum_ocr.label("ocr_cost"),
                    sum_vlm.label("vlm_cost"),
                    sum_total.label("total_cost"),
                )
                .where(*filters)
                .group_by(period_expr)
                .order_by(period_expr.asc())
            )
        ).all()

        return {
            "date_from": date_from,
            "date_to": date_to,
            "summary": {
                "ocr_cost": summary_row.ocr_cost,
                "vlm_cost": summary_row.vlm_cost,
                "total_cost": summary_row.total_cost,
                "documents_processed": int(summary_row.documents_processed),
            },
            "timeseries": [
                {
                    "period": row.period,
                    "ocr_cost": row.ocr_cost,
                    "vlm_cost": row.vlm_cost,
                    "total_cost": row.total_cost,
                }
                for row in ts_rows
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
    ) -> tuple[list[DocumentCostHistory], int]:
        filters = self._build_history_filters(
            date_from=date_from,
            date_to=date_to,
            account_id=account_id,
            group_id=group_id,
        )
        total = int(
            (
                await self.db.execute(
                    select(func.count()).select_from(DocumentCostHistory).where(*filters)
                )
            ).scalar_one()
        )
        stmt = (
            select(DocumentCostHistory)
            .options(
                selectinload(DocumentCostHistory.account),
                selectinload(DocumentCostHistory.group),
            )
            .where(*filters)
            .order_by(DocumentCostHistory.created_at.desc(), DocumentCostHistory.cost_history_id.desc())
            .offset((max(page, 1) - 1) * page_size)
            .limit(page_size)
        )
        return list((await self.db.execute(stmt)).scalars().all()), total

    async def get_filter_options(self) -> dict[str, list[User]]:
        users = list(
            (
                await self.db.execute(
                    select(User).order_by(User.role.asc(), User.full_name.asc().nullslast(), User.email.asc())
                )
            ).scalars().all()
        )
        group_ids = {int(user.parent_id) for user in users if user.parent_id is not None}
        groups = [user for user in users if int(user.user_id) in group_ids]
        return {"accounts": users, "groups": groups}

    async def _get_pricing_rows(self) -> dict[str, ModelPricing]:
        existing = {
            row.model_type: row
            for row in (
                await self.db.execute(
                    select(ModelPricing).where(
                        ModelPricing.model_type.in_([self.OCR_MODEL_TYPE, self.VLM_MODEL_TYPE])
                    )
                )
            ).scalars().all()
        }
        for model_type in (self.OCR_MODEL_TYPE, self.VLM_MODEL_TYPE):
            if model_type not in existing:
                row = ModelPricing(model_type=model_type)
                self.db.add(row)
                existing[model_type] = row
        await self.db.flush()
        return existing

    async def _resolve_group_id(self, account_id: int | None) -> int | None:
        if account_id is None:
            return None
        user = await self.db.get(User, account_id)
        if user is None:
            return account_id
        parent_id = user.parent_id
        current = user
        while parent_id is not None:
            parent = await self.db.get(User, int(parent_id))
            if parent is None or parent.role == "admin":
                break
            current = parent
            parent_id = parent.parent_id
        return int(current.user_id)

    @staticmethod
    def _build_history_filters(
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        account_id: int | None = None,
        group_id: int | None = None,
    ) -> list[object]:
        filters: list[object] = []
        if date_from is not None:
            filters.append(DocumentCostHistory.created_at >= date_from)
        if date_to is not None:
            filters.append(DocumentCostHistory.created_at < (date_to + timedelta(days=1)))
        if account_id is not None:
            filters.append(DocumentCostHistory.account_id == account_id)
        if group_id is not None:
            filters.append(DocumentCostHistory.group_id == group_id)
        return filters
