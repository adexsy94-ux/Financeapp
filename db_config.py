# db_config.py
# Multi-tenant database config, schema initialization, and audit logging.

import os
from contextlib import closing

import psycopg2
from psycopg2.extras import DictCursor


# ------------------------
# DSN resolution
# ------------------------

def get_db_dsn() -> str:
    """
    Resolve the PostgreSQL DSN.

    Priority:
    1. st.secrets["DATABASE_URL"]  (Streamlit Cloud / local secrets.toml)
    2. Env vars: DATABASE_URL, DB_DSN, DB_URL, POSTGRES_DSN
    """
    # 1) Try Streamlit secrets (if Streamlit is available)
    try:
        import streamlit as st  # local import to avoid hard dependency in non-streamlit contexts

        if "DATABASE_URL" in st.secrets:
            dsn = st.secrets["DATABASE_URL"]
            if dsn:
                return dsn
    except Exception:
        # Either streamlit not installed yet or no secrets configured
        pass

    # 2) Try environment variables
    for name in ("DATABASE_URL", "DB_DSN", "DB_URL", "POSTGRES_DSN"):
        dsn = os.getenv(name)
        if dsn:
            return dsn

    raise RuntimeError(
        "No database DSN found. Set DATABASE_URL either in Streamlit secrets or as an environment variable."
    )


def connect():
    """
    Open a PostgreSQL connection with DictCursor.
    """
    dsn = get_db_dsn()
    return psycopg2.connect(dsn, cursor_factory=DictCursor)


# ------------------------
# Schema SQL
# ------------------------

COMPANIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    code    TEXT NOT NULL UNIQUE
);
"""

AUTH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id                  SERIAL PRIMARY KEY,
    username            TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    company_id          INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    role                TEXT NOT NULL DEFAULT 'user',
    can_create_voucher  BOOLEAN NOT NULL DEFAULT FALSE,
    can_approve_voucher BOOLEAN NOT NULL DEFAULT FALSE,
    can_manage_users    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, username)
);
"""

VENDORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vendors (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    contact_person  TEXT,
    bank_name       TEXT,
    bank_account    TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, name)
);
"""

STAFF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS staff (
    id          SERIAL PRIMARY KEY,
    company_id  INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    email       TEXT,
    phone       TEXT,
    status      TEXT,
    position    TEXT,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
"""

ACCOUNTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id          SERIAL PRIMARY KEY,
    company_id  INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    code        TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (company_id, code),
    UNIQUE (company_id, name)
);
"""

VOUCHER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vouchers (
    id              SERIAL PRIMARY KEY,
    parent_id       INTEGER,
    version         INTEGER NOT NULL DEFAULT 1,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,

    voucher_number  TEXT NOT NULL,
    vendor          TEXT NOT NULL,
    requester       TEXT NOT NULL,
    invoice_ref     TEXT,
    currency        TEXT NOT NULL DEFAULT 'NGN',
    status          TEXT NOT NULL DEFAULT 'draft',

    file_name       TEXT,
    file_data       BYTEA,

    last_modified   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,

    UNIQUE (company_id, voucher_number, version)
);
"""

VOUCHER_LINES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS voucher_lines (
    id              SERIAL PRIMARY KEY,
    voucher_id      INTEGER REFERENCES vouchers(id) ON DELETE CASCADE,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,

    line_no         INTEGER,
    description     TEXT,
    amount          NUMERIC(18, 2),

    account_name    TEXT,

    vat_percent     NUMERIC(5, 2),
    wht_percent     NUMERIC(5, 2),
    vat_value       NUMERIC(18, 2),
    wht_value       NUMERIC(18, 2),
    total           NUMERIC(18, 2)
);
"""

VOUCHER_DOCS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS voucher_documents (
    id          SERIAL PRIMARY KEY,
    voucher_id  INTEGER REFERENCES vouchers(id) ON DELETE CASCADE,
    company_id  INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    file_name   TEXT NOT NULL,
    file_data   BYTEA NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (voucher_id, file_name)
);
"""

INVOICE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS invoices (
    id                      SERIAL PRIMARY KEY,
    parent_id               INTEGER,
    version                 INTEGER NOT NULL DEFAULT 1,
    company_id              INTEGER REFERENCES companies(id) ON DELETE CASCADE,

    invoice_number          TEXT NOT NULL,
    vendor_invoice_number   TEXT,
    vendor                  TEXT NOT NULL,
    summary                 TEXT,

    vatable_amount          NUMERIC(18, 2) NOT NULL DEFAULT 0,
    non_vatable_amount      NUMERIC(18, 2) NOT NULL DEFAULT 0,
    vat_rate                NUMERIC(5, 2)  NOT NULL DEFAULT 0,
    wht_rate                NUMERIC(5, 2)  NOT NULL DEFAULT 0,

    vat_amount              NUMERIC(18, 2) NOT NULL DEFAULT 0,
    wht_amount              NUMERIC(18, 2) NOT NULL DEFAULT 0,
    subtotal                NUMERIC(18, 2) NOT NULL DEFAULT 0,
    total_amount            NUMERIC(18, 2) NOT NULL DEFAULT 0,

    terms                   TEXT,
    payable_account         TEXT,
    expense_asset_account   TEXT,
    currency                TEXT NOT NULL DEFAULT 'NGN',

    file_name               TEXT,
    file_data               BYTEA,
    last_modified           TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (company_id, invoice_number, version)
);
"""

AUDIT_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL,
    username    TEXT,
    company_id  INTEGER,
    action      TEXT,
    entity      TEXT,
    ref         TEXT,
    details     TEXT
);
"""


# ------------------------
# Schema init
# ------------------------

def init_schema() -> None:
    """
    Initialize all core schemas.
    Safe to call on every startup.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
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
        conn.commit()


# ------------------------
# Audit logging
# ------------------------

def log_action(
    username: str,
    action: str,
    entity: str,
    ref: str = "",
    details: str = "",
    company_id: int | None = None,
) -> None:
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
        # Logging must never break the main flow
        pass
