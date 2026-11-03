"""One-boundary task worker using only public structured task contracts."""

from __future__ import annotations

from wealth_agent.capabilities.tasks.contract import (
    ApplyStepResultRequest,
    ClaimTaskRequest,
    ExecuteStepRequest,
    ExecutorPort,
    GetTaskRequest,
    TaskError,
    TaskRecord,
    TaskService,
    TaskStateConflictError,
    TaskStepResult,
    WorkerRunRequest,
    WorkerRunResult,
)
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.logging import trace_context, trace_public_methods


@trace_public_methods(layer="capability")
class TaskWorker:
    def __init__(self, tasks: TaskService, executor: ExecutorPort) -> None:
        self._tasks = tasks
        self._executor = executor

    def run_once(self, request: WorkerRunRequest) -> WorkerRunResult:
        claimed = self._tasks.claim(
            ClaimTaskRequest(
                worker_id=request.worker_id,
                environment_epoch=request.environment_epoch,
                current_ledger_revision=request.current_ledger_revision,
            )
        ).task
        if claimed is None:
            return WorkerRunResult(status="idle")

        with trace_context(claimed.trace_id):
            return self._execute_claimed(claimed)

    def _execute_claimed(self, task: TaskRecord) -> WorkerRunResult:
        step_state = task.steps[task.current_step_index]
        step = task.plan.steps[task.current_step_index]
        confirmed_revision = task.confirmed_ledger_revision
        if confirmed_revision is None:
            return WorkerRunResult(
                status="conflict",
                task=task,
                error=ErrorDetail(
                    code="task_confirmation_missing",
                    message="已领取任务缺少确认台账版本",
                    details={"task_id": task.task_id},
                ),
            )
        try:
            result = self._executor.execute(
                ExecuteStepRequest(
                    task_id=task.task_id,
                    correlation_id=task.correlation_id,
                    step=step,
                    attempt=step_state.attempt,
                    confirmed_ledger_revision=confirmed_revision,
                    checkpoint=task.checkpoint,
                )
            )
        except TaskError as exc:
            result = TaskStepResult(
                step_id=step.step_id,
                status="failed",
                error=exc.detail,
            )
        except Exception as exc:
            result = TaskStepResult(
                step_id=step.step_id,
                status="failed",
                error=ErrorDetail(
                    code="executor_unhandled_error",
                    message="执行器发生未处理错误",
                    details={"error_type": type(exc).__name__},
                ),
            )

        current = self._tasks.get(GetTaskRequest(task_id=task.task_id))
        try:
            updated = self._tasks.apply_step_result(
                ApplyStepResultRequest(
                    task_id=current.task_id,
                    expected_state_version=current.state_version,
                    result=result,
                )
            )
        except TaskStateConflictError as exc:
            latest = self._tasks.get(GetTaskRequest(task_id=task.task_id))
            return WorkerRunResult(
                status="conflict",
                task=latest,
                error=exc.detail,
            )
        return WorkerRunResult(status="executed", task=updated)
