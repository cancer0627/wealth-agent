from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from wealth_agent.capabilities.portfolio.contract import (
    PortfolioCorruptError,
    ProductNotFoundError,
)
from wealth_agent.capabilities.portfolio.implementation.sqlite_reader import (
    APPLICATION_ID,
    SqlitePortfolioReader,
)


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 3")
        connection.executescript(
            """
            CREATE TABLE ledger_metadata (
                singleton_id INTEGER PRIMARY KEY,
                ledger_schema_version INTEGER NOT NULL,
                currency TEXT NOT NULL,
                revision TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE products (
                product_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                revision TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                product_type TEXT,
                risk_level TEXT,
                performance_benchmark TEXT
            );
            CREATE TABLE valuations (
                product_id TEXT NOT NULL,
                valuation_date TEXT NOT NULL,
                amount TEXT NOT NULL,
                source_text TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE cash_flows (
                flow_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                flow_date TEXT NOT NULL,
                type TEXT NOT NULL,
                amount TEXT NOT NULL,
                source_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO ledger_metadata VALUES
                (1, 2, 'CNY', 'ledger-r1', '2026-09-12T00:00:00+00:00');
            INSERT INTO products VALUES
                ('p1', '稳健产品', 'product-r1', '2026-09-12T00:00:00+00:00',
                 '固收', 'R2', '年化收益率');
            INSERT INTO valuations VALUES
                ('p1', '2026-09-12', '100.00', '录入', '2026-09-12T00:00:00+00:00');
            INSERT INTO cash_flows VALUES
                ('f1', 'p1', '2026-09-11', 'transfer_in', '80.00', '转入',
                 '2026-09-11T00:00:00+00:00');
            """
        )


def test_reader_returns_catalog_and_ordered_product_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "wealth.db"
    _database(path)
    reader = SqlitePortfolioReader(path)

    catalog = reader.list_products()
    product = reader.get_product("p1")

    assert catalog.ledger_revision == "ledger-r1"
    assert catalog.products[0].latest_valuation_amount == 100
    assert product.product.profile.is_complete is True
    assert product.product.cash_flows[0].signed_amount == 80


def test_reader_rejects_missing_product_and_non_wealth_database(tmp_path: Path) -> None:
    path = tmp_path / "wealth.db"
    _database(path)
    reader = SqlitePortfolioReader(path)
    with pytest.raises(ProductNotFoundError) as captured:
        reader.get_product("missing")
    assert captured.value.detail.code == "product_not_found"
    assert captured.value.detail.details == {"product_id": "missing"}

    invalid = tmp_path / "invalid.db"
    sqlite3.connect(invalid).close()
    with pytest.raises(PortfolioCorruptError):
        SqlitePortfolioReader(invalid).snapshot()
