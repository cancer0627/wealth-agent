"""Backup HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Path

from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.adapters.http.errors import HttpApiError
from wealth_agent.capabilities.backup.contract import (
    BackupListResult,
    BackupManifest,
    BackupVerification,
    RestoreRequest,
    RestoreResult,
)
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.logging import trace_call


def build_backup_router(dependencies: HttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/backups")
    @trace_call(layer="adapter")
    def list_backups() -> BackupListResult:
        return dependencies.backup.list()

    @router.post("/api/v1/backups")
    @trace_call(layer="adapter")
    def create_backup() -> BackupManifest:
        return dependencies.backup.create()

    @router.get("/api/v1/backups/{backup_id}/verification")
    @trace_call(layer="adapter")
    def verify_backup(backup_id: str = Path(min_length=1)) -> BackupVerification:
        return dependencies.backup.verify(backup_id)

    @router.post("/api/v1/backups/{backup_id}/restore")
    @trace_call(layer="adapter")
    def restore_backup(request: RestoreRequest, backup_id: str) -> RestoreResult:
        if request.backup_id != backup_id:
            raise HttpApiError(
                422,
                ErrorDetail(
                    code="backup_id_mismatch",
                    message="路径与请求中的备份标识不一致",
                ),
            )
        return dependencies.backup.restore(request)

    return router


__all__ = ["build_backup_router"]
