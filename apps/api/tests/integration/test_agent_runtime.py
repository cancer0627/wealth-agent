from datetime import UTC, datetime
from pathlib import Path

from wealth_agent.capabilities.agent.contract import (
    AgentModelConfig,
    PlanAgentRequest,
    RouteAgentResult,
    RunAgentRequest,
)
from wealth_agent.capabilities.agent.implementation import (
    ModelAgentRouter,
    ReadOnlyAgentPlanner,
    ReadOnlyAgentRuntime,
    ReadOnlyAgentToolRegistry,
)
from wealth_agent.capabilities.analysis.implementation import DeterministicAnalysis
from wealth_agent.capabilities.portfolio.implementation.sqlite_store import (
    SqlitePortfolioStore,
)
from wealth_agent.shared.model import (
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ToolFunctionCall,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class ScriptedGateway:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _response(*, content: str | None = None, tool: str | None = None) -> ModelResponse:
    calls = []
    if tool is not None:
        calls.append(
            ModelToolCall(
                id="call-1",
                function=ToolFunctionCall(
                    name=tool,
                    arguments='{"schema_version":1}',
                ),
            )
        )
    return ModelResponse(content=content, tool_calls=calls, model="scripted")


def _runtime(
    tmp_path: Path, gateway: ScriptedGateway
) -> tuple[ReadOnlyAgentRuntime, SqlitePortfolioStore]:
    clock = FixedClock()
    portfolio = SqlitePortfolioStore(tmp_path / "wealth.db", clock)
    tools = ReadOnlyAgentToolRegistry(portfolio, DeterministicAnalysis(), clock)
    router = ModelAgentRouter(gateway)
    planner = ReadOnlyAgentPlanner()
    return ReadOnlyAgentRuntime(gateway, tools, router, planner), portfolio


def _request(revision: str, *, active_product_id: str | None = None) -> RunAgentRequest:
    return RunAgentRequest(
        correlation_id="correlation-1",
        question="列出我的产品",
        active_product_id=active_product_id,
        config=AgentModelConfig(model="scripted"),
        granted_permissions=["ledger:read"],
        expected_ledger_revision=revision,
    )


def test_runtime_routes_plans_executes_tool_and_returns_structured_answer(
    tmp_path: Path,
) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                content=(
                    '{"intent":"list_products","requested_tools":["list_products"],'
                    '"clarification":null}'
                )
            ),
            _response(tool="list_products"),
            _response(content='{"status":"answered","answer":"当前台账没有产品。"}'),
        ]
    )
    runtime, portfolio = _runtime(tmp_path, gateway)

    result = runtime.run(_request(portfolio.snapshot().revision))

    assert result.status == "answered"
    assert result.plan is not None
    assert [step.action for step in result.plan.steps] == ["list_products"]
    assert result.answer is not None
    assert result.answer.answer == "当前台账没有产品。"
    assert len(result.tool_traces) == 1
    assert result.tool_traces[0].status == "completed"
    assert len(result.model_usages) == 3
    assert gateway.requests[0].tools is None
    assert [item.function.name for item in gateway.requests[1].tools or []] == [
        "list_products"
    ]
    assert gateway.requests[2].messages[-1].role == "tool"


def test_runtime_requests_product_clarification_without_starting_tool_loop(
    tmp_path: Path,
) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                content=(
                    '{"intent":"product_question",'
                    '"requested_tools":["get_performance_report"],'
                    '"clarification":null}'
                )
            )
        ]
    )
    runtime, portfolio = _runtime(tmp_path, gateway)

    result = runtime.run(_request(portfolio.snapshot().revision))

    assert result.status == "needs_clarification"
    assert result.answer is not None
    assert result.answer.answer == "请先明确要查询的产品。"
    assert result.plan is None
    assert len(gateway.requests) == 1


def test_runtime_rejects_tool_outside_generated_plan(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                content=(
                    '{"intent":"list_products","requested_tools":["list_products"],'
                    '"clarification":null}'
                )
            ),
            _response(tool="get_product_snapshot"),
        ]
    )
    runtime, portfolio = _runtime(tmp_path, gateway)

    result = runtime.run(_request(portfolio.snapshot().revision))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "tool_not_in_plan"
    assert result.tool_traces[0].status == "failed"


def test_planner_generates_versioned_product_tool_payload() -> None:
    planned = ReadOnlyAgentPlanner().plan(
        PlanAgentRequest(
            source_text="查看产品收益",
            active_product_id="product-1",
            route=RouteAgentResult(
                intent="product_question",
                requested_tools=["get_performance_report"],
            ),
        )
    )

    assert planned.status == "planned"
    assert planned.plan is not None
    assert planned.plan.steps[0].arguments.model_dump(mode="json") == {
        "schema_version": 1,
        "type": "agent.tool.arguments.v1",
        "data": {"schema_version": 1, "product_id": "product-1"},
    }


def test_runtime_enforces_total_model_request_budget(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                content=(
                    '{"intent":"list_products","requested_tools":["list_products"],'
                    '"clarification":null}'
                )
            ),
            _response(tool="list_products"),
        ]
    )
    runtime, portfolio = _runtime(tmp_path, gateway)
    request = _request(portfolio.snapshot().revision).model_copy(
        update={
            "config": AgentModelConfig(model="scripted", max_model_requests=2),
        }
    )

    result = runtime.run(request)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "max_model_requests"
    assert len(gateway.requests) == 2


def test_runtime_rejects_invalid_structured_router_output(tmp_path: Path) -> None:
    gateway = ScriptedGateway([_response(content="不是 JSON")])
    runtime, portfolio = _runtime(tmp_path, gateway)

    result = runtime.run(_request(portfolio.snapshot().revision))

    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "invalid_router_output"
    assert result.tool_traces == []
