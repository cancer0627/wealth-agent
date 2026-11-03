from datetime import UTC, date, datetime
from decimal import Decimal

from wealth_agent.capabilities.analysis.contract import AnalyzeProductRequest
from wealth_agent.capabilities.analysis.implementation import ModelAssistedAnalysis
from wealth_agent.capabilities.portfolio.contract import (
    CashFlow,
    ProductProfile,
    ProductSnapshot,
    RiskLevel,
    Valuation,
)
from wealth_agent.shared.model import (
    ModelGatewayError,
    ModelRequest,
    ModelResponse,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _product() -> ProductSnapshot:
    return ProductSnapshot(
        product_id="p1",
        name="测试产品",
        revision="product-r1",
        updated_at=NOW,
        profile=ProductProfile(
            product_type="固定收益类净值型",
            risk_level=RiskLevel.R3,
            performance_benchmark="2.85%",
        ),
        valuations=[
            Valuation(
                date=date(2026, 1, 1),
                amount=Decimal("100"),
                source_text="x",
                updated_at=NOW,
            ),
            Valuation(
                date=date(2026, 1, 11),
                amount=Decimal("110"),
                source_text="x",
                updated_at=NOW,
            ),
        ],
    )


class SuccessGateway:
    def complete(self, request: ModelRequest) -> ModelResponse:
        assert "确定性报表" in request.messages[0].content  # type: ignore[operator]
        return ModelResponse(
            content=(
                '{"direction":"hold","summary":"保持观察",'
                '"rationale":"仅有有限历史数据","limitations":["不含外部行情"]}'
            ),
            model=request.model,
        )


class FailedGateway:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise ModelGatewayError("模型暂不可用", code="network_error", retryable=True)


class InvalidOutputGateway:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content='{"direction":"unknown"}', model=request.model)


class ScalarLimitationsGateway:
    def complete(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=(
                '{"direction":"insufficient_data",'
                '"summary":"短期数据仅供参考",'
                '"rationale":"统计区间较短，无法判断长期表现",'
                '"limitations":"仅依据确定性报表"}'
            ),
            model=request.model,
        )


class UnexpectedGateway:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise AssertionError("数据不足时不应调用模型")


def test_analysis_owns_prompt_and_uses_shared_gateway() -> None:
    result = ModelAssistedAnalysis(SuccessGateway(), model="test-model").analyze(
        AnalyzeProductRequest(
            product=_product(), ledger_revision="ledger-r1", generated_at=NOW
        )
    )
    assert result.report.overall is not None
    assert result.suggestion is not None
    assert result.suggestion.direction == "hold"


def test_model_failure_preserves_deterministic_report() -> None:
    result = ModelAssistedAnalysis(FailedGateway(), model="test-model").analyze(
        AnalyzeProductRequest(
            product=_product(), ledger_revision="ledger-r1", generated_at=NOW
        )
    )
    assert result.report.overall is not None
    assert result.suggestion is None
    assert result.model_error is not None
    assert result.model_error.code == "network_error"
    assert result.model_error.message == "模型暂不可用"
    assert result.model_error.retryable is True
    assert result.model_error.details == {"model": "test-model"}


def test_incomplete_data_returns_insufficient_data_suggestion_without_model() -> None:
    product = _product().model_copy(
        update={"profile": ProductProfile(product_type="固定收益类净值型")}
    )
    result = ModelAssistedAnalysis(UnexpectedGateway(), model="test-model").analyze(
        AnalyzeProductRequest(
            product=product, ledger_revision="ledger-r1", generated_at=NOW
        )
    )

    assert result.suggestion is not None
    assert result.suggestion.direction == "insufficient_data"
    assert "风险等级" in result.suggestion.rationale
    assert result.model_error is None


def test_invalid_return_denominator_returns_insufficient_data_suggestion() -> None:
    product = _product().model_copy(
        update={
            "cash_flows": [
                CashFlow(
                    flow_id="f1",
                    date=date(2026, 1, 6),
                    type="transfer_out",
                    amount=Decimal("200"),
                    source_text="test",
                    created_at=NOW,
                )
            ]
        }
    )
    result = ModelAssistedAnalysis(UnexpectedGateway(), model="test-model").analyze(
        AnalyzeProductRequest(
            product=product, ledger_revision="ledger-r1", generated_at=NOW
        )
    )

    assert result.report.overall is not None
    assert result.report.overall.modified_dietz_return is None
    assert result.suggestion is not None
    assert result.suggestion.direction == "insufficient_data"
    assert "无法计算" in result.suggestion.rationale


def test_invalid_model_output_returns_structured_error() -> None:
    result = ModelAssistedAnalysis(InvalidOutputGateway(), model="test-model").analyze(
        AnalyzeProductRequest(
            product=_product(), ledger_revision="ledger-r1", generated_at=NOW
        )
    )

    assert result.report.overall is not None
    assert result.suggestion is None
    assert result.model_error is not None
    assert result.model_error.code == "model_output_invalid"
    assert result.model_error.retryable is False
    assert result.model_error.details == {
        "model": "test-model",
        "validation_error_count": 3,
    }


def test_scalar_limitations_are_normalized_for_provider_compatibility() -> None:
    result = ModelAssistedAnalysis(
        ScalarLimitationsGateway(), model="test-model"
    ).analyze(
        AnalyzeProductRequest(
            product=_product(), ledger_revision="ledger-r1", generated_at=NOW
        )
    )

    assert result.model_error is None
    assert result.suggestion is not None
    assert result.suggestion.direction == "insufficient_data"
    assert result.suggestion.limitations == ["仅依据确定性报表"]
