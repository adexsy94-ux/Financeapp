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
    Resolve the database DSN from environment variables or Streamlit secrets.

    Priority:
    1. st.secrets["DATABASE_URL"]  (Streamlit Cloud / local secrets.toml)
    2. Env vars: DATABASE_URL, DB_DSN, DB_URL, POSTGRES_DSN
    """
    # 1) Try Streamlit secrets if available (import lazily to avoid hard dependency)
    try:
        import streamlit as st  # type: ignore

        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass

    # 2) Fallback to environment variables
    candidates = ["DATABASE_URL", "DB_DSN", "DB_URL", "POSTGRES_DSN", "VOUCHER_DB_URL"]
    for name in candidates:
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
    username            TEXT NOT NULL UNIQUE,
    password_hash       TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT 'user',
    company_id          INTEGER REFERENCES companies(id),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE
);
"""

VENDORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vendors (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    bank_name       TEXT,
    bank_account    TEXT,
    tax_id          TEXT,
    UNIQUE (company_id, name)
);
"""

STAFF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS staff (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,
    address         TEXT,
    status          TEXT,
    position        TEXT
);
"""

ACCOUNTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS accounts (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    code            TEXT NOT NULL,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (company_id, code)
);
"""

VOUCHER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vouchers (
    id                  SERIAL PRIMARY KEY,
    parent_id           INTEGER,
    version             INTEGER NOT NULL DEFAULT 1,
    company_id          INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    voucher_number      TEXT NOT NULL,
    vendor              TEXT,
    account_name        TEXT,
    currency            TEXT NOT NULL DEFAULT 'NGN',
    status              TEXT NOT NULL DEFAULT 'draft',
    file_name           TEXT,
    file_data           BYTEA,
    created_at          TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    last_modified       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    approved_by         TEXT,
    approved_at         TIMESTAMPTZ,
    requester           TEXT,
    invoice_ref         TEXT,
    total_amount        NUMERIC(18, 2) DEFAULT 0,
    vatable_amount      NUMERIC(18, 2) DEFAULT 0,
    vat_amount          NUMERIC(18, 2) DEFAULT 0,
    non_vatable_amount  NUMERIC(18, 2) DEFAULT 0,
    wht_amount          NUMERIC(18, 2) DEFAULT 0,
    payable_amount      NUMERIC(18, 2) DEFAULT 0,
    notes               TEXT,
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
    account_code    TEXT,
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
    uploaded_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
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
    customer_name           TEXT NOT NULL,
    customer_email          TEXT,
    customer_phone          TEXT,
    customer_address        TEXT,
    currency                TEXT NOT NULL DEFAULT 'NGN',
    issue_date              DATE,
    due_date                DATE,
    status                  TEXT NOT NULL DEFAULT 'draft',
    subtotal                NUMERIC(18, 2) DEFAULT 0,
    vatable_amount          NUMERIC(18, 2) DEFAULT 0,
    vat_amount              NUMERIC(18, 2) DEFAULT 0,
    non_vatable_amount      NUMERIC(18, 2) DEFAULT 0,
    wht_amount              NUMERIC(18, 2) DEFAULT 0,
    total_amount            NUMERIC(18, 2) DEFAULT 0,
    notes                   TEXT,
    created_at              TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
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
# Schema initialization
# ------------------------

def init_schema():
    """
    Initialize all core schemas.
    Safe to call on every startup.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        # Base tables
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

        # --- Lightweight migrations for existing staff table ---
        cur.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS company_id INTEGER;")
        cur.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS status TEXT;")
        cur.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS position TEXT;")

        # --- Lightweight migrations for existing vouchers table ---
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS parent_id INTEGER;")
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS company_id INTEGER;"
        )
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS vendor TEXT;")
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS account_name TEXT;")
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'NGN';"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft';"
        )
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS file_name TEXT;")
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS file_data BYTEA;")
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS last_modified TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;"
        )
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS approved_by TEXT;")
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;"
        )
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS requester TEXT;")
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS invoice_ref TEXT;")
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS total_amount NUMERIC(18,2) DEFAULT 0;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS vatable_amount NUMERIC(18,2) DEFAULT 0;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS vat_amount NUMERIC(18,2) DEFAULT 0;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS non_vatable_amount NUMERIC(18,2) DEFAULT 0;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS wht_amount NUMERIC(18,2) DEFAULT 0;"
        )
        cur.execute(
            "ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS payable_amount NUMERIC(18,2) DEFAULT 0;"
        )
        cur.execute("ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS notes TEXT;")

        # --- Lightweight migrations for existing voucher_lines table ---
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS company_id INTEGER;")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS line_no INTEGER;")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS description TEXT;")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS amount NUMERIC(18,2);")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS account_code TEXT;")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS account_name TEXT;")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS vat_percent NUMERIC(5,2);")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS wht_percent NUMERIC(5,2);")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS vat_value NUMERIC(18,2);")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS wht_value NUMERIC(18,2);")
        cur.execute("ALTER TABLE voucher_lines ADD COLUMN IF NOT EXISTS total NUMERIC(18,2);")

        # --- Lightweight migrations for existing voucher_documents table ---
        cur.execute(
            "ALTER TABLE voucher_documents ADD COLUMN IF NOT EXISTS company_id INTEGER;"
        )
        cur.execute(
            "ALTER TABLE voucher_documents ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;"
        )

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
):
    """
    Write an audit log entry. Failures are swallowed so logging never breaks main flow.
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
