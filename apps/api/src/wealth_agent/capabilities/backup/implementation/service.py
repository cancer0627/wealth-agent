"""Filesystem backup media built on the public portfolio snapshot port."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError
from wealth_agent.capabilities.backup.contract import (
    BackupCorruptError,
    BackupError,
    BackupListItem,
    BackupListResult,
    BackupManifest,
    BackupNotFoundError,
    BackupVerification,
    RestoreRequest,
    RestoreResult,
)
from wealth_agent.capabilities.portfolio.contract import (
    DatabaseSnapshot,
    PortfolioBackupPort,
    RestoreSnapshotRequest,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.logging import trace_public_methods

BACKUP_ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{8}$")


@trace_public_methods(layer="capability")
class LocalBackupService:
    def __init__(self, backups_dir: Path, portfolio: PortfolioBackupPort, clock: Clock) -> None:
        self._backups_dir = backups_dir
        self._portfolio = portfolio
        self._clock = clock

    def create(self) -> BackupManifest:
        try:
            return self._create(reason="manual")
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError(
                "创建备份失败",
                details={"backups_dir": str(self._backups_dir)},
            ) from exc

    def list(self) -> BackupListResult:
        try:
            if not self._backups_dir.exists():
                return BackupListResult()
            items: list[BackupListItem] = []
            for path in sorted(self._backups_dir.iterdir(), reverse=True):
                if not path.is_dir() or path.name.startswith("."):
                    continue
                verification = self.verify(path.name)
                items.append(
                    BackupListItem(
                        backup_id=path.name,
                        valid=verification.valid,
                        manifest=verification.manifest,
                        errors=verification.errors,
                    )
                )
            return BackupListResult(backups=items)
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError(
                "读取备份列表失败",
                details={"backups_dir": str(self._backups_dir)},
            ) from exc

    def verify(self, backup_id: str) -> BackupVerification:
        try:
            return self._verify(backup_id)
        except BackupError:
            raise
        except OSError:
            return BackupVerification(
                backup_id=backup_id,
                valid=False,
                errors=[
                    ErrorDetail(
                        code="backup_io_error",
                        message="读取备份文件失败",
                        details={"backup_id": backup_id},
                    )
                ],
            )

    def _verify(self, backup_id: str) -> BackupVerification:
        try:
            manifest = self._read_manifest(backup_id)
        except BackupCorruptError as exc:
            return BackupVerification(backup_id=backup_id, valid=False, errors=[exc.detail])
        database = self._backup_path(backup_id) / manifest.file_name
        errors: list[ErrorDetail] = []
        if not database.is_file():
            errors.append(
                ErrorDetail(
                    code="backup_database_missing",
                    message="备份数据库不存在",
                    details={"backup_id": backup_id, "file_name": manifest.file_name},
                )
            )
        else:
            actual_size = database.stat().st_size
            if actual_size != manifest.file_size:
                errors.append(
                    ErrorDetail(
                        code="backup_size_mismatch",
                        message="备份文件大小与 manifest 不一致",
                        details={"expected": manifest.file_size, "actual": actual_size},
                    )
                )
            actual_sha256 = self._sha256(database)
            if actual_sha256 != manifest.sha256:
                errors.append(
                    ErrorDetail(
                        code="backup_checksum_mismatch",
                        message="备份 SHA-256 与 manifest 不一致",
                        details={"expected": manifest.sha256, "actual": actual_sha256},
                    )
                )
            try:
                snapshot = self._portfolio.inspect_snapshot(database)
                if snapshot.database_schema_version != manifest.database_schema_version:
                    errors.append(
                        ErrorDetail(
                            code="backup_database_schema_mismatch",
                            message="备份数据库 Schema 与 manifest 不一致",
                            details={
                                "expected": manifest.database_schema_version,
                                "actual": snapshot.database_schema_version,
                            },
                        )
                    )
                if snapshot.ledger_revision != manifest.ledger_revision:
                    errors.append(
                        ErrorDetail(
                            code="backup_ledger_revision_mismatch",
                            message="备份 ledger revision 与 manifest 不一致",
                            details={
                                "expected": manifest.ledger_revision,
                                "actual": snapshot.ledger_revision,
                            },
                        )
                    )
            except (OSError, RuntimeError) as exc:
                errors.append(
                    ErrorDetail(
                        code="backup_snapshot_invalid",
                        message=str(exc),
                        details={"backup_id": backup_id},
                    )
                )
        return BackupVerification(
            backup_id=backup_id,
            valid=not errors,
            manifest=manifest,
            errors=errors,
        )

    def restore(self, request: RestoreRequest) -> RestoreResult:
        try:
            return self._restore(request)
        except BackupError:
            raise
        except OSError as exc:
            raise BackupError(
                "恢复备份失败",
                details={"backup_id": request.backup_id},
            ) from exc

    def _restore(self, request: RestoreRequest) -> RestoreResult:
        if request.confirmation_backup_id != request.backup_id:
            raise BackupCorruptError(
                "确认输入必须与目标 backup ID 完全一致",
                details={
                    "backup_id": request.backup_id,
                    "confirmation_backup_id": request.confirmation_backup_id,
                },
            )
        verification = self.verify(request.backup_id)
        if not verification.valid or verification.manifest is None:
            raise BackupCorruptError(
                "；".join(error.message for error in verification.errors) or "备份校验失败",
                details={
                    "backup_id": request.backup_id,
                    "errors": [error.model_dump(mode="json") for error in verification.errors],
                },
            )

        now = self._clock.now()
        pre_restore_id = self._new_backup_id(now)
        staging = self._backups_dir / f".{pre_restore_id}.tmp"
        staging.mkdir(parents=True, mode=0o700)
        previous_database = staging / "wealth.db"
        source = self._backup_path(request.backup_id) / "wealth.db"
        try:
            result = self._portfolio.restore_snapshot(
                RestoreSnapshotRequest(
                    source=source,
                    expected_revision=request.expected_revision,
                    previous_target=previous_database,
                )
            )
            manifest = self._manifest(
                pre_restore_id,
                previous_database,
                result.previous,
                reason="pre_restore",
            )
            self._write_manifest(staging, manifest)
            os.replace(staging, self._backups_dir / pre_restore_id)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return RestoreResult(
            backup_id=request.backup_id,
            restored_ledger_revision=result.restored.ledger_revision,
            pre_restore_backup_id=pre_restore_id,
        )

    def _create(self, *, reason: Literal["manual", "pre_restore"]) -> BackupManifest:
        now = self._clock.now()
        backup_id = self._new_backup_id(now)
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        staging = self._backups_dir / f".{backup_id}.tmp"
        staging.mkdir(mode=0o700)
        database = staging / "wealth.db"
        try:
            snapshot = self._portfolio.create_snapshot(database)
            manifest = self._manifest(backup_id, database, snapshot, reason=reason)
            self._write_manifest(staging, manifest)
            os.replace(staging, self._backups_dir / backup_id)
            return manifest
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _manifest(
        self,
        backup_id: str,
        database: Path,
        snapshot: DatabaseSnapshot,
        *,
        reason: Literal["manual", "pre_restore"],
    ) -> BackupManifest:
        return BackupManifest(
            backup_id=backup_id,
            created_at=self._clock.now(),
            database_schema_version=snapshot.database_schema_version,
            ledger_schema_version=snapshot.ledger_schema_version,
            ledger_revision=snapshot.ledger_revision,
            file_size=database.stat().st_size,
            sha256=self._sha256(database),
            reason=reason,
        )

    @staticmethod
    def _write_manifest(directory: Path, manifest: BackupManifest) -> None:
        target = directory / "manifest.json"
        temporary = directory / ".manifest.json.tmp"
        temporary.write_text(
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)

    def _read_manifest(self, backup_id: str) -> BackupManifest:
        path = self._backup_path(backup_id)
        if not path.is_dir():
            raise BackupNotFoundError("指定备份不存在", details={"backup_id": backup_id})
        try:
            return BackupManifest.model_validate_json(
                (path / "manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise BackupCorruptError(
                "备份 manifest 损坏或不兼容",
                details={"backup_id": backup_id},
            ) from exc

    def _backup_path(self, backup_id: str) -> Path:
        if not BACKUP_ID_PATTERN.fullmatch(backup_id):
            raise BackupNotFoundError("备份 ID 格式无效", details={"backup_id": backup_id})
        return self._backups_dir / backup_id

    @staticmethod
    def _new_backup_id(now: datetime) -> str:
        return f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
