"""Portfolio public read contracts and asset facts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator
from wealth_agent.shared.errors import ErrorDetail, ErrorDetails

MONEY_QUANTUM = Decimal("0.01")


class PortfolioContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_positive_money(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("金额必须大于 0")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("金额最多保留两位小数")
    return value.quantize(MONEY_QUANTUM)


class RiskLevel(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"
    R5 = "R5"


class RecordKind(StrEnum):
    VALUATION = "valuation"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class ProductProfile(PortfolioContract):
    product_type: str | None = None
    risk_level: RiskLevel | None = None
    performance_benchmark: str | None = None

    @field_validator("product_type", "performance_benchmark")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("资料字段不能为空")
        return normalized

    @property
    def missing_fields(self) -> list[str]:
        labels = (
            ("产品类型", self.product_type),
            ("风险等级", self.risk_level),
            ("业绩比较基准", self.performance_benchmark),
        )
        return [label for label, value in labels if value is None]

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields


class Valuation(PortfolioContract):
    date: date
    amount: Decimal = Field(gt=Decimal("0"))
    source_text: str
    updated_at: datetime

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class CashFlow(PortfolioContract):
    flow_id: str
    date: date
    type: Literal["transfer_in", "transfer_out"]
    amount: Decimal = Field(gt=Decimal("0"))
    source_text: str
    created_at: datetime

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)

    @property
    def signed_amount(self) -> Decimal:
        return self.amount if self.type == "transfer_in" else -self.amount


class ProductSnapshot(PortfolioContract):
    product_id: str
    name: str
    revision: str
    updated_at: datetime
    profile: ProductProfile = Field(default_factory=ProductProfile)
    valuations: list[Valuation] = Field(default_factory=list)
    cash_flows: list[CashFlow] = Field(default_factory=list)


class PortfolioSnapshot(PortfolioContract):
    schema_version: Literal[1, 2] = 2
    currency: Literal["CNY"] = "CNY"
    revision: str
    products: list[ProductSnapshot] = Field(default_factory=list)
    updated_at: datetime


class ProductSummary(PortfolioContract):
    product_id: str
    product_name: str
    product_revision: str
    valuation_count: int = Field(ge=0)
    cash_flow_count: int = Field(ge=0)
    latest_valuation_date: date | None
    latest_valuation_amount: Decimal | None
    profile_complete: bool


class ProductCatalog(PortfolioContract):
    ledger_revision: str
    products: list[ProductSummary]


class ProductLedgerView(PortfolioContract):
    ledger_revision: str
    product: ProductSnapshot


class PortfolioError(RuntimeError):
    code = "portfolio_error"

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


class ProductNotFoundError(PortfolioError):
    code = "product_not_found"


class PortfolioCorruptError(PortfolioError):
    code = "portfolio_corrupt"


class PortfolioReader(Protocol):
    def snapshot(self) -> PortfolioSnapshot: ...

    def list_products(self) -> ProductCatalog: ...

    def get_product(self, product_id: str) -> ProductLedgerView: ...


class WriteCommand(PortfolioContract):
    expected_revision: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class AddRecord(WriteCommand):
    kind: Literal["add_record"] = "add_record"
    product_name: str = Field(min_length=1, max_length=200)
    record_date: date
    record_kind: RecordKind
    amount: Decimal
    source_text: str = Field(min_length=1)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class AddRecordInput(PortfolioContract):
    product_name: str = Field(min_length=1, max_length=200)
    record_date: date
    record_kind: RecordKind
    amount: Decimal
    source_text: str = Field(min_length=1)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class AddRecordsBatchRequest(WriteCommand):
    records: list[AddRecordInput] = Field(min_length=1, max_length=50)


class UpdateProductProfile(WriteCommand):
    kind: Literal["update_product_profile"] = "update_product_profile"
    product_id: str = Field(min_length=1)
    product_type: str | None = None
    risk_level: RiskLevel | None = None
    performance_benchmark: str | None = None


class UpdateValuation(WriteCommand):
    kind: Literal["update_valuation"] = "update_valuation"
    product_id: str = Field(min_length=1)
    valuation_date: date
    amount: Decimal
    source_text: str = Field(min_length=1)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class DeleteValuation(WriteCommand):
    kind: Literal["delete_valuation"] = "delete_valuation"
    product_id: str = Field(min_length=1)
    valuation_date: date


class UpdateCashFlow(WriteCommand):
    kind: Literal["update_cash_flow"] = "update_cash_flow"
    product_id: str = Field(min_length=1)
    flow_id: str = Field(min_length=1)
    flow_date: date
    flow_type: Literal["transfer_in", "transfer_out"]
    amount: Decimal
    source_text: str = Field(min_length=1)

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: Decimal) -> Decimal:
        return normalize_positive_money(value)


class DeleteCashFlow(WriteCommand):
    kind: Literal["delete_cash_flow"] = "delete_cash_flow"
    product_id: str = Field(min_length=1)
    flow_id: str = Field(min_length=1)


class DeleteProduct(WriteCommand):
    kind: Literal["delete_product"] = "delete_product"
    product_id: str = Field(min_length=1)


PortfolioWriteCommand = (
    AddRecord
    | UpdateProductProfile
    | UpdateValuation
    | DeleteValuation
    | UpdateCashFlow
    | DeleteCashFlow
    | DeleteProduct
)


class MutationResult(PortfolioContract):
    changed: bool
    product_id: str
    ledger_revision: str
    product_revision: str | None


class BatchMutationResult(PortfolioContract):
    mutations: list[MutationResult] = Field(min_length=1, max_length=50)
    ledger_revision: str = Field(min_length=1)


class RevisionConflictError(PortfolioError):
    code = "revision_conflict"


class IdempotencyConflictError(PortfolioError):
    code = "idempotency_conflict"


class PortfolioWriter(Protocol):
    def execute(self, command: PortfolioWriteCommand) -> MutationResult: ...


class PortfolioBatchWriter(Protocol):
    def execute_batch(self, request: AddRecordsBatchRequest) -> BatchMutationResult: ...


class DatabaseSnapshot(PortfolioContract):
    database_schema_version: int = Field(ge=1)
    ledger_schema_version: int = Field(ge=1)
    ledger_revision: str


class RestoreSnapshotResult(PortfolioContract):
    restored: DatabaseSnapshot
    previous: DatabaseSnapshot


class RestoreSnapshotRequest(PortfolioContract):
    source: Path
    expected_revision: str = Field(min_length=1)
    previous_target: Path


class PortfolioBackupPort(Protocol):
    def create_snapshot(self, target: Path) -> DatabaseSnapshot: ...

    def inspect_snapshot(self, source: Path) -> DatabaseSnapshot: ...

    def restore_snapshot(
        self,
        request: RestoreSnapshotRequest,
    ) -> RestoreSnapshotResult: ...


__all__ = [
    "AddRecord",
    "AddRecordInput",
    "AddRecordsBatchRequest",
    "BatchMutationResult",
    "CashFlow",
    "DatabaseSnapshot",
    "DeleteCashFlow",
    "DeleteProduct",
    "DeleteValuation",
    "IdempotencyConflictError",
    "MutationResult",
    "PortfolioCorruptError",
    "PortfolioError",
    "PortfolioBackupPort",
    "PortfolioBatchWriter",
    "PortfolioReader",
    "PortfolioSnapshot",
    "PortfolioWriteCommand",
    "PortfolioWriter",
    "ProductCatalog",
    "ProductLedgerView",
    "ProductNotFoundError",
    "ProductProfile",
    "ProductSnapshot",
    "ProductSummary",
    "RecordKind",
    "RevisionConflictError",
    "RestoreSnapshotResult",
    "RestoreSnapshotRequest",
    "RiskLevel",
    "UpdateCashFlow",
    "UpdateProductProfile",
    "UpdateValuation",
    "Valuation",
    "normalize_positive_money",
]
