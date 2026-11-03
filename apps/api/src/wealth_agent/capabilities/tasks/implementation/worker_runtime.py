"""Single-process background lifecycle for the persistent task worker."""

from __future__ import annotations

from threading import Event, RLock, Thread
from typing import Literal

from wealth_agent.capabilities.tasks.contract import (
    RecoverTasksRequest,
    RecoverTasksResult,
    TaskEnvironmentPort,
    TaskEnvironmentRequest,
    TaskError,
    TaskService,
    TaskStateConflictError,
    WorkerRunRequest,
    WorkerRunResult,
    WorkerRuntimeState,
    WorkerShutdownRequest,
    WorkerStartRequest,
    WorkerWakeRequest,
)
from wealth_agent.capabilities.tasks.implementation.worker import TaskWorker
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.logging import trace_public_methods


@trace_public_methods(layer="capability")
class TaskWorkerRuntime:
    def __init__(
        self,
        tasks: TaskService,
        worker: TaskWorker,
        environment: TaskEnvironmentPort,
        *,
        worker_id: str,
        idle_wait_seconds: float = 0.5,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if idle_wait_seconds <= 0:
            raise ValueError("idle_wait_seconds 必须大于 0")
        self._tasks = tasks
        self._worker = worker
        self._environment = environment
        self._worker_id = worker_id
        self._idle_wait_seconds = idle_wait_seconds
        self._stop = Event()
        self._wakeup = Event()
        self._thread: Thread | None = None
        self._recovery: RecoverTasksResult | None = None
        self._last_run: WorkerRunResult | None = None
        self._status: Literal["stopped", "running", "stopping"] = "stopped"
        self._lock = RLock()

    def start(self, _request: WorkerStartRequest) -> WorkerRuntimeState:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._state()
            environment = self._environment.read(TaskEnvironmentRequest())
            self._recovery = self._tasks.recover_interrupted(
                RecoverTasksRequest(
                    environment_epoch=environment.environment_epoch,
                    current_ledger_revision=environment.current_ledger_revision,
                )
            )
            self._stop.clear()
            self._wakeup.set()
            self._status = "running"
            self._thread = Thread(
                target=self._run,
                name="wealth-task-worker",
                daemon=True,
            )
            self._thread.start()
            return self._state()

    def wake(self, _request: WorkerWakeRequest) -> WorkerRuntimeState:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                raise TaskStateConflictError("任务 Worker 尚未启动")
            self._wakeup.set()
            return self._state()

    def shutdown(self, request: WorkerShutdownRequest) -> WorkerRuntimeState:
        with self._lock:
            thread = self._thread
            if thread is None:
                self._status = "stopped"
                return self._state()
            self._status = "stopping"
            self._stop.set()
            self._wakeup.set()
        thread.join(timeout=request.timeout_seconds)
        with self._lock:
            if thread.is_alive():
                return self._state()
            self._thread = None
            self._status = "stopped"
            return self._state()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wakeup.wait(self._idle_wait_seconds)
                self._wakeup.clear()
                if self._stop.is_set():
                    break
                try:
                    environment = self._environment.read(TaskEnvironmentRequest())
                    outcome = self._worker.run_once(
                        WorkerRunRequest(
                            worker_id=self._worker_id,
                            environment_epoch=environment.environment_epoch,
                            current_ledger_revision=environment.current_ledger_revision,
                        )
                    )
                except TaskError as exc:
                    outcome = WorkerRunResult(status="conflict", error=exc.detail)
                except Exception as exc:
                    outcome = WorkerRunResult(
                        status="conflict",
                        error=ErrorDetail(
                            code="worker_runtime_error",
                            message="任务 Worker 运行失败",
                            details={"error_type": type(exc).__name__},
                        ),
                    )
                with self._lock:
                    self._last_run = outcome
                if outcome.status == "executed":
                    self._wakeup.set()
        finally:
            with self._lock:
                self._status = "stopped"

    def _state(self) -> WorkerRuntimeState:
        return WorkerRuntimeState(
            status=self._status,
            recovery=self._recovery,
            last_run=self._last_run,
        )
