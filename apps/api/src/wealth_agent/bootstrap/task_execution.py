"""Bootstrap adapter from task steps to read-only Agent tools."""

from __future__ import annotations

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from wealth_agent.capabilities.agent.contract import (
    AgentToolError,
    AgentToolPort,
    ExecuteAgentToolRequest,
)
from wealth_agent.capabilities.analysis.contract import (
    AnalysisError,
    AnalysisService,
    AnalyzeProductRequest,
)
from wealth_agent.capabilities.portfolio.contract import (
    PortfolioError,
    PortfolioReader,
    PortfolioWriteCommand,
    PortfolioWriter,
)
from wealth_agent.capabilities.tasks.contract import (
    ExecuteStepRequest,
    TaskPayload,
    TaskStep,
    TaskStepResult,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails
from wealth_agent.shared.model import ModelToolCall, ToolFunctionCall


class AgentReadToolTaskExecutor:
    def __init__(
        self,
        tools: AgentToolPort,
        *,
        granted_permissions: tuple[str, ...],
    ) -> None:
        self._tools = tools
        self._granted_permissions = granted_permissions

    def execute(self, request: ExecuteStepRequest) -> TaskStepResult:
        if not isinstance(request.step, TaskStep):
            return TaskStepResult(
                step_id=request.step.step_id,
                status="failed",
                error=ErrorDetail(
                    code="executor_plan_version_unsupported",
                    message="旧任务执行器不支持 V2 Agent 任务",
                ),
            )
        arguments = request.step.arguments
        if arguments.type != "agent.tool.arguments.v1" or not isinstance(
            arguments.data, dict
        ):
            return self._failure(
                request,
                ErrorDetail(
                    code="invalid_task_payload",
                    message="Agent Tool 任务参数类型无效",
                    details={"payload_type": arguments.type},
                ),
            )
        try:
            result = self._tools.execute(
                ExecuteAgentToolRequest(
                    call=ModelToolCall(
                        id=f"{request.task_id}:{request.step.step_id}",
                        function=ToolFunctionCall(
                            name=request.step.action,
                            arguments=json.dumps(
                                arguments.data,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    ),
                    granted_permissions=list(self._granted_permissions),
                )
            )
        except AgentToolError as exc:
            return self._failure(request, exc.detail)

        if result.output.ledger_revision != request.confirmed_ledger_revision:
            return self._failure(
                request,
                ErrorDetail(
                    code="ledger_revision_conflict",
                    message="Agent Tool 返回的台账版本与任务确认版本不一致",
                    details={
                        "confirmed_revision": request.confirmed_ledger_revision,
                        "actual_revision": result.output.ledger_revision,
                    },
                ),
            )
        return TaskStepResult(
            step_id=request.step.step_id,
            status="completed",
            output=TaskPayload(
                type="agent.tool.result.v1",
                data=result.model_dump(mode="json"),
            ),
        )

    @staticmethod
    def _failure(request: ExecuteStepRequest, error: ErrorDetail) -> TaskStepResult:
        return TaskStepResult(
            step_id=request.step.step_id,
            status="failed",
            error=error,
        )


class _AnalysisTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    product_id: str = Field(min_length=1)
    question: str | None = Field(default=None, max_length=4_000)


class ApplicationTaskExecutor:
    def __init__(
        self,
        read_tools: AgentReadToolTaskExecutor,
        portfolio_reader: PortfolioReader,
        portfolio_writer: PortfolioWriter,
        analysis: AnalysisService,
        clock: Clock,
    ) -> None:
        self._read_tools = read_tools
        self._portfolio_reader = portfolio_reader
        self._portfolio_writer = portfolio_writer
        self._analysis = analysis
        self._clock = clock
        self._portfolio_commands: TypeAdapter[PortfolioWriteCommand] = TypeAdapter(
            PortfolioWriteCommand
        )

    def execute(self, request: ExecuteStepRequest) -> TaskStepResult:
        if not isinstance(request.step, TaskStep):
            return TaskStepResult(
                step_id=request.step.step_id,
                status="failed",
                error=ErrorDetail(
                    code="executor_plan_version_unsupported",
                    message="应用任务执行器不支持 V2 Agent 任务",
                ),
            )
        if request.step.action in {
            "list_products",
            "get_product_profile",
            "get_product_snapshot",
            "get_performance_report",
            "get_data_quality_warnings",
            "get_model_analysis",
        }:
            return self._read_tools.execute(request)
        if not isinstance(request.step.arguments.data, dict):
            return self._failure(request, "invalid_task_payload", "任务参数必须是对象")
        if request.step.action == "portfolio.write":
            return self._execute_portfolio_write(request)
        if request.step.action == "analysis.run":
            return self._execute_analysis(request)
        return self._failure(request, "executor_action_unknown", "任务步骤没有执行器")

    def _execute_portfolio_write(self, request: ExecuteStepRequest) -> TaskStepResult:
        step = cast(TaskStep, request.step)
        try:
            command = self._portfolio_commands.validate_python(step.arguments.data)
            if command.expected_revision != request.confirmed_ledger_revision:
                return self._failure(
                    request,
                    "ledger_revision_conflict",
                    "写入命令版本与任务确认版本不一致",
                )
            mutation = self._portfolio_writer.execute(command)
            return TaskStepResult(
                step_id=request.step.step_id,
                status="completed",
                output=TaskPayload(
                    type="portfolio.mutation_result.v1",
                    data=mutation.model_dump(mode="json"),
                ),
            )
        except ValidationError as exc:
            return self._failure(
                request,
                "invalid_task_payload",
                "资产写入参数未通过结构校验",
                details={"error_count": exc.error_count()},
            )
        except PortfolioError as exc:
            return TaskStepResult(
                step_id=request.step.step_id, status="failed", error=exc.detail
            )

    def _execute_analysis(self, request: ExecuteStepRequest) -> TaskStepResult:
        step = cast(TaskStep, request.step)
        try:
            value = _AnalysisTaskInput.model_validate(step.arguments.data)
            view = self._portfolio_reader.get_product(value.product_id)
            if view.ledger_revision != request.confirmed_ledger_revision:
                return self._failure(
                    request,
                    "ledger_revision_conflict",
                    "分析输入版本与任务确认版本不一致",
                )
            result = self._analysis.analyze(
                AnalyzeProductRequest(
                    product=view.product,
                    ledger_revision=view.ledger_revision,
                    generated_at=self._clock.now(),
                    question=value.question,
                )
            )
            return TaskStepResult(
                step_id=request.step.step_id,
                status="completed",
                output=TaskPayload(
                    type="analysis.result.v1",
                    data=result.model_dump(mode="json"),
                ),
            )
        except ValidationError as exc:
            return self._failure(
                request,
                "invalid_task_payload",
                "分析任务参数未通过结构校验",
                details={"error_count": exc.error_count()},
            )
        except (AnalysisError, PortfolioError) as exc:
            return TaskStepResult(
                step_id=request.step.step_id, status="failed", error=exc.detail
            )

    @staticmethod
    def _failure(
        request: ExecuteStepRequest,
        code: str,
        message: str,
        *,
        details: ErrorDetails | None = None,
    ) -> TaskStepResult:
        return TaskStepResult(
            step_id=request.step.step_id,
            status="failed",
            error=ErrorDetail(code=code, message=message, details=details or {}),
        )
