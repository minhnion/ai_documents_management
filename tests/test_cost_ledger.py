from decimal import Decimal
from types import SimpleNamespace

from app.services.cost_calculation_service import CostCalculationService, UsageEventInput
from app.services.pipeline.landingai_ocr_service import billable_page_count
from app.services.pipeline.toc_builder_service import TocBuilderService


def rates(model_type: str, **prices):
    return {
        (model_type, unit): SimpleNamespace(unit_divisor=divisor, price_usd=Decimal(str(prices.get(unit, 0))))
        for unit, _, divisor in CostCalculationService.UNIT_SPECS[model_type]
    }


def test_ocr_formula_uses_pages_requests_and_output_chars():
    usage = UsageEventInput(
        idempotency_key="ocr-1",
        model_type="ocr",
        operation="ocr_parse",
        page_count=1250,
        request_count=2,
        output_chars=2_000_000,
    )

    result = CostCalculationService.calculate_cost(
        "ocr",
        CostCalculationService.quantities_for_usage(usage),
        rates("ocr", page=10, request=5, output_char=1.25),
    )

    assert result.cost_usd == Decimal("15.010000000000")
    assert result.pricing_status == "priced"


def test_llm_formula_separates_cached_input_tokens():
    usage = UsageEventInput(
        idempotency_key="llm-1",
        model_type="llm",
        operation="toc_generation",
        input_tokens=1_000_000,
        cached_input_tokens=200_000,
        output_tokens=100_000,
    )

    result = CostCalculationService.calculate_cost(
        "llm",
        CostCalculationService.quantities_for_usage(usage),
        rates("llm", input_token=2, cached_input_token=0.2, output_token=10),
    )

    assert result.cost_usd == Decimal("2.640000000000")
    assert CostCalculationService.quantities_for_usage(usage)["input_token"] == 800_000
    assert CostCalculationService.quantities_for_usage(usage)["cached_input_token"] == 200_000


def test_embedding_formula_uses_input_tokens_only():
    usage = UsageEventInput(
        idempotency_key="embedding-1",
        model_type="embedding",
        operation="chunk_embedding",
        input_tokens=2_000_000,
        request_count=1,
    )
    result = CostCalculationService.calculate_cost(
        "embedding",
        CostCalculationService.quantities_for_usage(usage),
        rates("embedding", input_token=0.1, request=3),
    )
    assert result.cost_usd == Decimal("0.203000000000")


def test_missing_price_is_visible_as_unpriced_instead_of_silent():
    usage = UsageEventInput(
        idempotency_key="missing-rate",
        model_type="ocr",
        operation="ocr_parse",
        page_count=10,
    )
    result = CostCalculationService.calculate_cost(
        "ocr",
        CostCalculationService.quantities_for_usage(usage),
        {},
    )
    assert result.cost_usd == Decimal("0E-12")
    assert result.pricing_status == "unpriced"
    assert result.missing_units == ("page",)


def test_split_ocr_counts_overlap_pages_as_billable():
    assert billable_page_count(50) == 50
    assert billable_page_count(51) == 54
    assert billable_page_count(100) == 106


def test_toc_usage_extracts_cached_tokens_from_response_details():
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=250,
        input_tokens_details=SimpleNamespace(cached_tokens=400),
    )
    response = SimpleNamespace(usage=usage)
    aggregate = {"input_tokens": 0, "output_tokens": 0}
    events: list[dict[str, int]] = []

    TocBuilderService._accumulate_response_usage(response, aggregate, events)

    assert aggregate == {"input_tokens": 1000, "output_tokens": 250}
    assert events == [{"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 250}]
