"""Local FastAPI application over public capability contracts."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import RequestResponseEndpoint

from wealth_agent.adapters.http.contracts import HealthResponse, HttpErrorResponse
from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.adapters.http.errors import register_error_handlers
from wealth_agent.adapters.http.routes import (
    build_analysis_router,
    build_backup_router,
    build_conversation_router,
    build_governance_router,
    build_portfolio_router,
)
from wealth_agent.shared.logging import trace_call, trace_context


def create_http_app(dependencies: HttpDependencies) -> FastAPI:
    app = FastAPI(
        title="wealth-agent-api",
        version="0.1.0",
        responses={
            400: {"model": HttpErrorResponse},
            404: {"model": HttpErrorResponse},
            409: {"model": HttpErrorResponse},
            422: {"model": HttpErrorResponse},
            500: {"model": HttpErrorResponse},
        },
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            "http://localhost:5174",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def bind_internal_trace(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        with trace_context():
            return await call_next(request)

    @app.get("/health", response_model=HealthResponse)
    @trace_call(layer="adapter")
    def health() -> HealthResponse:
        return HealthResponse()

    app.include_router(build_portfolio_router(dependencies))
    app.include_router(build_analysis_router(dependencies))
    app.include_router(build_conversation_router(dependencies))
    app.include_router(build_backup_router(dependencies))
    app.include_router(build_governance_router(dependencies))
    return app


__all__ = ["create_http_app"]
