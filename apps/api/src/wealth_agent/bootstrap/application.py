"""Complete local application composition over capability contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from wealth_agent.adapters.http.dependencies import HttpDependencies
from wealth_agent.adapters.logging import DailyJsonlSink
from wealth_agent.bootstrap.task_execution import (
    AgentReadToolTaskExecutor,
    ApplicationTaskExecutor,
)
from wealth_agent.bootstrap.task_runtime import (
    BuildTaskRuntimeRequest,
    TaskRuntimeBundle,
    TaskRuntimeConfig,
    build_task_runtime,
)
from wealth_agent.capabilities.agent.contract import AgentModelConfig
from wealth_agent.capabilities.agent.implementation import (
    AgentToolRegistry,
    ModelTaskPlanner,
    TaskOrchestratingAgentRuntime,
)
from wealth_agent.capabilities.analysis.implementation import (
    DeterministicAnalysis,
    ModelAssistedAnalysis,
    SqliteAnalysisRepository,
    StoredAnalysisService,
)
from wealth_agent.capabilities.backup.implementation import LocalBackupService
from wealth_agent.capabilities.conversation.contract import OcrEngine
from wealth_agent.capabilities.conversation.implementation import SqliteConversationService
from wealth_agent.capabilities.governance.implementation import (
    DefaultGovernanceService,
    ExactJsonEvaluationService,
    SqliteTelemetryRepository,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_backup_port import (
    SqlitePortfolioBackupPort,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_store import (
    SqlitePortfolioStore,
)
from wealth_agent.shared.clock import Clock, SystemClock
from wealth_agent.shared.logging import configure_logging
from wealth_agent.shared.model import ModelGateway


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path
    logs_dir: Path = Path("logs")
    analysis_provider: str = Field(min_length=1)
    analysis_model: str = Field(min_length=1)
    agent: AgentModelConfig
    worker_id: str = Field(default="local-worker", min_length=1)


@dataclass(frozen=True, slots=True)
class BuildApplicationRequest:
    config: ApplicationConfig
    model_gateway: ModelGateway
    clock: Clock | None = None
    ocr_engine: OcrEngine | None = None


@dataclass(frozen=True, slots=True)
class ApplicationBundle:
    config: ApplicationConfig
    clock: Clock
    portfolio: SqlitePortfolioStore
    reporting: DeterministicAnalysis
    analysis: StoredAnalysisService
    analysis_repository: SqliteAnalysisRepository
    backup: LocalBackupService
    conversation: SqliteConversationService
    governance: DefaultGovernanceService
    telemetry: SqliteTelemetryRepository
    evaluation: ExactJsonEvaluationService
    agent: TaskOrchestratingAgentRuntime
    task_runtime: TaskRuntimeBundle


def build_application(request: BuildApplicationRequest) -> ApplicationBundle:
    clock = request.clock or SystemClock()
    config = request.config
    configure_logging(DailyJsonlSink(config.logs_dir))
    portfolio = SqlitePortfolioStore(config.data_dir / "wealth.db", clock)
    deterministic = DeterministicAnalysis()
    analysis_repository = SqliteAnalysisRepository(config.data_dir / "analysis.db")
    analysis = StoredAnalysisService(
        ModelAssistedAnalysis(
            request.model_gateway,
            model=config.analysis_model,
        ),
        analysis_repository,
        provider=config.analysis_provider,
        model=config.analysis_model,
    )
    governance = DefaultGovernanceService()
    telemetry = SqliteTelemetryRepository(config.data_dir / "telemetry.db")
    evaluation = ExactJsonEvaluationService()
    tools = AgentToolRegistry(
        portfolio,
        deterministic,
        clock,
        portfolio_writer=portfolio,
        analysis_service=analysis,
    )
    backup = LocalBackupService(
        config.data_dir / "backups",
        SqlitePortfolioBackupPort(config.data_dir / "wealth.db"),
        clock,
    )
    read_executor = AgentReadToolTaskExecutor(
        tools,
        granted_permissions=("ledger:read",),
    )
    executor = ApplicationTaskExecutor(
        read_executor,
        portfolio,
        portfolio,
        analysis,
        clock,
    )
    task_runtime = build_task_runtime(
        BuildTaskRuntimeRequest(
            config=TaskRuntimeConfig(
                database_path=config.data_dir / "task_state.db",
                worker_id=config.worker_id,
            ),
            portfolio=portfolio,
            executor=executor,
            clock=clock,
        )
    )
    agent = TaskOrchestratingAgentRuntime(
        request.model_gateway,
        tools,
        ModelTaskPlanner(request.model_gateway),
        task_runtime.tasks,
        task_runtime.environment,
        governance,
        worker_id=config.worker_id,
        telemetry=telemetry,
        clock=clock,
    )
    conversation = SqliteConversationService(
        config.data_dir / "conversation.db",
        clock,
        request.ocr_engine,
        agent=agent,
        governance=governance,
        telemetry=telemetry,
        portfolio_reader=portfolio,
        portfolio_writer=portfolio,
        portfolio_batch_writer=portfolio,
    )
    return ApplicationBundle(
        config=config,
        clock=clock,
        portfolio=portfolio,
        reporting=deterministic,
        analysis=analysis,
        analysis_repository=analysis_repository,
        backup=backup,
        conversation=conversation,
        governance=governance,
        telemetry=telemetry,
        evaluation=evaluation,
        agent=agent,
        task_runtime=task_runtime,
    )


def build_http_dependencies(bundle: ApplicationBundle) -> HttpDependencies:
    """Expose application services to HTTP through public contracts only."""

    return HttpDependencies(
        agent_config=bundle.config.agent,
        clock=bundle.clock,
        portfolio_reader=bundle.portfolio,
        portfolio_writer=bundle.portfolio,
        reporting=bundle.reporting,
        analysis=bundle.analysis,
        analysis_repository=bundle.analysis_repository,
        conversation=bundle.conversation,
        backup=bundle.backup,
        governance=bundle.governance,
        telemetry=bundle.telemetry,
        evaluation=bundle.evaluation,
    )


__all__ = [
    "ApplicationBundle",
    "ApplicationConfig",
    "BuildApplicationRequest",
    "build_application",
    "build_http_dependencies",
]
