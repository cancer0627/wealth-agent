from __future__ import annotations

from datetime import UTC, datetime
from inspect import signature
from pathlib import Path

import pytest
from pydantic import ValidationError
from wealth_agent.capabilities.analysis.contract import (
    AnalysisEngine,
    AnalysisService,
    AnalyzeProductRequest,
    ProductReportRequest,
)
from wealth_agent.capabilities.backup.contract import BackupListItem, BackupListResult
from wealth_agent.capabilities.portfolio.contract import (
    PortfolioBackupPort,
    ProductSnapshot,
    RestoreSnapshotRequest,
)
from wealth_agent.shared.errors import ErrorDetail

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def _product() -> ProductSnapshot:
    return ProductSnapshot(
        product_id="p1",
        name="测试产品",
        revision="product-r1",
        updated_at=NOW,
    )


def test_request_models_are_strict_and_json_serializable(tmp_path: Path) -> None:
    report_request = ProductReportRequest(
        product=_product(), ledger_revision="ledger-r1", generated_at=NOW
    )
    analyze_request = AnalyzeProductRequest(
        product=_product(),
        ledger_revision="ledger-r1",
        generated_at=NOW,
        question="是否继续持有？",
    )
    restore_request = RestoreSnapshotRequest(
        source=tmp_path / "source.db",
        expected_revision="ledger-r1",
        previous_target=tmp_path / "previous.db",
    )

    assert report_request.model_dump(mode="json")["product"]["product_id"] == "p1"
    assert analyze_request.model_dump(mode="json")["question"] == "是否继续持有？"
    assert restore_request.model_dump(mode="json") == {
        "source": str(tmp_path / "source.db"),
        "expected_revision": "ledger-r1",
        "previous_target": str(tmp_path / "previous.db"),
    }

    with pytest.raises(ValidationError):
        ProductReportRequest.model_validate(
            {
                **report_request.model_dump(),
                "unexpected": True,
            }
        )


def test_public_multi_value_operations_accept_one_request_model() -> None:
    assert list(signature(AnalysisEngine.report).parameters) == ["self", "request"]
    assert list(signature(AnalysisService.analyze).parameters) == ["self", "request"]
    assert list(signature(PortfolioBackupPort.restore_snapshot).parameters) == [
        "self",
        "request",
    ]


def test_error_detail_is_strict_and_json_serializable() -> None:
    detail = ErrorDetail(
        code="revision_conflict",
        message="台账 revision 已变化",
        retryable=False,
        details={
            "expected_revision": "r1",
            "actual_revision": "r2",
            "conflicts": ["ledger"],
        },
    )

    assert detail.model_dump(mode="json") == {
        "code": "revision_conflict",
        "message": "台账 revision 已变化",
        "retryable": False,
        "details": {
            "expected_revision": "r1",
            "actual_revision": "r2",
            "conflicts": ["ledger"],
        },
    }
    with pytest.raises(ValidationError):
        ErrorDetail.model_validate(
            {
                "code": "error",
                "message": "错误",
                "unexpected": True,
            }
        )
    with pytest.raises(ValidationError):
        ErrorDetail(
            code="error",
            message="错误",
            details={"not_json": object()},
        )


def test_backup_list_result_wraps_collection() -> None:
    result = BackupListResult(
        backups=[BackupListItem(backup_id="backup-1", valid=True)]
    )

    assert result.model_dump(mode="json") == {
        "backups": [
            {
                "backup_id": "backup-1",
                "valid": True,
                "manifest": None,
                "errors": [],
            }
        ]
    }
