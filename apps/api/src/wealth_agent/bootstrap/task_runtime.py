"""Bootstrap composition for persistent task execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wealth_agent.capabilities.portfolio.contract import PortfolioReader
from wealth_agent.capabilities.tasks.contract import (
    ExecutorPort,
    TaskEnvironment,
    TaskEnvironmentRequest,
)
from wealth_agent.capabilities.tasks.implementation import (
    SqliteTaskService,
    TaskWorker,
)
from wealth_agent.capabilities.tasks.implementation.worker_runtime import (
    TaskWorkerRuntime,
)
from wealth_agent.shared.clock import Clock


@dataclass(frozen=True, slots=True)
class TaskRuntimeConfig:
    database_path: Path
    worker_id: str
    environment_epoch: int = 1
    idle_wait_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not self.worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if self.environment_epoch < 1:
            raise ValueError("environment_epoch 必须大于等于 1")
        if self.idle_wait_seconds <= 0:
            raise ValueError("idle_wait_seconds 必须大于 0")


@dataclass(frozen=True, slots=True)
class BuildTaskRuntimeRequest:
    config: TaskRuntimeConfig
    portfolio: PortfolioReader
    executor: ExecutorPort
    clock: Clock


@dataclass(frozen=True, slots=True)
class TaskRuntimeBundle:
    tasks: SqliteTaskService
    worker: TaskWorker
    runtime: TaskWorkerRuntime
    environment: PortfolioTaskEnvironment


class PortfolioTaskEnvironment:
    def __init__(self, portfolio: PortfolioReader, *, environment_epoch: int) -> None:
        self._portfolio = portfolio
        self._environment_epoch = environment_epoch

    def read(self, _request: TaskEnvironmentRequest) -> TaskEnvironment:
        snapshot = self._portfolio.snapshot()
        return TaskEnvironment(
            environment_epoch=self._environment_epoch,
            current_ledger_revision=snapshot.revision,
        )


def build_task_runtime(request: BuildTaskRuntimeRequest) -> TaskRuntimeBundle:
    tasks = SqliteTaskService(request.config.database_path, request.clock)
    worker = TaskWorker(tasks, request.executor)
    environment = PortfolioTaskEnvironment(
        request.portfolio,
        environment_epoch=request.config.environment_epoch,
    )
    runtime = TaskWorkerRuntime(
        tasks,
        worker,
        environment,
        worker_id=request.config.worker_id,
        idle_wait_seconds=request.config.idle_wait_seconds,
    )
    return TaskRuntimeBundle(
        tasks=tasks,
        worker=worker,
        runtime=runtime,
        environment=environment,
    )
