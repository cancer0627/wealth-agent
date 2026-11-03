"""Environment composition and localhost-only HTTP process entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from wealth_agent.adapters.http import create_http_app
from wealth_agent.adapters.model import (
    DeepSeekGateway,
    KimiGateway,
    OpenAiCompatibleGateway,
)
from wealth_agent.adapters.ocr import RapidOcrAdapter
from wealth_agent.bootstrap.application import (
    ApplicationConfig,
    BuildApplicationRequest,
    build_application,
    build_http_dependencies,
)
from wealth_agent.capabilities.agent.contract import AgentModelConfig
from wealth_agent.shared.model import ModelGateway


class HttpRuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Field(
        default=Path("data"), validation_alias="WEALTH_AGENT_DATA_DIR"
    )
    logs_dir: Path = Field(
        default=Path("logs"), validation_alias="WEALTH_AGENT_LOG_DIR"
    )
    provider: Literal["deepseek", "kimi", "openai-compatible"] = Field(
        validation_alias="WEALTH_AGENT_MODEL_PROVIDER"
    )
    model_api_key: SecretStr = Field(
        validation_alias="WEALTH_AGENT_MODEL_API_KEY"
    )
    model_endpoint: str | None = Field(
        default=None, validation_alias="WEALTH_AGENT_MODEL_ENDPOINT"
    )
    analysis_model: str = Field(
        min_length=1, validation_alias="WEALTH_AGENT_ANALYSIS_MODEL"
    )
    agent_model: str = Field(
        min_length=1, validation_alias="WEALTH_AGENT_AGENT_MODEL"
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65_535,
        validation_alias="WEALTH_AGENT_HTTP_PORT",
    )
    @classmethod
    def from_env_file(cls, path: Path = Path(".env")) -> HttpRuntimeConfig:
        return cls(_env_file=path)  # type: ignore[call-arg]

def create_app(config: HttpRuntimeConfig | None = None) -> FastAPI:
    resolved = config or HttpRuntimeConfig.from_env_file()
    gateway = _model_gateway(resolved)
    bundle = build_application(
        BuildApplicationRequest(
            config=ApplicationConfig(
                data_dir=resolved.data_dir,
                logs_dir=resolved.logs_dir,
                analysis_provider=resolved.provider,
                analysis_model=resolved.analysis_model,
                agent=AgentModelConfig(model=resolved.agent_model),
            ),
            model_gateway=gateway,
            ocr_engine=RapidOcrAdapter(),
        )
    )
    return create_http_app(build_http_dependencies(bundle))


def main() -> None:
    config = HttpRuntimeConfig.from_env_file()
    uvicorn.run(
        create_app(config),
        host="127.0.0.1",
        port=config.port,
        access_log=False,
    )


def _model_gateway(config: HttpRuntimeConfig) -> ModelGateway:
    api_key = config.model_api_key.get_secret_value()
    if config.provider == "deepseek":
        return DeepSeekGateway(api_key)
    if config.provider == "kimi":
        return KimiGateway(api_key)
    if config.model_endpoint is None:
        raise ValueError("OpenAI-compatible 模型必须配置 WEALTH_AGENT_MODEL_ENDPOINT")
    return OpenAiCompatibleGateway(
        api_key,
        endpoint=config.model_endpoint,
        display_name="OpenAI-compatible",
        api_key_name="WEALTH_AGENT_MODEL_API_KEY",
    )


__all__ = ["HttpRuntimeConfig", "create_app", "main"]
