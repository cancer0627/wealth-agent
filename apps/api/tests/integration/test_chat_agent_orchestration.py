import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from wealth_agent.bootstrap.application import (
    ApplicationConfig,
    BuildApplicationRequest,
    build_application,
)
from wealth_agent.capabilities.agent.contract import AgentModelConfig
from wealth_agent.capabilities.analysis.contract import LatestAnalysisRequest
from wealth_agent.capabilities.conversation.contract import (
    AppendMessageRequest,
    CreateSessionRequest,
    GetSessionRequest,
    RespondApprovalRequest,
    SendAgentMessageRequest,
)
from wealth_agent.capabilities.portfolio.contract import (
    AddRecord,
    RecordKind,
    RiskLevel,
    UpdateProductProfile,
)
from wealth_agent.capabilities.tasks.contract import (
    ClaimTaskRequest,
    ListTasksRequest,
    ResolveTaskApprovalRequest,
)
from wealth_agent.shared.model import (
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ToolFunctionCall,
)

NOW = datetime(2026, 9, 13, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class ScriptedGateway:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"没有为 {request.request_id} 配置模型响应")
        return self.responses.pop(0)


def _response(
    *, content: str | None = None, tool: str | None = None, arguments: str = "{}"
) -> ModelResponse:
    calls = []
    if tool is not None:
        calls.append(
            ModelToolCall(
                id="call-1",
                function=ToolFunctionCall(name=tool, arguments=arguments),
            )
        )
    return ModelResponse(content=content, tool_calls=calls, model="scripted")


def _parallel_tool_response(tool: str) -> ModelResponse:
    return ModelResponse(
        tool_calls=[
            ModelToolCall(
                id=f"call-{index}",
                function=ToolFunctionCall(name=tool, arguments='{"schema_version":1}'),
            )
            for index in range(1, 3)
        ],
        model="scripted",
    )


def _plan(tool: str, objective: str) -> ModelResponse:
    return _response(
        content=(
            '{"status":"planned","plan":{"schema_version":2,'
            '"steps":[{"step_id":"step-1",'
            f'"objective":"{objective}","success_criteria":"完成",'
            f'"allowed_tools":["{tool}"]' + "}]}}"
        )
    )


def _bundle(tmp_path: Path, gateway: ScriptedGateway):
    return build_application(
        BuildApplicationRequest(
            config=ApplicationConfig(
                data_dir=tmp_path,
                logs_dir=tmp_path / "logs",
                analysis_provider="fake",
                analysis_model="analysis-model",
                agent=AgentModelConfig(model="scripted", max_model_requests=5),
            ),
            model_gateway=gateway,
            clock=FixedClock(),
        )
    )


def test_read_only_chat_uses_persistent_task_and_returns_summary(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _plan("list_products", "读取产品列表"),
            _response(tool="list_products", arguments='{"schema_version":1}'),
            _response(content='{"status":"answered","answer":"读取完成"}'),
            _response(content='{"status":"answered","answer":"当前没有产品。"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())

    turn = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-read",
            content="列出我的产品",
            config=bundle.config.agent,
        )
    )

    assert turn.status == "answered", turn.session.messages
    assert turn.session.messages[-1].content == "当前没有产品。"
    records = bundle.task_runtime.tasks.list(ListTasksRequest()).tasks
    assert len(records) == 1
    assert records[0].status == "completed"
    assert records[0].plan.source_text == "列出我的产品"
    assert all(request.request_id != "correlation-read:route" for request in gateway.requests)


def test_chat_limits_agent_history_to_contract_maximum(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _plan("list_products", "读取产品列表"),
            _response(tool="list_products", arguments='{"schema_version":1}'),
            _response(content='{"status":"answered","answer":"读取完成"}'),
            _response(content='{"status":"answered","answer":"历史已截断后读取完成。"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())
    for index in range(22):
        session = bundle.conversation.append(
            AppendMessageRequest(
                session_id=session.session_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"历史消息 {index}",
            )
        )

    turn = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-history-limit",
            content="继续读取产品列表",
            config=bundle.config.agent,
        )
    )

    assert turn.status == "answered"
    planner_messages = gateway.requests[0].messages
    assert [item.content for item in planner_messages[1:-1]] == [
        f"历史消息 {index}" for index in range(2, 22)
    ]


def test_redundant_summary_step_does_not_start_another_tool_loop(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                content=json.dumps(
                    {
                        "status": "planned",
                        "plan": {
                            "schema_version": 2,
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "objective": "读取产品事实",
                                    "success_criteria": "完成读取",
                                    "allowed_tools": [
                                        "list_products",
                                        "get_product_profile",
                                    ],
                                },
                                {
                                    "step_id": "step-2",
                                    "objective": "综合以上信息输出结论",
                                    "success_criteria": "完成综合",
                                    "allowed_tools": [
                                        "list_products",
                                        "get_product_profile",
                                    ],
                                },
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
            ),
            _response(tool="list_products", arguments='{"schema_version":1}'),
            _response(content='{"status":"answered","answer":"已读取产品事实"}'),
            _response(content='{"status":"answered","answer":"综合完成"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())

    turn = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-redundant-summary",
            content="分析产品",
            config=bundle.config.agent,
        )
    )

    assert turn.status == "answered", turn.session.messages
    task = bundle.task_runtime.tasks.list(ListTasksRequest()).tasks[0]
    assert task.status == "completed"
    assert [step.step_id for step in task.plan.steps] == ["step-1"]
    assert gateway.requests[-1].request_id == "correlation-redundant-summary:summary"


def test_analysis_chat_returns_structured_direction_in_final_answer(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _response(
                content=json.dumps(
                    {
                        "status": "planned",
                        "plan": {
                            "schema_version": 2,
                            "steps": [
                                {
                                    "step_id": "step-1",
                                    "objective": "定位指定产品",
                                    "success_criteria": "返回产品ID",
                                    "allowed_tools": ["list_products"],
                                },
                                {
                                    "step_id": "step-2",
                                    "objective": "生成产品分析和增减持方向",
                                    "success_criteria": "返回结构化增减持方向",
                                    "allowed_tools": ["get_model_analysis"],
                                },
                            ],
                        },
                    },
                    ensure_ascii=False,
                )
            ),
            _response(tool="list_products", arguments='{"schema_version":1}'),
            _response(content='{"status":"answered","answer":"产品已定位。"}'),
            _response(
                tool="get_model_analysis",
                arguments='{"schema_version":1,"product_id":"placeholder"}',
            ),
            _response(
                content=(
                    '{"direction":"increase","summary":"收益表现稳定，可在风险承受范围内考虑增持",'
                    '"rationale":"区间收益为正且资料完整","limitations":[]}'
                )
            ),
            _response(content='{"status":"answered","answer":"模型分析已完成。"}'),
            _response(content='{"status":"answered","answer":"分析完成。"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    first = bundle.portfolio.execute(
        AddRecord(
            product_name="华安资管众赢12M008",
            record_date=date(2026, 9, 1),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("300270"),
            source_text="测试估值 1",
            expected_revision=bundle.portfolio.snapshot().revision,
            idempotency_key="analysis-product-1",
        )
    )
    second = bundle.portfolio.execute(
        AddRecord(
            product_name="华安资管众赢12M008",
            record_date=date(2026, 9, 12),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("300360"),
            source_text="测试估值 2",
            expected_revision=first.ledger_revision,
            idempotency_key="analysis-product-2",
        )
    )
    bundle.portfolio.execute(
        UpdateProductProfile(
            product_id=first.product_id,
            product_type="固定收益类净值型",
            risk_level=RiskLevel.R3,
            performance_benchmark="2.85%",
            expected_revision=second.ledger_revision,
            idempotency_key="analysis-product-profile",
        )
    )
    gateway.responses[3] = _response(
        tool="get_model_analysis",
        arguments=json.dumps(
            {"schema_version": 1, "product_id": first.product_id},
            ensure_ascii=False,
        ),
    )
    session = bundle.conversation.create(CreateSessionRequest())

    turn = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-model-analysis",
            content="分析 华安资管众赢12M008",
            config=bundle.config.agent,
        )
    )

    assert turn.status == "answered", turn.session.messages
    assert "增减持建议：增持" in turn.session.messages[-1].content
    record = bundle.analysis_repository.latest(
        LatestAnalysisRequest(product_id=first.product_id)
    ).record
    assert record is not None
    assert record.result.suggestion is not None
    assert record.result.suggestion.direction == "increase"
    assert any(
        request.request_id.startswith("analysis_")
        for request in gateway.requests
    )


def test_parallel_tool_candidates_are_serialized_to_one_execution(tmp_path: Path) -> None:
    gateway = ScriptedGateway(
        [
            _plan("list_products", "读取产品列表"),
            _parallel_tool_response("list_products"),
            _response(content='{"status":"answered","answer":"读取完成"}'),
            _response(content='{"status":"answered","answer":"当前没有产品。"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())

    turn = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-parallel-tools",
            content="列出我的产品",
            config=bundle.config.agent,
        )
    )

    assert turn.status == "answered", turn.session.messages
    assert turn.session.messages[-1].content == "当前没有产品。"
    records = bundle.task_runtime.tasks.list(ListTasksRequest()).tasks
    assert records[0].status == "completed"
    follow_up_messages = gateway.requests[2].messages
    assistant_call = follow_up_messages[-2]
    assert assistant_call.tool_calls is not None
    assert [call.id for call in assistant_call.tool_calls] == ["call-1"]


def test_write_waits_for_bound_approval_and_survives_restart(tmp_path: Path) -> None:
    write_arguments = (
        '{"schema_version":1,"product_name":"确认产品",'
        '"record_date":"2026-09-13","record_kind":"valuation","amount":"100.00"}'
    )
    first_gateway = ScriptedGateway(
        [
            _plan("add_ledger_record", "新增估值记录"),
            _response(tool="add_ledger_record", arguments=write_arguments),
        ]
    )
    first = _bundle(tmp_path, first_gateway)
    session = first.conversation.create(CreateSessionRequest())
    before = first.portfolio.snapshot().revision

    pending_turn = first.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-write",
            content="给确认产品新增今天 100 元估值",
            config=first.config.agent,
        )
    )

    assert pending_turn.status == "awaiting_approval", pending_turn.session.messages
    assert first.portfolio.snapshot().revision == before
    card = pending_turn.session.pending_approval
    assert card is not None
    assert card.amount == "100.00"
    original_task = first.task_runtime.tasks.list(ListTasksRequest()).tasks[0]
    original_trace_id = original_task.trace_id

    resumed_gateway = ScriptedGateway(
        [
            _response(content='{"status":"answered","answer":"写入完成"}'),
            _response(content='{"status":"answered","answer":"已新增估值记录。"}'),
        ]
    )
    resumed = _bundle(tmp_path, resumed_gateway)
    restored = resumed.conversation.get(GetSessionRequest(session_id=session.session_id))
    assert restored.pending_approval == card
    resumed_task = resumed.task_runtime.tasks.list(ListTasksRequest()).tasks[0]
    assert resumed_task.trace_id == original_trace_id

    completed = resumed.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=card.approval_id,
            decision="approve",
            config=resumed.config.agent,
        )
    )

    assert completed.status == "answered"
    assert completed.session.pending_approval is None
    assert completed.session.messages[-1].content == "已新增估值记录。"
    assert resumed.portfolio.list_products().products[0].product_name == "确认产品"
    log_events = [
        json.loads(line)
        for path in (tmp_path / "logs").glob("wealth-agent-*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    continued_calls = [
        event
        for event in log_events
        if event["event"] == "method_call"
        and event["method"]
        in {
            "TaskOrchestratingAgentRuntime.respond_approval",
            "AgentToolRegistry.execute",
        }
    ]
    assert len(continued_calls) == 2
    assert {event["trace_id"] for event in continued_calls} == {original_trace_id}
    revision = resumed.portfolio.snapshot().revision
    message_count = len(completed.session.messages)

    replay = resumed.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=card.approval_id,
            decision="approve",
            config=resumed.config.agent,
        )
    )

    assert replay.status == "answered"
    assert resumed.portfolio.snapshot().revision == revision
    assert len(replay.session.messages) == message_count


def test_profile_update_waits_for_confirmation_and_survives_restart(
    tmp_path: Path,
) -> None:
    first_gateway = ScriptedGateway([])
    first = _bundle(tmp_path, first_gateway)
    seeded = first.portfolio.execute(
        AddRecord(
            product_name="华安资管众赢12M008",
            record_date=date(2026, 9, 14),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("300360"),
            source_text="测试估值",
            expected_revision=first.portfolio.snapshot().revision,
            idempotency_key="seed-profile-product",
        )
    )
    first_gateway.responses = [
        _response(
            content=json.dumps(
                {
                    "status": "planned",
                    "plan": {
                        "schema_version": 2,
                        "steps": [
                            {
                                "step_id": "step-1",
                                "objective": "读取产品资料",
                                "success_criteria": "完成读取",
                                "allowed_tools": ["get_product_profile"],
                            },
                            {
                                "step_id": "step-2",
                                "objective": "更新产品资料",
                                "success_criteria": "完成更新",
                                "allowed_tools": ["update_product_profile"],
                            },
                            {
                                "step_id": "step-3",
                                "objective": "复核产品资料",
                                "success_criteria": "完成复核",
                                "allowed_tools": ["get_product_profile"],
                            },
                        ],
                    },
                },
                ensure_ascii=False,
            )
        ),
        _response(
            tool="get_product_profile",
            arguments=(
                f'{{"schema_version":1,"product_id":"{seeded.product_id}"}}'
            ),
        ),
        _response(content='{"status":"answered","answer":"读取完成"}'),
        _response(
            tool="update_product_profile",
            arguments=(
                f'{{"schema_version":1,"product_id":"{seeded.product_id}",'
                '"product_type":"固定收益类净值型","risk_level":"r3",'
                '"performance_benchmark":"2.85%"}'
            ),
        ),
    ]
    session = first.conversation.create(CreateSessionRequest())
    before = first.portfolio.snapshot().revision

    pending_turn = first.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-profile-update",
            content="补充到 华安资管众赢12M008 产品资料",
            config=first.config.agent,
        )
    )

    assert pending_turn.status == "awaiting_approval", pending_turn.session.messages
    assert first.portfolio.snapshot().revision == before
    card = pending_turn.session.pending_approval
    assert card is not None
    assert card.tool_name == "update_product_profile"
    assert card.product_name == "华安资管众赢12M008"
    assert card.product_id == seeded.product_id
    assert card.product_type == "固定收益类净值型"
    assert card.risk_level == "R3"
    assert card.performance_benchmark == "2.85%"

    resumed_gateway = ScriptedGateway(
        [
            _response(content='{"status":"answered","answer":"更新完成"}'),
            _response(
                tool="get_product_profile",
                arguments='{"schema_version":1,"product_id":"华安资管众赢12M008"}',
            ),
            _response(content='{"status":"answered","answer":"复核完成"}'),
            _response(content='{"status":"answered","answer":"产品资料已更新。"}'),
        ]
    )
    resumed = _bundle(tmp_path, resumed_gateway)
    restored = resumed.conversation.get(GetSessionRequest(session_id=session.session_id))
    assert restored.pending_approval == card

    completed = resumed.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=card.approval_id,
            decision="approve",
            config=resumed.config.agent,
        )
    )

    assert completed.status == "answered"
    assert completed.session.pending_approval is None
    assert completed.session.messages[-1].content == "产品资料已更新。"
    profile = resumed.portfolio.get_product(seeded.product_id).product.profile
    assert profile.product_type == "固定收益类净值型"
    assert profile.risk_level == "R3"
    assert profile.performance_benchmark == "2.85%"


def test_rejected_write_does_not_change_ledger(tmp_path: Path) -> None:
    write_arguments = (
        '{"schema_version":1,"product_name":"拒绝产品",'
        '"record_date":"2026-09-13","record_kind":"transfer_in","amount":"20"}'
    )
    gateway = ScriptedGateway(
        [
            _plan("add_ledger_record", "新增转入记录"),
            _response(tool="add_ledger_record", arguments=write_arguments),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())
    before = bundle.portfolio.snapshot().revision
    pending = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-reject",
            content="给拒绝产品新增 20 元转入",
            config=bundle.config.agent,
        )
    )
    card = pending.session.pending_approval
    assert card is not None

    rejected = bundle.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=card.approval_id,
            decision="reject",
            config=bundle.config.agent,
        )
    )

    assert rejected.status == "answered"
    assert bundle.portfolio.snapshot().revision == before
    assert bundle.portfolio.list_products().products == []


@pytest.mark.parametrize("record_kind", ["valuation", "transfer_in", "transfer_out"])
def test_chat_write_supports_each_v2_record_kind(
    tmp_path: Path, record_kind: str
) -> None:
    gateway = ScriptedGateway(
        [
            _plan("add_ledger_record", "新增台账记录"),
            _response(
                tool="add_ledger_record",
                arguments=(
                    '{"schema_version":1,"product_name":"三类记录产品",'
                    f'"record_date":"2026-09-13","record_kind":"{record_kind}",'
                    '"amount":"30.00"}'
                ),
            ),
            _response(content='{"status":"answered","answer":"写入完成"}'),
            _response(content='{"status":"answered","answer":"记录已新增。"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())
    pending = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id=f"correlation-{record_kind}",
            content=f"新增一条 {record_kind} 记录",
            config=bundle.config.agent,
        )
    )
    card = pending.session.pending_approval
    assert card is not None

    bundle.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=card.approval_id,
            decision="approve",
            config=bundle.config.agent,
        )
    )

    product_id = bundle.portfolio.list_products().products[0].product_id
    product = bundle.portfolio.get_product(product_id).product
    if record_kind == "valuation":
        assert [str(item.amount) for item in product.valuations] == ["30.00"]
        assert product.cash_flows == []
    else:
        assert product.valuations == []
        assert [item.type for item in product.cash_flows] == [record_kind]


def test_approved_interrupted_write_replays_same_idempotency_key(tmp_path: Path) -> None:
    arguments = (
        '{"schema_version":1,"product_name":"恢复产品",'
        '"record_date":"2026-09-13","record_kind":"valuation","amount":"66.00"}'
    )
    first = _bundle(
        tmp_path,
        ScriptedGateway(
            [
                _plan("add_ledger_record", "新增估值记录"),
                _response(tool="add_ledger_record", arguments=arguments),
            ]
        ),
    )
    session = first.conversation.create(CreateSessionRequest())
    pending_turn = first.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-interrupted",
            content="给恢复产品新增 66 元估值",
            config=first.config.agent,
        )
    )
    card = pending_turn.session.pending_approval
    assert card is not None
    task = first.task_runtime.tasks.list(ListTasksRequest()).tasks[0]
    pending = task.pending_approval
    assert pending is not None
    queued = first.task_runtime.tasks.resolve_approval(
        ResolveTaskApprovalRequest(
            task_id=task.task_id,
            expected_state_version=task.state_version,
            approval_id=pending.approval_id,
            decision="approve",
            current_ledger_revision=task.current_ledger_revision,
        )
    )
    running = first.task_runtime.tasks.claim(
        ClaimTaskRequest(
            worker_id="local-worker",
            environment_epoch=queued.environment_epoch,
            current_ledger_revision=queued.current_ledger_revision,
            task_id=queued.task_id,
        )
    ).task
    assert running is not None
    first.portfolio.execute(
        AddRecord(
            product_name="恢复产品",
            record_date=date(2026, 9, 13),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("66.00"),
            source_text="给恢复产品新增 66 元估值",
            expected_revision=running.current_ledger_revision,
            idempotency_key=f"{running.task_id}:0:call-1",
        )
    )

    resumed = _bundle(
        tmp_path,
        ScriptedGateway(
            [
                _response(content='{"status":"answered","answer":"写入完成"}'),
                _response(content='{"status":"answered","answer":"恢复完成。"}'),
            ]
        ),
    )
    restored = resumed.conversation.get(GetSessionRequest(session_id=session.session_id))
    assert restored.pending_approval == card
    completed = resumed.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=card.approval_id,
            decision="approve",
            config=resumed.config.agent,
        )
    )

    assert completed.status == "answered"
    product_id = resumed.portfolio.list_products().products[0].product_id
    product = resumed.portfolio.get_product(product_id).product
    assert len(product.valuations) == 1


def test_ledger_change_invalidates_old_approval_and_replans(tmp_path: Path) -> None:
    arguments = (
        '{"schema_version":1,"product_name":"重规划产品",'
        '"record_date":"2026-09-13","record_kind":"valuation","amount":"88.00"}'
    )
    gateway = ScriptedGateway(
        [
            _plan("add_ledger_record", "新增估值记录"),
            _response(tool="add_ledger_record", arguments=arguments),
            _plan("add_ledger_record", "台账变化后重新新增估值记录"),
            _response(tool="add_ledger_record", arguments=arguments),
            _response(content='{"status":"answered","answer":"写入完成"}'),
            _response(content='{"status":"answered","answer":"已按新台账写入。"}'),
        ]
    )
    bundle = _bundle(tmp_path, gateway)
    session = bundle.conversation.create(CreateSessionRequest())
    first = bundle.conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-replan",
            content="给重规划产品新增 88 元估值",
            config=bundle.config.agent,
        )
    )
    old_card = first.session.pending_approval
    assert old_card is not None
    old_revision = bundle.portfolio.snapshot().revision
    bundle.portfolio.execute(
        AddRecord(
            product_name="并发产品",
            record_date=date(2026, 9, 13),
            record_kind=RecordKind.TRANSFER_IN,
            amount=Decimal("1.00"),
            source_text="并发写入",
            expected_revision=old_revision,
            idempotency_key="concurrent-write",
        )
    )

    replanned = bundle.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=old_card.approval_id,
            decision="approve",
            config=bundle.config.agent,
        )
    )
    new_card = replanned.session.pending_approval
    assert replanned.status == "awaiting_approval"
    assert new_card is not None
    assert new_card.approval_id != old_card.approval_id
    assert [item.product_name for item in bundle.portfolio.list_products().products] == [
        "并发产品"
    ]
    assert any(request.request_id.endswith(":replan:plan") for request in gateway.requests)

    completed = bundle.conversation.respond_approval(
        RespondApprovalRequest(
            session_id=session.session_id,
            approval_id=new_card.approval_id,
            decision="approve",
            config=bundle.config.agent,
        )
    )

    assert completed.status == "answered"
    assert {item.product_name for item in bundle.portfolio.list_products().products} == {
        "并发产品",
        "重规划产品",
    }
