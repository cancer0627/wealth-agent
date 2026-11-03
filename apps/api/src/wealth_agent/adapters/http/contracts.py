"""Transport-only structured HTTP contracts."""

from __future__ import annotations

import base64
import binascii
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from wealth_agent.capabilities.portfolio.contract import PortfolioWriteCommand
from wealth_agent.shared.errors import ErrorDetail


class HttpContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(HttpContract):
    status: Literal["ok"] = "ok"
    service: Literal["wealth-agent-api"] = "wealth-agent-api"


class HttpErrorResponse(HttpContract):
    error: ErrorDetail


class PortfolioCommandRequest(RootModel[PortfolioWriteCommand]):
    pass


class AnalysisQuestionRequest(HttpContract):
    question: str | None = Field(default=None, max_length=4_000)


class SelectProductHttpRequest(HttpContract):
    product_id: str | None = Field(default=None, min_length=1)


class SendMessageHttpRequest(HttpContract):
    correlation_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4_000)


class CreateOcrDraftHttpRequest(HttpContract):
    content_base64: str = Field(min_length=1)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    default_date: date

    def decoded_content(self) -> bytes:
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 不是有效 Base64") from exc
        if not content:
            raise ValueError("图片内容不能为空")
        return content


class ApprovalHttpRequest(HttpContract):
    decision: Literal["approve", "reject"]


__all__ = [
    "AnalysisQuestionRequest",
    "ApprovalHttpRequest",
    "CreateOcrDraftHttpRequest",
    "HealthResponse",
    "HttpErrorResponse",
    "PortfolioCommandRequest",
    "SelectProductHttpRequest",
    "SendMessageHttpRequest",
]
