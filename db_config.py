import os
import glob
import pathlib
from contextlib import contextmanager
from typing import Optional, Tuple

import psycopg2
from psycopg2.extras import DictCursor


# =========================
# DATABASE CONFIG
# =========================

DEFAULT_DB_URL_ENV = "VOUCHER_DB_URL"


def get_db_url(env_var: str = DEFAULT_DB_URL_ENV, override_url: Optional[str] = None) -> str:
    """
    Return a Postgres connection URL.

    Priority:
    1. override_url (explicit argument)
    2. environment variable (e.g. VOUCHER_DB_URL)
    """
    if override_url:
        return override_url

    url = os.getenv(env_var)
    if not url:
        raise RuntimeError(
            f"Database URL not set. Please define the environment variable {env_var}."
        )
    return url


def get_connection(db_url: Optional[str] = None) -> psycopg2.extensions.connection:
    """
    Create a new psycopg2 connection.
    """
    url = db_url or get_db_url()
    return psycopg2.connect(url)


@contextmanager
def get_db_cursor(
    db_url: Optional[str] = None,
) -> Tuple[psycopg2.extensions.connection, psycopg2.extensions.cursor]:
    """
    Context manager that:

    - Opens a new DB connection
    - Yields (connection, cursor) with DictCursor
    - Commits on success
    - Rolls back on error
    - Closes cursor and connection in all cases
    """
    conn = get_connection(db_url)
    cur = conn.cursor(cursor_factory=DictCursor)
    try:
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# =========================
# SCHEMA MIGRATIONS
# =========================

def _ensure_schema_migrations_table(db_url: Optional[str] = None) -> None:
    """
    Ensure the schema_migrations table exists.

    schema_migrations:
      - version TEXT PRIMARY KEY
      - applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """
    with get_db_cursor(db_url) as (conn, cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _has_migration_been_applied(
    version: str,
    db_url: Optional[str] = None,
) -> bool:
    with get_db_cursor(db_url) as (conn, cur):
        cur.execute(
            "SELECT 1 FROM schema_migrations WHERE version = %s",
            (version,),
        )
        return cur.fetchone() is not None


def _mark_migration_applied(
    version: str,
    db_url: Optional[str] = None,
) -> None:
    with get_db_cursor(db_url) as (conn, cur):
        cur.execute(
            "INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
            (version,),
        )


def run_migrations(
    db_url: Optional[str] = None,
    migrations_path: str = "migrations",
) -> None:
    """
    Run all SQL migrations in the given folder that have not yet been applied.

    - Each .sql file is considered one migration.
    - The migration 'version' is taken from the file name without extension.
      Example: migrations/001_add_auth_security_columns.sql -> version '001_add_auth_security_columns'

    This function is idempotent: already-applied migrations are skipped.
    """
    path = pathlib.Path(migrations_path)
    if not path.exists():
        # No migrations folder yet → nothing to do
        return

    _ensure_schema_migrations_table(db_url)

    # Sort files so they run in a deterministic order
    sql_files = sorted(glob.glob(str(path / "*.sql")))

    for file_path in sql_files:
        file_name = pathlib.Path(file_path).name
        version = pathlib.Path(file_path).stem

        if _has_migration_been_applied(version, db_url=db_url):
            continue

        # Read SQL from file
        with open(file_path, "r", encoding="utf-8") as f:
            sql = f.read().strip()

        if not sql:
            # Empty migration, but still mark as applied
            _mark_migration_applied(version, db_url=db_url)
            continue

        # Apply the SQL migration in a transaction
        with get_db_cursor(db_url) as (conn, cur):
            cur.execute(sql)

        # Mark as applied
        _mark_migration_applied(version, db_url=db_url)
