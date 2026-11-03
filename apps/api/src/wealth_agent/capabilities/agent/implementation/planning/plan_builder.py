"""Deterministic TaskPlan builder for routed read-only tools."""

from __future__ import annotations

from pydantic import JsonValue
from wealth_agent.capabilities.agent.contract import PlanAgentRequest, PlanAgentResult
from wealth_agent.capabilities.tasks.contract import TaskPayload, TaskPlan, TaskStep
from wealth_agent.shared.logging import trace_public_methods


@trace_public_methods(layer="capability")
class ReadOnlyAgentPlanner:
    def plan(self, request: PlanAgentRequest) -> PlanAgentResult:
        if request.route.intent == "unsupported":
            return PlanAgentResult(
                status="needs_clarification",
                clarification=request.route.clarification,
            )
        product_tools = [
            name for name in request.route.requested_tools if name != "list_products"
        ]
        if product_tools and request.active_product_id is None:
            return PlanAgentResult(
                status="needs_clarification",
                clarification="请先明确要查询的产品。",
            )
        steps = []
        for index, action in enumerate(request.route.requested_tools, start=1):
            data: dict[str, JsonValue] = {"schema_version": 1}
            if action != "list_products":
                data["product_id"] = request.active_product_id
            steps.append(
                TaskStep(
                    step_id=f"read-{index}",
                    action=action,
                    arguments=TaskPayload(
                        type="agent.tool.arguments.v1",
                        data=data,
                    ),
                    description=f"执行只读工具 {action}",
                )
            )
        return PlanAgentResult(
            status="planned",
            plan=TaskPlan(source_text=request.source_text, steps=steps),
        )
