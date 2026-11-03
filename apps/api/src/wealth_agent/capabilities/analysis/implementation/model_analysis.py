"""Analysis-owned prompt and model suggestion workflow."""

from __future__ import annotations

import json

from pydantic import ValidationError
from wealth_agent.capabilities.analysis.contract import (
    AnalysisResult,
    AnalysisSuggestion,
    AnalyzeProductRequest,
    ProductReportRequest,
)
from wealth_agent.capabilities.analysis.implementation.report import DeterministicAnalysis
from wealth_agent.shared.errors import ErrorDetail
from wealth_agent.shared.identity import new_id
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.model import (
    ModelGateway,
    ModelGatewayError,
    ModelMessage,
    ModelRequest,
)

ANALYSIS_INSTRUCTIONS = """你是本地理财分析助手。只根据提供的确定性报表给出方向性建议。
输出 JSON，字段为 direction、summary、rationale、limitations。
direction 只能是 increase、hold、reduce 或 insufficient_data。
limitations 必须是字符串数组；没有限制时必须输出 []，不能输出单个字符串。
示例：{"direction":"hold","summary":"...","rationale":"...","limitations":["..."]}
不得给出交易金额、配置比例、收益保证或报表中不存在的事实。"""


@trace_public_methods(layer="capability")
class ModelAssistedAnalysis:
    def __init__(
        self,
        gateway: ModelGateway,
        *,
        model: str,
        timeout_seconds: float = 30,
        max_output_tokens: int = 1_200,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._deterministic = DeterministicAnalysis()

    def analyze(self, request: AnalyzeProductRequest) -> AnalysisResult:
        report = self._deterministic.report(
            ProductReportRequest(
                product=request.product,
                ledger_revision=request.ledger_revision,
                generated_at=request.generated_at,
            )
        )
        analysis_run_id = new_id("analysis")
        return_is_invalid = (
            report.overall is None
            or report.overall.modified_dietz_return is None
            or any(item.modified_dietz_return is None for item in report.intervals)
        )
        if (
            report.status == "insufficient_data"
            or not report.profile.is_complete
            or return_is_invalid
        ):
            limitations = report.warnings or ["需要完整的产品资料和有效的收益区间"]
            return AnalysisResult(
                analysis_run_id=analysis_run_id,
                report=report,
                suggestion=AnalysisSuggestion(
                    direction="insufficient_data",
                    summary="当前数据不足，暂不提供增减持建议",
                    rationale="；".join(limitations),
                    limitations=limitations,
                ),
            )
        model_request = ModelRequest(
            messages=[
                ModelMessage(role="system", content=ANALYSIS_INSTRUCTIONS),
                ModelMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "question": request.question,
                            "report": report.model_dump(mode="json"),
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            model=self._model,
            timeout_seconds=self._timeout_seconds,
            max_output_tokens=self._max_output_tokens,
            request_id=analysis_run_id,
        )
        try:
            response = self._gateway.complete(model_request)
            if response.content is None:
                return AnalysisResult(
                    analysis_run_id=analysis_run_id,
                    report=report,
                    model_error=ErrorDetail(
                        code="model_output_empty",
                        message="模型没有返回建议内容",
                        details={"model": self._model},
                    ),
                )
            suggestion = self._parse_suggestion(response.content)
            return AnalysisResult(
                analysis_run_id=analysis_run_id,
                report=report,
                suggestion=suggestion,
            )
        except ModelGatewayError as exc:
            return AnalysisResult(
                analysis_run_id=analysis_run_id,
                report=report,
                model_error=ErrorDetail(
                    code=exc.code,
                    message=str(exc),
                    retryable=exc.retryable,
                    details={"model": self._model},
                ),
            )
        except (ValidationError, ValueError) as exc:
            return AnalysisResult(
                analysis_run_id=analysis_run_id,
                report=report,
                model_error=ErrorDetail(
                    code="model_output_invalid",
                    message="模型输出不符合 AnalysisSuggestion 契约",
                    details={
                        "model": self._model,
                        "validation_error_count": (
                            exc.error_count() if isinstance(exc, ValidationError) else 1
                        ),
                    },
                ),
            )

    @staticmethod
    def _parse_suggestion(content: str) -> AnalysisSuggestion:
        payload = json.loads(content)
        if isinstance(payload, dict) and isinstance(payload.get("limitations"), str):
            payload = dict(payload)
            payload["limitations"] = [payload["limitations"]]
        return AnalysisSuggestion.model_validate(payload)
