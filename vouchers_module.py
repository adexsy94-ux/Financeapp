# vouchers_module.py
# Multi-tenant vouchers: numbering, lines, CRM linking, status changes.

from contextlib import closing
from datetime import datetime
from typing import List, Dict, Optional

from db_config import (
    connect,
    VOUCHER_TABLE_SQL,
    VOUCHER_LINES_TABLE_SQL,
    VOUCHER_DOCS_TABLE_SQL,
    log_action,
)
from crm_gateway import get_vendor_name_list, get_requester_options


# ------------------------
# Schema init
# ------------------------

def init_voucher_schema() -> None:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(VOUCHER_TABLE_SQL)
        cur.execute(VOUCHER_LINES_TABLE_SQL)
        cur.execute(VOUCHER_DOCS_TABLE_SQL)
        conn.commit()


# ------------------------
# Helpers
# ------------------------

def _now_ts() -> datetime:
    return datetime.utcnow()


def generate_voucher_number(company_id: int) -> str:
    """
    Simple timestamp-based voucher number, unique per company.
    """
    now = datetime.utcnow()
    return now.strftime("VCH-%Y%m%d%H%M%S")


# ------------------------
# Create
# ------------------------

def create_voucher(
    company_id: int,
    username: str,
    vendor: str,
    requester: str,
    invoice_ref: str,
    currency: str,
    lines: List[Dict],
    file_name: Optional[str],
    file_bytes: Optional[bytes],
) -> Optional[str]:
    """
    Create a new voucher with line items and optional attachment.
    """
    vendor = (vendor or "").strip()
    requester = (requester or "").strip()
    invoice_ref = (invoice_ref or "").strip()
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        return "Vendor is required."
    if not requester:
        return "Requester is required."

    # Validate vendor exists in CRM
    vendor_list = get_vendor_name_list(company_id)
    if vendor not in vendor_list:
        return f"Vendor '{vendor}' not found in CRM. Please create it first in the CRM tab."

    # Validate requester exists in staff options (soft check)
    requester_opts = get_requester_options(company_id)
    if requester not in requester_opts:
        # Not fatal, but warn
        pass

    if not lines:
        return "At least one voucher line is required."

    # Basic validation of lines
    valid_lines: List[Dict] = []
    for idx, ln in enumerate(lines):
        desc = (ln.get("description") or "").strip()
        account_name = (ln.get("account_name") or "").strip()
        amount = float(ln.get("amount") or 0.0)
        vat_percent = float(ln.get("vat_percent") or 0.0)
        wht_percent = float(ln.get("wht_percent") or 0.0)

        if not desc and amount == 0:
            # ignore empty rows
            continue
        if not desc:
            return f"Description is required for line {idx + 1}."
        if amount <= 0:
            return f"Amount must be > 0 for line {idx + 1}."
        if not account_name:
            return f"Account (Chart of Accounts) is required for line {idx + 1}."

        vat_value = round(amount * vat_percent / 100.0, 2)
        wht_value = round(amount * wht_percent / 100.0, 2)

        valid_lines.append(
            {
                "description": desc,
                "account_name": account_name,
                "amount": amount,
                "vat_percent": vat_percent,
                "wht_percent": wht_percent,
                "vat_value": vat_value,
                "wht_value": wht_value,
            }
        )

    if not valid_lines:
        return "No valid voucher lines found."

    voucher_number = generate_voucher_number(company_id)
    ts = _now_ts()

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            # Insert voucher header
            cur.execute(
                """
                INSERT INTO vouchers (
                    company_id,
                    parent_id,
                    version,
                    voucher_number,
                    vendor,
                    requester,
                    invoice_ref,
                    currency,
                    status,
                    created_at,
                    last_modified,
                    approved_by,
                    approved_at
                ) VALUES (
                    %s, NULL, 1,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NULL,
                    NULL
                )
                RETURNING id
                """,
                (
                    company_id,
                    voucher_number,
                    vendor,
                    requester,
                    invoice_ref or None,
                    currency,
                    "draft",
                    ts,
                    ts,
                ),
            )
            (voucher_id,) = cur.fetchone()

            # Insert lines
            for ln in valid_lines:
                cur.execute(
                    """
                    INSERT INTO voucher_lines (
                        company_id,
                        voucher_id,
                        description,
                        account_name,
                        amount,
                        vat_percent,
                        wht_percent,
                        vat_value,
                        wht_value
                    ) VALUES (
                        %s, %s,
                        %s, %s,
                        %s,
                        %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        company_id,
                        voucher_id,
                        ln["description"],
                        ln["account_name"],
                        ln["amount"],
                        ln["vat_percent"],
                        ln["wht_percent"],
                        ln["vat_value"],
                        ln["wht_value"],
                    ),
                )

            # Insert attachment (optional)
            if file_name and file_bytes:
                cur.execute(
                    """
                    INSERT INTO voucher_documents (
                        company_id,
                        voucher_id,
                        file_name,
                        file_data,
                        uploaded_at
                    ) VALUES (
                        %s, %s,
                        %s, %s,
                        %s
                    )
                    """,
                    (
                        company_id,
                        voucher_id,
                        file_name,
                        file_bytes,
                        ts,
                    ),
                )

            conn.commit()

        log_action(
            username,
            "create_voucher",
            "vouchers",
            ref=voucher_number,
            company_id=company_id,
        )
        return None
    except Exception as ex:
        return f"Error creating voucher: {ex}"


# ------------------------
# Status change / delete
# ------------------------

def change_voucher_status(
    company_id: int,
    voucher_id: int,
    new_status: str,
    actor_username: str,
) -> Optional[str]:
    """
    Change the status of a voucher (draft / submitted / approved / rejected).
    """
    new_status = (new_status or "").strip().lower()
    if new_status not in {"draft", "submitted", "approved", "rejected"}:
        return f"Invalid status '{new_status}'."

    ts = _now_ts()
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if new_status in ("approved", "rejected"):
                cur.execute(
                    """
                    UPDATE vouchers
                    SET status      = %s,
                        approved_by = %s,
                        approved_at = %s,
                        last_modified = %s
                    WHERE id = %s
                      AND company_id = %s
                    """,
                    (new_status, actor_username, ts, ts, voucher_id, company_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE vouchers
                    SET status = %s,
                        last_modified = %s
                    WHERE id = %s
                      AND company_id = %s
                    """,
                    (new_status, ts, voucher_id, company_id),
                )
            conn.commit()

        log_action(
            actor_username,
            f"voucher_status_{new_status}",
            "vouchers",
            ref=str(voucher_id),
            company_id=company_id,
        )
        return None
    except Exception as ex:
        return f"Error changing voucher status: {ex}"


def delete_voucher(
    company_id: int,
    voucher_id: int,
    actor_username: str,
) -> Optional[str]:
    """
    Hard-delete a voucher for this company.
    Assuming ON DELETE CASCADE on voucher_lines and voucher_documents.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                DELETE FROM vouchers
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, voucher_id),
            )
            conn.commit()

        log_action(
            actor_username,
            "voucher_delete",
            "vouchers",
            ref=str(voucher_id),
            company_id=company_id,
        )
        return None
    except Exception as ex:
        return f"Error deleting voucher: {ex}"


# ------------------------
# Queries
# ------------------------

def list_vouchers(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                parent_id,
                version,
                voucher_number,
                vendor,
                requester,
                invoice_ref,
                currency,
                status,
                created_at,
                last_modified,
                approved_by,
                approved_at
            FROM vouchers
            WHERE company_id = %s
            ORDER BY last_modified DESC, id DESC
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    result: List[Dict] = []
    for r in rows:
        (
            vid,
            parent_id,
            version,
            voucher_number,
            vendor,
            requester,
            invoice_ref,
            currency,
            status,
            created_at,
            last_modified,
            approved_by,
            approved_at,
        ) = r
        result.append(
            {
                "id": vid,
                "parent_id": parent_id,
                "version": version,
                "voucher_number": voucher_number,
                "vendor": vendor,
                "requester": requester,
                "invoice_ref": invoice_ref,
                "currency": currency,
                "status": status,
                "created_at": created_at,
                "last_modified": last_modified,
                "approved_by": approved_by,
                "approved_at": approved_at,
            }
        )
    return result
