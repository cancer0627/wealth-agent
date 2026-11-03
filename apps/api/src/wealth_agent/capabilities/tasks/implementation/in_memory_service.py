"""In-memory task state machine used before SQLite persistence is introduced."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from wealth_agent.capabilities.tasks.contract import (
    ApplyStepResultRequest,
    ClaimTaskRequest,
    ClaimTaskResult,
    ConfirmTaskRequest,
    ControlTaskRequest,
    CreateTaskRequest,
    CreateTaskResult,
    GetTaskRequest,
    ListTasksRequest,
    RecoverTasksRequest,
    RecoverTasksResult,
    RequestTaskApprovalRequest,
    ResolveTaskApprovalRequest,
    TaskCheckpoint,
    TaskListResult,
    TaskNotFoundError,
    TaskPlanConflictError,
    TaskRecord,
    TaskResult,
    TaskStateConflictError,
    TaskStepResult,
    TaskStepState,
    TerminalTaskStatus,
)
from wealth_agent.capabilities.tasks.implementation.state_store import (
    InMemoryTaskStateStore,
    StoreCreateRequest,
    StoreSaveRequest,
    TaskStateStore,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.identity import new_id
from wealth_agent.shared.logging import current_trace_id, trace_public_methods
from wealth_agent.shared.serialization import sha256_json

MAX_STEP_ATTEMPTS = 3


@trace_public_methods(layer="capability")
class TaskStateMachineService:
    def __init__(self, clock: Clock, store: TaskStateStore) -> None:
        self._clock = clock
        self._store = store
        self._lock = RLock()

    def create(self, request: CreateTaskRequest) -> CreateTaskResult:
        request_hash = sha256_json(
            request.model_dump(mode="json", exclude={"idempotency_key", "trace_id"})
        )
        with self._lock:
            now = self._clock.now()
            task = TaskRecord(
                task_id=new_id("task"),
                trace_id=request.trace_id or current_trace_id(),
                correlation_id=request.correlation_id,
                kind=request.kind,
                status=("awaiting_confirmation" if request.requires_confirmation else "queued"),
                environment_epoch=request.environment_epoch,
                plan=request.plan,
                plan_hash=request.plan.plan_hash,
                current_ledger_revision=request.current_ledger_revision,
                steps=[TaskStepState(step_id=step.step_id) for step in request.plan.steps],
                checkpoint=TaskCheckpoint(
                    ledger_revision=request.current_ledger_revision,
                ),
                session_id=request.session_id,
                agent_run_id=request.agent_run_id,
                confirmed_ledger_revision=(
                    None if request.requires_confirmation else request.current_ledger_revision
                ),
                created_at=now,
                updated_at=now,
            )
            stored = self._store.create(
                StoreCreateRequest(
                    task=task,
                    idempotency_key=request.idempotency_key,
                    request_hash=request_hash,
                )
            )
            return CreateTaskResult(task=stored.task, replayed=stored.replayed)

    def get(self, request: GetTaskRequest) -> TaskRecord:
        with self._lock:
            return self._find(request.task_id).model_copy(deep=True)

    def list(self, request: ListTasksRequest) -> TaskListResult:
        with self._lock:
            return self._store.list(request)

    def confirm(self, request: ConfirmTaskRequest) -> TaskRecord:
        with self._lock:
            task = self._find(request.task_id)
            self._require_version(task, request.expected_state_version)
            if task.status != "awaiting_confirmation":
                raise TaskStateConflictError("只有待确认任务可以确认")
            if task.plan_hash != request.plan_hash:
                raise TaskPlanConflictError("确认的计划哈希与当前计划不一致")
            if task.current_ledger_revision != request.current_ledger_revision:
                raise TaskStateConflictError(
                    "确认前台账版本已变化",
                    details={
                        "expected_revision": task.current_ledger_revision,
                        "actual_revision": request.current_ledger_revision,
                    },
                )
            return self._update(
                task,
                status="queued",
                confirmed_ledger_revision=request.current_ledger_revision,
            )

    def pause(self, request: ControlTaskRequest) -> TaskRecord:
        return self._control(
            request,
            transitions={"queued": "paused", "running": "pause_requested"},
        )

    def resume(self, request: ControlTaskRequest) -> TaskRecord:
        return self._control(request, transitions={"paused": "queued"})

    def cancel(self, request: ControlTaskRequest) -> TaskRecord:
        return self._control(
            request,
            transitions={
                "awaiting_confirmation": "cancelled",
                "queued": "cancelled",
                "paused": "cancelled",
                "running": "cancel_requested",
                "pause_requested": "cancel_requested",
                "needs_attention": "cancelled",
                "failed": "cancelled",
            },
        )

    def claim(self, request: ClaimTaskRequest) -> ClaimTaskResult:
        with self._lock:
            queued = [
                task
                for task in self._store.list(ListTasksRequest(limit=500)).tasks
                if task.status == "queued"
                and (request.task_id is None or task.task_id == request.task_id)
            ]
            queued.sort(key=lambda task: (task.created_at, task.task_id))
            for task in queued:
                if (
                    task.environment_epoch != request.environment_epoch
                    or task.current_ledger_revision != request.current_ledger_revision
                ):
                    self._invalidate(task)
                    continue
                step_state = task.steps[task.current_step_index]
                now = self._clock.now()
                steps = list(task.steps)
                steps[task.current_step_index] = step_state.model_copy(
                    update={
                        "status": "running",
                        "attempt": step_state.attempt + 1,
                        "started_at": now,
                        "finished_at": None,
                        "result": None,
                    }
                )
                claimed = self._update(task, status="running", steps=steps)
                return ClaimTaskResult(task=claimed)
            return ClaimTaskResult()

    def apply_step_result(self, request: ApplyStepResultRequest) -> TaskRecord:
        with self._lock:
            task = self._find(request.task_id)
            self._require_version(task, request.expected_state_version)
            if task.status not in {"running", "pause_requested", "cancel_requested"}:
                raise TaskStateConflictError("当前任务不接受步骤结果")
            step_state = task.steps[task.current_step_index]
            if step_state.step_id != request.result.step_id:
                raise TaskStateConflictError(
                    "步骤结果与当前步骤不一致",
                    details={
                        "expected_step_id": step_state.step_id,
                        "actual_step_id": request.result.step_id,
                    },
                )
            now = self._clock.now()
            steps = list(task.steps)
            steps[task.current_step_index] = step_state.model_copy(
                update={
                    "status": request.result.status,
                    "finished_at": now,
                    "result": request.result,
                }
            )
            if task.status == "cancel_requested":
                return self._finish(task, "cancelled", steps, error=None)
            if request.result.status == "failed":
                error = request.result.error
                if error is not None and error.retryable and step_state.attempt < MAX_STEP_ATTEMPTS:
                    steps[task.current_step_index] = steps[task.current_step_index].model_copy(
                        update={"status": "pending"}
                    )
                    return self._update(task, status="queued", steps=steps, error=error)
                return self._finish(task, "failed", steps, error=error)

            next_index = task.current_step_index + 1
            completed_ids = [state.step_id for state in steps if state.status == "completed"]
            checkpoint = task.checkpoint.model_copy(
                update={
                    "next_step_index": next_index,
                    "completed_step_ids": completed_ids,
                    "ledger_revision": (
                        request.next_ledger_revision or task.checkpoint.ledger_revision
                    ),
                    "executor_state": request.executor_state,
                }
            )
            if next_index == len(steps):
                return self._finish(
                    task,
                    "completed",
                    steps,
                    error=None,
                    checkpoint=checkpoint,
                    current_step_index=next_index,
                    current_ledger_revision=(
                        request.next_ledger_revision or task.current_ledger_revision
                    ),
                    confirmed_ledger_revision=(
                        request.next_ledger_revision or task.confirmed_ledger_revision
                    ),
                )
            next_status = "paused" if task.status == "pause_requested" else "queued"
            return self._update(
                task,
                status=next_status,
                steps=steps,
                checkpoint=checkpoint,
                current_step_index=next_index,
                current_ledger_revision=(
                    request.next_ledger_revision or task.current_ledger_revision
                ),
                confirmed_ledger_revision=(
                    request.next_ledger_revision or task.confirmed_ledger_revision
                ),
                error=None,
            )

    def request_approval(self, request: RequestTaskApprovalRequest) -> TaskRecord:
        with self._lock:
            task = self._find(request.task_id)
            self._require_version(task, request.expected_state_version)
            if task.status != "running":
                raise TaskStateConflictError("只有运行中的任务可以请求确认")
            if request.pending_approval.step_id != task.steps[task.current_step_index].step_id:
                raise TaskStateConflictError("确认请求与当前任务步骤不一致")
            checkpoint = task.checkpoint.model_copy(
                update={"executor_state": request.executor_state}
            )
            return self._update(
                task,
                status="awaiting_confirmation",
                checkpoint=checkpoint,
                pending_approval=request.pending_approval,
            )

    def resolve_approval(self, request: ResolveTaskApprovalRequest) -> TaskRecord:
        with self._lock:
            task = self._find(request.task_id)
            self._require_version(task, request.expected_state_version)
            pending = task.pending_approval
            if task.status != "awaiting_confirmation" or pending is None:
                raise TaskStateConflictError("当前任务没有待确认操作")
            if pending.approval_id != request.approval_id:
                raise TaskStateConflictError("确认标识与当前操作不一致")
            if request.decision == "reject":
                return self._finish(task, "cancelled", task.steps, error=None)
            return self._update(
                task,
                status="queued",
                current_ledger_revision=request.current_ledger_revision,
                confirmed_ledger_revision=request.current_ledger_revision,
                pending_approval=pending.model_copy(update={"approved": True}),
            )

    def recover_interrupted(self, request: RecoverTasksRequest) -> RecoverTasksResult:
        requeued: list[str] = []
        paused: list[str] = []
        cancelled: list[str] = []
        failed: list[str] = []
        invalidated: list[str] = []
        with self._lock:
            tasks = self._store.list(ListTasksRequest(limit=500)).tasks
            interrupted = {
                "running",
                "pause_requested",
                "cancel_requested",
            }
            for task in tasks:
                if task.status not in interrupted:
                    continue
                if (
                    task.environment_epoch != request.environment_epoch
                    or task.current_ledger_revision != request.current_ledger_revision
                ):
                    self._invalidate(task)
                    invalidated.append(task.task_id)
                    continue
                step_state = task.steps[task.current_step_index]
                steps = list(task.steps)
                if task.status == "cancel_requested":
                    self._finish(task, "cancelled", steps, error=None)
                    cancelled.append(task.task_id)
                    continue
                steps[task.current_step_index] = step_state.model_copy(
                    update={
                        "status": "pending",
                        "started_at": None,
                        "finished_at": None,
                        "result": None,
                    }
                )
                if task.status == "pause_requested":
                    self._update(task, status="paused", steps=steps)
                    paused.append(task.task_id)
                    continue
                if step_state.attempt >= MAX_STEP_ATTEMPTS:
                    error = ErrorDetail(
                        code="task_interrupted_attempt_limit",
                        message="步骤中断且已达到最大尝试次数",
                        retryable=False,
                        details={"step_id": step_state.step_id},
                    )
                    failed_result = TaskStepResult(
                        step_id=step_state.step_id,
                        status="failed",
                        error=error,
                    )
                    steps[task.current_step_index] = steps[task.current_step_index].model_copy(
                        update={
                            "status": "failed",
                            "finished_at": self._clock.now(),
                            "result": failed_result,
                        }
                    )
                    self._finish(task, "failed", steps, error=error)
                    failed.append(task.task_id)
                    continue
                self._update(task, status="queued", steps=steps)
                requeued.append(task.task_id)
        return RecoverTasksResult(
            requeued_task_ids=requeued,
            paused_task_ids=paused,
            cancelled_task_ids=cancelled,
            failed_task_ids=failed,
            invalidated_task_ids=invalidated,
        )

    def _control(self, request: ControlTaskRequest, *, transitions: dict[str, str]) -> TaskRecord:
        with self._lock:
            task = self._find(request.task_id)
            self._require_version(task, request.expected_state_version)
            next_status = transitions.get(task.status)
            if next_status is None:
                raise TaskStateConflictError(
                    "当前状态不允许该控制操作",
                    details={"status": task.status},
                )
            if next_status == "cancelled":
                return self._finish(task, "cancelled", task.steps, error=None)
            return self._update(task, status=next_status)

    def _invalidate(self, task: TaskRecord) -> TaskRecord:
        return self._finish(
            task,
            "invalidated_by_restore",
            task.steps,
            error=None,
        )

    def _finish(
        self,
        task: TaskRecord,
        status: TerminalTaskStatus,
        steps: Sequence[TaskStepState],
        *,
        error: ErrorDetail | None,
        checkpoint: TaskCheckpoint | None = None,
        current_step_index: int | None = None,
        current_ledger_revision: str | None = None,
        confirmed_ledger_revision: str | None = None,
    ) -> TaskRecord:
        step_list = list(steps)
        results = [state.result for state in step_list if state.result is not None]
        result = TaskResult(
            task_id=task.task_id,
            correlation_id=task.correlation_id,
            status=status,
            steps=results,
            error=error,
        )
        return self._update(
            task,
            status=status,
            steps=step_list,
            error=error,
            result=result,
            terminal_at=self._clock.now(),
            checkpoint=checkpoint or task.checkpoint,
            current_step_index=(
                task.current_step_index if current_step_index is None else current_step_index
            ),
            current_ledger_revision=(current_ledger_revision or task.current_ledger_revision),
            confirmed_ledger_revision=(confirmed_ledger_revision or task.confirmed_ledger_revision),
            pending_approval=None,
        )

    def _update(self, task: TaskRecord, **changes: object) -> TaskRecord:
        updated = TaskRecord.model_validate(
            {
                **task.model_dump(mode="python"),
                **changes,
                "state_version": task.state_version + 1,
                "updated_at": self._clock.now(),
            }
        )
        return self._store.save(
            StoreSaveRequest(
                task=updated,
                expected_state_version=task.state_version,
            )
        )

    def _find(self, task_id: str) -> TaskRecord:
        task = self._store.get(GetTaskRequest(task_id=task_id))
        if task is None:
            raise TaskNotFoundError(
                "任务不存在",
                details={"task_id": task_id},
            )
        return task

    @staticmethod
    def _require_version(task: TaskRecord, expected: int) -> None:
        if task.state_version != expected:
            raise TaskStateConflictError(
                "任务状态版本冲突",
                details={
                    "expected_state_version": expected,
                    "actual_state_version": task.state_version,
                },
            )


@trace_public_methods(layer="capability")
class InMemoryTaskService(TaskStateMachineService):
    def __init__(self, clock: Clock) -> None:
        super().__init__(clock, InMemoryTaskStateStore())
