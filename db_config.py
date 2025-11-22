# db_config.py
# Multi-tenant database config, schema initialization, and audit logging.

import os
from contextlib import closing
from datetime import datetime

import psycopg2
from psycopg2.extras import DictCursor


# ------------------------
# Connection + utilities
# ------------------------

def get_db_dsn() -> str:
    """
    Get the Postgres DSN for the finance app.
    Uses VOUCHER_DB_URL (recommended) or DATABASE_URL as fallback.
    """
    dsn = os.getenv("VOUCHER_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Environment variable VOUCHER_DB_URL is not set.")
    return dsn


def connect():
    """
    Return a psycopg2 connection using DictCursor.
    """
    dsn = get_db_dsn()
    return psycopg2.connect(dsn, cursor_factory=DictCursor)


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ------------------------
# Table DDLs
# ------------------------

COMPANIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

AUTH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL,

    company_id INTEGER REFERENCES companies(id),

    -- Access control
    role TEXT NOT NULL DEFAULT 'user',              -- 'admin', 'finance', 'viewer', etc.
    can_create_voucher BOOLEAN NOT NULL DEFAULT TRUE,
    can_approve_voucher BOOLEAN NOT NULL DEFAULT FALSE,
    can_manage_users BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, username)
);
"""

VENDORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vendors (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id),
    name            TEXT NOT NULL,
    contact_person  TEXT,
    bank_name       TEXT,
    bank_account    TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, lower(name))
);
"""

STAFF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS staff (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id),
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    status          TEXT NOT NULL DEFAULT 'Active', -- Active / Inactive
    position        TEXT,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

ACCOUNTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id),
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL, -- 'Asset', 'Liability', 'Equity', 'Income', 'Expense'
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, code)
);
"""

VOUCHER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vouchers (
    id              SERIAL PRIMARY KEY,
    parent_id       INTEGER,
    version         INTEGER DEFAULT 1,
    company_id      INTEGER REFERENCES companies(id),

    voucher_number  TEXT NOT NULL,
    vendor          TEXT,
    requester       TEXT,
    invoice_ref     TEXT,

    currency        TEXT DEFAULT 'NGN',
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft / submitted / approved / rejected

    file_name       TEXT,
    file_data       BYTEA,

    last_modified   TIMESTAMPTZ,
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,

    UNIQUE (company_id, voucher_number, version)
);
"""

VOUCHER_LINES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS voucher_lines (
    id              SERIAL PRIMARY KEY,
    voucher_id      INTEGER REFERENCES vouchers(id) ON DELETE CASCADE,
    company_id      INTEGER REFERENCES companies(id),

    line_no         INTEGER,
    description     TEXT,
    amount          NUMERIC(18,2),
    account_name    TEXT,           -- from accounts.name
    vat_percent     NUMERIC(5,2),
    wht_percent     NUMERIC(5,2),
    vat_value       NUMERIC(18,2),
    wht_value       NUMERIC(18,2),
    total           NUMERIC(18,2)
);
"""

VOUCHER_DOCS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS voucher_documents (
    id              SERIAL PRIMARY KEY,
    voucher_id      INTEGER REFERENCES vouchers(id) ON DELETE CASCADE,
    company_id      INTEGER REFERENCES companies(id),

    file_name       TEXT NOT NULL,
    file_data       BYTEA NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (voucher_id, file_name)
);
"""

INVOICE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS invoices (
    id                      SERIAL PRIMARY KEY,
    parent_id               INTEGER,
    version                 INTEGER DEFAULT 1,
    company_id              INTEGER REFERENCES companies(id),

    invoice_number          TEXT NOT NULL,
    vendor_invoice_number   TEXT,
    vendor                  TEXT,
    summary                 TEXT,

    vatable_amount          NUMERIC(18,2) DEFAULT 0.0,
    non_vatable_amount      NUMERIC(18,2) DEFAULT 0.0,
    vat_rate                NUMERIC(5,2) DEFAULT 0.0,
    wht_rate                NUMERIC(5,2) DEFAULT 0.0,
    vat_amount              NUMERIC(18,2) DEFAULT 0.0,
    wht_amount              NUMERIC(18,2) DEFAULT 0.0,
    subtotal                NUMERIC(18,2) DEFAULT 0.0,
    total_amount            NUMERIC(18,2) DEFAULT 0.0,

    terms                   TEXT,
    payable_account         TEXT,
    expense_asset_account   TEXT,
    currency                TEXT DEFAULT 'NGN',

    file_name               TEXT,
    file_data               BYTEA,

    last_modified           TIMESTAMPTZ,

    UNIQUE (company_id, invoice_number, version)
);
"""

AUDIT_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ,
    username    TEXT,
    company_id  INTEGER,
    action      TEXT,
    entity      TEXT,
    ref         TEXT,
    details     TEXT
);
"""


# ------------------------
# Schema init + migrations
# ------------------------

def init_schema():
    """
    Create all tables if they do not exist.
    Also try to add missing columns for older DBs (best-effort).
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        # Base entities
        cur.execute(COMPANIES_TABLE_SQL)
        cur.execute(AUTH_TABLE_SQL)
        cur.execute(VENDORS_TABLE_SQL)
        cur.execute(STAFF_TABLE_SQL)
        cur.execute(ACCOUNTS_TABLE_SQL)
        cur.execute(VOUCHER_TABLE_SQL)
        cur.execute(VOUCHER_LINES_TABLE_SQL)
        cur.execute(VOUCHER_DOCS_TABLE_SQL)
        cur.execute(INVOICE_TABLE_SQL)
        cur.execute(AUDIT_LOG_TABLE_SQL)

        # Backfill auth columns if missing
        for alter in (
            "ALTER TABLE users ADD COLUMN company_id INTEGER;",
            "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user';",
            "ALTER TABLE users ADD COLUMN can_create_voucher BOOLEAN NOT NULL DEFAULT TRUE;",
            "ALTER TABLE users ADD COLUMN can_approve_voucher BOOLEAN NOT NULL DEFAULT FALSE;",
            "ALTER TABLE users ADD COLUMN can_manage_users BOOLEAN NOT NULL DEFAULT FALSE;",
        ):
            try:
                cur.execute(alter)
            except Exception:
                pass

        conn.commit()


def log_action(username, action, entity, ref=None, details=None, company_id=None):
    """
    Insert a row into audit_log.
    Non-blocking – failures are swallowed.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO audit_log (ts, username, company_id, action, entity, ref, details)
                VALUES (CURRENT_TIMESTAMP, %s, %s, %s, %s, %s, %s)
                """,
                (username, company_id, action, entity, ref, details),
            )
            conn.commit()
    except Exception:
        pass
