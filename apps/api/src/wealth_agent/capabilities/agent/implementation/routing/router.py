"""Model-backed router constrained to read-only Agent intents."""

from __future__ import annotations

from pydantic import ValidationError
from wealth_agent.capabilities.agent.contract import (
    AgentRuntimeError,
    RouteAgentRequest,
    RouteAgentResult,
)
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.model import (
    ModelGateway,
    ModelGatewayError,
    ModelMessage,
    ModelRequest,
)

_ROUTING_INSTRUCTIONS = (
    "你是本地理财应用的只读请求路由器。只输出 JSON，不执行工具，不提供投资结论。"
    "intent 只能是 list_products、product_question 或 unsupported。"
    "list_products 的 requested_tools 必须仅为 list_products。"
    "product_question 的 requested_tools 只能从 get_product_profile、"
    "get_product_snapshot、get_performance_report、get_data_quality_warnings、"
    "get_model_analysis 中选择，"
    "按获取事实所需顺序返回且不得重复。unsupported 必须返回非空 clarification。"
    "不要生成或猜测 product_id；产品由应用会话上下文绑定。"
)


@trace_public_methods(layer="capability")
class ModelAgentRouter:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    def route(self, request: RouteAgentRequest) -> RouteAgentResult:
        messages = [ModelMessage(role="system", content=_ROUTING_INSTRUCTIONS)]
        messages.extend(
            ModelMessage(role=item.role, content=item.content) for item in request.history
        )
        messages.append(ModelMessage(role="user", content=request.question))
        try:
            response = self._gateway.complete(
                ModelRequest(
                    messages=messages,
                    model=request.config.model,
                    timeout_seconds=request.config.timeout_seconds,
                    max_output_tokens=request.config.max_output_tokens,
                    response_format="json_object",
                    tool_choice="none",
                    request_id=f"{request.correlation_id}:route",
                )
            )
        except ModelGatewayError as exc:
            raise AgentRuntimeError(
                str(exc),
                code="model_gateway_error",
                recoverable=exc.retryable,
                retryable=exc.retryable,
            ) from exc
        if response.tool_calls or response.content is None:
            raise AgentRuntimeError(
                "路由模型必须只返回结构化 JSON",
                code="invalid_router_output",
                recoverable=True,
            )
        try:
            route = RouteAgentResult.model_validate_json(response.content)
        except (ValidationError, ValueError) as exc:
            raise AgentRuntimeError(
                "路由模型输出未通过结构校验",
                code="invalid_router_output",
                recoverable=True,
            ) from exc
        return route.model_copy(update={"usage": response.usage})
