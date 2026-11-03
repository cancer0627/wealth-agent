"""Deterministic analysis public contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_serializer
from wealth_agent.capabilities.portfolio.contract import (
    CashFlow,
    ProductProfile,
    ProductSnapshot,
    Valuation,
)
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails


class AnalysisContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeightedCashFlow(AnalysisContract):
    flow_id: str
    date: date
    type: Literal["transfer_in", "transfer_out"]
    amount: Decimal
    signed_amount: Decimal
    days_from_start: int
    weight: Decimal
    weighted_amount: Decimal

    @field_serializer("weight", when_used="json")
    def serialize_weight(self, value: Decimal) -> str:
        return format(value, "f")


class ReturnResult(AnalysisContract):
    status: Literal["calculated", "invalid_denominator"]
    start_date: date
    end_date: date
    total_days: int
    beginning_value: Decimal
    ending_value: Decimal
    cash_flows: list[WeightedCashFlow] = Field(default_factory=list)
    net_cash_flow: Decimal
    net_profit: Decimal
    weighted_cash_flow: Decimal
    weighted_capital: Decimal
    modified_dietz_return: Decimal | None
    warnings: list[str] = Field(default_factory=list)

    @field_serializer("modified_dietz_return", when_used="json")
    def serialize_return(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class ProductReport(AnalysisContract):
    schema_version: Literal[1, 2] = 2
    ledger_revision: str
    product_id: str
    product_name: str
    product_revision: str
    generated_at: datetime
    status: Literal["calculated", "insufficient_data"]
    profile: ProductProfile = Field(default_factory=ProductProfile)
    total_transfer_in: Decimal
    total_transfer_out: Decimal
    net_cash_flow: Decimal
    valuations: list[Valuation] = Field(default_factory=list)
    cash_flows: list[CashFlow] = Field(default_factory=list)
    overall: ReturnResult | None
    intervals: list[ReturnResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProductReportRequest(AnalysisContract):
    product: ProductSnapshot
    ledger_revision: str = Field(min_length=1)
    generated_at: datetime


class AnalyzeProductRequest(ProductReportRequest):
    question: str | None = None


class AnalysisSuggestion(AnalysisContract):
    direction: Literal["increase", "hold", "reduce", "insufficient_data"]
    summary: str = Field(min_length=1, max_length=4_000)
    rationale: str = Field(min_length=1, max_length=4_000)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class AnalysisResult(AnalysisContract):
    analysis_run_id: str
    report: ProductReport
    suggestion: AnalysisSuggestion | None = None
    model_error: ErrorDetail | None = None


class AnalysisRecord(AnalysisContract):
    schema_version: Literal[1] = 1
    created_at: datetime
    question: str | None = None
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    result: AnalysisResult


class SaveAnalysisResult(AnalysisContract):
    record: AnalysisRecord
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created: bool


class LatestAnalysisRequest(AnalysisContract):
    product_id: str = Field(min_length=1)
    ledger_revision: str | None = Field(default=None, min_length=1)


class LatestAnalysisResult(AnalysisContract):
    record: AnalysisRecord | None = None
    errors: list[ErrorDetail] = Field(default_factory=list)


class AnalysisError(RuntimeError):
    code = "analysis_error"

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        details: ErrorDetails | None = None,
    ) -> None:
        self.detail = ErrorDetail(
            code=self.code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
        super().__init__(message)


class AnalysisPersistenceError(AnalysisError):
    code = "analysis_persistence_error"


class AnalysisPersistenceConflictError(AnalysisPersistenceError):
    code = "analysis_persistence_conflict"


class AnalysisEngine(Protocol):
    def report(self, request: ProductReportRequest) -> ProductReport: ...


class AnalysisService(Protocol):
    def analyze(self, request: AnalyzeProductRequest) -> AnalysisResult: ...


class AnalysisRepository(Protocol):
    def save(self, record: AnalysisRecord) -> SaveAnalysisResult: ...

    def latest(self, request: LatestAnalysisRequest) -> LatestAnalysisResult: ...


__all__ = [
    "AnalysisError",
    "AnalysisEngine",
    "AnalysisPersistenceConflictError",
    "AnalysisPersistenceError",
    "AnalysisRecord",
    "AnalysisRepository",
    "AnalysisResult",
    "AnalysisService",
    "AnalysisSuggestion",
    "AnalyzeProductRequest",
    "LatestAnalysisRequest",
    "LatestAnalysisResult",
    "ProductReport",
    "ProductReportRequest",
    "ReturnResult",
    "SaveAnalysisResult",
    "WeightedCashFlow",
]
