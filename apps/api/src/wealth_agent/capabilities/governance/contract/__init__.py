"""Structured governance, telemetry, audit, and evaluation contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails


class GovernanceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorizationRequest(GovernanceContract):
    actor: Literal["agent", "page"]
    operation: str = Field(min_length=1, max_length=100)
    requested_permissions: list[str] = Field(default_factory=list)
    side_effects: Literal["none", "ledger_write", "backup_restore"]
    confirmed: bool = False


class AuthorizationResult(GovernanceContract):
    decision: Literal["allowed", "confirmation_required", "denied"]
    granted_permissions: list[str] = Field(default_factory=list)
    rule_version: Literal["governance-v2"] = "governance-v2"
    reason: str = Field(min_length=1)


class UserToolApproval(GovernanceContract):
    approval_id: str = Field(min_length=1)
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approve", "reject"]


class ToolAuthorizationRequest(GovernanceContract):
    actor: Literal["agent"] = "agent"
    task_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1, max_length=100)
    requested_permissions: list[str] = Field(default_factory=list)
    side_effects: Literal["none", "ledger_write"]
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval: UserToolApproval | None = None


class GovernanceEvent(GovernanceContract):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    category: Literal["model", "tool", "task", "audit", "evaluation"]
    name: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    correlation_id: str | None = None
    payload: JsonValue


class RecordEventRequest(GovernanceContract):
    event: GovernanceEvent


class RecordEventResult(GovernanceContract):
    event: GovernanceEvent
    created: bool
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ListEventsRequest(GovernanceContract):
    category: Literal["model", "tool", "task", "audit", "evaluation"] | None = None
    limit: int = Field(default=100, ge=1, le=500)


class ListEventsResult(GovernanceContract):
    events: list[GovernanceEvent] = Field(default_factory=list)


class EvaluationCase(GovernanceContract):
    case_id: str = Field(min_length=1)
    expected: JsonValue
    actual: JsonValue


class EvaluationRequest(GovernanceContract):
    evaluation_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=1_000)


class EvaluationCaseResult(GovernanceContract):
    case_id: str
    passed: bool


class EvaluationResult(GovernanceContract):
    evaluation_id: str
    dataset_version: str
    scorer_version: Literal["exact-json-v1"] = "exact-json-v1"
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    cases: list[EvaluationCaseResult]

    @model_validator(mode="after")
    def validate_counts(self) -> EvaluationResult:
        if self.passed + self.failed != len(self.cases):
            raise ValueError("评测汇总与用例数量不一致")
        return self


class GovernanceError(RuntimeError):
    code = "governance_error"

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


class GovernancePersistenceError(GovernanceError):
    code = "governance_persistence_error"


class GovernanceService(Protocol):
    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult: ...

    def authorize_tool(self, request: ToolAuthorizationRequest) -> AuthorizationResult: ...


class TelemetryRepository(Protocol):
    def record(self, request: RecordEventRequest) -> RecordEventResult: ...

    def list(self, request: ListEventsRequest) -> ListEventsResult: ...


class EvaluationService(Protocol):
    def evaluate(self, request: EvaluationRequest) -> EvaluationResult: ...


__all__ = [
    "AuthorizationRequest",
    "AuthorizationResult",
    "EvaluationCase",
    "EvaluationCaseResult",
    "EvaluationRequest",
    "EvaluationResult",
    "EvaluationService",
    "GovernanceContract",
    "GovernanceError",
    "GovernanceEvent",
    "GovernancePersistenceError",
    "GovernanceService",
    "ListEventsRequest",
    "ListEventsResult",
    "RecordEventRequest",
    "RecordEventResult",
    "TelemetryRepository",
    "ToolAuthorizationRequest",
    "UserToolApproval",
]
