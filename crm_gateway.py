# crm_gateway.py
# Multi-tenant CRM gateway: Vendors, Staff, Chart of Accounts.

from contextlib import closing
from typing import List, Dict, Optional

from db_config import connect, VENDORS_TABLE_SQL, STAFF_TABLE_SQL, ACCOUNTS_TABLE_SQL


def init_crm_schema() -> None:
    """
    Ensure CRM-related tables exist.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(VENDORS_TABLE_SQL)
        cur.execute(STAFF_TABLE_SQL)
        cur.execute(ACCOUNTS_TABLE_SQL)
        conn.commit()


# ------------------------
# Vendors
# ------------------------

def list_vendors(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, name, contact_person, bank_name, bank_account, notes, created_at
            FROM vendors
            WHERE company_id = %s
            ORDER BY lower(name)
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_vendor(
    company_id: int,
    name: str,
    contact_person: Optional[str],
    bank_name: Optional[str],
    bank_account: Optional[str],
    notes: Optional[str],
    vendor_id: Optional[int] = None,
) -> Optional[str]:
    name_norm = (name or "").strip()
    if not name_norm:
        return "Vendor name is required."

    contact_person = (contact_person or "").strip() or None
    bank_name = (bank_name or "").strip() or None
    bank_account = (bank_account or "").strip() or None
    notes = (notes or "").strip() or None

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if vendor_id:
                cur.execute(
                    """
                    UPDATE vendors
                    SET name = %s,
                        contact_person = %s,
                        bank_name = %s,
                        bank_account = %s,
                        notes = %s
                    WHERE id = %s
                      AND company_id = %s
                    """,
                    (
                        name_norm,
                        contact_person,
                        bank_name,
                        bank_account,
                        notes,
                        vendor_id,
                        company_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO vendors (
                        company_id,
                        name,
                        contact_person,
                        bank_name,
                        bank_account,
                        notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        name_norm,
                        contact_person,
                        bank_name,
                        bank_account,
                        notes,
                    ),
                )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error saving vendor: {ex}"


def delete_vendor(company_id: int, vendor_id: int) -> Optional[str]:
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                DELETE FROM vendors
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, vendor_id),
            )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error deleting vendor: {ex}"


def get_vendor_name_list(company_id: int) -> List[str]:
    vendors = list_vendors(company_id)
    names = [v["name"] for v in vendors if v.get("name")]
    return names or ["-- Add vendors in CRM first --"]


# ------------------------
# Staff
# ------------------------

def list_staff(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, first_name, last_name, email, phone, status, position, created_at
            FROM staff
            WHERE company_id = %s
            ORDER BY lower(last_name), lower(first_name)
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_staff(
    company_id: int,
    first_name: str,
    last_name: str,
    email: Optional[str],
    phone: Optional[str],
    status: str,
    position: Optional[str],
    staff_id: Optional[int] = None,
) -> Optional[str]:
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    if not fn or not ln:
        return "First name and last name are required."

    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    status = (status or "").strip() or "Active"
    position = (position or "").strip() or None

    if status not in ("Active", "Inactive"):
        return "Status must be Active or Inactive."

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if staff_id:
                cur.execute(
                    """
                    UPDATE staff
                    SET first_name = %s,
                        last_name = %s,
                        email = %s,
                        phone = %s,
                        status = %s,
                        position = %s
                    WHERE id = %s
                      AND company_id = %s
                    """,
                    (fn, ln, email, phone, status, position, staff_id, company_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO staff (
                        company_id, first_name, last_name, email, phone, status, position
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (company_id, fn, ln, email, phone, status, position),
                )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error saving staff: {ex}"


def get_requester_options(company_id: int) -> List[str]:
    staff = list_staff(company_id)
    names: List[str] = []
    for s in staff:
        if s.get("status") != "Active":
            continue
        fn = (s.get("first_name") or "").strip()
        ln = (s.get("last_name") or "").strip()
        full = f"{fn} {ln}".strip()
        if full:
            names.append(full)
    return names or ["-- Add staff in CRM first --"]


# ------------------------
# Chart of Accounts
# ------------------------

def list_accounts(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT id, code, name, type, created_at
            FROM accounts
            WHERE company_id = %s
            ORDER BY code
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def upsert_account(
    company_id: int,
    code: str,
    name: str,
    acc_type: str,
    account_id: Optional[int] = None,
) -> Optional[str]:
    code = (code or "").strip()
    name = (name or "").strip()
    acc_type = (acc_type or "").strip()

    if not code or not name or not acc_type:
        return "Code, name and type are required."

    if acc_type not in ("Asset", "Liability", "Equity", "Income", "Expense"):
        return "Invalid account type."

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if account_id:
                cur.execute(
                    """
                    UPDATE accounts
                    SET code = %s,
                        name = %s,
                        type = %s
                    WHERE id = %s
                      AND company_id = %s
                    """,
                    (code, name, acc_type, account_id, company_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO accounts (company_id, code, name, type)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (company_id, code, name, acc_type),
                )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error saving account: {ex}"


def get_payable_account_options(company_id: int) -> List[str]:
    accounts = list_accounts(company_id)
    liab = [a["name"] for a in accounts if a.get("type") in ("Liability", "Equity")]
    return liab or [a["name"] for a in accounts] or ["-- Add accounts in CRM first --"]


def get_expense_asset_account_options(company_id: int) -> List[str]:
    accounts = list_accounts(company_id)
    exp_asset = [a["name"] for a in accounts if a.get("type") in ("Expense", "Asset")]
    return exp_asset or [a["name"] for a in accounts] or ["-- Add accounts in CRM first --"]
