"""Concrete outbound model adapters."""

from wealth_agent.adapters.model.deepseek import DeepSeekGateway
from wealth_agent.adapters.model.kimi import KimiGateway
from wealth_agent.adapters.model.openai_compatible import OpenAiCompatibleGateway

__all__ = ["DeepSeekGateway", "KimiGateway", "OpenAiCompatibleGateway"]
