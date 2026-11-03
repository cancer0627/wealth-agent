"""Governance HTTP routes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query

from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.capabilities.governance.contract import (
    AuthorizationRequest,
    AuthorizationResult,
    EvaluationRequest,
    EvaluationResult,
    ListEventsRequest,
    ListEventsResult,
)
from wealth_agent.shared.logging import trace_call


def build_governance_router(dependencies: HttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/governance/authorize")
    @trace_call(layer="adapter")
    def authorize(request: AuthorizationRequest) -> AuthorizationResult:
        return dependencies.governance.authorize(request)

    @router.get("/api/v1/governance/events")
    @trace_call(layer="adapter")
    def list_governance_events(
        category: Literal["model", "tool", "task", "audit", "evaluation"] | None = Query(
            default=None
        ),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> ListEventsResult:
        return dependencies.telemetry.list(
            ListEventsRequest(category=category, limit=limit)
        )

    @router.post("/api/v1/governance/evaluations")
    @trace_call(layer="adapter")
    def evaluate(request: EvaluationRequest) -> EvaluationResult:
        return dependencies.evaluation.evaluate(request)

    return router


__all__ = ["build_governance_router"]
