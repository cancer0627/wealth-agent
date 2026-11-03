"""Deterministic product report assembly."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from wealth_agent.capabilities.analysis.contract import (
    AnalysisError,
    ProductReport,
    ProductReportRequest,
    ReturnResult,
)
from wealth_agent.capabilities.analysis.implementation.modified_dietz import calculate_return
from wealth_agent.capabilities.portfolio.contract import (
    CashFlow,
    ProductSnapshot,
    Valuation,
)
from wealth_agent.shared.logging import trace_public_methods


@trace_public_methods(layer="capability")
class DeterministicAnalysis:
    def report(self, request: ProductReportRequest) -> ProductReport:
        product = request.product
        valuations = sorted(product.valuations, key=lambda item: item.date)
        cash_flows = sorted(product.cash_flows, key=lambda item: (item.date, item.created_at))
        total_transfer_in = sum(
            (flow.amount for flow in cash_flows if flow.type == "transfer_in"),
            Decimal("0"),
        )
        total_transfer_out = sum(
            (flow.amount for flow in cash_flows if flow.type == "transfer_out"),
            Decimal("0"),
        )
        warnings = self._warnings(product, valuations, cash_flows)
        intervals: list[ReturnResult] = []
        overall: ReturnResult | None = None
        status: Literal["calculated", "insufficient_data"]
        if len(valuations) < 2:
            status = "insufficient_data"
            warnings.append("至少需要两个估值日才能计算收益")
        else:
            status = "calculated"
            try:
                for beginning, ending in zip(valuations, valuations[1:], strict=False):
                    result = calculate_return(beginning, ending, cash_flows)
                    intervals.append(result)
                    warnings.extend(result.warnings)
                overall = calculate_return(valuations[0], valuations[-1], cash_flows)
                warnings.extend(overall.warnings)
            except ValueError as exc:
                raise AnalysisError(
                    str(exc),
                    details={
                        "product_id": product.product_id,
                        "ledger_revision": request.ledger_revision,
                    },
                ) from exc

        return ProductReport(
            ledger_revision=request.ledger_revision,
            product_id=product.product_id,
            product_name=product.name,
            product_revision=product.revision,
            generated_at=request.generated_at,
            status=status,
            profile=product.profile,
            total_transfer_in=total_transfer_in,
            total_transfer_out=total_transfer_out,
            net_cash_flow=total_transfer_in - total_transfer_out,
            valuations=valuations,
            cash_flows=cash_flows,
            overall=overall,
            intervals=intervals,
            warnings=list(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _warnings(
        product: ProductSnapshot,
        valuations: list[Valuation],
        cash_flows: list[CashFlow],
    ) -> list[str]:
        warnings: list[str] = []
        if product.profile.missing_fields:
            warnings.append("产品资料不完整：缺少" + "、".join(product.profile.missing_fields))
        if not valuations:
            return warnings
        before = [flow for flow in cash_flows if flow.date <= valuations[0].date]
        after = [flow for flow in cash_flows if flow.date > valuations[-1].date]
        if before:
            warnings.append(f"有 {len(before)} 笔现金流发生在首个估值日或之前，未纳入收益计算")
        if after:
            warnings.append(f"有 {len(after)} 笔现金流发生在最新估值日之后，未纳入收益计算")
        return warnings
