from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from wealth_agent.bootstrap import AgentReadToolTaskExecutor, ApplicationTaskExecutor
from wealth_agent.bootstrap.application import (
    ApplicationConfig,
    BuildApplicationRequest,
    build_application,
)
from wealth_agent.capabilities.agent.contract import (
    AgentAnswer,
    AgentModelConfig,
    RunAgentRequest,
    RunAgentResult,
)
from wealth_agent.capabilities.agent.implementation import ReadOnlyAgentToolRegistry
from wealth_agent.capabilities.analysis.implementation import (
    DeterministicAnalysis,
    ModelAssistedAnalysis,
)
from wealth_agent.capabilities.conversation.contract import (
    ConfirmOcrDraftRequest,
    CreateOcrDraftRequest,
    CreateSessionRequest,
    DeleteOcrDraftRecordRequest,
    OcrDraftRecordUpdate,
    OcrExtraction,
    OcrExtractionRequest,
    OcrFieldConfidence,
    OcrRecordCandidate,
    PendingOcrDraftError,
    SendAgentMessageRequest,
    UpdateOcrDraftRecordRequest,
)
from wealth_agent.capabilities.conversation.implementation import InMemoryConversationService
from wealth_agent.capabilities.governance.contract import (
    AuthorizationRequest,
    EvaluationCase,
    EvaluationRequest,
    GovernanceEvent,
    ListEventsRequest,
    RecordEventRequest,
    ToolAuthorizationRequest,
    UserToolApproval,
)
from wealth_agent.capabilities.governance.implementation import (
    DefaultGovernanceService,
    ExactJsonEvaluationService,
    SqliteTelemetryRepository,
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
from wealth_agent.shared.model import ModelRequest, ModelResponse

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class UnusedGateway:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise AssertionError("本测试不应调用模型")


class FakeAgent:
    def __init__(self) -> None:
        self.requests: list[RunAgentRequest] = []

    def run(self, request: RunAgentRequest) -> RunAgentResult:
        self.requests.append(request)
        return RunAgentResult(
            status="answered",
            answer=AgentAnswer(status="answered", answer="结构化回答"),
        )


class FakeOcr:
    def extract(self, request: OcrExtractionRequest) -> OcrExtraction:
        return OcrExtraction(
            image_sha256="a" * 64,
            mime_type=request.mime_type,
            width=100,
            height=100,
            orientation_degrees=0,
            engine="fake",
            model="fake",
            parser="fake-v1",
            records=[
                OcrRecordCandidate(
                    product_name="OCR 产品",
                    record_date=request.default_date,
                    amount=Decimal("88.00"),
                    confidence=OcrFieldConfidence(
                        product_name=1,
                        record_date=1,
                        amount=1,
                    ),
                )
            ],
        )


def test_governance_authorization_telemetry_and_evaluation(tmp_path: Path) -> None:
    governance = DefaultGovernanceService()
    assert governance.authorize(
        AuthorizationRequest(
            actor="agent",
            operation="read",
            requested_permissions=["ledger:read"],
            side_effects="none",
        )
    ).decision == "allowed"
    assert governance.authorize(
        AuthorizationRequest(
            actor="agent",
            operation="backup.restore",
            requested_permissions=["ledger:write"],
            side_effects="backup_restore",
            confirmed=True,
        )
    ).decision == "denied"
    action_hash = "a" * 64
    assert governance.authorize_tool(
        ToolAuthorizationRequest(
            task_id="task-1",
            step_id="step-1",
            tool_name="add_ledger_record",
            requested_permissions=["ledger:write"],
            side_effects="ledger_write",
            action_hash=action_hash,
        )
    ).decision == "confirmation_required"
    assert governance.authorize_tool(
        ToolAuthorizationRequest(
            task_id="task-1",
            step_id="step-1",
            tool_name="add_ledger_record",
            requested_permissions=["ledger:write"],
            side_effects="ledger_write",
            action_hash=action_hash,
            approval=UserToolApproval(
                approval_id="approval-1",
                action_hash="b" * 64,
                decision="approve",
            ),
        )
    ).decision == "denied"

    repository = SqliteTelemetryRepository(tmp_path / "telemetry.db")
    event = GovernanceEvent(
        event_id="event-1",
        category="audit",
        name="authorization",
        occurred_at=NOW,
        payload={"decision": "allowed"},
    )
    first = repository.record(RecordEventRequest(event=event))
    replay = repository.record(RecordEventRequest(event=event))
    assert first.created is True
    assert replay.created is False
    assert repository.list(ListEventsRequest()).events == [event]

    evaluation = ExactJsonEvaluationService().evaluate(
        EvaluationRequest(
            evaluation_id="evaluation-1",
            dataset_version="dataset-v1",
            cases=[
                EvaluationCase(case_id="pass", expected={"a": 1}, actual={"a": 1}),
                EvaluationCase(case_id="fail", expected={"a": 1}, actual={"a": 2}),
            ],
        )
    )
    assert (evaluation.passed, evaluation.failed, evaluation.pass_rate) == (1, 1, 0.5)


def test_conversation_connects_governance_agent_and_confirmed_ocr_write(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    portfolio = SqlitePortfolioStore(tmp_path / "wealth.db", clock)
    agent = FakeAgent()
    telemetry = SqliteTelemetryRepository(tmp_path / "telemetry.db")
    conversation = InMemoryConversationService(
        clock,
        FakeOcr(),
        agent=agent,
        governance=DefaultGovernanceService(),
        telemetry=telemetry,
        portfolio_reader=portfolio,
        portfolio_writer=portfolio,
    )
    session = conversation.create(CreateSessionRequest())
    turn = conversation.send_agent_message(
        SendAgentMessageRequest(
            session_id=session.session_id,
            correlation_id="correlation-1",
            content="列出产品",
            config=AgentModelConfig(model="fake"),
        )
    )
    assert [item.role for item in turn.session.messages] == ["user", "assistant"]
    assert agent.requests[0].granted_permissions == []
    events = telemetry.list(ListEventsRequest()).events
    assert len(events) == 1
    assert events[0].name == "agent.run"
    assert "content" not in str(events[0].payload)

    drafted = conversation.create_ocr_draft(
        CreateOcrDraftRequest(
            session_id=session.session_id,
            extraction=OcrExtractionRequest(
                content=b"fake-image",
                mime_type="image/png",
                default_date=date(2026, 9, 12),
            ),
        )
    )
    assert drafted.ocr_draft is not None
    updated = conversation.update_ocr_draft_record(
        UpdateOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=drafted.ocr_draft.records[0].record_id,
            values=OcrDraftRecordUpdate(
                product_name="修改后的 OCR 产品",
                record_date=date(2026, 9, 13),
                amount=Decimal("99.00"),
            ),
        )
    )
    assert updated.ocr_draft is not None
    assert updated.ocr_draft.records[0].product_name == "修改后的 OCR 产品"
    confirmed = conversation.confirm_ocr_draft(
        ConfirmOcrDraftRequest(
            session_id=session.session_id,
            draft_id=updated.ocr_draft.draft_id,
            expected_revision=portfolio.snapshot().revision,
            idempotency_key_prefix="ocr-confirm-1",
            confirmed=True,
        )
    )
    assert len(confirmed.mutations) == 1
    assert confirmed.session.ocr_draft is None
    assert portfolio.list_products().products[0].product_name == "修改后的 OCR 产品"


def test_empty_ocr_draft_cannot_be_confirmed_or_write_to_portfolio(tmp_path: Path) -> None:
    clock = FixedClock()
    portfolio = SqlitePortfolioStore(tmp_path / "wealth.db", clock)
    conversation = InMemoryConversationService(clock, FakeOcr(), portfolio_writer=portfolio)
    session = conversation.create(CreateSessionRequest())
    drafted = conversation.create_ocr_draft(
        CreateOcrDraftRequest(
            session_id=session.session_id,
            extraction=OcrExtractionRequest(
                content=b"fake-image",
                mime_type="image/png",
                default_date=date(2026, 9, 12),
            ),
        )
    )
    assert drafted.ocr_draft is not None
    empty = conversation.delete_ocr_draft_record(
        DeleteOcrDraftRecordRequest(
            session_id=session.session_id,
            draft_id=drafted.ocr_draft.draft_id,
            record_id=drafted.ocr_draft.records[0].record_id,
        )
    )
    assert empty.ocr_draft is not None
    assert empty.ocr_draft.records == []
    revision = portfolio.snapshot().revision

    with pytest.raises(PendingOcrDraftError) as error:
        conversation.confirm_ocr_draft(
            ConfirmOcrDraftRequest(
                session_id=session.session_id,
                draft_id=empty.ocr_draft.draft_id,
                expected_revision=revision,
                idempotency_key_prefix="empty-ocr-confirm-1",
                confirmed=True,
            )
        )
    assert error.value.detail.code == "pending_ocr_draft"
    assert portfolio.snapshot().revision == revision
    assert portfolio.list_products().products == []


def test_application_task_executor_handles_structured_write_and_analysis(
    tmp_path: Path,
) -> None:
    clock = FixedClock()
    portfolio = SqlitePortfolioStore(tmp_path / "wealth.db", clock)
    tools = ReadOnlyAgentToolRegistry(portfolio, DeterministicAnalysis(), clock)
    executor = ApplicationTaskExecutor(
        AgentReadToolTaskExecutor(tools, granted_permissions=("ledger:read",)),
        portfolio,
        portfolio,
        ModelAssistedAnalysis(UnusedGateway(), model="fake"),
        clock,
    )
    revision = portfolio.snapshot().revision
    command = AddRecord(
        product_name="任务产品",
        record_date=date(2026, 9, 12),
        record_kind=RecordKind.VALUATION,
        amount=Decimal("100"),
        source_text="任务写入",
        expected_revision=revision,
        idempotency_key="task-write-1",
    )
    result = executor.execute(
        ExecuteStepRequest(
            task_id="task-1",
            correlation_id="correlation-1",
            step=TaskStep(
                step_id="step-1",
                action="portfolio.write",
                arguments=TaskPayload(
                    type="portfolio.command.v1",
                    data=command.model_dump(mode="json"),
                ),
                description="写入估值",
                side_effects=["ledger_write"],
            ),
            attempt=1,
            confirmed_ledger_revision=revision,
            checkpoint=TaskCheckpoint(ledger_revision=revision),
        )
    )
    assert result.status == "completed"
    assert result.output is not None
    assert result.output.type == "portfolio.mutation_result.v1"


def test_build_application_initializes_separate_local_databases(tmp_path: Path) -> None:
    bundle = build_application(
        BuildApplicationRequest(
            config=ApplicationConfig(
                data_dir=tmp_path,
                logs_dir=tmp_path / "logs",
                analysis_provider="fake",
                analysis_model="analysis-model",
                agent=AgentModelConfig(model="agent-model"),
            ),
            model_gateway=UnusedGateway(),
            clock=FixedClock(),
            ocr_engine=FakeOcr(),
        )
    )

    assert bundle.portfolio.snapshot().products == []
    assert (tmp_path / "wealth.db").is_file()
    assert (tmp_path / "task_state.db").is_file()
    assert (tmp_path / "telemetry.db").is_file()
