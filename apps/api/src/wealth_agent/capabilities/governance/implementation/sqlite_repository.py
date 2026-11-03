"""SQLite storage for structured governance events."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pydantic import ValidationError
from wealth_agent.capabilities.governance.contract import (
    GovernanceEvent,
    GovernancePersistenceError,
    ListEventsRequest,
    ListEventsResult,
    RecordEventRequest,
    RecordEventResult,
)
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.serialization import canonical_json, sha256_json

APPLICATION_ID = 0x57414756
DATABASE_SCHEMA_VERSION = 1


@trace_public_methods(layer="capability")
class SqliteTelemetryRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize(connection)

    def record(self, request: RecordEventRequest) -> RecordEventResult:
        payload = request.event.model_dump(mode="json")
        payload_json = canonical_json(payload)
        digest = sha256_json(payload)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT payload_sha256 FROM governance_events WHERE event_id = ?",
                    (request.event.event_id,),
                ).fetchone()
                if existing is not None:
                    if str(existing[0]) != digest:
                        raise GovernancePersistenceError(
                            "事件标识已对应其他内容",
                            details={"event_id": request.event.event_id},
                        )
                    return RecordEventResult(
                        event=request.event, created=False, payload_sha256=digest
                    )
                connection.execute(
                    "INSERT INTO governance_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        request.event.event_id,
                        request.event.category,
                        request.event.name,
                        request.event.occurred_at.isoformat(),
                        request.event.correlation_id,
                        payload_json,
                        digest,
                    ),
                )
                connection.commit()
            return RecordEventResult(
                event=request.event, created=True, payload_sha256=digest
            )
        except sqlite3.DatabaseError as exc:
            raise GovernancePersistenceError("保存治理事件失败") from exc

    def list(self, request: ListEventsRequest) -> ListEventsResult:
        sql = "SELECT payload_json, payload_sha256 FROM governance_events"
        parameters: list[object] = []
        if request.category is not None:
            sql += " WHERE category = ?"
            parameters.append(request.category)
        sql += " ORDER BY occurred_at DESC, event_id DESC LIMIT ?"
        parameters.append(request.limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(sql, parameters).fetchall()
            events = []
            for payload_json, digest in rows:
                event = GovernanceEvent.model_validate_json(str(payload_json))
                if sha256_json(event.model_dump(mode="json")) != str(digest):
                    raise GovernancePersistenceError(
                        "治理事件完整性校验失败", details={"event_id": event.event_id}
                    )
                events.append(event)
            return ListEventsResult(events=events)
        except (sqlite3.DatabaseError, ValidationError) as exc:
            raise GovernancePersistenceError("读取治理事件失败") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if application_id not in {0, APPLICATION_ID} or version not in {
            0,
            DATABASE_SCHEMA_VERSION,
        }:
            connection.close()
            raise GovernancePersistenceError("SQLite 遥测库格式不兼容")
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS governance_events ("
            "event_id TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL, "
            "occurred_at TEXT NOT NULL, correlation_id TEXT, payload_json TEXT NOT NULL, "
            "payload_sha256 TEXT NOT NULL)"
        )
        connection.commit()
