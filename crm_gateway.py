# crm_gateway.py
# Minimal CRM helpers (vendors + accounts) with multi-tenant support

from contextlib import closing
from typing import List, Dict

from db_config import connect, log_action


def list_vendors(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, name, contact_person, bank_name, bank_account, notes
            FROM vendors
            WHERE company_id = %s
            ORDER BY name
            """
            ,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_vendor(
    company_id: int,
    name: str,
    contact_person: str = "",
    bank_name: str = "",
    bank_account: str = "",
    notes: str = "",
    username: str = "",
):
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT id FROM vendors WHERE company_id = %s AND name = %s",
            (company_id, name),
        )
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
                  AND company_id = %s
                """
                ,
                (contact_person, bank_name, bank_account, notes, vid, company_id),
            )
            action = "update_vendor"
        else:
            cur.execute(
                """
                INSERT INTO vendors (company_id, name, contact_person, bank_name, bank_account, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """
                ,
                (company_id, name, contact_person, bank_name, bank_account, notes),
            )
            vid = cur.fetchone()["id"]
            action = "create_vendor"

        conn.commit()

    log_action(username, action, "vendors", ref=str(vid),
               details=f"company_id={company_id}")


def list_accounts(account_type: str, company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, code, name, type
            FROM accounts
            WHERE type = %s
              AND company_id = %s
            ORDER BY code
            """
            ,
            (account_type, company_id),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_account(
    company_id: int,
    code: str,
    name: str,
    account_type: str,
    username: str = "",
):
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT id FROM accounts WHERE company_id = %s AND code = %s",
            (company_id, code),
        )
        row = cur.fetchone()
        if row:
            aid = row["id"]
            cur.execute(
                """
                UPDATE accounts
                SET name = %s, type = %s
                WHERE id = %s
                  AND company_id = %s
                """
                ,
                (name, account_type, aid, company_id),
            )
            action = "update_account"
        else:
            cur.execute(
                """
                INSERT INTO accounts (company_id, code, name, type)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """
                ,
                (company_id, code, name, account_type),
            )
            aid = cur.fetchone()["id"]
            action = "create_account"

        conn.commit()

    log_action(username, action, "accounts", ref=str(aid),
               details=f"company_id={company_id}, code={code}")
