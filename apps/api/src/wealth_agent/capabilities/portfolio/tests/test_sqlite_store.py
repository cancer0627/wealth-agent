from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from wealth_agent.capabilities.portfolio.contract import (
    AddRecord,
    AddRecordInput,
    AddRecordsBatchRequest,
    DeleteProduct,
    IdempotencyConflictError,
    RecordKind,
    RevisionConflictError,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_store import (
    SqlitePortfolioStore,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 12, tzinfo=UTC)


def _store(tmp_path: Path) -> SqlitePortfolioStore:
    return SqlitePortfolioStore(tmp_path / "wealth.db", FixedClock())


def test_write_is_revision_checked_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.snapshot().revision
    command = AddRecord(
        product_name="测试产品",
        record_date=date(2026, 9, 12),
        record_kind=RecordKind.VALUATION,
        amount=Decimal("100"),
        source_text="测试产品 100",
        expected_revision=original,
        idempotency_key="operation-1",
    )

    first = store.execute(command)
    replay = store.execute(command)

    assert replay == first
    assert len(store.get_product(first.product_id).product.valuations) == 1

    with pytest.raises(RevisionConflictError) as revision_error:
        store.execute(command.model_copy(update={"idempotency_key": "operation-2"}))
    assert revision_error.value.detail.code == "revision_conflict"
    assert revision_error.value.detail.details == {
        "expected_revision": original,
        "actual_revision": first.ledger_revision,
    }

    with pytest.raises(IdempotencyConflictError) as idempotency_error:
        store.execute(command.model_copy(update={"amount": Decimal("200")}))
    assert idempotency_error.value.detail.code == "idempotency_conflict"
    assert idempotency_error.value.detail.details == {"idempotency_key": "operation-1"}


def test_product_delete_cascades_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    added = store.execute(
        AddRecord(
            product_name="测试产品",
            record_date=date(2026, 9, 12),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("100"),
            source_text="测试产品 100",
            expected_revision=store.snapshot().revision,
            idempotency_key="operation-1",
        )
    )
    deleted = store.execute(
        DeleteProduct(
            product_id=added.product_id,
            expected_revision=added.ledger_revision,
            idempotency_key="operation-2",
        )
    )
    assert deleted.product_revision is None
    assert store.list_products().products == []


def test_batch_add_is_atomic_idempotent_and_revision_checked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.snapshot().revision
    request = AddRecordsBatchRequest(
        expected_revision=original,
        idempotency_key="ocr-batch-1",
        records=[
            AddRecordInput(
                product_name="产品甲",
                record_date=date(2026, 9, 11),
                record_kind=RecordKind.VALUATION,
                amount=Decimal("100"),
                source_text="OCR 草稿",
            ),
            AddRecordInput(
                product_name="产品乙",
                record_date=date(2026, 9, 12),
                record_kind=RecordKind.TRANSFER_IN,
                amount=Decimal("20"),
                source_text="OCR 草稿",
            ),
        ],
    )

    first = store.execute_batch(request)
    replay = store.execute_batch(request)

    assert replay == first
    assert first.ledger_revision == first.mutations[-1].ledger_revision
    assert [item.product_name for item in store.list_products().products] == [
        "产品乙",
        "产品甲",
    ]

    with pytest.raises(RevisionConflictError):
        store.execute_batch(
            request.model_copy(update={"idempotency_key": "ocr-batch-2"})
        )
    with pytest.raises(IdempotencyConflictError):
        store.execute_batch(
            request.model_copy(
                update={
                    "records": [
                        request.records[0].model_copy(
                            update={"amount": Decimal("200")}
                        )
                    ]
                }
            )
        )
