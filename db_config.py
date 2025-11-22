# db_config.py
# Database connection, schema initialization, and audit helpers (multi-tenant)

import os
from contextlib import closing
from datetime import datetime

import psycopg2
from psycopg2.extras import DictCursor


# ------------------------
# Connection
# ------------------------

def get_db_dsn() -> str:
    """
    Get the Postgres DSN.

    Priority:
    1. Environment / secrets: VOUCHER_DB_URL (recommended)
    2. Fallback: DATABASE_URL (if you ever use that name)

    You MUST set at least VOUCHER_DB_URL in:
      - Streamlit Cloud secrets, or
      - Your local environment.
    """
    dsn = os.getenv("VOUCHER_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Environment variable VOUCHER_DB_URL is not set.")
    return dsn


def connect():
    """
    Return a psycopg2 connection using DictCursor for convenience.
    Caller is responsible for closing.
    """
    dsn = get_db_dsn()
    return psycopg2.connect(dsn, cursor_factory=DictCursor)


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ------------------------
# Schema (CREATE TABLE statements)
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

VOUCHER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vouchers (
    id              SERIAL PRIMARY KEY,
    parent_id       INTEGER,
    version         INTEGER DEFAULT 1,
    company_id      INTEGER REFERENCES companies(id),
    voucher_number  TEXT,
    vendor          TEXT,
    requester       TEXT,
    invoice         TEXT,
    file_name       TEXT,
    file_data       BYTEA,
    last_modified   TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft / submitted / approved / rejected
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ
);
"""

VOUCHER_LINES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS voucher_lines (
    id              SERIAL PRIMARY KEY,
    voucher_id      INTEGER,
    description     TEXT,
    amount          NUMERIC,
    expense_account TEXT,
    vat_percent     NUMERIC,
    wht_percent     NUMERIC,
    vat_value       NUMERIC,
    wht_value       NUMERIC,
    total           NUMERIC
);
"""

INVOICE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS invoices (
    id                      SERIAL PRIMARY KEY,
    parent_id               INTEGER,
    version                 INTEGER DEFAULT 1,
    company_id              INTEGER REFERENCES companies(id),
    invoice_number          TEXT,
    vendor_invoice_number   TEXT,
    vendor                  TEXT,
    summary                 TEXT,
    vatable_amount          NUMERIC DEFAULT 0.0,
    vat_rate                NUMERIC DEFAULT 0.0,
    wht_rate                NUMERIC DEFAULT 0.0,
    vat_amount              NUMERIC DEFAULT 0.0,
    wht_amount              NUMERIC DEFAULT 0.0,
    non_vatable_amount      NUMERIC DEFAULT 0.0,
    subtotal                NUMERIC DEFAULT 0.0,
    total_amount            NUMERIC DEFAULT 0.0,
    terms                   TEXT,
    last_modified           TIMESTAMPTZ,
    payable_account         TEXT,
    expense_asset_account   TEXT,
    currency                TEXT DEFAULT 'NGN',
    file_name               TEXT,
    file_data               BYTEA
);
"""

AUDIT_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ,
    username    TEXT,
    action      TEXT,
    entity      TEXT,
    ref         TEXT,
    details     TEXT
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
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

ACCOUNTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id),
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL, -- 'payable', 'expense', 'asset', etc.
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

def init_schema():
    """
    Create all required tables if they don't exist.
    Also attempts to backfill new columns on existing tables.
    Call this once at app startup.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        # Base tables in safe order (companies first)
        cur.execute(COMPANIES_TABLE_SQL)
        cur.execute(AUTH_TABLE_SQL)
        cur.execute(VOUCHER_TABLE_SQL)
        cur.execute(VOUCHER_LINES_TABLE_SQL)
        cur.execute(INVOICE_TABLE_SQL)
        cur.execute(AUDIT_LOG_TABLE_SQL)
        cur.execute(VENDORS_TABLE_SQL)
        cur.execute(ACCOUNTS_TABLE_SQL)

        # Ensure company_id columns exist on older schemas
        try:
            cur.execute("ALTER TABLE users ADD COLUMN company_id INTEGER;")
        except psycopg2.Error:
            pass

        try:
            cur.execute("ALTER TABLE vouchers ADD COLUMN company_id INTEGER;")
        except psycopg2.Error:
            pass

        try:
            cur.execute("ALTER TABLE invoices ADD COLUMN company_id INTEGER;")
        except psycopg2.Error:
            pass

        try:
            cur.execute("ALTER TABLE vendors ADD COLUMN company_id INTEGER;")
        except psycopg2.Error:
            pass

        try:
            cur.execute("ALTER TABLE accounts ADD COLUMN company_id INTEGER;")
        except psycopg2.Error:
            pass

        conn.commit()


def log_action(
    username,
    action,
    entity,
    ref=None,
    details=None,
) -> None:
    """
    Insert a row into audit_log.
    This should NEVER crash the whole app: errors are swallowed.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO audit_log (ts, username, action, entity, ref, details)
                VALUES (CURRENT_TIMESTAMP, %s, %s, %s, %s, %s)
                """
                ,
                (username, action, entity, ref, details),
            )
            conn.commit()
    except Exception:
        # Do not let logging failures kill the request.
        pass
