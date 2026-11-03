"""Bounded read-only Agent orchestration and tool loop."""

from __future__ import annotations

import json

from pydantic import ValidationError
from wealth_agent.capabilities.agent.contract import (
    AgentAnswer,
    AgentPlannerPort,
    AgentRouterPort,
    AgentRuntimeError,
    AgentToolDefinitionsRequest,
    AgentToolError,
    AgentToolPort,
    AgentToolTrace,
    ExecuteAgentToolRequest,
    PlanAgentRequest,
    RouteAgentResult,
    RunAgentRequest,
    RunAgentResult,
)
from wealth_agent.capabilities.tasks.contract import TaskPlan
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.model import (
    ModelGateway,
    ModelGatewayError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelToolDefinition,
    ModelUsage,
)

_AGENT_INSTRUCTIONS = (
    "你是本地理财应用的只读事实助手。只能调用提供的工具，不能写入资产或执行交易。"
    "不得调用计划之外的工具，不得猜测工具未返回的事实。"
    "完成后只输出 JSON：status 为 answered 或 needs_clarification，answer 为非空中文文本。"
)


@trace_public_methods(layer="capability")
class ReadOnlyAgentRuntime:
    def __init__(
        self,
        gateway: ModelGateway,
        tools: AgentToolPort,
        router: AgentRouterPort,
        planner: AgentPlannerPort,
    ) -> None:
        self._gateway = gateway
        self._tools = tools
        self._router = router
        self._planner = planner

    def run(self, request: RunAgentRequest) -> RunAgentResult:
        usages: list[ModelUsage] = []
        traces: list[AgentToolTrace] = []
        try:
            route = self._router.route(request)
            usages.append(route.usage)
            planned = self._planner.plan(
                PlanAgentRequest(
                    source_text=request.question,
                    route=route,
                    active_product_id=request.active_product_id,
                )
            )
            if planned.status == "needs_clarification":
                answer = AgentAnswer(
                    status="needs_clarification",
                    answer=planned.clarification or "请补充查询信息。",
                )
                return RunAgentResult(
                    status="needs_clarification",
                    route=route,
                    answer=answer,
                    model_usages=usages,
                )
            plan = planned.plan
            if plan is None:
                raise AgentRuntimeError(
                    "计划器未返回任务计划",
                    code="invalid_agent_plan",
                )
            if not isinstance(plan, TaskPlan):
                raise AgentRuntimeError(
                    "旧只读运行时不支持 V2 任务计划",
                    code="invalid_agent_plan",
                )
            allowed_actions = {step.action for step in plan.steps}
            definitions = self._tools.definitions(AgentToolDefinitionsRequest())
            model_tools = [
                item
                for item in definitions.definitions
                if item.function.name in allowed_actions
            ]
            messages = [ModelMessage(role="system", content=_AGENT_INSTRUCTIONS)]
            messages.extend(
                ModelMessage(role=item.role, content=item.content)
                for item in request.history
            )
            messages.append(ModelMessage(role="user", content=request.question))

            for request_number in range(2, request.config.max_model_requests + 1):
                response = self._complete(request, messages, model_tools, request_number)
                usages.append(response.usage)
                if response.tool_calls:
                    if len(traces) + len(response.tool_calls) > request.config.max_tool_calls:
                        raise AgentRuntimeError(
                            "Agent 已达到最大工具调用次数",
                            code="max_tool_calls",
                            recoverable=True,
                        )
                    messages.append(
                        ModelMessage(
                            role="assistant",
                            content=response.content,
                            tool_calls=response.tool_calls,
                        )
                    )
                    for call in response.tool_calls:
                        if call.function.name not in allowed_actions:
                            error = ErrorDetail(
                                code="tool_not_in_plan",
                                message="模型调用了计划之外的工具",
                                details={"tool_name": call.function.name},
                            )
                            traces.append(
                                AgentToolTrace(
                                    tool_call_id=call.id,
                                    tool_name=call.function.name,
                                    status="failed",
                                    error=error,
                                )
                            )
                            return self._failed(route, plan, traces, usages, error)
                        try:
                            tool_result = self._tools.execute(
                                ExecuteAgentToolRequest(
                                    call=call,
                                    granted_permissions=request.granted_permissions,
                                    selected_product_id=request.active_product_id,
                                )
                            )
                        except AgentToolError as exc:
                            traces.append(
                                AgentToolTrace(
                                    tool_call_id=call.id,
                                    tool_name=call.function.name,
                                    status="failed",
                                    error=exc.detail,
                                )
                            )
                            return self._failed(
                                route, plan, traces, usages, exc.detail
                            )
                        if (
                            tool_result.output.ledger_revision
                            != request.expected_ledger_revision
                        ):
                            error = ErrorDetail(
                                code="ledger_revision_conflict",
                                message="工具结果与 Agent 固定台账版本不一致",
                                details={
                                    "expected_revision": request.expected_ledger_revision,
                                    "actual_revision": tool_result.output.ledger_revision,
                                },
                            )
                            traces.append(
                                AgentToolTrace(
                                    tool_call_id=call.id,
                                    tool_name=call.function.name,
                                    status="failed",
                                    error=error,
                                )
                            )
                            return self._failed(route, plan, traces, usages, error)
                        traces.append(
                            AgentToolTrace(
                                tool_call_id=call.id,
                                tool_name=tool_result.tool_name,
                                status="completed",
                                output=tool_result.output,
                            )
                        )
                        messages.append(
                            ModelMessage(
                                role="tool",
                                tool_call_id=call.id,
                                content=json.dumps(
                                    tool_result.output.model_dump(mode="json"),
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            )
                        )
                    continue

                answer = self._parse_answer(response.content)
                if answer.status == "answered" and not traces:
                    raise AgentRuntimeError(
                        "Agent 未读取工具就生成了事实回答",
                        code="answer_without_evidence",
                        recoverable=True,
                    )
                return RunAgentResult(
                    status=answer.status,
                    route=route,
                    plan=plan,
                    answer=answer,
                    tool_traces=traces,
                    model_usages=usages,
                )
            raise AgentRuntimeError(
                "Agent 已达到最大模型请求次数",
                code="max_model_requests",
                recoverable=True,
            )
        except AgentToolError as exc:
            return RunAgentResult(
                status="failed",
                tool_traces=traces,
                model_usages=usages,
                error=exc.detail,
            )

    def _complete(
        self,
        request: RunAgentRequest,
        messages: list[ModelMessage],
        tools: list[ModelToolDefinition],
        request_number: int,
    ) -> ModelResponse:
        try:
            return self._gateway.complete(
                ModelRequest(
                    messages=messages,
                    model=request.config.model,
                    timeout_seconds=request.config.timeout_seconds,
                    max_output_tokens=request.config.max_output_tokens,
                    response_format="json_object",
                    tools=tools,
                    tool_choice="auto",
                    request_id=f"{request.correlation_id}:agent:{request_number}",
                )
            )
        except ModelGatewayError as exc:
            raise AgentRuntimeError(
                str(exc),
                code="model_gateway_error",
                recoverable=exc.retryable,
                retryable=exc.retryable,
            ) from exc

    @staticmethod
    def _parse_answer(content: str | None) -> AgentAnswer:
        if content is None:
            raise AgentRuntimeError(
                "模型未返回回答或工具调用",
                code="invalid_agent_output",
                recoverable=True,
            )
        try:
            return AgentAnswer.model_validate_json(content)
        except (ValidationError, ValueError) as exc:
            raise AgentRuntimeError(
                "Agent 回答未通过结构校验",
                code="invalid_agent_output",
                recoverable=True,
            ) from exc

    @staticmethod
    def _failed(
        route: RouteAgentResult,
        plan: TaskPlan,
        traces: list[AgentToolTrace],
        usages: list[ModelUsage],
        error: ErrorDetail,
    ) -> RunAgentResult:
        return RunAgentResult(
            status="failed",
            route=route,
            plan=plan,
            tool_traces=traces,
            model_usages=usages,
            error=error,
        )
