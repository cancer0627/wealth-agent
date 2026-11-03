"""Public capability dependencies required by the HTTP adapter."""

from __future__ import annotations

from dataclasses import dataclass

from wealth_agent.capabilities.agent.contract import AgentModelConfig
from wealth_agent.capabilities.analysis.contract import (
    AnalysisEngine,
    AnalysisRepository,
    AnalysisService,
)
from wealth_agent.capabilities.backup.contract import BackupService
from wealth_agent.capabilities.conversation.contract import ConversationService
from wealth_agent.capabilities.governance.contract import (
    EvaluationService,
    GovernanceService,
    TelemetryRepository,
)
from wealth_agent.capabilities.portfolio.contract import PortfolioReader, PortfolioWriter
from wealth_agent.shared.clock import Clock


@dataclass(frozen=True, slots=True)
class HttpDependencies:
    """HTTP-facing services expressed only through public contracts."""

    agent_config: AgentModelConfig
    clock: Clock
    portfolio_reader: PortfolioReader
    portfolio_writer: PortfolioWriter
    reporting: AnalysisEngine
    analysis: AnalysisService
    analysis_repository: AnalysisRepository
    conversation: ConversationService
    backup: BackupService
    governance: GovernanceService
    telemetry: TelemetryRepository
    evaluation: EvaluationService


__all__ = ["HttpDependencies"]
