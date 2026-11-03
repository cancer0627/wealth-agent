from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from wealth_agent.capabilities.analysis.contract import AnalysisError, ProductReportRequest
from wealth_agent.capabilities.analysis.implementation import DeterministicAnalysis
from wealth_agent.capabilities.analysis.implementation.modified_dietz import calculate_return
from wealth_agent.capabilities.portfolio.contract import (
    CashFlow,
    ProductSnapshot,
    Valuation,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _valuation(day: int, amount: str) -> Valuation:
    return Valuation(
        date=date(2026, 1, day),
        amount=Decimal(amount),
        source_text="test",
        updated_at=NOW,
    )


def test_modified_dietz_preserves_cash_flow_semantics() -> None:
    result = calculate_return(
        _valuation(1, "100"),
        _valuation(11, "95"),
        [
            CashFlow(
                flow_id="f1",
                date=date(2026, 1, 6),
                type="transfer_out",
                amount=Decimal("10"),
                source_text="test",
                created_at=NOW,
            )
        ],
    )
    assert result.net_cash_flow == Decimal("-10.00")
    assert result.net_profit == Decimal("5.00")
    assert result.weighted_capital == Decimal("95.000")


def test_report_is_bound_to_ledger_and_product_revisions() -> None:
    product = ProductSnapshot(
        product_id="p1",
        name="测试产品",
        revision="product-r1",
        updated_at=NOW,
        valuations=[_valuation(1, "100"), _valuation(11, "110")],
    )
    report = DeterministicAnalysis().report(
        ProductReportRequest(
            product=product,
            ledger_revision="ledger-r1",
            generated_at=NOW,
        )
    )
    assert report.ledger_revision == "ledger-r1"
    assert report.product_revision == "product-r1"
    assert report.overall is not None
    assert report.overall.modified_dietz_return == Decimal("0.1")


def test_one_valuation_returns_insufficient_data_without_model() -> None:
    product = ProductSnapshot(
        product_id="p1",
        name="测试产品",
        revision="product-r1",
        updated_at=NOW,
        valuations=[_valuation(1, "100")],
    )
    report = DeterministicAnalysis().report(
        ProductReportRequest(
            product=product,
            ledger_revision="ledger-r1",
            generated_at=NOW,
        )
    )
    assert report.status == "insufficient_data"
    assert report.overall is None
    assert any("至少需要两个估值日" in warning for warning in report.warnings)


def test_invalid_report_interval_returns_structured_error() -> None:
    product = ProductSnapshot(
        product_id="p1",
        name="测试产品",
        revision="product-r1",
        updated_at=NOW,
        valuations=[_valuation(1, "100"), _valuation(1, "110")],
    )

    with pytest.raises(AnalysisError) as captured:
        DeterministicAnalysis().report(
            ProductReportRequest(
                product=product,
                ledger_revision="ledger-r1",
                generated_at=NOW,
            )
        )

    assert captured.value.detail.code == "analysis_error"
    assert captured.value.detail.details == {
        "product_id": "p1",
        "ledger_revision": "ledger-r1",
    }
