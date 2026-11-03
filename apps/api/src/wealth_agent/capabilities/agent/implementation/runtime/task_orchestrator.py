"""Persistent task-backed Agent planner and executor."""

from __future__ import annotations

import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from wealth_agent.capabilities.agent.contract import (
    AddLedgerRecordToolInput,
    AgentAnswer,
    AgentHistoryMessage,
    AgentModelConfig,
    AgentPendingApproval,
    AgentRuntimeError,
    AgentTaskPlannerPort,
    AgentToolDefinitionsRequest,
    AgentToolError,
    AgentToolName,
    AgentToolOutput,
    AgentToolPort,
    AgentToolTrace,
    CancelAgentRunRequest,
    DataQualityWarningsToolOutput,
    ExecuteAgentToolRequest,
    GetAgentRunRequest,
    ModelAnalysisToolOutput,
    PerformanceReportToolOutput,
    PlanAgentTasksRequest,
    ProductProfileToolOutput,
    ProductSnapshotToolOutput,
    ProductToolInput,
    RespondAgentApprovalRequest,
    RunAgentRequest,
    RunAgentResult,
    UpdateProductProfileToolInput,
    UpdateProductProfileToolOutput,
)
from wealth_agent.capabilities.governance.contract import (
    GovernanceEvent,
    GovernanceService,
    RecordEventRequest,
    TelemetryRepository,
    ToolAuthorizationRequest,
    UserToolApproval,
)
from wealth_agent.capabilities.tasks.contract import (
    AgentTaskStep,
    ApplyStepResultRequest,
    ClaimTaskRequest,
    ControlTaskRequest,
    CreateTaskRequest,
    GetTaskRequest,
    RequestTaskApprovalRequest,
    ResolveTaskApprovalRequest,
    TaskEnvironmentPort,
    TaskEnvironmentRequest,
    TaskPayload,
    TaskPendingApproval,
    TaskPlanV2,
    TaskRecord,
    TaskService,
    TaskStepResult,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.identity import new_id
from wealth_agent.shared.logging import current_trace_id, trace_call, trace_public_methods
from wealth_agent.shared.model import (
    ModelGateway,
    ModelGatewayError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
    ModelUsage,
)
from wealth_agent.shared.serialization import canonical_json, sha256_json

_EXECUTOR_INSTRUCTIONS = (
    "你是本地理财 Agent Executor。围绕当前任务逐步工作。"
    "每轮只能调用一个允许的 Tool，或在已有 Tool 结果足够时输出严格 JSON："
    "{status:'answered'|'needs_clarification',answer:'...'}。"
    "不得调用未提供的 Tool，不得猜测 Tool 未返回的事实。"
    "如果 Tool 返回结构化增减持建议，回答必须明确保留该建议及其数据限制。"
)
_SUMMARY_INSTRUCTIONS = (
    "你是本地理财 Agent。根据已完成任务的结构化结果汇总最终答复。"
    "只输出严格 JSON：{status:'answered'|'needs_clarification',answer:'...'}。"
    "如果已有结果包含结构化增减持建议，最终答复必须明确保留该建议。"
)
_PRODUCT_ID_TOOLS = frozenset(
    {
        "get_product_profile",
        "get_product_snapshot",
        "get_performance_report",
        "get_data_quality_warnings",
        "get_model_analysis",
        "update_product_profile",
    }
)


class _ExecutionCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ModelMessage]
    traces: list[AgentToolTrace] = Field(default_factory=list)
    usages: list[ModelUsage] = Field(default_factory=list)
    model_requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    source_text: str
    active_product_id: str | None = None
    active_product_name: str | None = None


@trace_public_methods(layer="capability")
class TaskOrchestratingAgentRuntime:
    def __init__(
        self,
        gateway: ModelGateway,
        tools: AgentToolPort,
        planner: AgentTaskPlannerPort,
        tasks: TaskService,
        environment: TaskEnvironmentPort,
        governance: GovernanceService,
        *,
        worker_id: str,
        telemetry: TelemetryRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._gateway = gateway
        self._tools = tools
        self._planner = planner
        self._tasks = tasks
        self._environment = environment
        self._governance = governance
        self._worker_id = worker_id
        self._telemetry = telemetry
        self._clock = clock

    def run(self, request: RunAgentRequest) -> RunAgentResult:
        definitions = self._tools.definitions(AgentToolDefinitionsRequest())
        try:
            planned = self._planner.plan(
                PlanAgentTasksRequest(
                    correlation_id=request.correlation_id,
                    source_text=request.question,
                    history=request.history,
                    active_product_id=request.active_product_id,
                    available_tools=[spec.name for spec in definitions.specs],
                    config=request.config,
                )
            )
            if planned.status == "needs_clarification":
                return RunAgentResult(
                    status="needs_clarification",
                    answer=AgentAnswer(
                        status="needs_clarification",
                        answer=planned.clarification or "请补充必要信息。",
                    ),
                    model_usages=[planned.usage],
                )
            if not isinstance(planned.plan, TaskPlanV2):
                raise AgentRuntimeError("规划器未返回 V2 任务计划", code="invalid_agent_plan")
            plan = self._without_redundant_summary_step(planned.plan)
            environment = self._environment.read(TaskEnvironmentRequest())
            agent_run_id = new_id("agent-run")
            created = self._tasks.create(
                CreateTaskRequest(
                    trace_id=current_trace_id(),
                    correlation_id=request.correlation_id,
                    kind="workflow",
                    plan=plan,
                    current_ledger_revision=environment.current_ledger_revision,
                    environment_epoch=environment.environment_epoch,
                    idempotency_key=f"agent:{request.session_id}:{request.correlation_id}",
                    session_id=request.session_id,
                    agent_run_id=agent_run_id,
                    requires_confirmation=False,
                )
            ).task
            self._record(
                created,
                category="task",
                name="agent.run.created",
                payload={
                    "plan_hash": created.plan_hash,
                    "step_count": len(created.plan.steps),
                },
            )
            return self._continue_task(
                created,
                request.config,
                source_text=request.question,
                history=request.history,
                active_product_id=request.active_product_id,
                active_product_name=None,
                initial_usages=[planned.usage],
            )
        except (AgentRuntimeError, AgentToolError) as exc:
            return RunAgentResult(status="failed", error=exc.detail)

    @trace_call(
        layer="capability",
        trace_resolver=lambda self, request: request.trace_id
        or self._tasks.get(GetTaskRequest(task_id=request.task_id)).trace_id,
    )
    def respond_approval(self, request: RespondAgentApprovalRequest) -> RunAgentResult:
        task = self._tasks.get(GetTaskRequest(task_id=request.task_id))
        if task.session_id != request.session_id:
            return self._failure("approval_session_mismatch", "确认不属于当前会话")
        pending = task.pending_approval
        if pending is None or pending.approval_id != request.approval_id:
            return self._failure("approval_not_pending", "确认不存在或已经处理")
        state = self._load_checkpoint(task)
        environment = self._environment.read(TaskEnvironmentRequest())
        if request.decision == "reject":
            if pending.approved:
                self._tasks.cancel(
                    ControlTaskRequest(
                        task_id=task.task_id,
                        expected_state_version=task.state_version,
                    )
                )
            else:
                self._tasks.resolve_approval(
                    ResolveTaskApprovalRequest(
                        task_id=task.task_id,
                        expected_state_version=task.state_version,
                        approval_id=pending.approval_id,
                        decision="reject",
                        current_ledger_revision=environment.current_ledger_revision,
                    )
                )
            self._record(
                task,
                category="audit",
                name="agent.approval.rejected",
                payload={"approval_id": pending.approval_id},
            )
            return RunAgentResult(
                status="answered",
                task_id=task.task_id,
                plan=task.plan,
                answer=AgentAnswer(status="answered", answer="已取消本次操作。"),
                tool_traces=state.traces,
                model_usages=state.usages,
            )
        was_already_approved = pending.approved
        if not was_already_approved:
            self._tasks.resolve_approval(
                ResolveTaskApprovalRequest(
                    task_id=task.task_id,
                    expected_state_version=task.state_version,
                    approval_id=pending.approval_id,
                    decision="approve",
                    current_ledger_revision=environment.current_ledger_revision,
                )
            )
        if not (was_already_approved and task.status == "running"):
            task = self._claim(task.task_id)
        if (
            not was_already_approved
            and pending.ledger_revision != environment.current_ledger_revision
        ):
            return self._replan_after_revision_change(
                task,
                state,
                request.config,
                environment.current_ledger_revision,
            )
        authorization = self._governance.authorize_tool(
            ToolAuthorizationRequest(
                task_id=task.task_id,
                step_id=pending.step_id,
                tool_name=pending.tool_name,
                requested_permissions=["ledger:write"],
                side_effects="ledger_write",
                action_hash=pending.action_hash,
                approval=UserToolApproval(
                    approval_id=pending.approval_id,
                    action_hash=pending.action_hash,
                    decision="approve",
                ),
            )
        )
        if authorization.decision != "allowed":
            return self._fail_step(
                task, state, "approval_denied", authorization.reason
            )
        call = self._call_from_pending(pending)
        self._record(
            task,
            category="audit",
            name="agent.approval.approved",
            payload={
                "approval_id": pending.approval_id,
                "action_hash": pending.action_hash,
            },
        )
        outcome = self._execute_authorized_call(
            task,
            state,
            call,
            authorization.granted_permissions,
            request.config,
            recover_approved_write=was_already_approved,
        )
        if outcome.status in {"failed", "awaiting_approval"}:
            return outcome
        current = self._tasks.get(GetTaskRequest(task_id=task.task_id))
        if current.is_terminal:
            return self._summarize(
                current,
                self._answers_from_task(current),
                outcome.tool_traces,
                outcome.model_usages,
                request.config,
            )
        return self._continue_task(
            current,
            request.config,
            source_text=state.source_text,
            history=[],
            active_product_id=state.active_product_id,
            active_product_name=state.active_product_name,
            initial_usages=outcome.model_usages,
        )

    @trace_call(
        layer="capability",
        trace_resolver=lambda self, request: request.trace_id
        or self._tasks.get(GetTaskRequest(task_id=request.task_id)).trace_id,
    )
    def cancel(self, request: CancelAgentRunRequest) -> RunAgentResult:
        task = self._tasks.get(GetTaskRequest(task_id=request.task_id))
        if task.session_id != request.session_id:
            return self._failure("task_session_mismatch", "任务不属于当前会话")
        if not task.is_terminal:
            task = self._tasks.cancel(
                ControlTaskRequest(
                    task_id=task.task_id,
                    expected_state_version=task.state_version,
                )
            )
        return RunAgentResult(
            status="answered",
            task_id=task.task_id,
            plan=task.plan,
            answer=AgentAnswer(status="answered", answer="已取消当前 Agent 运行。"),
        )

    @trace_call(
        layer="capability",
        trace_resolver=lambda self, request: request.trace_id
        or self._tasks.get(GetTaskRequest(task_id=request.task_id)).trace_id,
    )
    def get_run(self, request: GetAgentRunRequest) -> RunAgentResult:
        task = self._tasks.get(GetTaskRequest(task_id=request.task_id))
        if task.session_id != request.session_id:
            return self._failure("task_session_mismatch", "任务不属于当前会话")
        if task.pending_approval is not None:
            pending = task.pending_approval
            state = self._load_checkpoint(task)
            try:
                value = self._write_input(pending.tool_name, pending.arguments)
            except (ValidationError, ValueError):
                return self._failure(
                    "agent_checkpoint_invalid", "待确认 Tool 参数已经损坏", task.task_id
                )
            return RunAgentResult(
                status="awaiting_approval",
                task_id=task.task_id,
                plan=task.plan,
                pending_approval=self._public_approval(
                    task, pending, value, product_name=state.active_product_name
                ),
            )
        if task.status == "completed":
            return RunAgentResult(
                status="answered",
                task_id=task.task_id,
                plan=task.plan,
                answer=AgentAnswer(
                    status="answered",
                    answer="\n".join(self._answers_from_task(task)) or "任务已完成。",
                ),
            )
        if task.status == "cancelled":
            return RunAgentResult(
                status="answered",
                task_id=task.task_id,
                plan=task.plan,
                answer=AgentAnswer(status="answered", answer="本次操作已取消。"),
            )
        if task.status in {"failed", "invalidated_by_restore"}:
            return RunAgentResult(
                status="failed",
                task_id=task.task_id,
                plan=task.plan,
                error=task.error
                or ErrorDetail(code="agent_run_failed", message="Agent 运行未完成"),
            )
        return self._failure("agent_run_incomplete", "Agent 运行尚未完成", task.task_id)

    def _continue_task(
        self,
        task: TaskRecord,
        config: AgentModelConfig,
        *,
        source_text: str,
        history: list[AgentHistoryMessage],
        active_product_id: str | None,
        active_product_name: str | None = None,
        initial_usages: list[ModelUsage] | None = None,
    ) -> RunAgentResult:
        task_answers: list[str] = []
        traces: list[AgentToolTrace] = []
        usages = list(initial_usages or [])
        while not task.is_terminal:
            task = self._claim(task.task_id)
            step = task.plan.steps[task.current_step_index]
            if not isinstance(step, AgentTaskStep):
                return self._failure(
                    "legacy_task_not_executable",
                    "旧任务不能由新 Agent 执行",
                    task.task_id,
                )
            state = _ExecutionCheckpoint(
                messages=self._initial_messages(
                    step,
                    source_text,
                    history,
                    active_product_id=active_product_id,
                    active_product_name=active_product_name,
                ),
                usages=usages,
                source_text=source_text,
                active_product_id=active_product_id,
                active_product_name=active_product_name,
            )
            outcome = self._run_step(task, step, state, config)
            active_product_id = state.active_product_id
            active_product_name = state.active_product_name
            if outcome.status == "awaiting_approval" or outcome.status == "failed":
                return outcome
            if outcome.answer is not None:
                task_answers.append(outcome.answer.answer)
            traces.extend(outcome.tool_traces)
            usages = outcome.model_usages
            task = self._tasks.get(GetTaskRequest(task_id=task.task_id))
        return self._summarize(
            task, self._answers_from_task(task) or task_answers, traces, usages, config
        )

    def _run_step(
        self,
        task: TaskRecord,
        step: AgentTaskStep,
        state: _ExecutionCheckpoint,
        config: AgentModelConfig,
    ) -> RunAgentResult:
        definitions = self._tools.definitions(AgentToolDefinitionsRequest())
        allowed = set(step.allowed_tools)
        model_tools = [item for item in definitions.definitions if item.function.name in allowed]
        specs = {item.name: item for item in definitions.specs}
        while state.model_requests < config.max_model_requests:
            try:
                response = self._complete(
                    task,
                    state,
                    config,
                    model_tools,
                    required=not state.traces,
                )
            except AgentRuntimeError as exc:
                return self._fail_step(
                    task,
                    state,
                    exc.detail.code,
                    exc.detail.message,
                )
            state.usages.append(response.usage)
            state.model_requests += 1
            if not response.tool_calls:
                if not state.traces:
                    return self._fail_step(
                        task, state, "answer_without_evidence", "任务没有 Tool 证据"
                    )
                try:
                    answer = self._parse_answer(response)
                except AgentRuntimeError as exc:
                    return self._fail_step(
                        task,
                        state,
                        exc.detail.code,
                        exc.detail.message,
                    )
                answer = self._ensure_analysis_answer(answer, state.traces)
                payload = TaskPayload(
                    type="agent.task.result.v2",
                    data={
                        "answer": answer.model_dump(mode="json"),
                        "tool_traces": [item.model_dump(mode="json") for item in state.traces],
                        "model_usages": [item.model_dump(mode="json") for item in state.usages],
                    },
                )
                updated = self._tasks.apply_step_result(
                    ApplyStepResultRequest(
                        task_id=task.task_id,
                        expected_state_version=task.state_version,
                        result=TaskStepResult(
                            step_id=step.step_id,
                            status="completed",
                            output=payload,
                        ),
                        next_ledger_revision=task.current_ledger_revision,
                        executor_state=payload,
                    )
                )
                return RunAgentResult(
                    status=answer.status,
                    task_id=updated.task_id,
                    plan=updated.plan,
                    answer=answer,
                    tool_traces=state.traces,
                    model_usages=state.usages,
                )
            if state.tool_calls >= config.max_tool_calls:
                return self._fail_step(
                    task,
                    state,
                    "max_tool_calls",
                    "Agent 已达到最大 Tool 调用次数",
                )
            # Providers may return parallel candidates even when the Agent contract
            # permits only one Tool execution per round. Execute the first candidate
            # and let the model re-evaluate the remaining work from its result.
            call = response.tool_calls[0]
            if call.function.name not in allowed or call.function.name not in specs:
                return self._fail_step(
                    task, state, "tool_not_in_plan", "模型调用了计划外 Tool"
                )
            try:
                action_hash, normalized = self._normalized_action(task, step, call, state)
            except AgentRuntimeError as exc:
                return self._fail_step(
                    task,
                    state,
                    exc.detail.code,
                    exc.detail.message,
                )
            call = self._with_normalized_arguments(call, normalized)
            state.messages.append(
                ModelMessage(role="assistant", content=response.content, tool_calls=[call])
            )
            spec = specs[cast(AgentToolName, call.function.name)]
            authorization = self._governance.authorize_tool(
                ToolAuthorizationRequest(
                    task_id=task.task_id,
                    step_id=step.step_id,
                    tool_name=call.function.name,
                    requested_permissions=list(spec.permissions),
                    side_effects=spec.side_effects,
                    action_hash=action_hash,
                )
            )
            if authorization.decision == "confirmation_required":
                return self._store_approval(
                    task, step, call, normalized, action_hash, authorization.reason, state
                )
            if authorization.decision != "allowed":
                return self._fail_step(task, state, "tool_denied", authorization.reason)
            return self._execute_authorized_call(
                task,
                state,
                call,
                authorization.granted_permissions,
                config,
            )
        return self._fail_step(
            task, state, "max_model_requests", "Agent 已达到最大模型请求次数"
        )

    def _execute_authorized_call(
        self,
        task: TaskRecord,
        state: _ExecutionCheckpoint,
        call: ModelToolCall,
        granted_permissions: list[str],
        config: AgentModelConfig,
        *,
        recover_approved_write: bool = False,
    ) -> RunAgentResult:
        try:
            result = self._tools.execute(
                ExecuteAgentToolRequest(
                    call=call,
                    granted_permissions=granted_permissions,
                    selected_product_id=state.active_product_id,
                    expected_ledger_revision=task.current_ledger_revision,
                    idempotency_key=f"{task.task_id}:{task.current_step_index}:{call.id}",
                    source_text=state.source_text,
                )
            )
        except AgentToolError as exc:
            if recover_approved_write and exc.detail.code == "revision_conflict":
                environment = self._environment.read(TaskEnvironmentRequest())
                return self._replan_after_revision_change(
                    task,
                    state,
                    config,
                    environment.current_ledger_revision,
                )
            state.traces.append(
                AgentToolTrace(
                    tool_call_id=call.id,
                    tool_name=call.function.name,
                    status="failed",
                    error=exc.detail,
                )
            )
            return self._fail_step(task, state, exc.detail.code, exc.detail.message)
        product_id, product_name = self._product_identity(result.output)
        if product_id is not None:
            state.active_product_id = product_id
            if product_name is not None:
                state.active_product_name = product_name
        self._record(
            task,
            category="tool",
            name="agent.tool.completed",
            payload={
                "step_id": task.steps[task.current_step_index].step_id,
                "tool_call_id": call.id,
                "tool_name": call.function.name,
                "ledger_revision": result.output.ledger_revision,
            },
        )
        state.tool_calls += 1
        state.traces.append(
            AgentToolTrace(
                tool_call_id=call.id,
                tool_name=result.tool_name,
                status="completed",
                output=result.output,
            )
        )
        state.messages.append(
            ModelMessage(
                role="tool",
                tool_call_id=call.id,
                content=canonical_json(result.output.model_dump(mode="json")),
            )
        )
        task = task.model_copy(update={"current_ledger_revision": result.output.ledger_revision})
        step = task.plan.steps[task.current_step_index]
        if not isinstance(step, AgentTaskStep):
            return self._fail_step(
                task,
                state,
                "legacy_task_not_executable",
                "旧任务不能由新 Agent 执行",
            )
        return self._run_step(task, step, state, config)

    def _store_approval(
        self,
        task: TaskRecord,
        step: AgentTaskStep,
        call: ModelToolCall,
        normalized: dict[str, JsonValue],
        action_hash: str,
        reason: str,
        state: _ExecutionCheckpoint,
    ) -> RunAgentResult:
        write_input = self._write_input(call.function.name, normalized)
        approval_id = new_id("approval")
        summary = self._approval_summary(write_input)
        pending = TaskPendingApproval(
            approval_id=approval_id,
            step_id=step.step_id,
            tool_call_id=call.id,
            tool_name=call.function.name,
            arguments=normalized,
            action_hash=action_hash,
            ledger_revision=task.current_ledger_revision,
            summary=summary,
            reason=reason,
        )
        stored = self._tasks.request_approval(
            RequestTaskApprovalRequest(
                task_id=task.task_id,
                expected_state_version=task.state_version,
                pending_approval=pending,
                executor_state=TaskPayload(
                    type="agent.executor.checkpoint.v2",
                    data=state.model_dump(mode="json"),
                ),
            )
        )
        self._record(
            stored,
            category="audit",
            name="agent.approval.requested",
            payload={
                "approval_id": approval_id,
                "step_id": step.step_id,
                "tool_name": call.function.name,
                "action_hash": action_hash,
            },
        )
        return RunAgentResult(
            status="awaiting_approval",
            task_id=stored.task_id,
            plan=stored.plan,
            pending_approval=self._public_approval(
                stored, pending, write_input, product_name=state.active_product_name
            ),
            tool_traces=state.traces,
            model_usages=state.usages,
        )

    def _replan_after_revision_change(
        self,
        task: TaskRecord,
        state: _ExecutionCheckpoint,
        config: AgentModelConfig,
        current_ledger_revision: str,
    ) -> RunAgentResult:
        self._fail_step(
            task,
            state,
            "ledger_revision_changed_replan",
            "台账版本已变化，旧确认和旧计划已失效",
        )
        definitions = self._tools.definitions(AgentToolDefinitionsRequest())
        try:
            planned = self._planner.plan(
                PlanAgentTasksRequest(
                    correlation_id=f"{task.correlation_id}:replan",
                    source_text=state.source_text,
                    history=[],
                    active_product_id=state.active_product_id,
                    available_tools=[spec.name for spec in definitions.specs],
                    config=config,
                )
            )
        except AgentRuntimeError as exc:
            return RunAgentResult(status="failed", task_id=task.task_id, error=exc.detail)
        usages = [*state.usages, planned.usage]
        if planned.status == "needs_clarification":
            return RunAgentResult(
                status="needs_clarification",
                task_id=task.task_id,
                answer=AgentAnswer(
                    status="needs_clarification",
                    answer=planned.clarification or "台账变化后需要补充信息。",
                ),
                model_usages=usages,
            )
        if not isinstance(planned.plan, TaskPlanV2):
            return self._failure("invalid_agent_plan", "重新规划未返回 V2 任务计划")
        plan = self._without_redundant_summary_step(planned.plan)
        environment = self._environment.read(TaskEnvironmentRequest())
        replacement = self._tasks.create(
            CreateTaskRequest(
                trace_id=task.trace_id,
                correlation_id=task.correlation_id,
                kind="workflow",
                plan=plan,
                current_ledger_revision=current_ledger_revision,
                environment_epoch=environment.environment_epoch,
                idempotency_key=(
                    f"agent-replan:{task.task_id}:{current_ledger_revision}"
                ),
                session_id=task.session_id,
                agent_run_id=task.agent_run_id,
                requires_confirmation=False,
            )
        ).task
        self._record(
            replacement,
            category="task",
            name="agent.run.replanned",
            payload={"replaces_task_id": task.task_id},
        )
        return self._continue_task(
            replacement,
            config,
            source_text=state.source_text,
            history=[],
            active_product_id=state.active_product_id,
            active_product_name=state.active_product_name,
            initial_usages=usages,
        )

    def _claim(self, task_id: str) -> TaskRecord:
        environment = self._environment.read(TaskEnvironmentRequest())
        claimed = self._tasks.claim(
            ClaimTaskRequest(
                worker_id=self._worker_id,
                environment_epoch=environment.environment_epoch,
                current_ledger_revision=environment.current_ledger_revision,
                task_id=task_id,
            )
        ).task
        if claimed is None:
            current = self._tasks.get(GetTaskRequest(task_id=task_id))
            raise AgentRuntimeError(
                "Agent 任务当前不可执行",
                code="task_not_runnable",
                details={"task_id": task_id, "status": current.status},
            )
        return claimed

    @staticmethod
    def _without_redundant_summary_step(plan: TaskPlanV2) -> TaskPlanV2:
        """Keep model plans focused on evidence collection.

        The runtime has a dedicated final summarization call. Models can still
        emit a trailing step that repeats every tool from earlier steps only to
        synthesize the answer; executing it starts a second tool loop without
        new evidence and can consume the per-step request budget.
        """
        if len(plan.steps) < 2:
            return plan
        final_step = plan.steps[-1]
        if len(final_step.allowed_tools) <= 1:
            return plan
        earlier_tools = {
            tool for step in plan.steps[:-1] for tool in step.allowed_tools
        }
        if not set(final_step.allowed_tools).issubset(earlier_tools):
            return plan
        return plan.model_copy(update={"steps": plan.steps[:-1]})

    def _complete(
        self,
        task: TaskRecord,
        state: _ExecutionCheckpoint,
        config: AgentModelConfig,
        tools: list[ModelToolDefinition],
        *,
        required: bool,
    ) -> ModelResponse:
        try:
            response = self._gateway.complete(
                ModelRequest(
                    messages=state.messages,
                    model=config.model,
                    timeout_seconds=config.timeout_seconds,
                    max_output_tokens=config.max_output_tokens,
                    response_format="json_object",
                    tools=tools,
                    tool_choice="required" if required else "auto",
                    request_id=(
                        f"{task.correlation_id}:task:{task.current_step_index}:"
                        f"{state.model_requests + 1}"
                    ),
                )
            )
            self._record(
                task,
                category="model",
                name="agent.model.completed",
                payload={
                    "step_index": task.current_step_index,
                    "model": response.model,
                    "finish_reason": response.finish_reason,
                    "tool_call_count": len(response.tool_calls),
                    "usage": response.usage.model_dump(mode="json"),
                },
            )
            return response
        except ModelGatewayError as exc:
            raise AgentRuntimeError(
                str(exc),
                code="model_gateway_error",
                recoverable=exc.retryable,
                retryable=exc.retryable,
            ) from exc

    def _summarize(
        self,
        task: TaskRecord,
        answers: list[str],
        traces: list[AgentToolTrace],
        usages: list[ModelUsage],
        config: AgentModelConfig,
    ) -> RunAgentResult:
        try:
            response = self._gateway.complete(
                ModelRequest(
                    messages=[
                        ModelMessage(role="system", content=_SUMMARY_INSTRUCTIONS),
                        ModelMessage(
                            role="user",
                            content=canonical_json({"task_answers": answers}),
                        ),
                    ],
                    model=config.model,
                    timeout_seconds=config.timeout_seconds,
                    max_output_tokens=config.max_output_tokens,
                    response_format="json_object",
                    tool_choice="none",
                    request_id=f"{task.correlation_id}:summary",
                )
            )
            usages.append(response.usage)
            self._record(
                task,
                category="model",
                name="agent.summary.completed",
                payload={
                    "model": response.model,
                    "finish_reason": response.finish_reason,
                    "usage": response.usage.model_dump(mode="json"),
                },
            )
            answer = self._parse_answer(response)
        except (AgentRuntimeError, ModelGatewayError, ValidationError, ValueError):
            answer = AgentAnswer(
                status="answered",
                answer="\n".join(answers) or "任务已完成。",
            )
        answer = self._ensure_analysis_answer(answer, traces)
        return RunAgentResult(
            status=answer.status,
            task_id=task.task_id,
            plan=task.plan,
            answer=answer,
            tool_traces=traces,
            model_usages=usages,
        )

    @staticmethod
    def _initial_messages(
        step: AgentTaskStep,
        source_text: str,
        history: list[AgentHistoryMessage],
        *,
        active_product_id: str | None,
        active_product_name: str | None,
    ) -> list[ModelMessage]:
        messages = [ModelMessage(role="system", content=_EXECUTOR_INSTRUCTIONS)]
        messages.extend(ModelMessage(role=item.role, content=item.content) for item in history)
        messages.append(
            ModelMessage(
                role="user",
                content=(
                    f"原始请求：{source_text}\n任务目标：{step.objective}\n"
                    f"完成条件：{step.success_criteria}\n允许工具：{', '.join(step.allowed_tools)}"
                    + (
                        f"\n本次运行已锁定产品：{active_product_name or '未命名产品'}；"
                        f"产品 ID：{active_product_id}。所有产品类 Tool 的 product_id "
                        "必须使用该 ID，"
                        "不得填产品名称。"
                        if active_product_id is not None
                        else ""
                    )
                ),
            )
        )
        return messages

    def _normalized_action(
        self,
        task: TaskRecord,
        step: AgentTaskStep,
        call: ModelToolCall,
        state: _ExecutionCheckpoint,
    ) -> tuple[str, dict[str, JsonValue]]:
        try:
            raw = json.loads(call.function.arguments)
            if not isinstance(raw, dict):
                raise ValueError
            if call.function.name == "add_ledger_record":
                normalized = cast(
                    dict[str, JsonValue],
                    AddLedgerRecordToolInput.model_validate(raw).model_dump(mode="json"),
                )
            elif call.function.name == "update_product_profile":
                normalized = cast(
                    dict[str, JsonValue],
                    UpdateProductProfileToolInput.model_validate(raw).model_dump(
                        mode="json"
                    ),
                )
            elif call.function.name in _PRODUCT_ID_TOOLS:
                normalized = cast(
                    dict[str, JsonValue],
                    ProductToolInput.model_validate(raw).model_dump(mode="json"),
                )
            else:
                normalized = cast(dict[str, JsonValue], raw)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise AgentRuntimeError(
                "Tool 参数未通过结构校验",
                code="invalid_arguments",
                recoverable=True,
            ) from exc
        normalized = self._bind_product_reference(call.function.name, normalized, state)
        action_hash = sha256_json(
            {
                "task_id": task.task_id,
                "step_id": step.step_id,
                "tool_call_id": call.id,
                "tool_name": call.function.name,
                "arguments": normalized,
                "ledger_revision": task.current_ledger_revision,
            }
        )
        return action_hash, normalized

    @staticmethod
    def _bind_product_reference(
        tool_name: str,
        arguments: dict[str, JsonValue],
        state: _ExecutionCheckpoint,
    ) -> dict[str, JsonValue]:
        if tool_name not in _PRODUCT_ID_TOOLS or state.active_product_id is None:
            return arguments
        requested_product_id = arguments.get("product_id")
        if not isinstance(requested_product_id, str):
            return arguments
        if requested_product_id == state.active_product_id:
            return arguments
        if (
            state.active_product_name is not None
            and requested_product_id.strip() == state.active_product_name
        ):
            return {**arguments, "product_id": state.active_product_id}
        raise AgentRuntimeError(
            "单次 Agent 运行不得切换产品",
            code="single_product_only",
            recoverable=True,
            details={
                "selected_product_id": state.active_product_id,
                "requested_product_id": requested_product_id,
            },
        )

    @staticmethod
    def _with_normalized_arguments(
        call: ModelToolCall, arguments: dict[str, JsonValue]
    ) -> ModelToolCall:
        return call.model_copy(
            update={
                "function": call.function.model_copy(
                    update={"arguments": canonical_json(arguments)}
                )
            }
        )

    @staticmethod
    def _product_identity(output: AgentToolOutput) -> tuple[str | None, str | None]:
        if isinstance(output, ProductProfileToolOutput):
            return output.product_id, output.product_name
        if isinstance(output, ProductSnapshotToolOutput):
            return output.product.product_id, output.product.name
        if isinstance(output, PerformanceReportToolOutput):
            return output.report.product_id, output.report.product_name
        if isinstance(output, ModelAnalysisToolOutput):
            return output.result.report.product_id, output.result.report.product_name
        if isinstance(output, DataQualityWarningsToolOutput):
            return output.product_id, output.product_name
        if isinstance(output, UpdateProductProfileToolOutput):
            return output.mutation.product_id, None
        return None, None

    @staticmethod
    def _ensure_analysis_answer(
        answer: AgentAnswer, traces: list[AgentToolTrace]
    ) -> AgentAnswer:
        if answer.status != "answered":
            return answer
        labels = {
            "increase": "增持",
            "hold": "持有",
            "reduce": "减持",
            "insufficient_data": "数据不足，暂不建议增减持",
        }
        for trace in traces:
            if not isinstance(trace.output, ModelAnalysisToolOutput):
                continue
            suggestion = trace.output.result.suggestion
            if suggestion is None:
                continue
            marker = f"增减持建议：{labels[suggestion.direction]}"
            if marker in answer.answer:
                return answer
            suffix = f"\n\n{marker}。{suggestion.summary}"
            available = max(1, 8_000 - len(suffix))
            return AgentAnswer(
                status=answer.status,
                answer=answer.answer[:available] + suffix,
            )
        return answer

    @staticmethod
    def _approval_summary(
        value: AddLedgerRecordToolInput | UpdateProductProfileToolInput,
    ) -> str:
        if isinstance(value, UpdateProductProfileToolInput):
            changes: list[str] = []
            if value.product_type is not None:
                changes.append(f"产品类型：{value.product_type}")
            if value.risk_level is not None:
                changes.append(f"风险等级：{value.risk_level.value}")
            if value.performance_benchmark is not None:
                changes.append(f"业绩比较基准：{value.performance_benchmark}")
            return f"更新产品 {value.product_id} 的产品资料：" + "；".join(changes)
        labels = {
            "valuation": "估值",
            "transfer_in": "转入",
            "transfer_out": "转出",
        }
        return (
            f"向“{value.product_name}”新增 {value.record_date} 的"
            f"{labels[value.record_kind]}记录，金额 {value.amount} 元"
        )

    @staticmethod
    def _public_approval(
        task: TaskRecord,
        pending: TaskPendingApproval,
        value: AddLedgerRecordToolInput | UpdateProductProfileToolInput,
        *,
        product_name: str | None = None,
    ) -> AgentPendingApproval:
        if isinstance(value, UpdateProductProfileToolInput):
            return AgentPendingApproval(
                approval_id=pending.approval_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                action_hash=pending.action_hash,
                tool_name="update_product_profile",
                summary=pending.summary,
                reason=pending.reason,
                product_name=product_name,
                product_id=value.product_id,
                product_type=value.product_type,
                risk_level=value.risk_level,
                performance_benchmark=value.performance_benchmark,
            )
        return AgentPendingApproval(
            approval_id=pending.approval_id,
            task_id=task.task_id,
            trace_id=task.trace_id,
            action_hash=pending.action_hash,
            tool_name="add_ledger_record",
            summary=pending.summary,
            reason=pending.reason,
            product_name=value.product_name,
            record_date=value.record_date,
            record_kind=value.record_kind,
            amount=value.amount,
        )

    @staticmethod
    def _write_input(
        tool_name: str, arguments: JsonValue
    ) -> AddLedgerRecordToolInput | UpdateProductProfileToolInput:
        if tool_name == "add_ledger_record":
            return AddLedgerRecordToolInput.model_validate(arguments)
        if tool_name == "update_product_profile":
            return UpdateProductProfileToolInput.model_validate(arguments)
        raise ValueError(f"不是受支持的写入 Tool：{tool_name}")

    @staticmethod
    def _call_from_pending(pending: TaskPendingApproval) -> ModelToolCall:
        from wealth_agent.shared.model import ToolFunctionCall

        return ModelToolCall(
            id=pending.tool_call_id,
            function=ToolFunctionCall(
                name=pending.tool_name,
                arguments=canonical_json(pending.arguments),
            ),
        )

    @staticmethod
    def _load_checkpoint(task: TaskRecord) -> _ExecutionCheckpoint:
        payload = task.checkpoint.executor_state
        if payload is None or payload.type != "agent.executor.checkpoint.v2":
            raise AgentRuntimeError(
                "待确认任务缺少 Agent 检查点",
                code="agent_checkpoint_missing",
            )
        return _ExecutionCheckpoint.model_validate(payload.data)

    @staticmethod
    def _parse_answer(response: ModelResponse) -> AgentAnswer:
        if response.content is None or response.tool_calls:
            raise AgentRuntimeError(
                "模型未返回结构化任务结果",
                code="invalid_agent_output",
                recoverable=True,
            )
        try:
            return AgentAnswer.model_validate_json(response.content)
        except (ValidationError, ValueError) as exc:
            raise AgentRuntimeError(
                "Agent 回答未通过结构校验",
                code="invalid_agent_output",
                recoverable=True,
            ) from exc

    @staticmethod
    def _failure(code: str, message: str, task_id: str | None = None) -> RunAgentResult:
        return RunAgentResult(
            status="failed",
            task_id=task_id,
            error=ErrorDetail(code=code, message=message),
        )

    def _fail_step(
        self,
        task: TaskRecord,
        state: _ExecutionCheckpoint,
        code: str,
        message: str,
    ) -> RunAgentResult:
        error = ErrorDetail(code=code, message=message)
        current = self._tasks.get(GetTaskRequest(task_id=task.task_id))
        if current.status == "running":
            self._tasks.apply_step_result(
                ApplyStepResultRequest(
                    task_id=current.task_id,
                    expected_state_version=current.state_version,
                    result=TaskStepResult(
                        step_id=current.steps[current.current_step_index].step_id,
                        status="failed",
                        error=error,
                    ),
                    next_ledger_revision=task.current_ledger_revision,
                    executor_state=TaskPayload(
                        type="agent.executor.checkpoint.v2",
                        data=state.model_dump(mode="json"),
                    ),
                )
            )
        return RunAgentResult(
            status="failed",
            task_id=task.task_id,
            plan=task.plan,
            tool_traces=state.traces,
            model_usages=state.usages,
            error=error,
        )

    @staticmethod
    def _answers_from_task(task: TaskRecord) -> list[str]:
        answers: list[str] = []
        for step in task.steps:
            output = None if step.result is None else step.result.output
            if output is None or output.type != "agent.task.result.v2":
                continue
            data = output.data
            if not isinstance(data, dict):
                continue
            answer = data.get("answer")
            answer_text = answer.get("answer") if isinstance(answer, dict) else None
            if isinstance(answer_text, str):
                answers.append(answer_text)
        return answers

    def _record(
        self,
        task: TaskRecord,
        *,
        category: Literal["model", "tool", "task", "audit", "evaluation"],
        name: str,
        payload: JsonValue,
    ) -> None:
        if self._telemetry is None or self._clock is None:
            return
        self._telemetry.record(
            RecordEventRequest(
                event=GovernanceEvent(
                    event_id=new_id("agent-event"),
                    category=category,
                    name=name,
                    occurred_at=self._clock.now(),
                    correlation_id=task.correlation_id,
                    payload={
                        "agent_run_id": task.agent_run_id,
                        "task_id": task.task_id,
                        "event": payload,
                    },
                )
            )
        )


__all__ = ["TaskOrchestratingAgentRuntime"]
