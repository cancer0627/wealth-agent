"""Clock（时钟）协议及其公共实现。"""

from wealth_agent.shared.clock.contracts import Clock
from wealth_agent.shared.clock.system import SystemClock

__all__ = ["Clock", "SystemClock"]
