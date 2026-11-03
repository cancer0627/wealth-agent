"""Export the HTTP contract without starting a server."""

from __future__ import annotations

import argparse
import json
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


class SchemaOnlyGateway:
    def complete(self, _request: ModelRequest) -> ModelResponse:
        raise RuntimeError("OpenAPI 导出不会调用模型")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as directory:
        bundle = build_application(
            BuildApplicationRequest(
                config=ApplicationConfig(
                    data_dir=Path(directory),
                    analysis_provider="schema-only",
                    analysis_model="schema-only",
                    agent=AgentModelConfig(model="schema-only"),
                ),
                model_gateway=SchemaOnlyGateway(),
            )
        )
        schema = create_http_app(build_http_dependencies(bundle)).openapi()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
