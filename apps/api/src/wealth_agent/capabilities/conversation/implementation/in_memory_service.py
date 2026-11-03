"""Thread-safe in-memory conversation state with OCR draft isolation."""

from __future__ import annotations

from threading import RLock

from wealth_agent.capabilities.agent.contract import (
    AgentHistoryMessage,
    AgentPendingApproval,
    AgentService,
    CancelAgentRunRequest,
    RespondAgentApprovalRequest,
    RunAgentRequest,
    RunAgentResult,
)
from wealth_agent.capabilities.conversation.contract import (
    AppendMessageRequest,
    ConfirmOcrDraftRequest,
    ConfirmOcrDraftResult,
    ConversationApprovalCard,
    ConversationBusyError,
    ConversationError,
    ConversationMessage,
    ConversationSession,
    ConversationTurnResult,
    CreateOcrDraftRequest,
    CreateSessionRequest,
    DeleteOcrDraftRecordRequest,
    DiscardOcrDraftRequest,
    GetSessionRequest,
    OcrDraft,
    OcrDraftRecord,
    OcrEngine,
    PendingOcrDraftError,
    ResetSessionRequest,
    RespondApprovalRequest,
    SelectProductRequest,
    SendAgentMessageRequest,
    SessionNotFoundError,
    UpdateOcrDraftRecordRequest,
)
from wealth_agent.capabilities.governance.contract import (
    GovernanceEvent,
    GovernanceService,
    RecordEventRequest,
    TelemetryRepository,
)
from wealth_agent.capabilities.portfolio.contract import (
    AddRecord,
    AddRecordInput,
    AddRecordsBatchRequest,
    PortfolioBatchWriter,
    PortfolioReader,
    PortfolioWriter,
    RecordKind,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.identity import new_id
from wealth_agent.shared.logging import trace_call, trace_public_methods
from wealth_agent.shared.serialization import sha256_json


@trace_public_methods(layer="capability")
class InMemoryConversationService:
    def __init__(
        self,
        clock: Clock,
        ocr_engine: OcrEngine | None = None,
        *,
        agent: AgentService | None = None,
        governance: GovernanceService | None = None,
        telemetry: TelemetryRepository | None = None,
        portfolio_reader: PortfolioReader | None = None,
        portfolio_writer: PortfolioWriter | None = None,
        portfolio_batch_writer: PortfolioBatchWriter | None = None,
    ) -> None:
        self._clock = clock
        self._ocr_engine = ocr_engine
        self._agent = agent
        self._governance = governance
        self._telemetry = telemetry
        self._portfolio_reader = portfolio_reader
        self._portfolio_writer = portfolio_writer
        self._portfolio_batch_writer = portfolio_batch_writer
        self._sessions: dict[str, ConversationSession] = {}
        self._pending_agent_approvals: dict[str, AgentPendingApproval] = {}
        self._resolved_agent_approvals: dict[str, dict[str, RunAgentResult]] = {}
        self._lock = RLock()

    def create(self, _request: CreateSessionRequest) -> ConversationSession:
        now = self._clock.now()
        session = ConversationSession(
            session_id=new_id("session"),
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session.model_copy(deep=True)

    def get(self, request: GetSessionRequest) -> ConversationSession:
        with self._lock:
            return self._find(request.session_id).model_copy(deep=True)

    def append(self, request: AppendMessageRequest) -> ConversationSession:
        with self._lock:
            session = self._find(request.session_id)
            message = ConversationMessage(
                message_id=new_id("message"),
                role=request.role,
                content=request.content.strip(),
                created_at=self._clock.now(),
            )
            updated = session.model_copy(
                update={
                    "messages": [*session.messages, message],
                    "updated_at": message.created_at,
                }
            )
            self._sessions[session.session_id] = updated
            return updated.model_copy(deep=True)

    def reset(self, request: ResetSessionRequest) -> ConversationSession:
        pending = self._pending_agent_approvals.get(request.session_id)
        if pending is not None and self._agent is not None:
            self._agent.cancel(
                CancelAgentRunRequest(
                    session_id=request.session_id,
                    task_id=pending.task_id,
                    trace_id=pending.trace_id,
                )
            )
        with self._lock:
            session = self._find(request.session_id)
            updated = session.model_copy(
                update={
                    "messages": [],
                    "active_product_id": None,
                    "ocr_draft": None,
                    "agent_status": "idle",
                    "pending_approval": None,
                    "updated_at": self._clock.now(),
                }
            )
            self._sessions[session.session_id] = updated
            self._pending_agent_approvals.pop(session.session_id, None)
            self._resolved_agent_approvals.pop(session.session_id, None)
            return updated.model_copy(deep=True)

    def create_ocr_draft(self, request: CreateOcrDraftRequest) -> ConversationSession:
        if self._ocr_engine is None:
            raise PendingOcrDraftError("OCR 服务不可用")
        with self._lock:
            session = self._find(request.session_id)
            if session.ocr_draft is not None:
                raise PendingOcrDraftError(
                    "每个会话最多保留一个 OCR 草稿",
                    details={"session_id": request.session_id},
                )
            extraction = self._ocr_engine.extract(request.extraction)
            now = self._clock.now()
            draft = OcrDraft(
                draft_id=new_id("ocr-draft"),
                image_sha256=extraction.image_sha256,
                mime_type=extraction.mime_type,
                width=extraction.width,
                height=extraction.height,
                engine=extraction.engine,
                model=extraction.model,
                parser=extraction.parser,
                records=[
                    OcrDraftRecord(
                        record_id=new_id("ocr-record"),
                        product_name=item.product_name,
                        record_date=item.record_date,
                        amount=item.amount,
                        confidence=item.confidence,
                        warnings=item.warnings,
                    )
                    for item in extraction.records
                ],
                warnings=extraction.warnings,
                created_at=now,
            )
            updated = session.model_copy(update={"ocr_draft": draft, "updated_at": now})
            self._sessions[session.session_id] = updated
            return updated.model_copy(deep=True)

    def discard_ocr_draft(self, request: DiscardOcrDraftRequest) -> ConversationSession:
        with self._lock:
            session = self._find(request.session_id)
            if session.ocr_draft is None or session.ocr_draft.draft_id != request.draft_id:
                raise PendingOcrDraftError(
                    "OCR 草稿不存在或已处理",
                    details={
                        "session_id": request.session_id,
                        "draft_id": request.draft_id,
                    },
                )
            updated = session.model_copy(
                update={"ocr_draft": None, "updated_at": self._clock.now()}
            )
            self._sessions[session.session_id] = updated
            return updated.model_copy(deep=True)

    def update_ocr_draft_record(
        self, request: UpdateOcrDraftRecordRequest
    ) -> ConversationSession:
        with self._lock:
            session = self._find(request.session_id)
            draft = self._require_ocr_draft_record(
                session.session_id, session.ocr_draft, request.draft_id, request.record_id
            )[0]
            records = [
                record.model_copy(
                    update=request.values.model_dump(mode="python")
                    if record.record_id == request.record_id
                    else {}
                )
                for record in draft.records
            ]
            now = self._clock.now()
            updated = session.model_copy(
                update={
                    "ocr_draft": draft.model_copy(update={"records": records}),
                    "updated_at": now,
                }
            )
            self._sessions[session.session_id] = updated
            return updated.model_copy(deep=True)

    def delete_ocr_draft_record(
        self, request: DeleteOcrDraftRecordRequest
    ) -> ConversationSession:
        with self._lock:
            session = self._find(request.session_id)
            draft = self._require_ocr_draft_record(
                session.session_id, session.ocr_draft, request.draft_id, request.record_id
            )[0]
            records = [record for record in draft.records if record.record_id != request.record_id]
            now = self._clock.now()
            updated = session.model_copy(
                update={
                    "ocr_draft": draft.model_copy(update={"records": records}),
                    "updated_at": now,
                }
            )
            self._sessions[session.session_id] = updated
            return updated.model_copy(deep=True)

    def select_product(self, request: SelectProductRequest) -> ConversationSession:
        if request.product_id is not None and self._portfolio_reader is not None:
            self._portfolio_reader.get_product(request.product_id)
        with self._lock:
            session = self._find(request.session_id)
            updated = session.model_copy(
                update={
                    "active_product_id": request.product_id,
                    "updated_at": self._clock.now(),
                }
            )
            self._sessions[session.session_id] = updated
            return updated.model_copy(deep=True)

    def send_agent_message(self, request: SendAgentMessageRequest) -> ConversationTurnResult:
        if self._agent is None or self._portfolio_reader is None:
            raise ConversationError("Agent 会话依赖尚未配置")
        session = self.get(GetSessionRequest(session_id=request.session_id))
        if session.agent_status == "awaiting_approval":
            raise ConversationBusyError("请先确认或拒绝当前待处理操作")
        revision = self._portfolio_reader.snapshot().revision
        self.append(
            AppendMessageRequest(
                session_id=session.session_id,
                role="user",
                content=request.content,
            )
        )
        result = self._agent.run(
            RunAgentRequest(
                session_id=session.session_id,
                correlation_id=request.correlation_id,
                question=request.content,
                history=[
                    AgentHistoryMessage(role=item.role, content=item.content)
                    for item in session.messages[-20:]
                ],
                active_product_id=session.active_product_id,
                config=request.config,
                granted_permissions=[],
                expected_ledger_revision=revision,
            )
        )
        if self._telemetry is not None:
            self._telemetry.record(
                RecordEventRequest(
                    event=GovernanceEvent(
                        event_id=new_id("agent-trace"),
                        category="audit",
                        name="agent.run",
                        occurred_at=self._clock.now(),
                        correlation_id=request.correlation_id,
                        payload={
                            "status": result.status,
                            "question_sha256": sha256_json({"question": request.content}),
                            "ledger_revision": revision,
                            "plan_hash": (
                                result.plan.plan_hash if result.plan is not None else None
                            ),
                            "tool_traces": [
                                {
                                    "tool_name": trace.tool_name,
                                    "status": trace.status,
                                }
                                for trace in result.tool_traces
                            ],
                            "model_usages": [
                                usage.model_dump(mode="json") for usage in result.model_usages
                            ],
                        },
                    )
                )
            )
        updated = self._apply_agent_result(session.session_id, result)
        return ConversationTurnResult(status=result.status, session=updated)

    @trace_call(
        layer="capability",
        trace_resolver=lambda self, request: self._pending_agent_approvals.get(
            request.session_id
        ).trace_id
        if self._pending_agent_approvals.get(request.session_id) is not None
        else None,
    )
    def respond_approval(self, request: RespondApprovalRequest) -> ConversationTurnResult:
        if self._agent is None:
            raise ConversationError("Agent 会话依赖尚未配置")
        session = self.get(GetSessionRequest(session_id=request.session_id))
        receipt = self._resolved_agent_approvals.get(request.session_id, {}).get(
            request.approval_id
        )
        if receipt is not None:
            status = (
                "awaiting_approval"
                if session.agent_status == "awaiting_approval"
                else receipt.status
            )
            return ConversationTurnResult(status=status, session=session)
        pending = self._pending_agent_approvals.get(request.session_id)
        if (
            session.pending_approval is None
            or pending is None
            or pending.approval_id != request.approval_id
        ):
            raise ConversationBusyError("确认不存在或已经处理")
        result = self._agent.respond_approval(
            RespondAgentApprovalRequest(
                session_id=request.session_id,
                task_id=pending.task_id,
                trace_id=pending.trace_id,
                approval_id=request.approval_id,
                decision=request.decision,
                config=request.config,
            )
        )
        self._resolved_agent_approvals.setdefault(request.session_id, {})[
            request.approval_id
        ] = result
        updated = self._apply_agent_result(request.session_id, result)
        return ConversationTurnResult(status=result.status, session=updated)

    def _apply_agent_result(self, session_id: str, result: RunAgentResult) -> ConversationSession:
        if result.status == "awaiting_approval" and result.pending_approval is not None:
            with self._lock:
                session = self._find(session_id)
                self._pending_agent_approvals[session_id] = result.pending_approval
                updated = session.model_copy(
                    update={
                        "agent_status": "awaiting_approval",
                        "pending_approval": ConversationApprovalCard.from_agent(
                            result.pending_approval
                        ),
                        "updated_at": self._clock.now(),
                    }
                )
                self._sessions[session_id] = updated
                return updated.model_copy(deep=True)
        answer = (
            result.answer.answer
            if result.answer is not None
            else result.error.message
            if result.error is not None
            else "Agent 未返回结果"
        )
        with self._lock:
            session = self._find(session_id)
            cleared = session.model_copy(
                update={
                    "agent_status": "idle",
                    "pending_approval": None,
                    "updated_at": self._clock.now(),
                }
            )
            self._sessions[session_id] = cleared
            self._pending_agent_approvals.pop(session_id, None)
        return self.append(
            AppendMessageRequest(
                session_id=session_id,
                role="assistant",
                content=answer,
            )
        )

    def confirm_ocr_draft(self, request: ConfirmOcrDraftRequest) -> ConfirmOcrDraftResult:
        if self._portfolio_writer is None:
            raise ConversationError("资产写入依赖尚未配置")
        session = self.get(GetSessionRequest(session_id=request.session_id))
        draft = session.ocr_draft
        if draft is None or draft.draft_id != request.draft_id:
            raise PendingOcrDraftError("OCR 草稿不存在或已处理")
        if not draft.records:
            raise PendingOcrDraftError(
                "OCR 草稿没有可写入记录",
                details={"draft_id": draft.draft_id},
            )
        revision = request.expected_revision
        if self._portfolio_batch_writer is not None:
            records = []
            for record in draft.records:
                if record.product_name is None or record.amount is None:
                    raise PendingOcrDraftError(
                        "OCR 草稿存在未补全记录",
                        details={"record_id": record.record_id},
                    )
                records.append(
                    AddRecordInput(
                        product_name=record.product_name,
                        record_date=record.record_date,
                        record_kind=RecordKind(record.kind),
                        amount=record.amount,
                        source_text=f"OCR 草稿 {draft.draft_id}",
                    )
                )
            batch = self._portfolio_batch_writer.execute_batch(
                AddRecordsBatchRequest(
                    records=records,
                    expected_revision=revision,
                    idempotency_key=request.idempotency_key_prefix,
                )
            )
            updated = self.discard_ocr_draft(
                DiscardOcrDraftRequest(session_id=session.session_id, draft_id=draft.draft_id)
            )
            return ConfirmOcrDraftResult(
                session=updated,
                mutations=batch.mutations,
            )
        mutations = []
        for record in draft.records:
            if record.product_name is None or record.amount is None:
                raise PendingOcrDraftError(
                    "OCR 草稿存在未补全记录",
                    details={"record_id": record.record_id},
                )
            mutation = self._portfolio_writer.execute(
                AddRecord(
                    product_name=record.product_name,
                    record_date=record.record_date,
                    record_kind=RecordKind(record.kind),
                    amount=record.amount,
                    source_text=f"OCR 草稿 {draft.draft_id}",
                    expected_revision=revision,
                    idempotency_key=(f"{request.idempotency_key_prefix}:{record.record_id}"),
                )
            )
            mutations.append(mutation)
            revision = mutation.ledger_revision
        updated = self.discard_ocr_draft(
            DiscardOcrDraftRequest(session_id=session.session_id, draft_id=draft.draft_id)
        )
        return ConfirmOcrDraftResult(session=updated, mutations=mutations)

    @staticmethod
    def _require_ocr_draft_record(
        session_id: str,
        draft: OcrDraft | None,
        draft_id: str,
        record_id: str,
    ) -> tuple[OcrDraft, OcrDraftRecord]:
        if draft is None or draft.draft_id != draft_id:
            raise PendingOcrDraftError(
                "OCR 草稿不存在或已处理",
                details={
                    "session_id": session_id,
                    "draft_id": draft_id,
                    "record_id": record_id,
                },
            )
        for record in draft.records:
            if record.record_id == record_id:
                return draft, record
        raise PendingOcrDraftError(
            "OCR 草稿记录不存在或已处理",
            details={
                "session_id": session_id,
                "draft_id": draft_id,
                "record_id": record_id,
            },
        )

    def _find(self, session_id: str) -> ConversationSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(
                "对话会话不存在或服务已重启",
                details={"session_id": session_id},
            )
        return session
