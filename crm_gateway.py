# crm_gateway.py
# Multi-tenant CRM gateway: Vendors, Staff, Chart of Accounts.

from contextlib import closing
from typing import List, Dict, Optional

from db_config import (
    connect,
    VENDORS_TABLE_SQL,
    STAFF_TABLE_SQL,
    ACCOUNTS_TABLE_SQL,
)


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
    """
    Return all vendors for a company.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                name,
                contact_person,
                bank_name,
                bank_account,
                notes,
                created_at
            FROM vendors
            WHERE company_id = %s
            ORDER BY lower(name)
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    result: List[Dict] = []
    for r in rows:
        (
            vid,
            name,
            contact_person,
            bank_name,
            bank_account,
            notes,
            created_at,
        ) = r
        result.append(
            {
                "id": vid,
                "name": name,
                "contact_person": contact_person,
                "bank_name": bank_name,
                "bank_account": bank_account,
                "notes": notes,
                "created_at": created_at,
            }
        )
    return result


def get_vendor_name_list(company_id: int) -> List[str]:
    """
    Simple list of vendor names for dropdowns.
    """
    vendors = list_vendors(company_id)
    names = [v["name"] for v in vendors if v.get("name")]
    if not names:
        return ["-- Add vendors in CRM first --"]
    return names


def upsert_vendor(
    company_id: int,
    name: str,
    contact_person: Optional[str] = None,
    bank_name: Optional[str] = None,
    bank_account: Optional[str] = None,
    notes: Optional[str] = None,
    username: Optional[str] = None,
    vendor_id: Optional[int] = None,
) -> Optional[str]:
    """
    Insert or update a vendor.

    - If vendor_id is None => INSERT
    - Else => UPDATE the existing vendor for this company.
    """
    name = (name or "").strip()
    contact_person = (contact_person or "").strip() or None
    bank_name = (bank_name or "").strip() or None
    bank_account = (bank_account or "").strip() or None
    notes = (notes or "").strip() or None

    if not name:
        return "Vendor name is required."

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if vendor_id is None:
                cur.execute(
                    """
                    INSERT INTO vendors (
                        company_id,
                        name,
                        contact_person,
                        bank_name,
                        bank_account,
                        notes
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (company_id, name)
                    DO UPDATE SET
                        contact_person = EXCLUDED.contact_person,
                        bank_name      = EXCLUDED.bank_name,
                        bank_account   = EXCLUDED.bank_account,
                        notes          = EXCLUDED.notes
                    """,
                    (
                        company_id,
                        name,
                        contact_person,
                        bank_name,
                        bank_account,
                        notes,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE vendors
                    SET name           = %s,
                        contact_person = %s,
                        bank_name      = %s,
                        bank_account   = %s,
                        notes          = %s
                    WHERE company_id = %s
                      AND id         = %s
                    """,
                    (
                        name,
                        contact_person,
                        bank_name,
                        bank_account,
                        notes,
                        company_id,
                        vendor_id,
                    ),
                )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error saving vendor: {ex}"


def delete_vendor(company_id: int, vendor_id: int) -> Optional[str]:
    """
    Delete a vendor for this company.
    Existing vouchers/invoices that already stored the vendor name
    as plain text will not be auto-updated.
    """
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


# ------------------------
# Staff
# ------------------------

def list_staff(company_id: int) -> List[Dict]:
    """
    Return all staff for a company.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                first_name,
                last_name,
                email,
                phone,
                status,
                position,
                created_at
            FROM staff
            WHERE company_id = %s
            ORDER BY lower(first_name), lower(last_name)
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    result: List[Dict] = []
    for r in rows:
        (
            sid,
            first_name,
            last_name,
            email,
            phone,
            status,
            position,
            created_at,
        ) = r
        result.append(
            {
                "id": sid,
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "status": status,
                "position": position,
                "created_at": created_at,
            }
        )
    return result


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
    """
    Insert or update staff.

    - If staff_id is None => INSERT
    - Else => UPDATE.
    """
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    status = (status or "Active").strip()
    position = (position or "").strip() or None

    if not first_name or not last_name:
        return "First name and last name are required."

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if staff_id is None:
                cur.execute(
                    """
                    INSERT INTO staff (
                        company_id,
                        first_name,
                        last_name,
                        email,
                        phone,
                        status,
                        position
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        company_id,
                        first_name,
                        last_name,
                        email,
                        phone,
                        status,
                        position,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE staff
                    SET first_name = %s,
                        last_name  = %s,
                        email      = %s,
                        phone      = %s,
                        status     = %s,
                        position   = %s
                    WHERE company_id = %s
                      AND id         = %s
                    """,
                    (
                        first_name,
                        last_name,
                        email,
                        phone,
                        status,
                        position,
                        company_id,
                        staff_id,
                    ),
                )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error saving staff: {ex}"


def delete_staff(company_id: int, staff_id: int) -> Optional[str]:
    """
    Delete a staff record for this company.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                DELETE FROM staff
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, staff_id),
            )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error deleting staff: {ex}"


def get_requester_options(company_id: int) -> List[str]:
    """
    Build requester dropdown from active staff.
    """
    staff = list_staff(company_id)
    options: List[str] = []
    for s in staff:
        if (s.get("status") or "Active") != "Active":
            continue
        first = s.get("first_name") or ""
        last = s.get("last_name") or ""
        name = (first + " " + last).strip()
        if not name:
            name = s.get("email") or s.get("phone") or ""
        if not name:
            continue
        options.append(name)
    if not options:
        return ["-- Add staff in CRM first --"]
    return options


# ------------------------
# Chart of Accounts
# ------------------------

def list_accounts(company_id: int) -> List[Dict]:
    """
    Return all chart of accounts rows for a company.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                code,
                name,
                type,
                created_at
            FROM accounts
            WHERE company_id = %s
            ORDER BY code
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    result: List[Dict] = []
    for r in rows:
        aid, code, name, acc_type, created_at = r
        result.append(
            {
                "id": aid,
                "code": code,
                "name": name,
                "type": acc_type,
                "created_at": created_at,
            }
        )
    return result


def upsert_account(
    company_id: int,
    code: str,
    name: str,
    **kwargs,
) -> Optional[str]:
    """
    Insert or update a chart of account row.

    Accepts extra kwargs for compatibility:
      - account_type / acc_type
      - username (ignored)
      - account_id
    """
    code = (code or "").strip()
    name = (name or "").strip()
    if not code or not name:
        return "Account code and name are required."

    account_type = (
        kwargs.get("account_type")
        or kwargs.get("acc_type")
        or "Asset"
    )
    account_type = (account_type or "Asset").strip()

    account_id: Optional[int] = kwargs.get("account_id")

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if account_id is None:
                cur.execute(
                    """
                    INSERT INTO accounts (
                        company_id,
                        code,
                        name,
                        type
                    ) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (company_id, code)
                    DO UPDATE SET
                        name = EXCLUDED.name,
                        type = EXCLUDED.type
                    """,
                    (
                        company_id,
                        code,
                        name,
                        account_type,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE accounts
                    SET code = %s,
                        name = %s,
                        type = %s
                    WHERE company_id = %s
                      AND id         = %s
                    """,
                    (
                        code,
                        name,
                        account_type,
                        company_id,
                        account_id,
                    ),
                )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error saving account: {ex}"


def delete_account(company_id: int, account_id: int) -> Optional[str]:
    """
    Delete an account for this company.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                DELETE FROM accounts
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, account_id),
            )
            conn.commit()
        return None
    except Exception as ex:
        return f"Error deleting account: {ex}"


def get_payable_account_options(company_id: int) -> List[str]:
    """
    Payables = Liability / Equity (fallback to all).
    """
    accounts = list_accounts(company_id)
    liab = [a["name"] for a in accounts if a.get("type") in ("Liability", "Equity")]
    if liab:
        return liab
    names = [a["name"] for a in accounts]
    return names or ["-- Add accounts in CRM first --"]


def get_expense_asset_account_options(company_id: int) -> List[str]:
    """
    Expense & Asset accounts (fallback to all).
    """
    accounts = list_accounts(company_id)
    exp_asset = [a["name"] for a in accounts if a.get("type") in ("Expense", "Asset")]
    if exp_asset:
        return exp_asset
    names = [a["name"] for a in accounts]
    return names or ["-- Add accounts in CRM first --"]






