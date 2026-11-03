"""Portfolio-owned SQLite snapshot and atomic replacement operations."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from wealth_agent.capabilities.portfolio.contract import (
    DatabaseSnapshot,
    PortfolioError,
    RestoreSnapshotRequest,
    RestoreSnapshotResult,
    RevisionConflictError,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_reader import (
    SqlitePortfolioReader,
)
from wealth_agent.shared.file_lock import FileLock, FileLockTimeoutError
from wealth_agent.shared.logging import trace_public_methods


@trace_public_methods(layer="capability")
class SqlitePortfolioBackupPort:
    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._lock_path = database_path.parent / ".wealth.lock"

    def create_snapshot(self, target: Path) -> DatabaseSnapshot:
        try:
            with FileLock(self._lock_path, exclusive=False):
                return self._copy_and_inspect(self._database_path, target)
        except PortfolioError:
            raise
        except FileLockTimeoutError as exc:
            raise PortfolioError(
                str(exc),
                retryable=True,
                details={"target": str(target)},
            ) from exc
        except (OSError, sqlite3.DatabaseError) as exc:
            raise PortfolioError(
                "创建数据库快照失败",
                details={"target": str(target)},
            ) from exc

    def inspect_snapshot(self, source: Path) -> DatabaseSnapshot:
        try:
            snapshot = SqlitePortfolioReader(source).snapshot()
            with sqlite3.connect(source) as connection:
                database_schema_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
            return DatabaseSnapshot(
                database_schema_version=database_schema_version,
                ledger_schema_version=snapshot.schema_version,
                ledger_revision=snapshot.revision,
            )
        except PortfolioError:
            raise
        except (OSError, sqlite3.DatabaseError, TypeError) as exc:
            raise PortfolioError(
                "检查数据库快照失败",
                details={"source": str(source)},
            ) from exc

    def restore_snapshot(
        self,
        request: RestoreSnapshotRequest,
    ) -> RestoreSnapshotResult:
        try:
            return self._restore_snapshot(request)
        except PortfolioError:
            raise
        except FileLockTimeoutError as exc:
            raise PortfolioError(
                str(exc),
                retryable=True,
                details={"source": str(request.source)},
            ) from exc
        except (OSError, sqlite3.DatabaseError) as exc:
            raise PortfolioError(
                "恢复数据库快照失败",
                details={"source": str(request.source)},
            ) from exc

    def _restore_snapshot(self, request: RestoreSnapshotRequest) -> RestoreSnapshotResult:
        with FileLock(self._lock_path, exclusive=True):
            current = self.inspect_snapshot(self._database_path)
            if current.ledger_revision != request.expected_revision:
                raise RevisionConflictError(
                    "当前台账 revision 已变化，请重新确认恢复",
                    details={
                        "expected_revision": request.expected_revision,
                        "actual_revision": current.ledger_revision,
                    },
                )
            previous = self._copy_and_inspect(self._database_path, request.previous_target)
            restored = self.inspect_snapshot(request.source)
            temporary = self._database_path.parent / f".wealth.{uuid4().hex}.restoring"
            try:
                self._copy(request.source, temporary)
                self.inspect_snapshot(temporary)
                os.replace(temporary, self._database_path)
                directory_fd = os.open(self._database_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
            return RestoreSnapshotResult(restored=restored, previous=previous)

    def _copy_and_inspect(self, source: Path, target: Path) -> DatabaseSnapshot:
        target.parent.mkdir(parents=True, exist_ok=True)
        self._copy(source, target)
        return self.inspect_snapshot(target)

    @staticmethod
    def _copy(source: Path, target: Path) -> None:
        source_connection = sqlite3.connect(source)
        target_connection = sqlite3.connect(target)
        try:
            source_connection.backup(target_connection)
            target_connection.commit()
        finally:
            source_connection.close()
            target_connection.close()
