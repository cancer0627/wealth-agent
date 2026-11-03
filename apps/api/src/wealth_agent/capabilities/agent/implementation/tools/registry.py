"""Agent tool registry over public business contracts."""

from __future__ import annotations

import time
from datetime import date
from decimal import Decimal
from typing import cast

from pydantic import BaseModel, ValidationError
from wealth_agent.capabilities.agent.contract import (
    AddLedgerRecordToolInput,
    AddLedgerRecordToolOutput,
    AgentToolDefinitionsRequest,
    AgentToolDefinitionsResult,
    AgentToolError,
    AgentToolName,
    AgentToolOutput,
    AgentToolPermissionError,
    AgentToolSpec,
    DataQualityWarningsToolOutput,
    ExecuteAgentToolRequest,
    ExecuteAgentToolResult,
    ListProductsToolInput,
    ListProductsToolOutput,
    ModelAnalysisToolOutput,
    PerformanceReportToolOutput,
    ProductProfileToolOutput,
    ProductSnapshotToolOutput,
    ProductToolInput,
    ToolErrorSpec,
    ToolIdempotencyPolicy,
    UpdateProductProfileToolInput,
    UpdateProductProfileToolOutput,
)
from wealth_agent.capabilities.analysis.contract import (
    AnalysisEngine,
    AnalysisError,
    AnalysisService,
    AnalyzeProductRequest,
    ProductReportRequest,
)
from wealth_agent.capabilities.portfolio.contract import (
    AddRecord,
    PortfolioError,
    PortfolioReader,
    PortfolioWriter,
    ProductNotFoundError,
    RecordKind,
    UpdateProductProfile,
)
from wealth_agent.shared.clock import Clock
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.model import ModelToolDefinition, ToolFunctionDefinition

_TOOL_DESCRIPTIONS: dict[AgentToolName, str] = {
    "list_products": "列出台账中的产品及数据摘要。",
    "get_product_profile": "读取一个产品的资料及完整度。",
    "get_product_snapshot": "读取一个产品的估值、现金流和资料快照。",
    "get_performance_report": "读取一个产品的确定性收益报表。",
    "get_data_quality_warnings": "读取一个产品的数据质量警告。",
    "get_model_analysis": "基于确定性报表生成结构化增减持方向建议，不执行交易。",
    "add_ledger_record": "向台账新增一条估值、转入或转出记录。",
    "update_product_profile": "更新一个产品的产品类型、风险等级或业绩比较基准。",
}

_INPUT_MODELS: dict[AgentToolName, type[BaseModel]] = {
    "list_products": ListProductsToolInput,
    "get_product_profile": ProductToolInput,
    "get_product_snapshot": ProductToolInput,
    "get_performance_report": ProductToolInput,
    "get_data_quality_warnings": ProductToolInput,
    "get_model_analysis": ProductToolInput,
    "add_ledger_record": AddLedgerRecordToolInput,
    "update_product_profile": UpdateProductProfileToolInput,
}

_OUTPUT_MODELS: dict[AgentToolName, type[BaseModel]] = {
    "list_products": ListProductsToolOutput,
    "get_product_profile": ProductProfileToolOutput,
    "get_product_snapshot": ProductSnapshotToolOutput,
    "get_performance_report": PerformanceReportToolOutput,
    "get_data_quality_warnings": DataQualityWarningsToolOutput,
    "get_model_analysis": ModelAnalysisToolOutput,
    "add_ledger_record": AddLedgerRecordToolOutput,
    "update_product_profile": UpdateProductProfileToolOutput,
}

_COMMON_ERRORS = [
    ToolErrorSpec(
        code="invalid_arguments",
        message="输入参数未通过 Tool Schema 校验",
        recoverable=True,
        retryable=False,
    ),
    ToolErrorSpec(
        code="permission_denied",
        message="当前 Agent 没有调用该 Tool 的权限",
        recoverable=False,
        retryable=False,
    ),
    ToolErrorSpec(
        code="single_product_only",
        message="单次 Agent 运行不得切换到第二个产品",
        recoverable=True,
        retryable=False,
    ),
    ToolErrorSpec(
        code="product_not_found",
        message="找不到产品",
        recoverable=True,
        retryable=False,
    ),
    ToolErrorSpec(
        code="tool_timeout",
        message="Tool 超过运行时限",
        recoverable=True,
        retryable=True,
    ),
]


@trace_public_methods(layer="capability")
class AgentToolRegistry:
    def __init__(
        self,
        portfolio: PortfolioReader,
        analysis: AnalysisEngine,
        clock: Clock,
        portfolio_writer: PortfolioWriter | None = None,
        analysis_service: AnalysisService | None = None,
    ) -> None:
        self._portfolio = portfolio
        self._analysis = analysis
        self._clock = clock
        self._portfolio_writer = portfolio_writer
        self._analysis_service = analysis_service
        self._specs = {spec.name: spec for spec in self._build_specs()}

    def definitions(self, _request: AgentToolDefinitionsRequest) -> AgentToolDefinitionsResult:
        specs = list(self._specs.values())
        return AgentToolDefinitionsResult(
            definitions=[
                ModelToolDefinition(
                    function=ToolFunctionDefinition(
                        name=spec.name,
                        description=spec.description,
                        parameters=cast(dict[str, object], spec.input_schema),
                    )
                )
                for spec in specs
            ],
            specs=specs,
        )

    def execute(self, request: ExecuteAgentToolRequest) -> ExecuteAgentToolResult:
        started_at = time.monotonic()
        name = self._tool_name(request.call.function.name)
        try:
            spec = self._specs[name]
        except KeyError as exc:
            raise AgentToolPermissionError("该 Agent Tool 当前未启用") from exc
        output: AgentToolOutput
        if not set(spec.permissions).issubset(request.granted_permissions):
            raise AgentToolPermissionError("缺少 Agent Tool 所需权限")
        arguments = self._parse_arguments(name, request.call.function.arguments)

        if name in {"add_ledger_record", "update_product_profile"}:
            if self._portfolio_writer is None:
                raise AgentToolPermissionError("台账写入 Tool 未启用")
            if (
                request.expected_ledger_revision is None
                or request.idempotency_key is None
                or request.source_text is None
            ):
                raise AgentToolError(
                    "台账写入缺少 Agent 生成的执行上下文",
                    code="write_context_missing",
                )
            try:
                if name == "add_ledger_record":
                    write_input = cast(AddLedgerRecordToolInput, arguments)
                    mutation = self._portfolio_writer.execute(
                        AddRecord(
                            product_name=write_input.product_name,
                            record_date=date.fromisoformat(write_input.record_date),
                            record_kind=RecordKind(write_input.record_kind),
                            amount=Decimal(write_input.amount),
                            source_text=request.source_text,
                            expected_revision=request.expected_ledger_revision,
                            idempotency_key=request.idempotency_key,
                        )
                    )
                    output = AddLedgerRecordToolOutput(
                        ledger_revision=mutation.ledger_revision,
                        mutation=mutation,
                    )
                else:
                    profile_input = cast(UpdateProductProfileToolInput, arguments)
                    self._ensure_selected_product(
                        request.selected_product_id, profile_input.product_id
                    )
                    view = self._portfolio.get_product(profile_input.product_id)
                    updates = {
                        key: value
                        for key, value in {
                            "product_type": profile_input.product_type,
                            "risk_level": profile_input.risk_level,
                            "performance_benchmark": profile_input.performance_benchmark,
                        }.items()
                        if value is not None
                    }
                    profile = view.product.profile.model_copy(update=updates)
                    mutation = self._portfolio_writer.execute(
                        UpdateProductProfile(
                            product_id=profile_input.product_id,
                            product_type=profile.product_type,
                            risk_level=profile.risk_level,
                            performance_benchmark=profile.performance_benchmark,
                            expected_revision=request.expected_ledger_revision,
                            idempotency_key=request.idempotency_key,
                        )
                    )
                    output = UpdateProductProfileToolOutput(
                        ledger_revision=mutation.ledger_revision,
                        mutation=mutation,
                    )
            except ProductNotFoundError as exc:
                raise AgentToolError(
                    str(exc),
                    code="product_not_found",
                    recoverable=True,
                    details=exc.detail.details,
                ) from exc
            except PortfolioError as exc:
                raise AgentToolError(
                    exc.detail.message,
                    code=exc.detail.code,
                    recoverable=exc.detail.retryable,
                    retryable=exc.detail.retryable,
                    details=exc.detail.details,
                ) from exc
        elif name == "list_products":
            catalog = self._portfolio.list_products()
            output = ListProductsToolOutput(
                ledger_revision=catalog.ledger_revision,
                products=catalog.products,
            )
        else:
            product_arguments = cast(ProductToolInput, arguments)
            self._ensure_selected_product(
                request.selected_product_id, product_arguments.product_id
            )
            try:
                view = self._portfolio.get_product(product_arguments.product_id)
            except ProductNotFoundError as exc:
                raise AgentToolError(
                    str(exc),
                    code="product_not_found",
                    recoverable=True,
                    details=exc.detail.details,
                ) from exc
            product = view.product
            if name == "get_product_profile":
                output = ProductProfileToolOutput(
                    ledger_revision=view.ledger_revision,
                    product_id=product.product_id,
                    product_name=product.name,
                    product_revision=product.revision,
                    profile=product.profile,
                    missing_fields=product.profile.missing_fields,
                )
            elif name == "get_product_snapshot":
                output = ProductSnapshotToolOutput(
                    ledger_revision=view.ledger_revision,
                    product=product,
                )
            elif name == "get_model_analysis":
                if self._analysis_service is None:
                    raise AgentToolPermissionError("模型分析 Tool 未启用")
                try:
                    result = self._analysis_service.analyze(
                        AnalyzeProductRequest(
                            product=product,
                            ledger_revision=view.ledger_revision,
                            generated_at=self._clock.now(),
                            question=request.source_text,
                        )
                    )
                except AnalysisError as exc:
                    raise AgentToolError(
                        exc.detail.message,
                        code=exc.detail.code,
                        recoverable=exc.detail.retryable,
                        retryable=exc.detail.retryable,
                        details=exc.detail.details,
                    ) from exc
                output = ModelAnalysisToolOutput(
                    ledger_revision=view.ledger_revision,
                    result=result,
                )
            else:
                report = self._analysis.report(
                    ProductReportRequest(
                        product=product,
                        ledger_revision=view.ledger_revision,
                        generated_at=self._clock.now(),
                    )
                )
                if name == "get_performance_report":
                    output = PerformanceReportToolOutput(
                        ledger_revision=view.ledger_revision,
                        report=report,
                    )
                else:
                    output = DataQualityWarningsToolOutput(
                        ledger_revision=view.ledger_revision,
                        product_id=report.product_id,
                        product_name=report.product_name,
                        product_revision=report.product_revision,
                        status=report.status,
                        warnings=report.warnings,
                    )
        if time.monotonic() - started_at > spec.timeout_seconds:
            raise AgentToolError(
                "Tool 超过运行时限",
                code="tool_timeout",
                recoverable=True,
                retryable=True,
            )
        return ExecuteAgentToolResult(
            tool_call_id=request.call.id,
            tool_name=name,
            contract_hash=spec.contract_hash,
            output=output,
        )

    @staticmethod
    def _tool_name(value: str) -> AgentToolName:
        if value not in _TOOL_DESCRIPTIONS:
            raise AgentToolPermissionError(f"未授权的 Agent Tool：{value}")
        return value

    @staticmethod
    def _parse_arguments(name: AgentToolName, raw: str) -> BaseModel:
        try:
            return _INPUT_MODELS[name].model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            raise AgentToolError(
                f"Tool 参数无效：{name}",
                code="invalid_arguments",
                recoverable=True,
            ) from exc

    def _build_specs(self) -> list[AgentToolSpec]:
        names = list(_TOOL_DESCRIPTIONS)
        if self._analysis_service is None:
            names.remove("get_model_analysis")
        if self._portfolio_writer is None:
            names.remove("add_ledger_record")
            names.remove("update_product_profile")
        specs = []
        for name in names:
            is_write = name in {"add_ledger_record", "update_product_profile"}
            specs.append(
                AgentToolSpec(
                    name=name,
                    description=_TOOL_DESCRIPTIONS[name],
                    input_schema=_INPUT_MODELS[name].model_json_schema(),
                    output_schema=_OUTPUT_MODELS[name].model_json_schema(),
                    permissions=["ledger:write" if is_write else "ledger:read"],
                    timeout_seconds=(30.0 if name == "get_model_analysis" else 5.0),
                    errors=_COMMON_ERRORS,
                    side_effects="ledger_write" if is_write else "none",
                    idempotency=ToolIdempotencyPolicy(
                        mode="write_command" if is_write else "read_only_revision_scoped",
                        key_required=is_write,
                    ),
                )
            )
        return specs

    @staticmethod
    def _ensure_selected_product(
        selected_product_id: str | None, requested_product_id: str
    ) -> None:
        if selected_product_id is not None and selected_product_id != requested_product_id:
            raise AgentToolError(
                "单次 Agent 运行不得切换产品",
                code="single_product_only",
                recoverable=True,
                details={
                    "selected_product_id": selected_product_id,
                    "requested_product_id": requested_product_id,
                },
            )


# 兼容仍在使用旧类名的内部测试和只读组装；有写入端口时实际发布 V2 工具集。
ReadOnlyAgentToolRegistry = AgentToolRegistry
