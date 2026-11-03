"""SQLite schema owned by the portfolio capability."""

import sqlite3

from wealth_agent.capabilities.portfolio.implementation.sqlite_reader import APPLICATION_ID

DATABASE_SCHEMA_VERSION = 3


def initialize(connection: sqlite3.Connection) -> None:
    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS ledger_metadata (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            ledger_schema_version INTEGER NOT NULL,
            currency TEXT NOT NULL CHECK (currency = 'CNY'),
            revision TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            revision TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            product_type TEXT,
            risk_level TEXT CHECK (risk_level IS NULL OR risk_level IN ('R1','R2','R3','R4','R5')),
            performance_benchmark TEXT
        );
        CREATE TABLE IF NOT EXISTS valuations (
            product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            valuation_date TEXT NOT NULL,
            amount TEXT NOT NULL,
            source_text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (product_id, valuation_date)
        );
        CREATE TABLE IF NOT EXISTS cash_flows (
            flow_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            flow_date TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('transfer_in','transfer_out')),
            amount TEXT NOT NULL,
            source_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cash_flows_product_date
            ON cash_flows(product_id, flow_date, created_at, flow_id);
        CREATE TABLE IF NOT EXISTS analysis_runs (
            analysis_run_id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            analysis_mode TEXT NOT NULL,
            product_id TEXT NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
            product_revision TEXT,
            ledger_revision TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL,
            question TEXT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS command_receipts (
            operation_id TEXT PRIMARY KEY,
            request_hash TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
