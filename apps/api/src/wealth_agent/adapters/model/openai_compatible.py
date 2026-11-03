"""OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import cast

from pydantic import ValidationError

from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.model import (
    ModelGatewayError,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
)

RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}
HttpSender = Callable[[urllib.request.Request, float], bytes]


def default_http_sender(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return cast(bytes, response.read())


@trace_public_methods(layer="adapter")
class OpenAiCompatibleGateway:
    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str,
        display_name: str,
        api_key_name: str,
        extra_payload: Mapping[str, object] | None = None,
        sender: HttpSender = default_http_sender,
    ) -> None:
        self._api_key = api_key.strip()
        self._endpoint = endpoint
        self._display_name = display_name
        self._api_key_name = api_key_name
        self._extra_payload = dict(extra_payload or {})
        self._sender = sender

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not self._api_key:
            raise ModelGatewayError(
                f"未配置 {self._api_key_name}", code="missing_api_key", retryable=False
            )

        payload: dict[str, object] = {
            "model": request.model,
            "messages": [
                message.model_dump(mode="json", exclude_none=True)
                for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
            "stream": False,
            "temperature": 0,
            **self._extra_payload,
        }
        if request.response_format is not None:
            payload["response_format"] = {"type": request.response_format}
        if request.tools is not None:
            payload["tools"] = [tool.model_dump(mode="json") for tool in request.tools]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice

        http_request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            raw_response = self._sender(http_request, request.timeout_seconds)
        except urllib.error.HTTPError as exc:
            raise ModelGatewayError(
                f"{self._display_name} 请求失败（HTTP {exc.code}）",
                code="http_error",
                retryable=exc.code in RETRYABLE_HTTP_STATUSES,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelGatewayError(
                f"{self._display_name} 请求超时或网络不可用",
                code="network_error",
                retryable=True,
            ) from exc

        try:
            data = json.loads(raw_response.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("response root must be an object")
            choices = data.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("response choices are invalid")
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict):
                raise ValueError("response message is invalid")
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("response content is invalid")
            raw_tool_calls = message.get("tool_calls") or []
            if not isinstance(raw_tool_calls, list):
                raise ValueError("response tool_calls are invalid")
            return ModelResponse(
                content=content,
                tool_calls=[ModelToolCall.model_validate(item) for item in raw_tool_calls],
                model=str(data.get("model") or request.model),
                finish_reason=self._optional_string(choice.get("finish_reason")),
                usage=self._parse_usage(data.get("usage")),
            )
        except (json.JSONDecodeError, UnicodeError, TypeError, ValueError, ValidationError) as exc:
            raise ModelGatewayError(
                f"{self._display_name} 返回了无效响应",
                code="invalid_response",
                retryable=not isinstance(exc, ValidationError),
            ) from exc

    @staticmethod
    def _optional_string(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _optional_non_negative_int(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return None

    @classmethod
    def _parse_usage(cls, value: object) -> ModelUsage:
        if not isinstance(value, Mapping):
            return ModelUsage()
        prompt_details = value.get("prompt_tokens_details")
        completion_details = value.get("completion_tokens_details")
        return ModelUsage(
            input_tokens=cls._optional_non_negative_int(value.get("prompt_tokens")),
            output_tokens=cls._optional_non_negative_int(value.get("completion_tokens")),
            total_tokens=cls._optional_non_negative_int(value.get("total_tokens")),
            cached_input_tokens=(
                cls._optional_non_negative_int(prompt_details.get("cached_tokens"))
                if isinstance(prompt_details, Mapping)
                else None
            ),
            reasoning_tokens=(
                cls._optional_non_negative_int(completion_details.get("reasoning_tokens"))
                if isinstance(completion_details, Mapping)
                else None
            ),
            usage_source="provider",
        )
