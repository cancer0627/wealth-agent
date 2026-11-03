"""Model-backed structured planner for goal-oriented Agent tasks."""

from __future__ import annotations

import json

from pydantic import ValidationError
from wealth_agent.capabilities.agent.contract import (
    AgentRuntimeError,
    PlanAgentResult,
    PlanAgentTasksRequest,
)
from wealth_agent.capabilities.tasks.contract import TaskPlanV2
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.model import ModelGateway, ModelGatewayError, ModelMessage, ModelRequest

_PLANNING_INSTRUCTIONS = (
    "你是本地理财 Agent 的规划器。只输出严格 JSON。"
    '若信息不足，输出 {"status":"needs_clarification","clarification":"..."}。'
    '否则输出 {"status":"planned","plan":{"schema_version":2,"steps":[...]}}。'
    "plan 不得包含 source_text，该字段由系统绑定原始用户请求。"
    "每个任务包含 step_id、objective、success_criteria、allowed_tools；"
    "allowed_tools 只能从用户消息提供的可用工具中选择，不得生成 Tool 参数。"
    "读取事实必须使用读取 Tool；新增估值、转入或转出只能使用 add_ledger_record；"
    "更新产品类型、风险等级或业绩比较基准只能使用 update_product_profile。"
    "用户要求分析、评价、是否继续持有或增持/减持建议时，必须使用 get_model_analysis；"
    "不得仅根据 get_performance_report 或 get_data_quality_warnings 自行生成增减持结论。"
    "update_product_profile 的产品 ID 和更新值必须来自用户请求或已调用 Tool 返回的事实，"
    "未提供的产品资料字段必须保留现有值。"
    "系统会在所有实际步骤完成后统一汇总最终答复，不要创建只用于综合、总结或输出结论的步骤。"
    "不得规划删除、批量写入、交易或备份恢复。"
)


@trace_public_methods(layer="capability")
class ModelTaskPlanner:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def plan(self, request: PlanAgentTasksRequest) -> PlanAgentResult:
        messages = [ModelMessage(role="system", content=_PLANNING_INSTRUCTIONS)]
        messages.extend(
            ModelMessage(role=item.role, content=item.content) for item in request.history
        )
        messages.append(
            ModelMessage(
                role="user",
                content=(
                    f"可用工具：{', '.join(request.available_tools)}\n"
                    f"活动产品：{request.active_product_id or '未选择'}\n"
                    f"用户请求：{request.source_text}"
                ),
            )
        )
        try:
            response = self._gateway.complete(
                ModelRequest(
                    messages=messages,
                    model=request.config.model,
                    timeout_seconds=request.config.timeout_seconds,
                    max_output_tokens=request.config.max_output_tokens,
                    response_format="json_object",
                    tool_choice="none",
                    request_id=f"{request.correlation_id}:plan",
                )
            )
        except ModelGatewayError as exc:
            raise AgentRuntimeError(
                str(exc),
                code="model_gateway_error",
                recoverable=exc.retryable,
                retryable=exc.retryable,
            ) from exc
        if response.content is None or response.tool_calls:
            raise AgentRuntimeError(
                "规划模型必须只返回结构化 JSON",
                code="invalid_agent_plan",
                recoverable=True,
            )
        try:
            payload = json.loads(response.content)
            if not isinstance(payload, dict):
                raise ValueError("Agent 计划根节点必须是对象")
            if payload.get("status") == "planned":
                plan = payload.get("plan")
                if not isinstance(plan, dict):
                    raise ValueError("已规划结果必须包含计划对象")
                # source_text is trusted execution context, not model-generated data.
                plan["source_text"] = request.source_text
            result = PlanAgentResult.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise AgentRuntimeError(
                "Agent 计划未通过结构校验",
                code="invalid_agent_plan",
                recoverable=True,
            ) from exc
        if result.status == "planned":
            if not isinstance(result.plan, TaskPlanV2):
                raise AgentRuntimeError(
                    "新 Agent 计划必须使用 TaskPlanV2",
                    code="invalid_agent_plan",
                    recoverable=True,
                )
            available = set(request.available_tools)
            selected = {tool for step in result.plan.steps for tool in step.allowed_tools}
            if not selected.issubset(available):
                raise AgentRuntimeError(
                    "Agent 计划包含不可用 Tool",
                    code="tool_not_available",
                    recoverable=True,
                )
        return result.model_copy(update={"usage": response.usage})


__all__ = ["ModelTaskPlanner"]
