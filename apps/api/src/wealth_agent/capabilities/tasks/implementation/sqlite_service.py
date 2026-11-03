"""Task state machine composed with the persistent SQLite state store."""

from pathlib import Path

from wealth_agent.capabilities.tasks.implementation.in_memory_service import (
    TaskStateMachineService,
)
from wealth_agent.capabilities.tasks.implementation.state_store import (
    SqliteTaskStateStore,
)
from wealth_agent.shared.clock import Clock


class SqliteTaskService(TaskStateMachineService):
    def __init__(self, database_path: Path, clock: Clock) -> None:
        super().__init__(clock, SqliteTaskStateStore(database_path))
