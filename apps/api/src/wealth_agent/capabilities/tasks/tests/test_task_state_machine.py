from datetime import UTC, datetime

import pytest
from wealth_agent.capabilities.tasks.contract import (
    ApplyStepResultRequest,
    ClaimTaskRequest,
    ConfirmTaskRequest,
    ControlTaskRequest,
    CreateTaskRequest,
    CreateTaskResult,
    GetTaskRequest,
    TaskIdempotencyConflictError,
    TaskPayload,
    TaskPlan,
    TaskStateConflictError,
    TaskStep,
    TaskStepResult,
)
from wealth_agent.capabilities.tasks.implementation import InMemoryTaskService
from wealth_agent.shared.errors import ErrorDetail

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _plan(step_count: int = 1) -> TaskPlan:
    return TaskPlan(
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


def _create(service: InMemoryTaskService, step_count: int = 1) -> CreateTaskResult:
    return service.create(
        CreateTaskRequest(
            correlation_id="correlation-1",
            kind="workflow",
            plan=_plan(step_count),
            current_ledger_revision="ledger-r1",
            environment_epoch=1,
            idempotency_key="create-1",
        )
    )


def _confirm(service: InMemoryTaskService, created: CreateTaskResult) -> None:
    task = created.task
    service.confirm(
        ConfirmTaskRequest(
            task_id=task.task_id,
            expected_state_version=task.state_version,
            plan_hash=task.plan_hash,
            current_ledger_revision="ledger-r1",
        )
    )


def test_create_is_idempotent_and_rejects_changed_request() -> None:
    service = InMemoryTaskService(FixedClock())
    request = CreateTaskRequest(
        correlation_id="correlation-1",
        kind="workflow",
        plan=_plan(),
        current_ledger_revision="ledger-r1",
        environment_epoch=1,
        idempotency_key="create-1",
    )

    first = service.create(request)
    replay = service.create(request)

    assert replay.replayed is True
    assert replay.task == first.task

    with pytest.raises(TaskIdempotencyConflictError) as error:
        service.create(request.model_copy(update={"correlation_id": "changed"}))
    assert error.value.detail.details == {"idempotency_key": "create-1"}


def test_confirmation_binds_plan_state_and_ledger_versions() -> None:
    service = InMemoryTaskService(FixedClock())
    created = _create(service)

    with pytest.raises(TaskStateConflictError) as error:
        service.confirm(
            ConfirmTaskRequest(
                task_id=created.task.task_id,
                expected_state_version=created.task.state_version,
                plan_hash=created.task.plan_hash,
                current_ledger_revision="ledger-r2",
            )
        )
    assert error.value.detail.details == {
        "expected_revision": "ledger-r1",
        "actual_revision": "ledger-r2",
    }

    _confirm(service, created)
    queued = service.get(GetTaskRequest(task_id=created.task.task_id))
    assert queued.status == "queued"
    assert queued.confirmed_ledger_revision == "ledger-r1"


def test_pause_takes_effect_after_running_step_boundary() -> None:
    service = InMemoryTaskService(FixedClock())
    created = _create(service, step_count=2)
    _confirm(service, created)
    claimed = service.claim(
        ClaimTaskRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r1",
        )
    ).task
    assert claimed is not None

    pause_requested = service.pause(
        ControlTaskRequest(
            task_id=claimed.task_id,
            expected_state_version=claimed.state_version,
        )
    )
    assert pause_requested.status == "pause_requested"

    paused = service.apply_step_result(
        ApplyStepResultRequest(
            task_id=claimed.task_id,
            expected_state_version=pause_requested.state_version,
            result=TaskStepResult(step_id="step-1", status="completed"),
        )
    )
    assert paused.status == "paused"
    assert paused.checkpoint.completed_step_ids == ["step-1"]
    assert paused.checkpoint.next_step_index == 1

    resumed = service.resume(
        ControlTaskRequest(
            task_id=paused.task_id,
            expected_state_version=paused.state_version,
        )
    )
    assert resumed.status == "queued"


def test_retryable_failure_retries_three_times_then_finishes_failed() -> None:
    service = InMemoryTaskService(FixedClock())
    created = _create(service)
    _confirm(service, created)
    task_id = created.task.task_id

    for attempt in range(1, 4):
        claimed = service.claim(
            ClaimTaskRequest(
                worker_id="worker-1",
                environment_epoch=1,
                current_ledger_revision="ledger-r1",
            )
        ).task
        assert claimed is not None
        assert claimed.steps[0].attempt == attempt
        failed = service.apply_step_result(
            ApplyStepResultRequest(
                task_id=task_id,
                expected_state_version=claimed.state_version,
                result=TaskStepResult(
                    step_id="step-1",
                    status="failed",
                    error=ErrorDetail(
                        code="temporary_error",
                        message="暂时不可用",
                        retryable=True,
                    ),
                ),
            )
        )
        assert failed.status == ("queued" if attempt < 3 else "failed")

    assert failed.result is not None
    assert failed.result.status == "failed"
    assert failed.result.error is not None
    assert failed.result.error.code == "temporary_error"


def test_restore_epoch_invalidates_queued_task() -> None:
    service = InMemoryTaskService(FixedClock())
    created = _create(service)
    _confirm(service, created)

    claim = service.claim(
        ClaimTaskRequest(
            worker_id="worker-1",
            environment_epoch=1,
            current_ledger_revision="ledger-r2",
        )
    )

    assert claim.task is None
    invalidated = service.get(GetTaskRequest(task_id=created.task.task_id))
    assert invalidated.status == "invalidated_by_restore"
    assert invalidated.result is not None
