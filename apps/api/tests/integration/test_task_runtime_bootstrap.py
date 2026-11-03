from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from wealth_agent.bootstrap import (
    BuildTaskRuntimeRequest,
    TaskRuntimeBundle,
    TaskRuntimeConfig,
    build_task_runtime,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_store import (
    SqlitePortfolioStore,
)
from wealth_agent.capabilities.tasks.contract import (
    ClaimTaskRequest,
    ConfirmTaskRequest,
    CreateTaskRequest,
    ExecuteStepRequest,
    GetTaskRequest,
    TaskEnvironmentRequest,
    TaskPayload,
    TaskPlan,
    TaskStateConflictError,
    TaskStep,
    TaskStepResult,
    WorkerShutdownRequest,
    WorkerStartRequest,
    WorkerWakeRequest,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class SignallingExecutor:
    def __init__(self) -> None:
        self.executed = Event()
        self.requests: list[ExecuteStepRequest] = []

    def execute(self, request: ExecuteStepRequest) -> TaskStepResult:
        self.requests.append(request)
        self.executed.set()
        return TaskStepResult(
            step_id=request.step.step_id,
            status="completed",
            output=TaskPayload(type="portfolio.report.result.v1", data={"ok": True}),
        )


def _create_confirm(bundle: TaskRuntimeBundle) -> str:
    tasks = bundle.tasks
    environment = bundle.environment.read(TaskEnvironmentRequest())
    plan = TaskPlan(
        source_text="读取资产报表",
        steps=[
            TaskStep(
                step_id="step-1",
                action="portfolio.report",
                arguments=TaskPayload(
                    type="portfolio.report.v1",
                    data={"product_id": "product-1"},
                ),
                description="读取资产报表",
            )
        ],
    )
    created = tasks.create(
        CreateTaskRequest(
            correlation_id="correlation-1",
            kind="workflow",
            plan=plan,
            current_ledger_revision=environment.current_ledger_revision,
            environment_epoch=environment.environment_epoch,
            idempotency_key="create-1",
        )
    ).task
    tasks.confirm(
        ConfirmTaskRequest(
            task_id=created.task_id,
            expected_state_version=created.state_version,
            plan_hash=created.plan_hash,
            current_ledger_revision=environment.current_ledger_revision,
        )
    )
    return created.task_id


def _bundle(tmp_path: Path, executor: SignallingExecutor) -> TaskRuntimeBundle:
    clock = FixedClock()
    portfolio = SqlitePortfolioStore(tmp_path / "wealth.db", clock)
    return build_task_runtime(
        BuildTaskRuntimeRequest(
            config=TaskRuntimeConfig(
                database_path=tmp_path / "task_state.db",
                worker_id="worker-1",
                idle_wait_seconds=0.01,
            ),
            portfolio=portfolio,
            executor=executor,
            clock=clock,
        )
    )


def test_bootstrap_runtime_executes_persistent_task_and_shuts_down(tmp_path: Path) -> None:
    executor = SignallingExecutor()
    bundle = _bundle(tmp_path, executor)
    task_id = _create_confirm(bundle)

    started = bundle.runtime.start(WorkerStartRequest())
    assert started.status == "running"
    assert executor.executed.wait(timeout=2)
    stopped = bundle.runtime.shutdown(WorkerShutdownRequest(timeout_seconds=2))

    assert stopped.status == "stopped"
    task = bundle.tasks.get(GetTaskRequest(task_id=task_id))
    assert task.status == "completed"
    assert executor.requests[0].confirmed_ledger_revision == task.current_ledger_revision


def test_runtime_start_recovers_interrupted_task_before_execution(tmp_path: Path) -> None:
    executor = SignallingExecutor()
    bundle = _bundle(tmp_path, executor)
    task_id = _create_confirm(bundle)
    environment = bundle.environment.read(TaskEnvironmentRequest())
    bundle.tasks.claim(
        ClaimTaskRequest(
            worker_id="crashed-worker",
            environment_epoch=environment.environment_epoch,
            current_ledger_revision=environment.current_ledger_revision,
        )
    )

    restarted = _bundle(tmp_path, executor)
    started = restarted.runtime.start(WorkerStartRequest())
    assert started.recovery is not None
    assert started.recovery.requeued_task_ids == [task_id]
    assert executor.executed.wait(timeout=2)
    restarted.runtime.shutdown(WorkerShutdownRequest(timeout_seconds=2))
    assert restarted.tasks.get(GetTaskRequest(task_id=task_id)).status == "completed"


def test_runtime_rejects_wakeup_before_start(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, SignallingExecutor())

    with pytest.raises(TaskStateConflictError) as error:
        bundle.runtime.wake(WorkerWakeRequest())

    assert error.value.detail.code == "task_state_conflict"
