"""HTTP route builders grouped by capability."""

from wealth_agent.adapters.http.routes.analysis import build_analysis_router
from wealth_agent.adapters.http.routes.backup import build_backup_router
from wealth_agent.adapters.http.routes.conversation import build_conversation_router
from wealth_agent.adapters.http.routes.governance import build_governance_router
from wealth_agent.adapters.http.routes.portfolio import build_portfolio_router

__all__ = [
    "build_analysis_router",
    "build_backup_router",
    "build_conversation_router",
    "build_governance_router",
    "build_portfolio_router",
]
