"""Analysis HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Path, Query

from wealth_agent.adapters.http.contracts import AnalysisQuestionRequest
from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.capabilities.analysis.contract import (
    AnalysisResult,
    AnalyzeProductRequest,
    LatestAnalysisRequest,
    LatestAnalysisResult,
    ProductReport,
    ProductReportRequest,
)
from wealth_agent.shared.logging import trace_call


def build_analysis_router(dependencies: HttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/products/{product_id}/report")
    @trace_call(layer="adapter")
    def product_report(product_id: str = Path(min_length=1)) -> ProductReport:
        view = dependencies.portfolio_reader.get_product(product_id)
        return dependencies.reporting.report(
            ProductReportRequest(
                product=view.product,
                ledger_revision=view.ledger_revision,
                generated_at=dependencies.clock.now(),
            )
        )

    @router.post("/api/v1/products/{product_id}/analysis")
    @trace_call(layer="adapter")
    def analyze_product(
        request: AnalysisQuestionRequest,
        product_id: str = Path(min_length=1),
    ) -> AnalysisResult:
        view = dependencies.portfolio_reader.get_product(product_id)
        return dependencies.analysis.analyze(
            AnalyzeProductRequest(
                product=view.product,
                ledger_revision=view.ledger_revision,
                generated_at=dependencies.clock.now(),
                question=request.question,
            )
        )

    @router.get("/api/v1/products/{product_id}/analysis/latest")
    @trace_call(layer="adapter")
    def latest_analysis(
        product_id: str = Path(min_length=1),
        ledger_revision: str | None = Query(default=None, min_length=1),
    ) -> LatestAnalysisResult:
        return dependencies.analysis_repository.latest(
            LatestAnalysisRequest(
                product_id=product_id,
                ledger_revision=ledger_revision,
            )
        )

    return router


__all__ = ["build_analysis_router"]
