# crm_gateway.py
# Minimal CRM helpers (vendors + accounts)

from contextlib import closing
from typing import List, Dict

from db_config import connect, log_action


# ------------------------
# Vendors
# ------------------------

def list_vendors() -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, name, contact_person, bank_name, bank_account, notes
            FROM vendors
            ORDER BY name
            """
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_vendor(
    name: str,
    contact_person: str = "",
    bank_name: str = "",
    bank_account: str = "",
    notes: str = "",
    username: str = "",
):
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        # Simple upsert: if vendor with same name exists, update; else insert
        cur.execute("SELECT id FROM vendors WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            vid = row["id"]
            cur.execute(
                """
                UPDATE vendors
                SET contact_person = %s,
                    bank_name = %s,
                    bank_account = %s,
                    notes = %s
                WHERE id = %s
                """,
                (contact_person, bank_name, bank_account, notes, vid),
            )
            action = "update_vendor"
        else:
            cur.execute(
                """
                INSERT INTO vendors (name, contact_person, bank_name, bank_account, notes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (name, contact_person, bank_name, bank_account, notes),
            )
            vid = cur.fetchone()["id"]
            action = "create_vendor"

        conn.commit()

    log_action(username, action, "vendors", ref=str(vid))


# ------------------------
# Accounts
# ------------------------

def list_accounts(account_type: str) -> List[Dict]:
    # account_type: 'payable', 'expense', 'asset', etc.
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, code, name, type
            FROM accounts
            WHERE type = %s
            ORDER BY code
            """,
            (account_type,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_account(code: str, name: str, account_type: str, username: str = ""):
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute("SELECT id FROM accounts WHERE code = %s", (code,))
        row = cur.fetchone()
        if row:
            aid = row["id"]
            cur.execute(
                """
                UPDATE accounts
                SET name = %s, type = %s
                WHERE id = %s
                """,
                (name, account_type, aid),
            )
            action = "update_account"
        else:
            cur.execute(
                """
                INSERT INTO accounts (code, name, type)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (code, name, account_type),
            )
            aid = cur.fetchone()["id"]
            action = "create_account"

        conn.commit()

    log_action(username, action, "accounts", ref=str(aid))
