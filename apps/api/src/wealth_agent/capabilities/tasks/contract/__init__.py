"""Structured task plans, state, execution, and control contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails
from wealth_agent.shared.serialization import sha256_json

TaskKind = Literal["workflow", "analysis"]
TaskStatus = Literal[
    "awaiting_confirmation",
    "queued",
    "running",
    "pause_requested",
    "paused",
    "cancel_requested",
    "cancelled",
    "completed",
    "needs_attention",
    "failed",
    "invalidated_by_restore",
]
StepStatus = Literal["pending", "running", "completed", "failed", "blocked"]
TerminalTaskStatus = Literal["cancelled", "completed", "failed", "invalidated_by_restore"]
TERMINAL_TASK_STATUSES = frozenset({"cancelled", "completed", "failed", "invalidated_by_restore"})


class TaskContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskPayload(TaskContract):
    schema_version: int = Field(default=1, ge=1)
    type: str = Field(min_length=1, max_length=100)
    data: JsonValue


class TaskStep(TaskContract):
    step_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=100)
    arguments: TaskPayload
    description: str = Field(min_length=1, max_length=1_000)
    side_effects: list[str] = Field(default_factory=list)


class AgentTaskStep(TaskContract):
    step_id: str = Field(min_length=1, max_length=64)
    objective: str = Field(min_length=1, max_length=1_000)
    success_criteria: str = Field(min_length=1, max_length=1_000)
    allowed_tools: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_allowed_tools(self) -> AgentTaskStep:
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("任务允许的工具不得重复")
        return self


class TaskPlan(TaskContract):
    schema_version: Literal[1] = 1
    source_text: str = Field(min_length=1, max_length=4_000)
    steps: list[TaskStep] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_unique_step_ids(self) -> TaskPlan:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("任务步骤标识不得重复")
        return self

    @property
    def plan_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class TaskPlanV2(TaskContract):
    schema_version: Literal[2] = 2
    source_text: str = Field(min_length=1, max_length=4_000)
    steps: list[AgentTaskStep] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def validate_unique_step_ids(self) -> TaskPlanV2:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("任务步骤标识不得重复")
        return self

    @property
    def plan_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


TaskPlanType = Annotated[TaskPlan | TaskPlanV2, Field(discriminator="schema_version")]
TaskStepDefinition = TaskStep | AgentTaskStep


class TaskStepResult(TaskContract):
    step_id: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    output: TaskPayload | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> TaskStepResult:
        if self.status == "completed" and self.error is not None:
            raise ValueError("成功步骤不能包含错误")
        if self.status == "failed" and self.error is None:
            raise ValueError("失败步骤必须包含错误")
        return self


class TaskStepState(TaskContract):
    step_id: str
    status: StepStatus = "pending"
    attempt: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: TaskStepResult | None = None


class TaskCheckpoint(TaskContract):
    schema_version: Literal[1] = 1
    next_step_index: int = Field(default=0, ge=0)
    completed_step_ids: list[str] = Field(default_factory=list)
    ledger_revision: str = Field(min_length=1)
    executor_state: TaskPayload | None = None


class TaskPendingApproval(TaskContract):
    approval_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: JsonValue
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    ledger_revision: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)
    approved: bool = False


class TaskResult(TaskContract):
    task_id: str
    correlation_id: str
    status: TerminalTaskStatus
    steps: list[TaskStepResult] = Field(default_factory=list)
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> TaskResult:
        if self.status == "failed" and self.error is None:
            raise ValueError("失败任务必须包含错误")
        return self


class TaskRecord(TaskContract):
    task_id: str
    trace_id: str = Field(min_length=1)
    correlation_id: str
    kind: TaskKind
    status: TaskStatus
    state_version: int = Field(default=1, ge=1)
    environment_epoch: int = Field(ge=1)
    plan: TaskPlanType
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_ledger_revision: str = Field(min_length=1)
    confirmed_ledger_revision: str | None = None
    current_step_index: int = Field(default=0, ge=0)
    steps: list[TaskStepState]
    checkpoint: TaskCheckpoint
    session_id: str | None = Field(default=None, min_length=1)
    agent_run_id: str | None = Field(default=None, min_length=1)
    pending_approval: TaskPendingApproval | None = None
    task_attempt: int = Field(default=0, ge=0)
    error: ErrorDetail | None = None
    result: TaskResult | None = None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None

    @model_validator(mode="after")
    def validate_state_shape(self) -> TaskRecord:
        plan_step_ids = [step.step_id for step in self.plan.steps]
        state_step_ids = [step.step_id for step in self.steps]
        if plan_step_ids != state_step_ids:
            raise ValueError("任务步骤状态必须与计划顺序一致")
        if self.current_step_index > len(self.steps):
            raise ValueError("当前步骤索引超出计划范围")
        if self.plan_hash != self.plan.plan_hash:
            raise ValueError("任务计划哈希不匹配")
        if self.result is not None and self.result.status != self.status:
            raise ValueError("任务结果状态与任务状态不一致")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES


class CreateTaskRequest(TaskContract):
    trace_id: str | None = Field(default=None, min_length=1)
    correlation_id: str = Field(min_length=1)
    kind: TaskKind
    plan: TaskPlanType
    current_ledger_revision: str = Field(min_length=1)
    environment_epoch: int = Field(ge=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    session_id: str | None = Field(default=None, min_length=1)
    agent_run_id: str | None = Field(default=None, min_length=1)
    requires_confirmation: bool = True


class CreateTaskResult(TaskContract):
    task: TaskRecord
    replayed: bool = False


class GetTaskRequest(TaskContract):
    task_id: str = Field(min_length=1)


class ListTasksRequest(TaskContract):
    limit: int = Field(default=100, ge=1, le=500)


class TaskListResult(TaskContract):
    tasks: list[TaskRecord] = Field(default_factory=list)


class ConfirmTaskRequest(GetTaskRequest):
    expected_state_version: int = Field(ge=1)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    current_ledger_revision: str = Field(min_length=1)


class ControlTaskRequest(GetTaskRequest):
    expected_state_version: int = Field(ge=1)


class ClaimTaskRequest(TaskContract):
    worker_id: str = Field(min_length=1)
    environment_epoch: int = Field(ge=1)
    current_ledger_revision: str = Field(min_length=1)
    task_id: str | None = Field(default=None, min_length=1)


class ClaimTaskResult(TaskContract):
    task: TaskRecord | None = None


class ExecuteStepRequest(TaskContract):
    task_id: str
    correlation_id: str
    step: TaskStepDefinition
    attempt: int = Field(ge=1, le=3)
    confirmed_ledger_revision: str = Field(min_length=1)
    checkpoint: TaskCheckpoint


class ApplyStepResultRequest(GetTaskRequest):
    expected_state_version: int = Field(ge=1)
    result: TaskStepResult
    next_ledger_revision: str | None = Field(default=None, min_length=1)
    executor_state: TaskPayload | None = None


class RequestTaskApprovalRequest(GetTaskRequest):
    expected_state_version: int = Field(ge=1)
    pending_approval: TaskPendingApproval
    executor_state: TaskPayload


class ResolveTaskApprovalRequest(GetTaskRequest):
    expected_state_version: int = Field(ge=1)
    approval_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    current_ledger_revision: str = Field(min_length=1)


class RecoverTasksRequest(TaskContract):
    environment_epoch: int = Field(ge=1)
    current_ledger_revision: str = Field(min_length=1)


class RecoverTasksResult(TaskContract):
    requeued_task_ids: list[str] = Field(default_factory=list)
    paused_task_ids: list[str] = Field(default_factory=list)
    cancelled_task_ids: list[str] = Field(default_factory=list)
    failed_task_ids: list[str] = Field(default_factory=list)
    invalidated_task_ids: list[str] = Field(default_factory=list)


class WorkerRunRequest(TaskContract):
    worker_id: str = Field(min_length=1)
    environment_epoch: int = Field(ge=1)
    current_ledger_revision: str = Field(min_length=1)


class WorkerRunResult(TaskContract):
    status: Literal["idle", "executed", "conflict"]
    task: TaskRecord | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def validate_worker_result(self) -> WorkerRunResult:
        if self.status == "idle" and (self.task is not None or self.error is not None):
            raise ValueError("空闲 Worker 结果不能包含任务或错误")
        if self.status == "executed" and self.task is None:
            raise ValueError("已执行 Worker 结果必须包含任务")
        if self.status == "conflict" and self.error is None:
            raise ValueError("冲突 Worker 结果必须包含错误")
        return self


class TaskEnvironmentRequest(TaskContract):
    pass


class TaskEnvironment(TaskContract):
    environment_epoch: int = Field(ge=1)
    current_ledger_revision: str = Field(min_length=1)


class WorkerStartRequest(TaskContract):
    pass


class WorkerWakeRequest(TaskContract):
    pass


class WorkerShutdownRequest(TaskContract):
    timeout_seconds: float = Field(default=30, ge=0, le=120)


class WorkerRuntimeState(TaskContract):
    status: Literal["stopped", "running", "stopping"]
    recovery: RecoverTasksResult | None = None
    last_run: WorkerRunResult | None = None


class TaskError(RuntimeError):
    code = "task_error"

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


class TaskNotFoundError(TaskError):
    code = "task_not_found"


class TaskStateConflictError(TaskError):
    code = "task_state_conflict"


class TaskPlanConflictError(TaskError):
    code = "task_plan_conflict"


class TaskIdempotencyConflictError(TaskError):
    code = "task_idempotency_conflict"


class TaskPersistenceError(TaskError):
    code = "task_persistence_error"


class ExecutorPort(Protocol):
    def execute(self, request: ExecuteStepRequest) -> TaskStepResult: ...


class TaskEnvironmentPort(Protocol):
    def read(self, request: TaskEnvironmentRequest) -> TaskEnvironment: ...


class TaskWorkerRuntimePort(Protocol):
    def start(self, request: WorkerStartRequest) -> WorkerRuntimeState: ...

    def wake(self, request: WorkerWakeRequest) -> WorkerRuntimeState: ...

    def shutdown(self, request: WorkerShutdownRequest) -> WorkerRuntimeState: ...


class TaskService(Protocol):
    def create(self, request: CreateTaskRequest) -> CreateTaskResult: ...

    def get(self, request: GetTaskRequest) -> TaskRecord: ...

    def list(self, request: ListTasksRequest) -> TaskListResult: ...

    def confirm(self, request: ConfirmTaskRequest) -> TaskRecord: ...

    def pause(self, request: ControlTaskRequest) -> TaskRecord: ...

    def resume(self, request: ControlTaskRequest) -> TaskRecord: ...

    def cancel(self, request: ControlTaskRequest) -> TaskRecord: ...

    def claim(self, request: ClaimTaskRequest) -> ClaimTaskResult: ...

    def apply_step_result(self, request: ApplyStepResultRequest) -> TaskRecord: ...

    def request_approval(self, request: RequestTaskApprovalRequest) -> TaskRecord: ...

    def resolve_approval(self, request: ResolveTaskApprovalRequest) -> TaskRecord: ...

    def recover_interrupted(self, request: RecoverTasksRequest) -> RecoverTasksResult: ...


__all__ = [
    "AgentTaskStep",
    "ApplyStepResultRequest",
    "ClaimTaskRequest",
    "ClaimTaskResult",
    "ConfirmTaskRequest",
    "ControlTaskRequest",
    "CreateTaskRequest",
    "CreateTaskResult",
    "ExecuteStepRequest",
    "ExecutorPort",
    "GetTaskRequest",
    "ListTasksRequest",
    "RecoverTasksRequest",
    "RecoverTasksResult",
    "RequestTaskApprovalRequest",
    "ResolveTaskApprovalRequest",
    "TaskCheckpoint",
    "TaskError",
    "TaskEnvironment",
    "TaskEnvironmentPort",
    "TaskEnvironmentRequest",
    "TaskIdempotencyConflictError",
    "TaskListResult",
    "TaskNotFoundError",
    "TaskPayload",
    "TaskPlan",
    "TaskPlanType",
    "TaskPlanV2",
    "TaskPlanConflictError",
    "TaskPersistenceError",
    "TaskRecord",
    "TaskResult",
    "TaskService",
    "TaskStateConflictError",
    "TaskStep",
    "TaskStepDefinition",
    "TaskStepResult",
    "TaskStepState",
    "TaskPendingApproval",
    "TaskWorkerRuntimePort",
    "TerminalTaskStatus",
    "WorkerRuntimeState",
    "WorkerRunRequest",
    "WorkerRunResult",
    "WorkerShutdownRequest",
    "WorkerStartRequest",
    "WorkerWakeRequest",
]
