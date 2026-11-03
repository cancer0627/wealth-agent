"""Analysis-owned SQLite persistence for structured analysis records."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError
from wealth_agent.capabilities.analysis.contract import (
    AnalysisPersistenceConflictError,
    AnalysisPersistenceError,
    AnalysisRecord,
    LatestAnalysisRequest,
    LatestAnalysisResult,
    SaveAnalysisResult,
)
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.file_lock import FileLock, FileLockTimeoutError
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.serialization import canonical_json

APPLICATION_ID = 0x57414E41
DATABASE_SCHEMA_VERSION = 1


@trace_public_methods(layer="capability")
class SqliteAnalysisRepository:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._lock_path = database_path.parent / ".analysis.lock"

    def save(self, record: AnalysisRecord) -> SaveAnalysisResult:
        payload_json = canonical_json(record.model_dump(mode="json"))
        payload_sha256 = self._sha256(payload_json)
        try:
            with FileLock(self._lock_path, exclusive=True):
                with closing(self._connect()) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    existing = connection.execute(
                        "SELECT payload_sha256 FROM analysis_records "
                        "WHERE analysis_run_id = ?",
                        (record.result.analysis_run_id,),
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != payload_sha256:
                            raise AnalysisPersistenceConflictError(
                                "分析运行标识已对应其他内容",
                                details={"analysis_run_id": record.result.analysis_run_id},
                            )
                        connection.commit()
                        return SaveAnalysisResult(
                            record=record,
                            payload_sha256=payload_sha256,
                            created=False,
                        )
                    connection.execute(
                        """
                        INSERT INTO analysis_records (
                            analysis_run_id, product_id, product_revision,
                            ledger_revision, created_at, payload_json, payload_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.result.analysis_run_id,
                            record.result.report.product_id,
                            record.result.report.product_revision,
                            record.result.report.ledger_revision,
                            record.created_at.isoformat(),
                            payload_json,
                            payload_sha256,
                        ),
                    )
                    connection.commit()
            return SaveAnalysisResult(
                record=record,
                payload_sha256=payload_sha256,
                created=True,
            )
        except AnalysisPersistenceError:
            raise
        except FileLockTimeoutError as exc:
            raise AnalysisPersistenceError(
                str(exc),
                retryable=True,
                details={"database_path": str(self._database_path)},
            ) from exc
        except (OSError, sqlite3.DatabaseError) as exc:
            raise AnalysisPersistenceError(
                "保存分析结果失败",
                details={"database_path": str(self._database_path)},
            ) from exc

    def latest(self, request: LatestAnalysisRequest) -> LatestAnalysisResult:
        try:
            with FileLock(self._lock_path, exclusive=False):
                with closing(self._connect()) as connection:
                    sql = (
                        "SELECT analysis_run_id, product_id, product_revision, "
                        "ledger_revision, payload_json, payload_sha256 "
                        "FROM analysis_records WHERE product_id = ?"
                    )
                    parameters: list[str] = [request.product_id]
                    if request.ledger_revision is not None:
                        sql += " AND ledger_revision = ?"
                        parameters.append(request.ledger_revision)
                    sql += " ORDER BY created_at DESC, analysis_run_id DESC"
                    rows = connection.execute(sql, parameters).fetchall()
        except FileLockTimeoutError as exc:
            raise AnalysisPersistenceError(
                str(exc),
                retryable=True,
                details={"database_path": str(self._database_path)},
            ) from exc
        except (OSError, sqlite3.DatabaseError) as exc:
            raise AnalysisPersistenceError(
                "读取分析结果失败",
                details={"database_path": str(self._database_path)},
            ) from exc

        errors: list[ErrorDetail] = []
        for row in rows:
            try:
                payload_json = str(row[4])
                if self._sha256(payload_json) != str(row[5]):
                    raise ValueError("分析记录哈希不一致")
                record = AnalysisRecord.model_validate_json(payload_json)
                report = record.result.report
                indexed_values = (
                    record.result.analysis_run_id,
                    report.product_id,
                    report.product_revision,
                    report.ledger_revision,
                )
                if indexed_values != tuple(str(value) for value in row[:4]):
                    raise ValueError("分析记录索引与内容不一致")
                return LatestAnalysisResult(record=record, errors=errors)
            except (ValidationError, ValueError) as exc:
                errors.append(
                    ErrorDetail(
                        code="analysis_record_invalid",
                        message=str(exc),
                        details={"analysis_run_id": str(row[0])},
                    )
                )
        return LatestAnalysisResult(errors=errors)

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        try:
            application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if application_id == 0 and version == 0:
                self._initialize(connection)
            elif application_id != APPLICATION_ID or version != DATABASE_SCHEMA_VERSION:
                raise AnalysisPersistenceError(
                    "SQLite 分析库格式不兼容",
                    details={
                        "application_id": application_id,
                        "database_schema_version": version,
                    },
                )
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.executescript(
            """
            CREATE TABLE analysis_records (
                analysis_run_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                product_revision TEXT NOT NULL,
                ledger_revision TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            CREATE INDEX idx_analysis_records_product_created
                ON analysis_records(product_id, created_at DESC, analysis_run_id DESC);
            """
        )
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.commit()

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
