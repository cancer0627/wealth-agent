"""Read-only SQLite implementation of the portfolio contract."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wealth_agent.capabilities.portfolio.contract import (
    PortfolioCorruptError,
    PortfolioSnapshot,
    ProductCatalog,
    ProductLedgerView,
    ProductNotFoundError,
    ProductSnapshot,
    ProductSummary,
)
from wealth_agent.shared.logging import trace_public_methods

APPLICATION_ID = 0x57414C54
SUPPORTED_DATABASE_VERSIONS = frozenset({1, 2, 3})


@trace_public_methods(layer="capability")
class SqlitePortfolioReader:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def snapshot(self) -> PortfolioSnapshot:
        if not self.database_path.is_file():
            raise PortfolioCorruptError(
                f"台账数据库不存在：{self.database_path}",
                details={"database_path": str(self.database_path)},
            )
        try:
            with sqlite3.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                self._validate(connection)
                return self._load(connection)
        except (sqlite3.DatabaseError, ValueError, KeyError) as exc:
            if isinstance(exc, PortfolioCorruptError):
                raise
            raise PortfolioCorruptError(
                "SQLite 台账损坏或数据不兼容",
                details={"database_path": str(self.database_path)},
            ) from exc

    def list_products(self) -> ProductCatalog:
        snapshot = self.snapshot()
        products: list[ProductSummary] = []
        for product in sorted(snapshot.products, key=lambda item: item.name):
            latest = max(product.valuations, key=lambda item: item.date, default=None)
            products.append(
                ProductSummary(
                    product_id=product.product_id,
                    product_name=product.name,
                    product_revision=product.revision,
                    valuation_count=len(product.valuations),
                    cash_flow_count=len(product.cash_flows),
                    latest_valuation_date=latest.date if latest else None,
                    latest_valuation_amount=latest.amount if latest else None,
                    profile_complete=product.profile.is_complete,
                )
            )
        return ProductCatalog(ledger_revision=snapshot.revision, products=products)

    def get_product(self, product_id: str) -> ProductLedgerView:
        normalized = product_id.strip()
        snapshot = self.snapshot()
        product = next(
            (item for item in snapshot.products if item.product_id == normalized),
            None,
        )
        if product is None:
            raise ProductNotFoundError(
                f"产品不存在：{normalized}", details={"product_id": normalized}
            )
        return ProductLedgerView(ledger_revision=snapshot.revision, product=product)

    @staticmethod
    def _validate(connection: sqlite3.Connection) -> None:
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        if application_id != APPLICATION_ID:
            raise PortfolioCorruptError("SQLite 文件不是 Wealth Agent 业务台账")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in SUPPORTED_DATABASE_VERSIONS:
            raise PortfolioCorruptError(f"SQLite Schema 版本不兼容：{version}")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise PortfolioCorruptError("SQLite integrity_check 失败")

    @staticmethod
    def _load(connection: sqlite3.Connection) -> PortfolioSnapshot:
        metadata = connection.execute(
            "SELECT ledger_schema_version, currency, revision, updated_at "
            "FROM ledger_metadata WHERE singleton_id = 1"
        ).fetchone()
        if metadata is None:
            raise PortfolioCorruptError("SQLite 台账缺少 ledger_metadata")

        products: list[ProductSnapshot] = []
        for row in connection.execute("SELECT * FROM products ORDER BY name"):
            valuations = [
                {
                    "date": item["valuation_date"],
                    "amount": item["amount"],
                    "source_text": item["source_text"],
                    "updated_at": item["updated_at"],
                }
                for item in connection.execute(
                    "SELECT * FROM valuations WHERE product_id = ? ORDER BY valuation_date",
                    (row["product_id"],),
                )
            ]
            cash_flows = [
                {
                    "flow_id": item["flow_id"],
                    "date": item["flow_date"],
                    "type": item["type"],
                    "amount": item["amount"],
                    "source_text": item["source_text"],
                    "created_at": item["created_at"],
                }
                for item in connection.execute(
                    "SELECT * FROM cash_flows WHERE product_id = ? "
                    "ORDER BY flow_date, created_at, flow_id",
                    (row["product_id"],),
                )
            ]
            products.append(
                ProductSnapshot.model_validate(
                    {
                        "product_id": row["product_id"],
                        "name": row["name"],
                        "revision": row["revision"],
                        "updated_at": row["updated_at"],
                        "profile": {
                            "product_type": row["product_type"],
                            "risk_level": row["risk_level"],
                            "performance_benchmark": row["performance_benchmark"],
                        },
                        "valuations": valuations,
                        "cash_flows": cash_flows,
                    }
                )
            )
        return PortfolioSnapshot.model_validate(
            {
                "schema_version": metadata["ledger_schema_version"],
                "currency": metadata["currency"],
                "revision": metadata["revision"],
                "updated_at": metadata["updated_at"],
                "products": products,
            }
        )
