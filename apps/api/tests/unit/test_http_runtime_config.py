from pathlib import Path

from wealth_agent.bootstrap.http_main import HttpRuntimeConfig


def test_runtime_config_loads_from_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "WEALTH_AGENT_DATA_DIR=runtime-data",
                "WEALTH_AGENT_LOG_DIR=runtime-logs",
                "WEALTH_AGENT_HTTP_PORT=8123",
                "WEALTH_AGENT_MODEL_PROVIDER=deepseek",
                "WEALTH_AGENT_MODEL_API_KEY=model-secret",
                "WEALTH_AGENT_ANALYSIS_MODEL=analysis-model",
                "WEALTH_AGENT_AGENT_MODEL=agent-model",
            ]
        ),
        encoding="utf-8",
    )

    config = HttpRuntimeConfig.from_env_file(env_file)

    assert config.data_dir == Path("runtime-data")
    assert config.logs_dir == Path("runtime-logs")
    assert config.port == 8123
    assert config.provider == "deepseek"


def test_example_env_is_valid() -> None:
    config = HttpRuntimeConfig.from_env_file(Path(".env.example"))

    assert config.data_dir == Path("data")
    assert config.logs_dir == Path("logs")
    assert config.port == 8000
    assert config.provider == "deepseek"
