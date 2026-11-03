"""系统 UTC（协调世界时）时钟实现。"""

from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
