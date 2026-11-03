from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

import pytest
from wealth_agent.bootstrap import AgentReadToolTaskExecutor
from wealth_agent.capabilities.agent.contract import (
    AgentToolDefinitionsRequest,
    AgentToolError,
    ExecuteAgentToolRequest,
    ModelAnalysisToolOutput,
    PerformanceReportToolOutput,
    UpdateProductProfileToolOutput,
)
from wealth_agent.capabilities.agent.implementation import (
    AgentToolRegistry,
    ReadOnlyAgentToolRegistry,
)
from wealth_agent.capabilities.analysis.implementation import (
    DeterministicAnalysis,
    ModelAssistedAnalysis,
)
from wealth_agent.capabilities.portfolio.contract import AddRecord, RecordKind
from wealth_agent.capabilities.portfolio.implementation.sqlite_store import (
    SqlitePortfolioStore,
)
from wealth_agent.capabilities.tasks.contract import (
    ExecuteStepRequest,
    TaskCheckpoint,
    TaskPayload,
    TaskStep,
)
from wealth_agent.shared.model import ModelToolCall, ToolFunctionCall

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class UnexpectedGateway:
    def complete(self, _request: object) -> NoReturn:
        raise AssertionError("资料不足时不应调用模型")


def _registry(
    tmp_path: Path,
) -> tuple[ReadOnlyAgentToolRegistry, SqlitePortfolioStore, str]:
    clock = FixedClock()
    store = SqlitePortfolioStore(tmp_path / "wealth.db", clock)
    first = store.execute(
        AddRecord(
            product_name="测试产品",
            record_date=date(2026, 1, 1),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("100"),
            source_text="年初估值 100",
            expected_revision=store.snapshot().revision,
            idempotency_key="record-1",
        )
    )
    store.execute(
        AddRecord(
            product_name="测试产品",
            record_date=date(2026, 9, 12),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("110"),
            source_text="当前估值 110",
            expected_revision=first.ledger_revision,
            idempotency_key="record-2",
        )
    )
    return (
        ReadOnlyAgentToolRegistry(store, DeterministicAnalysis(), clock),
        store,
        first.product_id,
    )


def _call(name: str, arguments: str) -> ModelToolCall:
    return ModelToolCall(
        id="call-1",
        function=ToolFunctionCall(name=name, arguments=arguments),
    )


def test_registry_exposes_five_versioned_read_only_tool_contracts(tmp_path: Path) -> None:
    registry, _, _ = _registry(tmp_path)

    result = registry.definitions(AgentToolDefinitionsRequest())

    assert result.schema_version == "wealth-agent-tools-v2"
    assert [item.function.name for item in result.definitions] == [
        "list_products",
        "get_product_profile",
        "get_product_snapshot",
        "get_performance_report",
        "get_data_quality_warnings",
    ]
    assert all(spec.side_effects == "none" for spec in result.specs)
    assert all(spec.permissions == ["ledger:read"] for spec in result.specs)
    assert all(len(spec.contract_hash) == 64 for spec in result.specs)


def test_registry_returns_structured_deterministic_report(tmp_path: Path) -> None:
    registry, store, product_id = _registry(tmp_path)

    result = registry.execute(
        ExecuteAgentToolRequest(
            call=_call(
                "get_performance_report",
                f'{{"schema_version":1,"product_id":"{product_id}"}}',
            ),
            granted_permissions=["ledger:read"],
            selected_product_id=product_id,
        )
    )

    assert isinstance(result.output, PerformanceReportToolOutput)
    assert result.output.ledger_revision == store.snapshot().revision
    assert result.output.report.overall is not None
    assert result.output.report.overall.modified_dietz_return == Decimal("0.1")
    assert result.model_dump(mode="json")["output"]["type"] == (
        "analysis.performance_report.v1"
    )


def test_registry_returns_structured_model_analysis_result(tmp_path: Path) -> None:
    _, store, product_id = _registry(tmp_path)
    registry = AgentToolRegistry(
        store,
        DeterministicAnalysis(),
        FixedClock(),
        analysis_service=ModelAssistedAnalysis(
            UnexpectedGateway(), model="analysis-model"
        ),
    )

    definitions = registry.definitions(AgentToolDefinitionsRequest())
    assert "get_model_analysis" in [item.function.name for item in definitions.definitions]
    spec = next(item for item in definitions.specs if item.name == "get_model_analysis")
    assert spec.permissions == ["ledger:read"]
    assert spec.side_effects == "none"

    result = registry.execute(
        ExecuteAgentToolRequest(
            call=_call(
                "get_model_analysis",
                f'{{"schema_version":1,"product_id":"{product_id}"}}',
            ),
            granted_permissions=["ledger:read"],
            selected_product_id=product_id,
            source_text="分析测试产品",
        )
    )

    assert isinstance(result.output, ModelAnalysisToolOutput)
    assert result.output.result.suggestion is not None
    assert result.output.result.suggestion.direction == "insufficient_data"
    assert result.model_dump(mode="json")["output"]["type"] == (
        "analysis.model_analysis.v1"
    )


def test_registry_updates_product_profile_with_confirmation_context(tmp_path: Path) -> None:
    _, store, product_id = _registry(tmp_path)
    registry = AgentToolRegistry(
        store,
        DeterministicAnalysis(),
        FixedClock(),
        portfolio_writer=store,
    )

    definitions = registry.definitions(AgentToolDefinitionsRequest())
    assert [item.function.name for item in definitions.definitions] == [
        "list_products",
        "get_product_profile",
        "get_product_snapshot",
        "get_performance_report",
        "get_data_quality_warnings",
        "add_ledger_record",
        "update_product_profile",
    ]
    profile_spec = next(
        spec for spec in definitions.specs if spec.name == "update_product_profile"
    )
    assert profile_spec.permissions == ["ledger:write"]
    assert profile_spec.side_effects == "ledger_write"
    assert profile_spec.idempotency.key_required is True

    first = registry.execute(
        ExecuteAgentToolRequest(
            call=_call(
                "update_product_profile",
                "{"
                f'"schema_version":1,"product_id":"{product_id}",'
                '"product_type":"固定收益类净值型","risk_level":"r3",'
                '"performance_benchmark":"2.85%"}',
            ),
            granted_permissions=["ledger:write"],
            selected_product_id=product_id,
            expected_ledger_revision=store.snapshot().revision,
            idempotency_key="profile-1",
            source_text="补充产品资料",
        )
    )

    assert isinstance(first.output, UpdateProductProfileToolOutput)
    updated = store.get_product(product_id).product
    assert updated.profile.product_type == "固定收益类净值型"
    assert updated.profile.risk_level == "R3"
    assert updated.profile.performance_benchmark == "2.85%"

    second = registry.execute(
        ExecuteAgentToolRequest(
            call=_call(
                "update_product_profile",
                "{"
                f'"schema_version":1,"product_id":"{product_id}",'
                '"performance_benchmark":"3.00%"}',
            ),
            granted_permissions=["ledger:write"],
            selected_product_id=product_id,
            expected_ledger_revision=first.output.ledger_revision,
            idempotency_key="profile-2",
            source_text="更新业绩比较基准",
        )
    )

    assert isinstance(second.output, UpdateProductProfileToolOutput)
    preserved = store.get_product(product_id).product.profile
    assert preserved.product_type == "固定收益类净值型"
    assert preserved.risk_level == "R3"
    assert preserved.performance_benchmark == "3.00%"


def test_registry_rejects_missing_permission_and_unknown_fields(tmp_path: Path) -> None:
    registry, _, product_id = _registry(tmp_path)

    with pytest.raises(AgentToolError) as permission_error:
        registry.execute(
            ExecuteAgentToolRequest(
                call=_call("list_products", '{"schema_version":1}'),
            )
        )
    assert permission_error.value.detail.code == "permission_denied"

    with pytest.raises(AgentToolError) as argument_error:
        registry.execute(
            ExecuteAgentToolRequest(
                call=_call(
                    "get_product_snapshot",
                    f'{{"schema_version":1,"product_id":"{product_id}","extra":true}}',
                ),
                granted_permissions=["ledger:read"],
            )
        )
    assert argument_error.value.detail.code == "invalid_arguments"


def test_task_executor_maps_tool_result_and_rejects_stale_revision(tmp_path: Path) -> None:
    registry, store, _ = _registry(tmp_path)
    executor = AgentReadToolTaskExecutor(
        registry,
        granted_permissions=("ledger:read",),
    )
    request = ExecuteStepRequest(
        task_id="task-1",
        correlation_id="correlation-1",
        step=TaskStep(
            step_id="step-1",
            action="list_products",
            arguments=TaskPayload(
                type="agent.tool.arguments.v1",
                data={"schema_version": 1},
            ),
            description="列出产品",
        ),
        attempt=1,
        confirmed_ledger_revision=store.snapshot().revision,
        checkpoint=TaskCheckpoint(ledger_revision=store.snapshot().revision),
    )

    completed = executor.execute(request)
    stale = executor.execute(
        request.model_copy(update={"confirmed_ledger_revision": "stale-revision"})
    )

    assert completed.status == "completed"
    assert completed.output is not None
    assert completed.output.type == "agent.tool.result.v1"
    assert stale.status == "failed"
    assert stale.error is not None
    assert stale.error.code == "ledger_revision_conflict"
