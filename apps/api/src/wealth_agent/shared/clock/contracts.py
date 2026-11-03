"""无业务语义的 Clock（时钟）协议。"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...
