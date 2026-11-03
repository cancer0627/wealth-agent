import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from wealth_agent.capabilities.analysis.contract import (
    AnalysisPersistenceConflictError,
    AnalysisRecord,
    AnalysisResult,
    AnalyzeProductRequest,
    LatestAnalysisRequest,
    ProductReportRequest,
)
from wealth_agent.capabilities.analysis.implementation import (
    DeterministicAnalysis,
    SqliteAnalysisRepository,
    StoredAnalysisService,
)
from wealth_agent.capabilities.portfolio.contract import ProductSnapshot, Valuation

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def _record(
    run_id: str,
    *,
    ledger_revision: str = "ledger-r1",
    created_at: datetime = NOW,
) -> AnalysisRecord:
    product = ProductSnapshot(
        product_id="product-1",
        name="测试产品",
        revision="product-r1",
        updated_at=NOW,
        valuations=[
            Valuation(
                date=date(2026, 9, 1),
                amount=Decimal("100"),
                source_text="期初估值",
                updated_at=NOW,
            ),
            Valuation(
                date=date(2026, 9, 12),
                amount=Decimal("110"),
                source_text="期末估值",
                updated_at=NOW,
            ),
        ],
    )
    report = DeterministicAnalysis().report(
        ProductReportRequest(
            product=product,
            ledger_revision=ledger_revision,
            generated_at=created_at,
        )
    )
    return AnalysisRecord(
        created_at=created_at,
        question="是否继续持有？",
        provider="test-provider",
        model="test-model",
        result=AnalysisResult(analysis_run_id=run_id, report=report),
    )


def test_save_is_idempotent_and_conflict_checked(tmp_path: Path) -> None:
    repository = SqliteAnalysisRepository(tmp_path / "analysis.db")
    record = _record("analysis-1")

    first = repository.save(record)
    replay = repository.save(record)

    assert first.created is True
    assert replay.created is False
    assert replay.payload_sha256 == first.payload_sha256
    assert replay.record == record

    with pytest.raises(AnalysisPersistenceConflictError) as error:
        repository.save(record.model_copy(update={"question": "内容已变化"}))
    assert error.value.detail.code == "analysis_persistence_conflict"
    assert error.value.detail.details == {"analysis_run_id": "analysis-1"}


def test_latest_filters_ledger_revision_and_reports_invalid_records(tmp_path: Path) -> None:
    database = tmp_path / "analysis.db"
    repository = SqliteAnalysisRepository(database)
    older = _record("analysis-1")
    newer = _record("analysis-2", created_at=NOW + timedelta(seconds=1))
    repository.save(older)
    repository.save(newer)

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE analysis_records SET payload_sha256 = ? WHERE analysis_run_id = ?",
            ("0" * 64, "analysis-2"),
        )

    latest = repository.latest(LatestAnalysisRequest(product_id="product-1"))
    assert latest.record == older
    assert [error.code for error in latest.errors] == ["analysis_record_invalid"]
    assert latest.errors[0].details == {"analysis_run_id": "analysis-2"}

    stale = repository.latest(
        LatestAnalysisRequest(product_id="product-1", ledger_revision="ledger-r2")
    )
    assert stale.record is None
    assert stale.errors == []


class FixedAnalysisService:
    def __init__(self, result: AnalysisResult) -> None:
        self.result = result

    def analyze(self, _request: AnalyzeProductRequest) -> AnalysisResult:
        return self.result


def test_stored_service_persists_structured_analysis_output(tmp_path: Path) -> None:
    record = _record("analysis-1")
    repository = SqliteAnalysisRepository(tmp_path / "analysis.db")
    service = StoredAnalysisService(
        FixedAnalysisService(record.result),
        repository,
        provider="test-provider",
        model="test-model",
    )
    request = record.result.report

    result = service.analyze(
        AnalyzeProductRequest(
            product=ProductSnapshot(
                product_id=request.product_id,
                name=request.product_name,
                revision=request.product_revision,
                updated_at=NOW,
                profile=request.profile,
                valuations=request.valuations,
                cash_flows=request.cash_flows,
            ),
            ledger_revision=request.ledger_revision,
            generated_at=record.created_at,
            question=record.question,
        )
    )

    assert result == record.result
    latest = repository.latest(LatestAnalysisRequest(product_id="product-1"))
    assert latest.record is not None
    assert latest.record.result == result
    assert latest.record.provider == "test-provider"
