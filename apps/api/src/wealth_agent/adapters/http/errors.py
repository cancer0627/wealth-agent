"""HTTP transport error mapping."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from wealth_agent.adapters.http.contracts import HttpErrorResponse
from wealth_agent.capabilities.analysis.contract import AnalysisError
from wealth_agent.capabilities.backup.contract import BackupError
from wealth_agent.capabilities.conversation.contract import ConversationError
from wealth_agent.capabilities.governance.contract import GovernanceError
from wealth_agent.capabilities.portfolio.contract import PortfolioError
from wealth_agent.capabilities.tasks.contract import TaskError
from wealth_agent.shared.errors import ErrorDetail

_CAPABILITY_ERRORS = (
    AnalysisError,
    BackupError,
    ConversationError,
    GovernanceError,
    PortfolioError,
    TaskError,
)


class HttpApiError(Exception):
    def __init__(self, status_code: int, detail: ErrorDetail) -> None:
        super().__init__(detail.message)
        self.status_code = status_code
        self.detail = detail


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HttpApiError)
    async def handle_http_error(_request: object, exc: HttpApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.detail)

    for error_type in _CAPABILITY_ERRORS:
        app.add_exception_handler(error_type, _handle_capability_error)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: object, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        detail = ErrorDetail(
            code="request_validation_error",
            message="请求结构校验失败",
            details={
                "location": [str(item) for item in first.get("loc", ())],
                "reason": str(first.get("msg", "invalid request")),
            },
        )
        return _error_response(422, detail)


async def _handle_capability_error(_request: object, exc: Exception) -> JSONResponse:
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, ErrorDetail):
        detail = ErrorDetail(code="internal_error", message="服务处理失败")
    return _error_response(_status_for(detail.code), detail)


def _status_for(code: str) -> int:
    if code.endswith("_not_found") or code in {"product_not_found", "task_not_found"}:
        return 404
    if "conflict" in code or code in {"backup_corrupt", "pending_ocr_draft"}:
        return 409
    if "corrupt" in code or "persistence" in code:
        return 500
    return 400


def _error_response(status_code: int, detail: ErrorDetail) -> JSONResponse:
    payload = HttpErrorResponse(error=detail)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


__all__ = ["HttpApiError", "register_error_handlers"]
