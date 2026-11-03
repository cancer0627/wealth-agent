"""Structured conversation and OCR draft public contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator
from wealth_agent.capabilities.agent.contract import (
    AgentModelConfig,
    AgentPendingApproval,
)
from wealth_agent.capabilities.portfolio.contract import (
    MutationResult,
    RiskLevel,
    normalize_positive_money,
)
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails


class ConversationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationMessage(ConversationContract):
    message_id: str = Field(min_length=1)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    created_at: datetime

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


class OcrFieldConfidence(ConversationContract):
    product_name: float = Field(ge=0, le=1)
    record_date: float = Field(ge=0, le=1)
    amount: float = Field(ge=0, le=1)


class OcrRecordCandidate(ConversationContract):
    product_name: str | None = None
    record_date: date
    amount: Decimal | None = Field(default=None, gt=0)
    confidence: OcrFieldConfidence
    warnings: list[str] = Field(default_factory=list)


class OcrExtractionRequest(ConversationContract):
    content: bytes = Field(min_length=1)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    default_date: date


class OcrExtraction(ConversationContract):
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    orientation_degrees: Literal[0, 90, 180, 270]
    engine: str = Field(min_length=1)
    model: str = Field(min_length=1)
    parser: str = Field(min_length=1)
    records: list[OcrRecordCandidate] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list)


class OcrDraftRecord(OcrRecordCandidate):
    record_id: str = Field(min_length=1)
    kind: Literal["valuation", "transfer_in", "transfer_out"] = "valuation"


class OcrDraft(ConversationContract):
    draft_id: str = Field(min_length=1)
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    engine: str = Field(min_length=1)
    model: str = Field(min_length=1)
    parser: str = Field(min_length=1)
    records: list[OcrDraftRecord] = Field(default_factory=list, max_length=50)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class ConversationSession(ConversationContract):
    session_id: str = Field(min_length=1)
    messages: list[ConversationMessage] = Field(default_factory=list)
    active_product_id: str | None = None
    ocr_draft: OcrDraft | None = None
    agent_status: Literal["idle", "awaiting_approval"] = "idle"
    pending_approval: ConversationApprovalCard | None = None
    created_at: datetime
    updated_at: datetime


class CreateSessionRequest(ConversationContract):
    pass


class GetSessionRequest(ConversationContract):
    session_id: str = Field(min_length=1)


class AppendMessageRequest(GetSessionRequest):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


class ResetSessionRequest(GetSessionRequest):
    pass


class CreateOcrDraftRequest(GetSessionRequest):
    extraction: OcrExtractionRequest


class DiscardOcrDraftRequest(GetSessionRequest):
    draft_id: str = Field(min_length=1)


class OcrDraftRecordUpdate(ConversationContract):
    product_name: str = Field(min_length=1, max_length=200)
    record_date: date
    amount: Decimal

    @field_validator("product_name")
    @classmethod
    def normalize_product_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("产品名称不能为空")
        return normalized

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class UpdateOcrDraftRecordRequest(GetSessionRequest):
    draft_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    values: OcrDraftRecordUpdate


class DeleteOcrDraftRecordRequest(GetSessionRequest):
    draft_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)


class SelectProductRequest(GetSessionRequest):
    product_id: str | None = Field(default=None, min_length=1)


class SendAgentMessageRequest(GetSessionRequest):
    correlation_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4_000)
    config: AgentModelConfig


class ConversationTurnResult(ConversationContract):
    status: Literal["answered", "needs_clarification", "awaiting_approval", "failed"]
    session: ConversationSession


class ConversationApprovalCard(ConversationContract):
    approval_id: str = Field(min_length=1)
    tool_name: Literal["add_ledger_record", "update_product_profile"]
    summary: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)
    product_name: str | None = Field(default=None, min_length=1)
    product_id: str | None = Field(default=None, min_length=1)
    record_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    record_kind: Literal["valuation", "transfer_in", "transfer_out"] | None = None
    amount: str | None = Field(default=None, min_length=1)
    product_type: str | None = Field(default=None, min_length=1)
    risk_level: RiskLevel | None = None
    performance_benchmark: str | None = Field(default=None, min_length=1)

    @classmethod
    def from_agent(cls, value: AgentPendingApproval) -> ConversationApprovalCard:
        return cls.model_validate(
            value.model_dump(exclude={"task_id", "trace_id", "action_hash"})
        )


class RespondApprovalRequest(GetSessionRequest):
    approval_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    config: AgentModelConfig


class ConfirmOcrDraftRequest(GetSessionRequest):
    draft_id: str = Field(min_length=1)
    expected_revision: str = Field(min_length=1)
    idempotency_key_prefix: str = Field(min_length=1, max_length=120)
    confirmed: Literal[True]


class ConfirmOcrDraftResult(ConversationContract):
    session: ConversationSession
    mutations: list[MutationResult] = Field(default_factory=list)


class ConversationError(RuntimeError):
    code = "conversation_error"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        details: ErrorDetails | None = None,
    ) -> None:
        self.detail = ErrorDetail(
            code=self.code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        super().__init__(message)


class SessionNotFoundError(ConversationError):
    code = "session_not_found"


class PendingOcrDraftError(ConversationError):
    code = "pending_ocr_draft"


class ConversationBusyError(ConversationError):
    code = "conversation_busy"


class ConversationPersistenceError(ConversationError):
    code = "conversation_persistence_error"


class OcrEngine(Protocol):
    def extract(self, request: OcrExtractionRequest) -> OcrExtraction: ...


class ConversationService(Protocol):
    def create(self, request: CreateSessionRequest) -> ConversationSession: ...

    def get(self, request: GetSessionRequest) -> ConversationSession: ...

    def append(self, request: AppendMessageRequest) -> ConversationSession: ...

    def reset(self, request: ResetSessionRequest) -> ConversationSession: ...

    def create_ocr_draft(self, request: CreateOcrDraftRequest) -> ConversationSession: ...

    def discard_ocr_draft(self, request: DiscardOcrDraftRequest) -> ConversationSession: ...

    def update_ocr_draft_record(
        self, request: UpdateOcrDraftRecordRequest
    ) -> ConversationSession: ...

    def delete_ocr_draft_record(
        self, request: DeleteOcrDraftRecordRequest
    ) -> ConversationSession: ...

    def select_product(self, request: SelectProductRequest) -> ConversationSession: ...

    def send_agent_message(self, request: SendAgentMessageRequest) -> ConversationTurnResult: ...

    def respond_approval(self, request: RespondApprovalRequest) -> ConversationTurnResult: ...

    def confirm_ocr_draft(self, request: ConfirmOcrDraftRequest) -> ConfirmOcrDraftResult: ...


__all__ = [
    "AppendMessageRequest",
    "ConversationError",
    "ConversationApprovalCard",
    "ConversationBusyError",
    "ConversationPersistenceError",
    "ConversationTurnResult",
    "ConversationMessage",
    "ConversationService",
    "ConversationSession",
    "CreateOcrDraftRequest",
    "CreateSessionRequest",
    "ConfirmOcrDraftRequest",
    "ConfirmOcrDraftResult",
    "DeleteOcrDraftRecordRequest",
    "DiscardOcrDraftRequest",
    "GetSessionRequest",
    "OcrDraft",
    "OcrDraftRecord",
    "OcrDraftRecordUpdate",
    "OcrEngine",
    "OcrExtraction",
    "OcrExtractionRequest",
    "OcrFieldConfidence",
    "OcrRecordCandidate",
    "PendingOcrDraftError",
    "ResetSessionRequest",
    "RespondApprovalRequest",
    "SelectProductRequest",
    "SendAgentMessageRequest",
    "SessionNotFoundError",
    "UpdateOcrDraftRecordRequest",
]
