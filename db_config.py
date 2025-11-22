# db_config.py

import os
from contextlib import closing
from datetime import datetime

import psycopg2
from psycopg2.extras import DictCursor


def get_db_dsn() -> str:
    """
    Get the Postgres DSN.

    It MUST be provided via environment / secrets (e.g. VOUCHER_DB_URL).
    """
    dsn = os.getenv("VOUCHER_DB_URL")
    if not dsn:
        raise RuntimeError("Environment variable VOUCHER_DB_URL is not set.")
    return dsn
