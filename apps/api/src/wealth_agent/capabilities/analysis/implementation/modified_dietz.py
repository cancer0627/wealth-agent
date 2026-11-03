"""Modified Dietz calculation without model or persistence dependencies."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Literal

from wealth_agent.capabilities.analysis.contract import ReturnResult, WeightedCashFlow
from wealth_agent.capabilities.portfolio.contract import CashFlow, Valuation
from wealth_agent.shared.logging import trace_call


@trace_call(layer="capability")
def calculate_return(
    beginning: Valuation,
    ending: Valuation,
    all_cash_flows: list[CashFlow],
) -> ReturnResult:
    total_days = (ending.date - beginning.date).days
    if total_days <= 0:
        raise ValueError("收益区间结束日期必须晚于开始日期")

    relevant_flows = [
        flow for flow in all_cash_flows if beginning.date < flow.date <= ending.date
    ]
    weighted_flows: list[WeightedCashFlow] = []
    with localcontext() as context:
        context.prec = 28
        for flow in relevant_flows:
            days_from_start = (flow.date - beginning.date).days
            weight = Decimal(total_days - days_from_start) / Decimal(total_days)
            signed_amount = flow.signed_amount
            weighted_flows.append(
                WeightedCashFlow(
                    flow_id=flow.flow_id,
                    date=flow.date,
                    type=flow.type,
                    amount=flow.amount,
                    signed_amount=signed_amount,
                    days_from_start=days_from_start,
                    weight=weight,
                    weighted_amount=signed_amount * weight,
                )
            )
        net_cash_flow = sum(
            (item.signed_amount for item in weighted_flows), Decimal("0")
        )
        weighted_cash_flow = sum(
            (item.weighted_amount for item in weighted_flows), Decimal("0")
        )
        net_profit = ending.amount - beginning.amount - net_cash_flow
        weighted_capital = beginning.amount + weighted_cash_flow
        warnings: list[str] = []
        status: Literal["calculated", "invalid_denominator"]
        if weighted_capital <= 0:
            status = "invalid_denominator"
            modified_dietz_return = None
            warnings.append(
                f"{beginning.date} 至 {ending.date} 的加权资金小于或等于零，"
                "无法计算 Modified Dietz 收益率"
            )
        else:
            status = "calculated"
            modified_dietz_return = net_profit / weighted_capital

    return ReturnResult(
        status=status,
        start_date=beginning.date,
        end_date=ending.date,
        total_days=total_days,
        beginning_value=beginning.amount,
        ending_value=ending.amount,
        cash_flows=weighted_flows,
        net_cash_flow=net_cash_flow,
        net_profit=net_profit,
        weighted_cash_flow=weighted_cash_flow,
        weighted_capital=weighted_capital,
        modified_dietz_return=modified_dietz_return,
        warnings=warnings,
    )
