import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from wealth_agent.capabilities.tasks.contract import (
    ConfirmTaskRequest,
    ControlTaskRequest,
    CreateTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    TaskIdempotencyConflictError,
    TaskPayload,
    TaskPersistenceError,
    TaskPlan,
    TaskRecord,
    TaskStateConflictError,
    TaskStep,
)
from wealth_agent.capabilities.tasks.implementation import SqliteTaskService
from wealth_agent.capabilities.tasks.implementation.state_store import (
    SqliteTaskStateStore,
    StoreSaveRequest,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def _request(
    *,
    correlation_id: str = "correlation-1",
    trace_id: str | None = None,
) -> CreateTaskRequest:
    plan = TaskPlan(
        source_text="查询产品报表",
        steps=[
            TaskStep(
                step_id="step-1",
                action="portfolio.report",
                arguments=TaskPayload(
                    type="portfolio.report.v1",
                    data={"product_id": "product-1"},
                ),
                description="查询产品报表",
            )
        ],
    )
    return CreateTaskRequest(
        trace_id=trace_id,
        correlation_id=correlation_id,
        kind="workflow",
        plan=plan,
        current_ledger_revision="ledger-r1",
        environment_epoch=1,
        idempotency_key="task-create-1",
    )


def test_task_and_idempotency_receipt_survive_service_restart(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    first_service = SqliteTaskService(database, FixedClock())
    request = _request()
    created = first_service.create(request)
    confirmed = first_service.confirm(
        ConfirmTaskRequest(
            task_id=created.task.task_id,
            expected_state_version=created.task.state_version,
            plan_hash=created.task.plan_hash,
            current_ledger_revision="ledger-r1",
        )
    )

    restarted = SqliteTaskService(database, FixedClock())
    loaded = restarted.get(GetTaskRequest(task_id=created.task.task_id))
    replay = restarted.create(request)

    assert loaded == confirmed
    assert replay.replayed is True
    assert replay.task == confirmed


def test_persistent_state_version_rejects_stale_control(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    first_service = SqliteTaskService(database, FixedClock())
    second_service = SqliteTaskService(database, FixedClock())
    created = first_service.create(_request())
    confirmed = first_service.confirm(
        ConfirmTaskRequest(
            task_id=created.task.task_id,
            expected_state_version=created.task.state_version,
            plan_hash=created.task.plan_hash,
            current_ledger_revision="ledger-r1",
        )
    )
    stale_version = second_service.get(GetTaskRequest(task_id=created.task.task_id)).state_version
    paused = first_service.pause(
        ControlTaskRequest(
            task_id=confirmed.task_id,
            expected_state_version=confirmed.state_version,
        )
    )

    with pytest.raises(TaskStateConflictError) as error:
        second_service.cancel(
            ControlTaskRequest(
                task_id=confirmed.task_id,
                expected_state_version=stale_version,
            )
        )
    assert error.value.detail.details == {
        "expected_state_version": stale_version,
        "actual_state_version": paused.state_version,
    }


def test_persistent_idempotency_conflict_does_not_create_another_task(
    tmp_path: Path,
) -> None:
    service = SqliteTaskService(tmp_path / "task_state.db", FixedClock())
    service.create(_request())

    with pytest.raises(TaskIdempotencyConflictError):
        service.create(_request(correlation_id="changed"))

    assert len(service.list(ListTasksRequest()).tasks) == 1


def test_idempotent_replay_keeps_original_trace_id(tmp_path: Path) -> None:
    service = SqliteTaskService(tmp_path / "task_state.db", FixedClock())
    created = service.create(_request(trace_id="trace-original"))
    replayed = service.create(_request(trace_id="trace-retry"))

    assert replayed.replayed is True
    assert replayed.task.task_id == created.task.task_id
    assert replayed.task.trace_id == "trace-original"


def test_payload_hash_tampering_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    service = SqliteTaskService(database, FixedClock())
    created = service.create(_request())
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE tasks SET payload_sha256 = ? WHERE task_id = ?",
            ("0" * 64, created.task.task_id),
        )

    with pytest.raises(TaskPersistenceError) as error:
        service.get(GetTaskRequest(task_id=created.task.task_id))
    assert error.value.detail.code == "task_persistence_error"
    assert error.value.detail.details == {"task_id": created.task.task_id}


def test_sqlite_save_uses_atomic_state_version_comparison(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    service = SqliteTaskService(database, FixedClock())
    created = service.create(_request()).task
    next_task = TaskRecord.model_validate(
        {
            **created.model_dump(mode="python"),
            "status": "queued",
            "state_version": created.state_version + 1,
        }
    )
    store = SqliteTaskStateStore(database)
    request = StoreSaveRequest(
        task=next_task,
        expected_state_version=created.state_version,
    )

    saved = store.save(request)
    assert saved.state_version == 2

    with pytest.raises(TaskStateConflictError) as error:
        store.save(request)
    assert error.value.detail.details == {
        "task_id": created.task_id,
        "expected_state_version": 1,
        "actual_state_version": 2,
    }


def test_v1_tasks_are_backed_up_archived_and_never_resumed(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    service = SqliteTaskService(database, FixedClock())
    legacy = service.create(_request()).task
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 1")

    migrated = SqliteTaskService(database, FixedClock())

    assert (tmp_path / "task_state.db.before-v2").is_file()
    assert migrated.list(ListTasksRequest()).tasks == []
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT original_status, migration_status, payload_json
            FROM legacy_task_records_v1 WHERE task_id = ?
            """,
            (legacy.task_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "awaiting_confirmation"
    assert row[1] == "invalidated_by_upgrade"
    assert legacy.task_id in row[2]


def test_v2_tasks_are_backed_up_and_receive_persistent_trace_id(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    original = SqliteTaskService(database, FixedClock()).create(_request()).task
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_json FROM tasks WHERE task_id = ?",
            (original.task_id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[0]))
        payload.pop("trace_id")
        payload_json = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        connection.execute(
            "UPDATE tasks SET payload_json = ?, payload_sha256 = ? WHERE task_id = ?",
            (payload_json, payload_sha256, original.task_id),
        )
        connection.execute("PRAGMA user_version = 2")

    migrated = SqliteTaskService(database, FixedClock())
    loaded = migrated.get(GetTaskRequest(task_id=original.task_id))
    replayed = migrated.create(_request())

    assert (tmp_path / "task_state.db.before-v3").is_file()
    assert loaded.task_id == original.task_id
    assert loaded.trace_id.startswith("trace_")
    assert replayed.replayed is True
    assert replayed.task.trace_id == loaded.trace_id
    with sqlite3.connect(database) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 3


def test_v2_migration_rejects_corrupt_payload_without_upgrading(tmp_path: Path) -> None:
    database = tmp_path / "task_state.db"
    SqliteTaskService(database, FixedClock()).create(_request())
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE tasks SET payload_sha256 = ?", ("0" * 64,))
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(TaskPersistenceError, match="V2 任务记录哈希不一致"):
        SqliteTaskService(database, FixedClock())

    assert (tmp_path / "task_state.db.before-v3").is_file()
    with sqlite3.connect(database) as connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == 2
