from datetime import UTC, datetime
from pathlib import Path

from wealth_agent.capabilities.tasks.contract import (
    ClaimTaskRequest,
    ConfirmTaskRequest,
    ControlTaskRequest,
    CreateTaskRequest,
    ExecuteStepRequest,
    GetTaskRequest,
    RecoverTasksRequest,
    TaskPayload,
    TaskPlan,
    TaskStep,
    TaskStepResult,
    WorkerRunRequest,
)
from wealth_agent.capabilities.tasks.implementation import (
    InMemoryTaskService,
    SqliteTaskService,
    TaskWorker,
)
from wealth_agent.shared.logging import current_trace_id

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _create_confirmed(
    service: InMemoryTaskService | SqliteTaskService,
    *,
    key: str = "create-1",
    step_count: int = 1,
) -> str:
    plan = TaskPlan(
        source_text="生成资产报表",
        steps=[
            TaskStep(
                step_id=f"step-{index}",
                action="portfolio.report",
                arguments=TaskPayload(
                    type="portfolio.report.v1",
                    data={"product_id": f"product-{index}"},
                ),
                description=f"生成产品 {index} 的报表",
            )
            for index in range(1, step_count + 1)
        ],
    )
    created = service.create(
        CreateTaskRequest(
            correlation_id=key,
            kind="workflow",
            plan=plan,
            current_ledger_revision="ledger-r1",
            environment_epoch=1,
            idempotency_key=key,
        )
    ).task
    service.confirm(
        ConfirmTaskRequest(
            task_id=created.task_id,
            expected_state_version=created.state_version,
            plan_hash=created.plan_hash,
            current_ledger_revision="ledger-r1",
        )
    )
    return created.task_id


def _claim(service: InMemoryTaskService | SqliteTaskService) -> None:
    claimed = service.claim(
        ClaimTaskRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    ).task
    assert claimed is not None


class SuccessfulExecutor:
    def execute(self, request: ExecuteStepRequest) -> TaskStepResult:
        return TaskStepResult(
            step_id=request.step.step_id,
            status="completed",
            output=TaskPayload(type="step.output.v1", data={"ok": True}),
        )


class TracingExecutor(SuccessfulExecutor):
    def __init__(self) -> None:
        self.trace_id: str | None = None

    def execute(self, request: ExecuteStepRequest) -> TaskStepResult:
        self.trace_id = current_trace_id()
        return super().execute(request)


class FailingExecutor:
    def execute(self, _request: ExecuteStepRequest) -> TaskStepResult:
        raise ValueError("不得暴露的内部错误")


class PausingExecutor:
    def __init__(self, service: InMemoryTaskService) -> None:
        self._service = service

    def execute(self, request: ExecuteStepRequest) -> TaskStepResult:
        running = self._service.get(GetTaskRequest(task_id=request.task_id))
        self._service.pause(
            ControlTaskRequest(
                task_id=request.task_id,
                expected_state_version=running.state_version,
            )
        )
        return TaskStepResult(step_id=request.step.step_id, status="completed")


def test_restart_requeues_running_task_and_worker_continues(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    first_service = SqliteTaskService(database, FixedClock())
    task_id = _create_confirmed(first_service)
    _claim(first_service)

    restarted = SqliteTaskService(database, FixedClock())
    recovered = restarted.recover_interrupted(
        RecoverTasksRequest(
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    )
    assert recovered.requeued_task_ids == [task_id]
    queued = restarted.get(GetTaskRequest(task_id=task_id))
    assert queued.status == "queued"
    assert queued.steps[0].status == "pending"
    assert queued.steps[0].attempt == 1

    outcome = TaskWorker(restarted, SuccessfulExecutor()).run_once(
        WorkerRunRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    )
    assert outcome.status == "executed"
    assert outcome.task is not None
    assert outcome.task.status == "completed"
    assert outcome.task.steps[0].attempt == 2


def test_worker_restores_persisted_task_trace_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    first_service = SqliteTaskService(database, FixedClock())
    task_id = _create_confirmed(first_service)
    persisted_trace = first_service.get(GetTaskRequest(task_id=task_id)).trace_id

    executor = TracingExecutor()
    restarted = SqliteTaskService(database, FixedClock())
    outcome = TaskWorker(restarted, executor).run_once(
        WorkerRunRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    )

    assert outcome.status == "executed"
    assert executor.trace_id == persisted_trace


def test_restart_finishes_pending_pause_and_cancel(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    service = SqliteTaskService(database, FixedClock())
    paused_id = _create_confirmed(service, key="pause", step_count=2)
    _claim(service)
    paused_running = service.get(GetTaskRequest(task_id=paused_id))
    service.pause(
        ControlTaskRequest(
            task_id=paused_id,
            expected_state_version=paused_running.state_version,
        )
    )

    cancelled_id = _create_confirmed(service, key="cancel")
    _claim(service)
    cancelled_running = service.get(GetTaskRequest(task_id=cancelled_id))
    service.cancel(
        ControlTaskRequest(
            task_id=cancelled_id,
            expected_state_version=cancelled_running.state_version,
        )
    )

    restarted = SqliteTaskService(database, FixedClock())
    recovered = restarted.recover_interrupted(
        RecoverTasksRequest(
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    )

    assert recovered.paused_task_ids == [paused_id]
    assert recovered.cancelled_task_ids == [cancelled_id]
    assert restarted.get(GetTaskRequest(task_id=paused_id)).status == "paused"
    assert restarted.get(GetTaskRequest(task_id=cancelled_id)).status == "cancelled"


def test_recovery_invalidates_task_bound_to_old_ledger(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    service = SqliteTaskService(database, FixedClock())
    task_id = _create_confirmed(service)
    _claim(service)

    recovered = SqliteTaskService(database, FixedClock()).recover_interrupted(
        RecoverTasksRequest(
            environment_epoch=1,
            current_ledger_revision="ledger-after-restore",
        )
    )

    assert recovered.invalidated_task_ids == [task_id]
    task = service.get(GetTaskRequest(task_id=task_id))
    assert task.status == "invalidated_by_restore"


def test_worker_converts_unhandled_executor_exception_to_structured_failure() -> None:
    service = InMemoryTaskService(FixedClock())
    _create_confirmed(service)

    outcome = TaskWorker(service, FailingExecutor()).run_once(
        WorkerRunRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    )

    assert outcome.status == "executed"
    assert outcome.task is not None
    assert outcome.task.status == "failed"
    assert outcome.task.error is not None
    assert outcome.task.error.code == "executor_unhandled_error"
    assert outcome.task.error.details == {"error_type": "ValueError"}


def test_worker_applies_pause_requested_during_execution_at_step_boundary() -> None:
    service = InMemoryTaskService(FixedClock())
    _create_confirmed(service, step_count=2)

    outcome = TaskWorker(service, PausingExecutor(service)).run_once(
        WorkerRunRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    )

    assert outcome.status == "executed"
    assert outcome.task is not None
    assert outcome.task.status == "paused"
    assert outcome.task.checkpoint.completed_step_ids == ["step-1"]
    assert outcome.task.current_step_index == 1
