from __future__ import annotations

import json
import urllib.error
from urllib.request import Request

import pytest
from pydantic import ValidationError
from wealth_agent.adapters.model import DeepSeekGateway, KimiGateway
from wealth_agent.shared.model import ModelGatewayError, ModelMessage, ModelRequest


def _request() -> ModelRequest:
    return ModelRequest(
        messages=[ModelMessage(role="user", content="test")],
        model="test-model",
        timeout_seconds=3,
        max_output_tokens=128,
    )


def test_message_contract_rejects_invalid_role_fields() -> None:
    with pytest.raises(ValidationError):
        ModelMessage(role="tool", content="result")


def test_deepseek_maps_request_response_and_usage() -> None:
    captured: dict[str, object] = {}

    def sender(request: Request, timeout: float) -> bytes:
        captured["timeout"] = timeout
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads((request.data or b"").decode("utf-8"))
        return json.dumps(
            {
                "model": "deepseek-chat",
                "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            }
        ).encode()

    response = DeepSeekGateway("secret", sender=sender).complete(_request())

    assert captured["authorization"] == "Bearer secret"
    assert captured["timeout"] == 3
    assert isinstance(captured["payload"], dict)
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert response.content == "ok"
    assert response.usage.total_tokens == 3
    assert "provider" not in response.model_dump()


def test_kimi_does_not_add_deepseek_private_fields() -> None:
    payload: dict[str, object] = {}

    def sender(request: Request, _timeout: float) -> bytes:
        payload.update(json.loads((request.data or b"").decode("utf-8")))
        return b'{"choices":[{"message":{"content":"ok"}}]}'

    KimiGateway("secret", sender=sender).complete(_request())
    assert "thinking" not in payload


def test_missing_key_and_http_error_have_stable_error_contract() -> None:
    with pytest.raises(ModelGatewayError) as missing:
        DeepSeekGateway("").complete(_request())
    assert missing.value.code == "missing_api_key"
    assert missing.value.retryable is False

    def sender(_request: Request, _timeout: float) -> bytes:
        raise urllib.error.HTTPError("url", 429, "limited", {}, None)

    with pytest.raises(ModelGatewayError) as limited:
        KimiGateway("secret", sender=sender).complete(_request())
    assert limited.value.code == "http_error"
    assert limited.value.retryable is True
