"""Private structured storage port and task-state store implementations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Protocol

from pydantic import BaseModel, ConfigDict, ValidationError
from wealth_agent.capabilities.tasks.contract import (
    GetTaskRequest,
    ListTasksRequest,
    TaskIdempotencyConflictError,
    TaskListResult,
    TaskPersistenceError,
    TaskRecord,
    TaskStateConflictError,
)
from wealth_agent.shared.identity import new_id
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.serialization import canonical_json

TASK_STATE_APPLICATION_ID = 0x57415453
TASK_STATE_SCHEMA_VERSION = 3


class StoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StoreCreateRequest(StoreModel):
    task: TaskRecord
    idempotency_key: str | None = None
    request_hash: str


class StoreCreateResult(StoreModel):
    task: TaskRecord
    replayed: bool


class StoreSaveRequest(StoreModel):
    task: TaskRecord
    expected_state_version: int


class TaskStateStore(Protocol):
    def create(self, request: StoreCreateRequest) -> StoreCreateResult: ...

    def get(self, request: GetTaskRequest) -> TaskRecord | None: ...

    def list(self, request: ListTasksRequest) -> TaskListResult: ...

    def save(self, request: StoreSaveRequest) -> TaskRecord: ...


@trace_public_methods(layer="capability")
class InMemoryTaskStateStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._receipts: dict[tuple[int, str], tuple[str, str]] = {}
        self._lock = RLock()

    def create(self, request: StoreCreateRequest) -> StoreCreateResult:
        with self._lock:
            if request.idempotency_key is not None:
                receipt_key = (request.task.environment_epoch, request.idempotency_key)
                receipt = self._receipts.get(receipt_key)
                if receipt is not None:
                    stored_hash, task_id = receipt
                    if stored_hash != request.request_hash:
                        raise TaskIdempotencyConflictError(
                            "任务幂等键已对应其他请求",
                            details={"idempotency_key": request.idempotency_key},
                        )
                    return StoreCreateResult(
                        task=self._tasks[task_id].model_copy(deep=True),
                        replayed=True,
                    )
            self._tasks[request.task.task_id] = request.task.model_copy(deep=True)
            if request.idempotency_key is not None:
                self._receipts[(request.task.environment_epoch, request.idempotency_key)] = (
                    request.request_hash,
                    request.task.task_id,
                )
            return StoreCreateResult(task=request.task.model_copy(deep=True), replayed=False)

    def get(self, request: GetTaskRequest) -> TaskRecord | None:
        with self._lock:
            task = self._tasks.get(request.task_id)
            return None if task is None else task.model_copy(deep=True)

    def list(self, request: ListTasksRequest) -> TaskListResult:
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda task: (task.created_at, task.task_id),
                reverse=True,
            )[: request.limit]
            return TaskListResult(tasks=[task.model_copy(deep=True) for task in tasks])

    def save(self, request: StoreSaveRequest) -> TaskRecord:
        with self._lock:
            current = self._tasks.get(request.task.task_id)
            if current is None or current.state_version != request.expected_state_version:
                raise TaskStateConflictError(
                    "任务状态已被其他控制请求修改",
                    details={
                        "task_id": request.task.task_id,
                        "expected_state_version": request.expected_state_version,
                        "actual_state_version": (
                            None if current is None else current.state_version
                        ),
                    },
                )
            self._tasks[request.task.task_id] = request.task.model_copy(deep=True)
            return request.task.model_copy(deep=True)


@trace_public_methods(layer="capability")
class SqliteTaskStateStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._backup_database_before_upgrade()
            with closing(self._connect()) as connection, connection:
                self._initialize(connection)
                self._validate(connection)
        except TaskPersistenceError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise TaskPersistenceError(
                "初始化任务状态库失败",
                details={"database_path": str(database_path)},
            ) from exc

    def create(self, request: StoreCreateRequest) -> StoreCreateResult:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if request.idempotency_key is not None:
                    row = connection.execute(
                        """
                        SELECT request_hash, task_id FROM task_idempotency_keys
                        WHERE environment_epoch = ? AND idempotency_key = ?
                        """,
                        (request.task.environment_epoch, request.idempotency_key),
                    ).fetchone()
                    if row is not None:
                        if str(row["request_hash"]) != request.request_hash:
                            raise TaskIdempotencyConflictError(
                                "任务幂等键已对应其他请求",
                                details={"idempotency_key": request.idempotency_key},
                            )
                        task = self._load_task(connection, str(row["task_id"]))
                        if task is None:
                            raise TaskPersistenceError("任务幂等回执指向不存在的任务")
                        return StoreCreateResult(task=task, replayed=True)
                self._insert(connection, request.task)
                if request.idempotency_key is not None:
                    connection.execute(
                        """
                        INSERT INTO task_idempotency_keys (
                            environment_epoch, idempotency_key, request_hash, task_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            request.task.environment_epoch,
                            request.idempotency_key,
                            request.request_hash,
                            request.task.task_id,
                        ),
                    )
                return StoreCreateResult(task=request.task, replayed=False)
        except (TaskIdempotencyConflictError, TaskPersistenceError):
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise TaskPersistenceError(
                "创建持久任务失败",
                details={"database_path": str(self.database_path)},
            ) from exc

    def get(self, request: GetTaskRequest) -> TaskRecord | None:
        try:
            with closing(self._connect()) as connection:
                return self._load_task(connection, request.task_id)
        except TaskPersistenceError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise TaskPersistenceError(
                "读取持久任务失败",
                details={"task_id": request.task_id},
            ) from exc

    def list(self, request: ListTasksRequest) -> TaskListResult:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC, task_id DESC LIMIT ?",
                    (request.limit,),
                ).fetchall()
                return TaskListResult(tasks=[self._record_from_row(row) for row in rows])
        except TaskPersistenceError:
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise TaskPersistenceError(
                "读取任务列表失败",
                details={"database_path": str(self.database_path)},
            ) from exc

    def save(self, request: StoreSaveRequest) -> TaskRecord:
        if request.task.state_version != request.expected_state_version + 1:
            raise TaskStateConflictError(
                "待保存状态版本不是期望版本的下一版",
                details={
                    "expected_state_version": request.expected_state_version,
                    "next_state_version": request.task.state_version,
                },
            )
        payload_json, payload_sha256 = self._serialize(request.task)
        try:
            with closing(self._connect()) as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE tasks SET
                        status = ?, state_version = ?, updated_at = ?, terminal_at = ?,
                        payload_json = ?, payload_sha256 = ?
                    WHERE task_id = ? AND state_version = ?
                    """,
                    (
                        request.task.status,
                        request.task.state_version,
                        request.task.updated_at.isoformat(),
                        (
                            None
                            if request.task.terminal_at is None
                            else request.task.terminal_at.isoformat()
                        ),
                        payload_json,
                        payload_sha256,
                        request.task.task_id,
                        request.expected_state_version,
                    ),
                )
                if cursor.rowcount != 1:
                    row = connection.execute(
                        "SELECT state_version FROM tasks WHERE task_id = ?",
                        (request.task.task_id,),
                    ).fetchone()
                    raise TaskStateConflictError(
                        "任务状态已被其他控制请求修改",
                        details={
                            "task_id": request.task.task_id,
                            "expected_state_version": request.expected_state_version,
                            "actual_state_version": None if row is None else int(row[0]),
                        },
                    )
                return request.task
        except (TaskStateConflictError, TaskPersistenceError):
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            raise TaskPersistenceError(
                "保存持久任务失败",
                details={"task_id": request.task.task_id},
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=4)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 4000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _backup_database_before_upgrade(self) -> None:
        if not self.database_path.is_file():
            return
        with closing(sqlite3.connect(self.database_path)) as source:
            user_version = int(source.execute("PRAGMA user_version").fetchone()[0])
            if user_version not in {1, 2}:
                return
            target_version = 2 if user_version == 1 else 3
            backup_path = self.database_path.with_name(
                f"{self.database_path.name}.before-v{target_version}"
            )
            if backup_path.exists():
                return
            with closing(sqlite3.connect(backup_path)) as target:
                source.backup(target)

    @classmethod
    def _initialize(cls, connection: sqlite3.Connection) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id not in {0, TASK_STATE_APPLICATION_ID}:
            raise TaskPersistenceError("task_state.db application_id 不兼容")
        if user_version not in {0, 1, 2, TASK_STATE_SCHEMA_VERSION}:
            raise TaskPersistenceError("task_state.db Schema 版本不兼容")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute(f"PRAGMA application_id = {TASK_STATE_APPLICATION_ID}")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                state_version INTEGER NOT NULL CHECK (state_version >= 1),
                environment_epoch INTEGER NOT NULL CHECK (environment_epoch >= 1),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                terminal_at TEXT,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status_updated
                ON tasks(status, updated_at, task_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_created
                ON tasks(created_at DESC, task_id DESC);
            CREATE TABLE IF NOT EXISTS task_idempotency_keys (
                environment_epoch INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                PRIMARY KEY (environment_epoch, idempotency_key)
            );
            """
        )
        if user_version == 1:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS legacy_task_records_v1 (
                    task_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    original_status TEXT NOT NULL,
                    migration_status TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    environment_epoch INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_at TEXT,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL
                );
                INSERT OR IGNORE INTO legacy_task_records_v1
                SELECT task_id, kind, status,
                       CASE
                           WHEN status IN ('cancelled', 'completed', 'failed',
                                           'invalidated_by_restore')
                           THEN 'archived_terminal'
                           ELSE 'invalidated_by_upgrade'
                       END,
                       state_version, environment_epoch,
                       created_at, updated_at, terminal_at, payload_json, payload_sha256
                FROM tasks;
                DELETE FROM task_idempotency_keys;
                DELETE FROM tasks;
                """
            )
        if user_version == 2:
            rows = connection.execute(
                "SELECT task_id, payload_json, payload_sha256 FROM tasks"
            ).fetchall()
            for row in rows:
                payload_json = str(row["payload_json"])
                if cls._sha256(payload_json) != str(row["payload_sha256"]):
                    raise TaskPersistenceError(
                        "V2 任务记录哈希不一致",
                        details={"task_id": str(row["task_id"])},
                    )
                try:
                    payload = json.loads(payload_json)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise TaskPersistenceError(
                        "V2 任务记录不是有效 JSON",
                        details={"task_id": str(row["task_id"])},
                    ) from exc
                if not isinstance(payload, dict):
                    raise TaskPersistenceError(
                        "V2 任务记录根节点不是对象",
                        details={"task_id": str(row["task_id"])},
                    )
                payload.setdefault("trace_id", new_id("trace"))
                upgraded_json = canonical_json(payload)
                connection.execute(
                    "UPDATE tasks SET payload_json = ?, payload_sha256 = ? WHERE task_id = ?",
                    (
                        upgraded_json,
                        cls._sha256(upgraded_json),
                        str(row["task_id"]),
                    ),
                )
        connection.execute(f"PRAGMA user_version = {TASK_STATE_SCHEMA_VERSION}")

    @staticmethod
    def _validate(connection: sqlite3.Connection) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if application_id != TASK_STATE_APPLICATION_ID:
            raise TaskPersistenceError("task_state.db application_id 不兼容")
        if user_version != TASK_STATE_SCHEMA_VERSION:
            raise TaskPersistenceError("task_state.db Schema 版本不兼容")
        if integrity != "ok":
            raise TaskPersistenceError("task_state.db 完整性检查失败")

    @classmethod
    def _insert(cls, connection: sqlite3.Connection, task: TaskRecord) -> None:
        payload_json, payload_sha256 = cls._serialize(task)
        connection.execute(
            """
            INSERT INTO tasks (
                task_id, kind, status, state_version, environment_epoch,
                created_at, updated_at, terminal_at, payload_json, payload_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.task_id,
                task.kind,
                task.status,
                task.state_version,
                task.environment_epoch,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                None if task.terminal_at is None else task.terminal_at.isoformat(),
                payload_json,
                payload_sha256,
            ),
        )

    @classmethod
    def _load_task(cls, connection: sqlite3.Connection, task_id: str) -> TaskRecord | None:
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return None if row is None else cls._record_from_row(row)

    @classmethod
    def _record_from_row(cls, row: sqlite3.Row) -> TaskRecord:
        payload_json = str(row["payload_json"])
        try:
            if cls._sha256(payload_json) != str(row["payload_sha256"]):
                raise ValueError("任务记录哈希不一致")
            task = TaskRecord.model_validate_json(payload_json)
            indexed = (
                task.task_id,
                task.kind,
                task.status,
                task.state_version,
                task.environment_epoch,
            )
            stored = (
                str(row["task_id"]),
                str(row["kind"]),
                str(row["status"]),
                int(row["state_version"]),
                int(row["environment_epoch"]),
            )
            if indexed != stored:
                raise ValueError("任务记录索引与内容不一致")
            return task
        except (ValidationError, ValueError) as exc:
            raise TaskPersistenceError(
                "任务记录损坏",
                details={"task_id": str(row["task_id"])},
            ) from exc

    @classmethod
    def _serialize(cls, task: TaskRecord) -> tuple[str, str]:
        payload_json = canonical_json(task.model_dump(mode="json"))
        return payload_json, cls._sha256(payload_json)

    @staticmethod
    def _sha256(payload_json: str) -> str:
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
