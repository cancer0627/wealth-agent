"""模型供应商无关的请求、响应、Tool（工具）、用量、异常和网关契约。"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelContract(BaseModel):
    """共享模型边界的严格基础数据模型。"""

    model_config = ConfigDict(extra="forbid")


class ToolFunctionCall(ModelContract):
    name: str = Field(min_length=1, max_length=64)
    arguments: str


class ModelToolCall(ModelContract):
    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: ToolFunctionCall
    index: int | None = Field(default=None, ge=0)


class ToolFunctionDefinition(ModelContract):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1)
    parameters: dict[str, object]


class ModelToolDefinition(ModelContract):
    type: Literal["function"] = "function"
    function: ToolFunctionDefinition


class ModelMessage(ModelContract):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ModelToolCall] | None = None
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> ModelMessage:
        if self.role in {"system", "user"} and self.content is None:
            raise ValueError(f"{self.role} message 必须包含 content")
        if self.role == "tool" and (self.content is None or not self.tool_call_id):
            raise ValueError("tool message 必须包含 content 和 tool_call_id")
        if self.role != "assistant" and self.tool_calls is not None:
            raise ValueError("只有 assistant message 可以包含 tool_calls")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("只有 tool message 可以包含 tool_call_id")
        if self.role == "assistant" and self.content is None and not self.tool_calls:
            raise ValueError("assistant message 必须包含 content 或 tool_calls")
        return self


class ModelRequest(ModelContract):
    messages: list[ModelMessage] = Field(min_length=1)
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    response_format: Literal["json_object"] | None = "json_object"
    tools: list[ModelToolDefinition] | None = None
    tool_choice: Literal["auto", "none", "required"] | None = None
    request_id: str | None = None


class ModelUsage(ModelContract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_source: Literal["provider", "estimated", "unavailable"] = "unavailable"


class ModelResponse(ModelContract):
    content: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    model: str
    model_revision: str | None = None
    finish_reason: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ModelGatewayError(RuntimeError):
    """具体模型适配器统一抛出的稳定异常。"""

    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ModelGateway(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...
