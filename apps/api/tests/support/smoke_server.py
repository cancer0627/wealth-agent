"""Temporary local server used only by browser smoke verification."""

from pathlib import Path
from tempfile import TemporaryDirectory

from wealth_agent.adapters.http import create_http_app
from wealth_agent.bootstrap.application import (
    ApplicationConfig,
    BuildApplicationRequest,
    build_application,
    build_http_dependencies,
)
from wealth_agent.capabilities.agent.contract import AgentModelConfig
from wealth_agent.shared.model import ModelRequest, ModelResponse


class UnusedGateway:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise AssertionError("浏览器冒烟测试不调用模型")


_temporary_directory = TemporaryDirectory()
_bundle = build_application(
    BuildApplicationRequest(
        config=ApplicationConfig(
            data_dir=Path(_temporary_directory.name),
            logs_dir=Path(_temporary_directory.name) / "logs",
            analysis_provider="smoke",
            analysis_model="smoke",
            agent=AgentModelConfig(model="smoke"),
        ),
        model_gateway=UnusedGateway(),
    )
)
app = create_http_app(build_http_dependencies(_bundle))
