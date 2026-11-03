"""SQLite-backed conversation sessions and pending Agent approvals."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from wealth_agent.capabilities.agent.contract import (
    AgentPendingApproval,
    AgentService,
    GetAgentRunRequest,
    RunAgentResult,
)
from wealth_agent.capabilities.conversation.contract import (
    AppendMessageRequest,
    ConfirmOcrDraftRequest,
    ConfirmOcrDraftResult,
    ConversationPersistenceError,
    ConversationSession,
    ConversationTurnResult,
    CreateOcrDraftRequest,
    CreateSessionRequest,
    DeleteOcrDraftRecordRequest,
    DiscardOcrDraftRequest,
    GetSessionRequest,
    OcrEngine,
    ResetSessionRequest,
    RespondApprovalRequest,
    SelectProductRequest,
    SendAgentMessageRequest,
    UpdateOcrDraftRecordRequest,
)
from wealth_agent.capabilities.conversation.implementation.in_memory_service import (
    InMemoryConversationService,
)
from wealth_agent.capabilities.governance.contract import (
    GovernanceService,
    TelemetryRepository,
)
from wealth_agent.capabilities.portfolio.contract import (
    PortfolioBatchWriter,
    PortfolioReader,
    PortfolioWriter,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.logging import trace_call, trace_public_methods
from wealth_agent.shared.serialization import canonical_json

CONVERSATION_APPLICATION_ID = 0x57414356
CONVERSATION_SCHEMA_VERSION = 1


class _StoredConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session: ConversationSession
    pending_agent_approval: AgentPendingApproval | None = None
    resolved_agent_approvals: dict[str, RunAgentResult] = Field(default_factory=dict)


@trace_public_methods(layer="capability")
class SqliteConversationService(InMemoryConversationService):
    def __init__(
        self,
        database_path: Path,
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
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            clock,
            ocr_engine,
            agent=agent,
            governance=governance,
            telemetry=telemetry,
            portfolio_reader=portfolio_reader,
            portfolio_writer=portfolio_writer,
            portfolio_batch_writer=portfolio_batch_writer,
        )
        self._initialize_database()
        self._load_sessions()

    def create(self, request: CreateSessionRequest) -> ConversationSession:
        result = super().create(request)
        self._save(result.session_id)
        return result

    def get(self, request: GetSessionRequest) -> ConversationSession:
        result = super().get(request)
        pending = self._pending_agent_approvals.get(result.session_id)
        if pending is None or self._agent is None:
            return result
        run = self._agent.get_run(
            GetAgentRunRequest(
                session_id=result.session_id,
                task_id=pending.task_id,
                trace_id=pending.trace_id,
            )
        )
        if run.status == "awaiting_approval":
            return result
        reconciled = self._apply_agent_result(result.session_id, run)
        self._save(result.session_id)
        return reconciled

    def append(self, request: AppendMessageRequest) -> ConversationSession:
        result = super().append(request)
        self._save(result.session_id)
        return result

    def reset(self, request: ResetSessionRequest) -> ConversationSession:
        result = super().reset(request)
        self._save(result.session_id)
        return result

    def create_ocr_draft(self, request: CreateOcrDraftRequest) -> ConversationSession:
        result = super().create_ocr_draft(request)
        self._save(result.session_id)
        return result

    def discard_ocr_draft(self, request: DiscardOcrDraftRequest) -> ConversationSession:
        result = super().discard_ocr_draft(request)
        self._save(result.session_id)
        return result

    def update_ocr_draft_record(
        self, request: UpdateOcrDraftRecordRequest
    ) -> ConversationSession:
        result = super().update_ocr_draft_record(request)
        self._save(result.session_id)
        return result

    def delete_ocr_draft_record(
        self, request: DeleteOcrDraftRecordRequest
    ) -> ConversationSession:
        result = super().delete_ocr_draft_record(request)
        self._save(result.session_id)
        return result

    def select_product(self, request: SelectProductRequest) -> ConversationSession:
        result = super().select_product(request)
        self._save(result.session_id)
        return result

    def send_agent_message(self, request: SendAgentMessageRequest) -> ConversationTurnResult:
        result = super().send_agent_message(request)
        self._save(result.session.session_id)
        return result

    @trace_call(
        layer="capability",
        trace_resolver=lambda self, request: self._pending_agent_approvals.get(
            request.session_id
        ).trace_id
        if self._pending_agent_approvals.get(request.session_id) is not None
        else None,
    )
    def respond_approval(self, request: RespondApprovalRequest) -> ConversationTurnResult:
        result = super().respond_approval(request)
        self._save(result.session.session_id)
        return result

    def confirm_ocr_draft(self, request: ConfirmOcrDraftRequest) -> ConfirmOcrDraftResult:
        result = super().confirm_ocr_draft(request)
        self._save(result.session.session_id)
        return result

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=4)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 4000")
        return connection

    def _initialize_database(self) -> None:
        try:
            with closing(self._connect()) as connection, connection:
                application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if application_id not in {0, CONVERSATION_APPLICATION_ID}:
                    raise ConversationPersistenceError("conversation.db application_id 不兼容")
                if user_version not in {0, CONVERSATION_SCHEMA_VERSION}:
                    raise ConversationPersistenceError("conversation.db Schema 版本不兼容")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute(f"PRAGMA application_id = {CONVERSATION_APPLICATION_ID}")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        session_id TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL
                    )
                    """
                )
                connection.execute(f"PRAGMA user_version = {CONVERSATION_SCHEMA_VERSION}")
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                if integrity != "ok":
                    raise ConversationPersistenceError("conversation.db 完整性检查失败")
        except ConversationPersistenceError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ConversationPersistenceError("初始化会话数据库失败") from exc

    def _load_sessions(self) -> None:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT session_id, payload_json, payload_sha256 FROM conversations"
                ).fetchall()
            for row in rows:
                payload_json = str(row["payload_json"])
                if self._hash(payload_json) != str(row["payload_sha256"]):
                    raise ConversationPersistenceError(
                        "会话记录哈希不一致",
                        details={"session_id": str(row["session_id"])},
                    )
                stored = _StoredConversation.model_validate_json(payload_json)
                self._sessions[stored.session.session_id] = stored.session
                if stored.pending_agent_approval is not None:
                    self._pending_agent_approvals[stored.session.session_id] = (
                        stored.pending_agent_approval
                    )
                self._resolved_agent_approvals[stored.session.session_id] = dict(
                    stored.resolved_agent_approvals
                )
        except (ValidationError, OSError, sqlite3.DatabaseError) as exc:
            raise ConversationPersistenceError("读取持久会话失败") from exc

    def _save(self, session_id: str) -> None:
        with self._lock:
            session = self._find(session_id).model_copy(deep=True)
            pending = self._pending_agent_approvals.get(session_id)
            resolved = dict(self._resolved_agent_approvals.get(session_id, {}))
        payload_json = canonical_json(
            _StoredConversation(
                session=session,
                pending_agent_approval=pending,
                resolved_agent_approvals=resolved,
            ).model_dump(mode="json")
        )
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO conversations (
                        session_id, updated_at, payload_json, payload_sha256
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        updated_at = excluded.updated_at,
                        payload_json = excluded.payload_json,
                        payload_sha256 = excluded.payload_sha256
                    """,
                    (
                        session.session_id,
                        session.updated_at.isoformat(),
                        payload_json,
                        self._hash(payload_json),
                    ),
                )
        except (OSError, sqlite3.DatabaseError) as exc:
            raise ConversationPersistenceError(
                "保存持久会话失败", details={"session_id": session_id}
            ) from exc

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["SqliteConversationService"]
