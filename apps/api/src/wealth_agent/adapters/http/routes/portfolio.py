"""Portfolio HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Path

from wealth_agent.adapters.http.contracts import PortfolioCommandRequest
from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.capabilities.portfolio.contract import (
    MutationResult,
    PortfolioSnapshot,
    ProductCatalog,
    ProductLedgerView,
)
from wealth_agent.shared.logging import trace_call


def build_portfolio_router(dependencies: HttpDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/portfolio")
    @trace_call(layer="adapter")
    def portfolio_snapshot() -> PortfolioSnapshot:
        return dependencies.portfolio_reader.snapshot()

    @router.get("/api/v1/products")
    @trace_call(layer="adapter")
    def list_products() -> ProductCatalog:
        return dependencies.portfolio_reader.list_products()

    @router.get("/api/v1/products/{product_id}")
    @trace_call(layer="adapter")
    def get_product(product_id: str = Path(min_length=1)) -> ProductLedgerView:
        return dependencies.portfolio_reader.get_product(product_id)

    @router.post("/api/v1/portfolio/commands")
    @trace_call(layer="adapter")
    def execute_portfolio_command(request: PortfolioCommandRequest) -> MutationResult:
        return dependencies.portfolio_writer.execute(request.root)

    return router


__all__ = ["build_portfolio_router"]
