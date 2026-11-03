"""Ledger backup media and restore public contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails


class BackupContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackupManifest(BackupContract):
    manifest_schema_version: Literal[1] = 1
    backup_id: str
    created_at: datetime
    database_schema_version: int = Field(ge=1)
    ledger_schema_version: int = Field(ge=1)
    ledger_revision: str
    file_name: Literal["wealth.db"] = "wealth.db"
    file_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: Literal["manual", "pre_restore"] = "manual"


class BackupListItem(BackupContract):
    backup_id: str
    valid: bool
    manifest: BackupManifest | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)


class BackupListResult(BackupContract):
    backups: list[BackupListItem] = Field(default_factory=list)


class BackupVerification(BackupContract):
    backup_id: str
    valid: bool
    manifest: BackupManifest | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)


class RestoreRequest(BackupContract):
    backup_id: str = Field(min_length=1)
    confirmation_backup_id: str = Field(min_length=1)
    expected_revision: str = Field(min_length=1)


class RestoreResult(BackupContract):
    backup_id: str
    restored_ledger_revision: str
    pre_restore_backup_id: str


class BackupError(RuntimeError):
    code = "backup_error"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        details: ErrorDetails | None = None,
    ) -> None:
        self.detail = ErrorDetail(
            code=self.code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        super().__init__(message)


class BackupNotFoundError(BackupError):
    code = "backup_not_found"


class BackupCorruptError(BackupError):
    code = "backup_corrupt"


class BackupService(Protocol):
    def create(self) -> BackupManifest: ...

    def list(self) -> BackupListResult: ...

    def verify(self, backup_id: str) -> BackupVerification: ...

    def restore(self, request: RestoreRequest) -> RestoreResult: ...


__all__ = [
    "BackupCorruptError",
    "BackupError",
    "BackupListItem",
    "BackupListResult",
    "BackupManifest",
    "BackupNotFoundError",
    "BackupService",
    "BackupVerification",
    "RestoreRequest",
    "RestoreResult",
]
