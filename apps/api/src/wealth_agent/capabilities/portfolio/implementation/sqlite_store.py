"""Transactional SQLite portfolio writes with revision and idempotency checks."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from wealth_agent.capabilities.portfolio.contract import (
    AddRecord,
    AddRecordsBatchRequest,
    BatchMutationResult,
    DeleteCashFlow,
    DeleteProduct,
    DeleteValuation,
    IdempotencyConflictError,
    MutationResult,
    PortfolioCorruptError,
    PortfolioError,
    PortfolioWriteCommand,
    ProductNotFoundError,
    RecordKind,
    RevisionConflictError,
    UpdateCashFlow,
    UpdateProductProfile,
    UpdateValuation,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_reader import (
    SqlitePortfolioReader,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_schema import initialize
from wealth_agent.shared.clock import Clock, SystemClock
from wealth_agent.shared.logging import trace_public_methods
from wealth_agent.shared.serialization import canonical_json, sha256_json


@trace_public_methods(layer="capability")
class SqlitePortfolioStore(SqlitePortfolioReader):
    def __init__(self, database_path: Path, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        if not database_path.exists():
            with sqlite3.connect(database_path) as connection:
                initialize(connection)
                now = self._clock.now().isoformat()
                connection.execute(
                    "INSERT INTO ledger_metadata VALUES (1, 2, 'CNY', ?, ?)",
                    (uuid4().hex, now),
                )
                connection.commit()
        super().__init__(database_path)

    def execute(self, command: PortfolioWriteCommand) -> MutationResult:
        try:
            request_hash = sha256_json(command.model_dump(mode="json"))
            with sqlite3.connect(self.database_path, timeout=4) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN IMMEDIATE")
                replay = self._replay(connection, command.idempotency_key, request_hash)
                if replay is not None:
                    return replay
                current_revision = self._revision(connection)
                if current_revision != command.expected_revision:
                    raise RevisionConflictError(
                        "台账 revision 已变化，请重新执行操作",
                        details={
                            "expected_revision": command.expected_revision,
                            "actual_revision": current_revision,
                        },
                    )

                now = self._clock.now()
                result = self._apply(connection, command, now)
                connection.execute(
                    "INSERT INTO command_receipts VALUES (?, ?, ?, ?)",
                    (
                        command.idempotency_key,
                        request_hash,
                        canonical_json(result.model_dump(mode="json")),
                        now.isoformat(),
                    ),
                )
                connection.commit()
                return result
        except PortfolioError:
            raise
        except sqlite3.DatabaseError as exc:
            raise PortfolioCorruptError(
                "SQLite 台账写入失败",
                details={"database_path": str(self.database_path)},
            ) from exc

    def execute_batch(self, request: AddRecordsBatchRequest) -> BatchMutationResult:
        try:
            request_hash = sha256_json(request.model_dump(mode="json"))
            with sqlite3.connect(self.database_path, timeout=4) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("BEGIN IMMEDIATE")
                replay = self._replay_batch(
                    connection, request.idempotency_key, request_hash
                )
                if replay is not None:
                    return replay
                current_revision = self._revision(connection)
                if current_revision != request.expected_revision:
                    raise RevisionConflictError(
                        "台账 revision 已变化，请重新执行操作",
                        details={
                            "expected_revision": request.expected_revision,
                            "actual_revision": current_revision,
                        },
                    )
                now = self._clock.now()
                mutations = [
                    self._add_record(
                        connection,
                        AddRecord(
                            **record.model_dump(),
                            expected_revision=current_revision,
                            idempotency_key=(
                                f"{request.idempotency_key}:{index}"
                            ),
                        ),
                        now,
                    )
                    for index, record in enumerate(request.records)
                ]
                result = BatchMutationResult(
                    mutations=mutations,
                    ledger_revision=mutations[-1].ledger_revision,
                )
                connection.execute(
                    "INSERT INTO command_receipts VALUES (?, ?, ?, ?)",
                    (
                        request.idempotency_key,
                        request_hash,
                        canonical_json(result.model_dump(mode="json")),
                        now.isoformat(),
                    ),
                )
                connection.commit()
                return result
        except PortfolioError:
            raise
        except sqlite3.DatabaseError as exc:
            raise PortfolioCorruptError(
                "SQLite 台账批量写入失败",
                details={"database_path": str(self.database_path)},
            ) from exc

    def _apply(
        self,
        connection: sqlite3.Connection,
        command: PortfolioWriteCommand,
        now: datetime,
    ) -> MutationResult:
        if isinstance(command, AddRecord):
            return self._add_record(connection, command, now)
        if isinstance(command, UpdateProductProfile):
            return self._update_profile(connection, command, now)
        if isinstance(command, UpdateValuation):
            return self._update_valuation(connection, command, now)
        if isinstance(command, DeleteValuation):
            return self._delete_valuation(connection, command, now)
        if isinstance(command, UpdateCashFlow):
            return self._update_cash_flow(connection, command, now)
        if isinstance(command, DeleteCashFlow):
            return self._delete_cash_flow(connection, command, now)
        return self._delete_product(connection, command, now)

    def _add_record(
        self, connection: sqlite3.Connection, command: AddRecord, now: datetime
    ) -> MutationResult:
        name = command.product_name.strip()
        row = connection.execute(
            "SELECT product_id FROM products WHERE name = ?", (name,)
        ).fetchone()
        product_id = uuid4().hex if row is None else str(row["product_id"])
        product_revision, ledger_revision = uuid4().hex, uuid4().hex
        if row is None:
            connection.execute(
                "INSERT INTO products VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
                (product_id, name, product_revision, now.isoformat()),
            )
        if command.record_kind == RecordKind.VALUATION:
            connection.execute(
                "INSERT INTO valuations VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(product_id, valuation_date) DO UPDATE SET "
                "amount=excluded.amount, source_text=excluded.source_text, "
                "updated_at=excluded.updated_at",
                (
                    product_id,
                    command.record_date.isoformat(),
                    str(command.amount),
                    command.source_text,
                    now.isoformat(),
                ),
            )
        else:
            connection.execute(
                "INSERT INTO cash_flows VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    product_id,
                    command.record_date.isoformat(),
                    command.record_kind.value,
                    str(command.amount),
                    command.source_text,
                    now.isoformat(),
                ),
            )
        self._touch(connection, product_id, product_revision, ledger_revision, now)
        return MutationResult(
            changed=True,
            product_id=product_id,
            ledger_revision=ledger_revision,
            product_revision=product_revision,
        )

    def _update_profile(
        self, connection: sqlite3.Connection, command: UpdateProductProfile, now: datetime
    ) -> MutationResult:
        self._require_product(connection, command.product_id)
        current = connection.execute(
            "SELECT product_type, risk_level, performance_benchmark "
            "FROM products WHERE product_id = ?",
            (command.product_id,),
        ).fetchone()
        values = (command.product_type, command.risk_level, command.performance_benchmark)
        if current is not None and tuple(current) == values:
            return self._unchanged(connection, command.product_id)
        connection.execute(
            "UPDATE products SET product_type = ?, risk_level = ?, performance_benchmark = ? "
            "WHERE product_id = ?",
            (*values, command.product_id),
        )
        return self._changed(connection, command.product_id, now)

    def _update_valuation(
        self, connection: sqlite3.Connection, command: UpdateValuation, now: datetime
    ) -> MutationResult:
        self._require_product(connection, command.product_id)
        cursor = connection.execute(
            "UPDATE valuations SET amount = ?, source_text = ?, updated_at = ? "
            "WHERE product_id = ? AND valuation_date = ?",
            (
                str(command.amount),
                command.source_text,
                now.isoformat(),
                command.product_id,
                command.valuation_date.isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            raise ProductNotFoundError(
                "估值记录不存在",
                details={
                    "product_id": command.product_id,
                    "record_kind": "valuation",
                    "valuation_date": command.valuation_date.isoformat(),
                },
            )
        return self._changed(connection, command.product_id, now)

    def _delete_valuation(
        self, connection: sqlite3.Connection, command: DeleteValuation, now: datetime
    ) -> MutationResult:
        self._require_product(connection, command.product_id)
        cursor = connection.execute(
            "DELETE FROM valuations WHERE product_id = ? AND valuation_date = ?",
            (command.product_id, command.valuation_date.isoformat()),
        )
        if cursor.rowcount != 1:
            raise ProductNotFoundError(
                "估值记录不存在",
                details={
                    "product_id": command.product_id,
                    "record_kind": "valuation",
                    "valuation_date": command.valuation_date.isoformat(),
                },
            )
        return self._changed(connection, command.product_id, now)

    def _update_cash_flow(
        self, connection: sqlite3.Connection, command: UpdateCashFlow, now: datetime
    ) -> MutationResult:
        self._require_product(connection, command.product_id)
        cursor = connection.execute(
            "UPDATE cash_flows SET flow_date = ?, type = ?, amount = ?, source_text = ? "
            "WHERE product_id = ? AND flow_id = ?",
            (
                command.flow_date.isoformat(),
                command.flow_type,
                str(command.amount),
                command.source_text,
                command.product_id,
                command.flow_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ProductNotFoundError(
                "现金流记录不存在",
                details={
                    "product_id": command.product_id,
                    "record_kind": "cash_flow",
                    "flow_id": command.flow_id,
                },
            )
        return self._changed(connection, command.product_id, now)

    def _delete_cash_flow(
        self, connection: sqlite3.Connection, command: DeleteCashFlow, now: datetime
    ) -> MutationResult:
        self._require_product(connection, command.product_id)
        cursor = connection.execute(
            "DELETE FROM cash_flows WHERE product_id = ? AND flow_id = ?",
            (command.product_id, command.flow_id),
        )
        if cursor.rowcount != 1:
            raise ProductNotFoundError(
                "现金流记录不存在",
                details={
                    "product_id": command.product_id,
                    "record_kind": "cash_flow",
                    "flow_id": command.flow_id,
                },
            )
        return self._changed(connection, command.product_id, now)

    def _delete_product(
        self, connection: sqlite3.Connection, command: DeleteProduct, now: datetime
    ) -> MutationResult:
        self._require_product(connection, command.product_id)
        connection.execute("DELETE FROM products WHERE product_id = ?", (command.product_id,))
        ledger_revision = uuid4().hex
        connection.execute(
            "UPDATE ledger_metadata SET revision = ?, updated_at = ? WHERE singleton_id = 1",
            (ledger_revision, now.isoformat()),
        )
        return MutationResult(
            changed=True,
            product_id=command.product_id,
            ledger_revision=ledger_revision,
            product_revision=None,
        )

    def _changed(
        self, connection: sqlite3.Connection, product_id: str, now: datetime
    ) -> MutationResult:
        product_revision, ledger_revision = uuid4().hex, uuid4().hex
        self._touch(connection, product_id, product_revision, ledger_revision, now)
        return MutationResult(
            changed=True,
            product_id=product_id,
            ledger_revision=ledger_revision,
            product_revision=product_revision,
        )

    @staticmethod
    def _touch(
        connection: sqlite3.Connection,
        product_id: str,
        product_revision: str,
        ledger_revision: str,
        now: datetime,
    ) -> None:
        connection.execute(
            "UPDATE products SET revision = ?, updated_at = ? WHERE product_id = ?",
            (product_revision, now.isoformat(), product_id),
        )
        connection.execute(
            "UPDATE ledger_metadata SET revision = ?, updated_at = ? WHERE singleton_id = 1",
            (ledger_revision, now.isoformat()),
        )

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT revision FROM ledger_metadata WHERE singleton_id = 1"
        ).fetchone()
        if row is None:
            raise PortfolioCorruptError("台账缺少 revision")
        return str(row["revision"])

    @staticmethod
    def _require_product(connection: sqlite3.Connection, product_id: str) -> None:
        row = connection.execute(
            "SELECT 1 FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        if row is None:
            raise ProductNotFoundError(
                f"产品不存在：{product_id}", details={"product_id": product_id}
            )

    @classmethod
    def _unchanged(cls, connection: sqlite3.Connection, product_id: str) -> MutationResult:
        row = connection.execute(
            "SELECT revision FROM products WHERE product_id = ?", (product_id,)
        ).fetchone()
        return MutationResult(
            changed=False,
            product_id=product_id,
            ledger_revision=cls._revision(connection),
            product_revision=str(row["revision"]) if row else None,
        )

    @staticmethod
    def _replay(
        connection: sqlite3.Connection, idempotency_key: str, request_hash: str
    ) -> MutationResult | None:
        row = connection.execute(
            "SELECT request_hash, result_json FROM command_receipts WHERE operation_id = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyConflictError(
                "幂等键已被不同请求使用",
                details={"idempotency_key": idempotency_key},
            )
        return MutationResult.model_validate(json.loads(str(row["result_json"])))

    @staticmethod
    def _replay_batch(
        connection: sqlite3.Connection, idempotency_key: str, request_hash: str
    ) -> BatchMutationResult | None:
        row = connection.execute(
            "SELECT request_hash, result_json FROM command_receipts WHERE operation_id = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyConflictError(
                "幂等键已被不同请求使用",
                details={"idempotency_key": idempotency_key},
            )
        return BatchMutationResult.model_validate(json.loads(str(row["result_json"])))
