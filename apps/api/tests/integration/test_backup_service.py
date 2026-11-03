from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from wealth_agent.capabilities.backup.contract import BackupCorruptError, RestoreRequest
from wealth_agent.capabilities.backup.implementation import LocalBackupService
from wealth_agent.capabilities.portfolio.contract import AddRecord, RecordKind
from wealth_agent.capabilities.portfolio.implementation.sqlite_backup_port import (
    SqlitePortfolioBackupPort,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_store import (
    SqlitePortfolioStore,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 12, tzinfo=UTC)


def _record(store: SqlitePortfolioStore, name: str, key: str) -> str:
    result = store.execute(
        AddRecord(
            product_name=name,
            record_date=date(2026, 9, 12),
            record_kind=RecordKind.VALUATION,
            amount=Decimal("100"),
            source_text=name,
            expected_revision=store.snapshot().revision,
            idempotency_key=key,
        )
    )
    return result.ledger_revision


def test_backup_verify_and_restore_create_pre_restore_backup(tmp_path: Path) -> None:
    database = tmp_path / "wealth.db"
    store = SqlitePortfolioStore(database, FixedClock())
    _record(store, "原产品", "one")
    service = LocalBackupService(
        tmp_path / "backups", SqlitePortfolioBackupPort(database), FixedClock()
    )
    backup = service.create()
    listed = service.list()
    assert [item.backup_id for item in listed.backups] == [backup.backup_id]
    changed_revision = _record(store, "新增产品", "two")

    restored = service.restore(
        RestoreRequest(
            backup_id=backup.backup_id,
            confirmation_backup_id=backup.backup_id,
            expected_revision=changed_revision,
        )
    )

    assert restored.restored_ledger_revision == backup.ledger_revision
    assert [item.product_name for item in store.list_products().products] == ["原产品"]
    assert service.verify(restored.pre_restore_backup_id).valid is True


def test_tampered_backup_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "wealth.db"
    store = SqlitePortfolioStore(database, FixedClock())
    _record(store, "原产品", "one")
    service = LocalBackupService(
        tmp_path / "backups", SqlitePortfolioBackupPort(database), FixedClock()
    )
    backup = service.create()
    (tmp_path / "backups" / backup.backup_id / "wealth.db").write_bytes(b"broken")
    verification = service.verify(backup.backup_id)
    assert verification.valid is False
    assert {error.code for error in verification.errors} == {
        "backup_checksum_mismatch",
        "backup_size_mismatch",
        "backup_snapshot_invalid",
    }
    with pytest.raises(BackupCorruptError) as captured:
        service.restore(
            RestoreRequest(
                backup_id=backup.backup_id,
                confirmation_backup_id=backup.backup_id,
                expected_revision=store.snapshot().revision,
            )
        )
    assert captured.value.detail.code == "backup_corrupt"
    assert captured.value.detail.details["backup_id"] == backup.backup_id
