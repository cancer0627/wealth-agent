"""Structured public contracts for Agent tools."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)
from wealth_agent.capabilities.analysis.contract import AnalysisResult, ProductReport
from wealth_agent.capabilities.portfolio.contract import (
    MutationResult,
    ProductProfile,
    ProductSnapshot,
    ProductSummary,
    RiskLevel,
)
from wealth_agent.capabilities.tasks.contract import TaskPlanType
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails
from wealth_agent.shared.model import ModelToolCall, ModelToolDefinition, ModelUsage
from wealth_agent.shared.serialization import sha256_json

TOOL_SCHEMA_VERSION = "wealth-agent-tools-v2"
DEFAULT_TOOL_TIMEOUT_SECONDS = 5.0

AgentToolName = Literal[
    "list_products",
    "get_product_profile",
    "get_product_snapshot",
    "get_performance_report",
    "get_data_quality_warnings",
    "get_model_analysis",
    "add_ledger_record",
    "update_product_profile",
]


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolErrorSpec(AgentContract):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool
    retryable: bool


class ToolIdempotencyPolicy(AgentContract):
    mode: Literal["read_only_revision_scoped", "write_command"]
    key_required: bool


class AgentToolSpec(AgentContract):
    schema_version: Literal["wealth-agent-tools-v2"] = "wealth-agent-tools-v2"
    name: AgentToolName
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    permissions: list[Literal["ledger:read", "ledger:write"]] = Field(min_length=1)
    timeout_seconds: float = Field(default=DEFAULT_TOOL_TIMEOUT_SECONDS, gt=0, le=30)
    errors: list[ToolErrorSpec] = Field(default_factory=list)
    side_effects: Literal["none", "ledger_write"] = "none"
    idempotency: ToolIdempotencyPolicy

    @property
    def contract_hash(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class ListProductsToolInput(AgentContract):
    schema_version: Literal[1] = 1


class ProductToolInput(AgentContract):
    schema_version: Literal[1] = 1
    product_id: str = Field(min_length=1, max_length=200)


class AddLedgerRecordToolInput(AgentContract):
    schema_version: Literal[1] = 1
    product_name: str = Field(min_length=1, max_length=200)
    record_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    record_kind: Literal["valuation", "transfer_in", "transfer_out"]
    amount: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")


class UpdateProductProfileToolInput(AgentContract):
    schema_version: Literal[1] = 1
    product_id: str = Field(min_length=1, max_length=200)
    product_type: str | None = Field(default=None, max_length=200)
    risk_level: RiskLevel | None = None
    performance_benchmark: str | None = Field(default=None, max_length=200)

    @field_validator("product_type", "performance_benchmark")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("资料字段不能为空")
        return normalized

    @field_validator("risk_level", mode="before")
    @classmethod
    def normalize_risk_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_updates(self) -> UpdateProductProfileToolInput:
        if (
            self.product_type is None
            and self.risk_level is None
            and self.performance_benchmark is None
        ):
            raise ValueError("至少提供一项需要更新的产品资料")
        return self


class ListProductsToolOutput(AgentContract):
    type: Literal["portfolio.product_catalog.v1"] = "portfolio.product_catalog.v1"
    ledger_revision: str = Field(min_length=1)
    products: list[ProductSummary] = Field(default_factory=list)


class ProductProfileToolOutput(AgentContract):
    type: Literal["portfolio.product_profile.v1"] = "portfolio.product_profile.v1"
    ledger_revision: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    product_revision: str = Field(min_length=1)
    profile: ProductProfile
    missing_fields: list[str] = Field(default_factory=list)


class ProductSnapshotToolOutput(AgentContract):
    type: Literal["portfolio.product_snapshot.v1"] = "portfolio.product_snapshot.v1"
    ledger_revision: str = Field(min_length=1)
    product: ProductSnapshot


class PerformanceReportToolOutput(AgentContract):
    type: Literal["analysis.performance_report.v1"] = "analysis.performance_report.v1"
    ledger_revision: str = Field(min_length=1)
    report: ProductReport


class DataQualityWarningsToolOutput(AgentContract):
    type: Literal["analysis.data_quality_warnings.v1"] = "analysis.data_quality_warnings.v1"
    ledger_revision: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    product_revision: str = Field(min_length=1)
    status: Literal["calculated", "insufficient_data"]
    warnings: list[str] = Field(default_factory=list)


class ModelAnalysisToolOutput(AgentContract):
    type: Literal["analysis.model_analysis.v1"] = "analysis.model_analysis.v1"
    ledger_revision: str = Field(min_length=1)
    result: AnalysisResult


class AddLedgerRecordToolOutput(AgentContract):
    type: Literal["portfolio.add_ledger_record.v1"] = "portfolio.add_ledger_record.v1"
    ledger_revision: str = Field(min_length=1)
    mutation: MutationResult


class UpdateProductProfileToolOutput(AgentContract):
    type: Literal["portfolio.update_product_profile.v1"] = (
        "portfolio.update_product_profile.v1"
    )
    ledger_revision: str = Field(min_length=1)
    mutation: MutationResult


AgentToolOutput = Annotated[
    ListProductsToolOutput
    | ProductProfileToolOutput
    | ProductSnapshotToolOutput
    | PerformanceReportToolOutput
    | DataQualityWarningsToolOutput
    | ModelAnalysisToolOutput
    | AddLedgerRecordToolOutput
    | UpdateProductProfileToolOutput,
    Field(discriminator="type"),
]


class AgentToolDefinitionsRequest(AgentContract):
    pass


class AgentToolDefinitionsResult(AgentContract):
    schema_version: Literal["wealth-agent-tools-v2"] = "wealth-agent-tools-v2"
    definitions: list[ModelToolDefinition]
    specs: list[AgentToolSpec]


class ExecuteAgentToolRequest(AgentContract):
    call: ModelToolCall
    granted_permissions: list[str] = Field(default_factory=list)
    selected_product_id: str | None = Field(default=None, min_length=1)
    expected_ledger_revision: str | None = Field(default=None, min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    source_text: str | None = Field(default=None, min_length=1, max_length=4_000)


class ExecuteAgentToolResult(AgentContract):
    tool_call_id: str = Field(min_length=1)
    tool_name: AgentToolName
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output: AgentToolOutput


class AgentHistoryMessage(AgentContract):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class AgentModelConfig(AgentContract):
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_output_tokens: int = Field(default=1_500, ge=1, le=16_000)
    max_model_requests: int = Field(default=4, ge=2, le=10)
    max_tool_calls: int = Field(default=5, ge=1, le=10)


class RouteAgentRequest(AgentContract):
    correlation_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=4_000)
    history: list[AgentHistoryMessage] = Field(default_factory=list, max_length=20)
    active_product_id: str | None = Field(default=None, min_length=1)
    config: AgentModelConfig


class RouteAgentResult(AgentContract):
    intent: Literal["list_products", "product_question", "unsupported"]
    requested_tools: list[AgentToolName] = Field(default_factory=list, max_length=5)
    clarification: str | None = Field(default=None, min_length=1, max_length=1_000)
    usage: ModelUsage = Field(default_factory=ModelUsage)

    @model_validator(mode="after")
    def validate_route(self) -> RouteAgentResult:
        if len(self.requested_tools) != len(set(self.requested_tools)):
            raise ValueError("路由工具不得重复")
        if self.intent == "unsupported":
            if self.requested_tools or self.clarification is None:
                raise ValueError("不支持的请求必须只返回澄清问题")
        elif self.clarification is not None or not self.requested_tools:
            raise ValueError("可执行路由必须返回工具且不能返回澄清问题")
        if self.intent == "list_products" and self.requested_tools != ["list_products"]:
            raise ValueError("产品列表意图只能调用 list_products")
        if self.intent == "product_question" and "list_products" in self.requested_tools:
            raise ValueError("单产品问题不能混入 list_products")
        return self


class PlanAgentRequest(AgentContract):
    source_text: str = Field(min_length=1, max_length=4_000)
    route: RouteAgentResult
    active_product_id: str | None = Field(default=None, min_length=1)


class PlanAgentTasksRequest(AgentContract):
    correlation_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1, max_length=4_000)
    history: list[AgentHistoryMessage] = Field(default_factory=list, max_length=20)
    active_product_id: str | None = Field(default=None, min_length=1)
    available_tools: list[AgentToolName] = Field(min_length=1, max_length=10)
    config: AgentModelConfig


class PlanAgentResult(AgentContract):
    status: Literal["planned", "needs_clarification"]
    plan: TaskPlanType | None = None
    clarification: str | None = Field(default=None, min_length=1, max_length=1_000)
    usage: ModelUsage = Field(default_factory=ModelUsage)

    @model_validator(mode="after")
    def validate_plan_result(self) -> PlanAgentResult:
        if self.status == "planned" and (self.plan is None or self.clarification is not None):
            raise ValueError("已规划结果必须只包含计划")
        if self.status == "needs_clarification" and (
            self.plan is not None or self.clarification is None
        ):
            raise ValueError("待澄清结果必须只包含澄清问题")
        return self


class AgentToolTrace(AgentContract):
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: Literal["completed", "failed"]
    output: AgentToolOutput | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def validate_trace(self) -> AgentToolTrace:
        if self.status == "completed" and (self.output is None or self.error is not None):
            raise ValueError("成功工具轨迹必须只包含输出")
        if self.status == "failed" and (self.output is not None or self.error is None):
            raise ValueError("失败工具轨迹必须只包含错误")
        return self


class AgentAnswer(AgentContract):
    status: Literal["answered", "needs_clarification"]
    answer: str = Field(min_length=1, max_length=8_000)


class RunAgentRequest(RouteAgentRequest):
    session_id: str = Field(default="legacy-session", min_length=1)
    granted_permissions: list[str] = Field(default_factory=list)
    expected_ledger_revision: str = Field(min_length=1)


class RunAgentResult(AgentContract):
    status: Literal["answered", "needs_clarification", "awaiting_approval", "failed"]
    route: RouteAgentResult | None = None
    plan: TaskPlanType | None = None
    answer: AgentAnswer | None = None
    tool_traces: list[AgentToolTrace] = Field(default_factory=list)
    model_usages: list[ModelUsage] = Field(default_factory=list)
    error: ErrorDetail | None = None
    task_id: str | None = None
    pending_approval: AgentPendingApproval | None = None

    @model_validator(mode="after")
    def validate_run_result(self) -> RunAgentResult:
        if self.status == "failed" and self.error is None:
            raise ValueError("失败 Agent 结果必须包含错误")
        if self.status == "awaiting_approval":
            if self.pending_approval is None or self.answer is not None or self.error is not None:
                raise ValueError("待确认 Agent 结果必须只包含确认请求")
        elif self.status != "failed" and (self.answer is None or self.error is not None):
            raise ValueError("成功 Agent 结果必须只包含回答")
        return self


class AgentPendingApproval(AgentContract):
    approval_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_name: Literal["add_ledger_record", "update_product_profile"]
    summary: str = Field(min_length=1, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)
    product_name: str | None = Field(default=None, min_length=1)
    product_id: str | None = Field(default=None, min_length=1)
    record_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    record_kind: Literal["valuation", "transfer_in", "transfer_out"] | None = None
    amount: str | None = Field(default=None, min_length=1)
    product_type: str | None = Field(default=None, min_length=1)
    risk_level: RiskLevel | None = None
    performance_benchmark: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_approval(self) -> AgentPendingApproval:
        if self.tool_name == "add_ledger_record":
            if (
                self.product_name is None
                or self.record_date is None
                or self.record_kind is None
                or self.amount is None
            ):
                raise ValueError("新增台账记录确认缺少必要字段")
            return self
        if self.product_id is None or not any(
            value is not None
            for value in (
                self.product_type,
                self.risk_level,
                self.performance_benchmark,
            )
        ):
            raise ValueError("更新产品资料确认缺少必要字段")
        return self


class RespondAgentApprovalRequest(AgentContract):
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)
    approval_id: str = Field(min_length=1)
    decision: Literal["approve", "reject"]
    config: AgentModelConfig


class CancelAgentRunRequest(AgentContract):
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)


class GetAgentRunRequest(AgentContract):
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)


class AgentToolError(RuntimeError):
    code = "agent_tool_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        recoverable: bool = False,
        retryable: bool = False,
        details: ErrorDetails | None = None,
    ) -> None:
        self.recoverable = recoverable
        self.detail = ErrorDetail(
            code=code or self.code,
            message=message,
            retryable=retryable,
            details={**(details or {}), "recoverable": recoverable},
        )
        super().__init__(message)


class AgentToolPermissionError(AgentToolError):
    code = "permission_denied"


class AgentRuntimeError(AgentToolError):
    code = "agent_runtime_error"


class AgentRouterPort(Protocol):
    def route(self, request: RouteAgentRequest) -> RouteAgentResult: ...


class AgentPlannerPort(Protocol):
    def plan(self, request: PlanAgentRequest) -> PlanAgentResult: ...


class AgentTaskPlannerPort(Protocol):
    def plan(self, request: PlanAgentTasksRequest) -> PlanAgentResult: ...


class AgentService(Protocol):
    def run(self, request: RunAgentRequest) -> RunAgentResult: ...

    def respond_approval(self, request: RespondAgentApprovalRequest) -> RunAgentResult: ...

    def cancel(self, request: CancelAgentRunRequest) -> RunAgentResult: ...

    def get_run(self, request: GetAgentRunRequest) -> RunAgentResult: ...


class AgentToolPort(Protocol):
    def definitions(self, request: AgentToolDefinitionsRequest) -> AgentToolDefinitionsResult: ...

    def execute(self, request: ExecuteAgentToolRequest) -> ExecuteAgentToolResult: ...


__all__ = [
    "AddLedgerRecordToolInput",
    "AddLedgerRecordToolOutput",
    "DEFAULT_TOOL_TIMEOUT_SECONDS",
    "TOOL_SCHEMA_VERSION",
    "AgentContract",
    "AgentAnswer",
    "AgentHistoryMessage",
    "AgentPendingApproval",
    "AgentModelConfig",
    "AgentPlannerPort",
    "AgentRouterPort",
    "AgentRuntimeError",
    "AgentService",
    "AgentToolTrace",
    "AgentTaskPlannerPort",
    "AgentToolDefinitionsRequest",
    "AgentToolDefinitionsResult",
    "AgentToolError",
    "AgentToolName",
    "AgentToolOutput",
    "AgentToolPermissionError",
    "AgentToolPort",
    "AgentToolSpec",
    "CancelAgentRunRequest",
    "DataQualityWarningsToolOutput",
    "ExecuteAgentToolRequest",
    "ExecuteAgentToolResult",
    "GetAgentRunRequest",
    "ListProductsToolInput",
    "ListProductsToolOutput",
    "ModelAnalysisToolOutput",
    "PerformanceReportToolOutput",
    "ProductProfileToolOutput",
    "ProductSnapshotToolOutput",
    "ProductToolInput",
    "PlanAgentRequest",
    "PlanAgentResult",
    "PlanAgentTasksRequest",
    "RouteAgentRequest",
    "RouteAgentResult",
    "RunAgentRequest",
    "RunAgentResult",
    "RespondAgentApprovalRequest",
    "ToolErrorSpec",
    "ToolIdempotencyPolicy",
    "UpdateProductProfileToolInput",
    "UpdateProductProfileToolOutput",
]
