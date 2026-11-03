"""供公开能力契约复用的可序列化错误结构。"""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

ErrorDetails = dict[str, JsonValue]


class ErrorDetail(BaseModel):
    """包含能力专属 JSON（结构化数据格式）详情的稳定错误结构。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    details: ErrorDetails = Field(default_factory=dict)


__all__ = ["ErrorDetail", "ErrorDetails"]
