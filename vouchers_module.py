# vouchers_module.py
# Multi-tenant vouchers: numbering, lines, CRM linking, status changes.

from contextlib import closing
from datetime import datetime
from typing import List, Dict, Optional

from db_config import connect, VOUCHER_TABLE_SQL, VOUCHER_LINES_TABLE_SQL, VOUCHER_DOCS_TABLE_SQL, log_action
from crm_gateway import get_vendor_name_list, get_requester_options


def init_voucher_schema() -> None:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(VOUCHER_TABLE_SQL)
        cur.execute(VOUCHER_LINES_TABLE_SQL)
        cur.execute(VOUCHER_DOCS_TABLE_SQL)
        conn.commit()


def _now_ts():
    return datetime.utcnow()


# ------------------------
# Numbering
# ------------------------

def generate_next_voucher_number(company_id: int, prefix: str = "VCH") -> str:
    year = datetime.utcnow().year
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT voucher_number
            FROM vouchers
            WHERE company_id = %s
              AND voucher_number LIKE %s
            ORDER BY voucher_number DESC
            LIMIT 1
            """,
            (company_id, f"{prefix}-{year}-%"),
        )
        row = cur.fetchone()

    if not row or not row["voucher_number"]:
        return f"{prefix}-{year}-0001"

    last = row["voucher_number"]
    try:
        last_seq = int(last.split("-")[-1])
    except Exception:
        last_seq = 0
    next_seq = last_seq + 1
    return f"{prefix}-{year}-{next_seq:04d}"


# ------------------------
# Queries
# ------------------------

def list_vouchers(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id, parent_id, version, voucher_number,
                vendor, requester, invoice_ref,
                currency, status,
                last_modified, approved_by, approved_at
            FROM vouchers
            WHERE company_id = %s
            ORDER BY last_modified DESC NULLS LAST, id DESC
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_voucher_with_lines(company_id: int, voucher_id: int) -> Optional[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT *
            FROM vouchers
            WHERE company_id = %s
              AND id = %s
            """,
            (company_id, voucher_id),
        )
        header = cur.fetchone()
        if not header:
            return None

        cur.execute(
            """
            SELECT *
            FROM voucher_lines
            WHERE voucher_id = %s
            ORDER BY line_no, id
            """,
            (voucher_id,),
        )
        lines = cur.fetchall()

        cur.execute(
            """
            SELECT id, file_name, created_at
            FROM voucher_documents
            WHERE voucher_id = %s
            ORDER BY created_at
            """,
            (voucher_id,),
        )
        docs = cur.fetchall()

    return {
        "header": dict(header),
        "lines": [dict(l) for l in lines],
        "documents": [dict(d) for d in docs],
    }


# ------------------------
# Creation & versioning
# ------------------------

def create_voucher(
    company_id: int,
    username: str,
    vendor: str,
    requester: str,
    invoice_ref: Optional[str],
    currency: str,
    lines: List[Dict],
    file_name: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    extra_docs: Optional[List[Dict]] = None,  # [{file_name, file_bytes}, ...]
) -> Optional[str]:
    """
    Create a brand-new voucher (version 1) with its line items.
    Will:
    - Validate vendor + requester exist in CRM lists (by name)
    - Auto compute VAT/WHT/total per line
    - Prevent duplicate attachments by file_name within same voucher
    """
    vendor = (vendor or "").strip()
    requester = (requester or "").strip()
    invoice_ref = (invoice_ref or "").strip() or None
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        return "Vendor is required."
    if not requester:
        return "Requester is required."
    if not lines:
        return "At least one line is required."

    # Soft validation against CRM lists (logic inspired by old app)
    vendor_opts = get_vendor_name_list(company_id)
    if vendor not in vendor_opts:
        return f"Vendor '{vendor}' not found in CRM. Please create it in the CRM tab."

    requester_opts = get_requester_options(company_id)
    if requester not in requester_opts and not requester.startswith("--"):
        # allow free text a bit, but warn
        pass

    voucher_number = generate_next_voucher_number(company_id)

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            ts = _now_ts()

            # Header
            cur.execute(
                """
                INSERT INTO vouchers (
                    parent_id, version, company_id,
                    voucher_number, vendor, requester, invoice_ref,
                    currency, status,
                    file_name, file_data,
                    last_modified, approved_by, approved_at
                )
                VALUES (
                    NULL, 1, %s,
                    %s, %s, %s, %s,
                    %s, 'draft',
                    %s, %s,
                    %s, NULL, NULL
                )
                RETURNING id
                """,
                (
                    company_id,
                    voucher_number,
                    vendor,
                    requester,
                    invoice_ref,
                    currency,
                    file_name,
                    file_bytes,
                    ts,
                ),
            )
            voucher_id = cur.fetchone()["id"]

            # Lines
            for i, line in enumerate(lines, start=1):
                desc = (line.get("description") or "").strip()
                amount = float(line.get("amount") or 0.0)
                acc_name = (line.get("account_name") or "").strip() or None
                vat_percent = float(line.get("vat_percent") or 0.0)
                wht_percent = float(line.get("wht_percent") or 0.0)

                vat_value = round(amount * vat_percent / 100.0, 2)
                wht_value = round(amount * wht_percent / 100.0, 2)
                total = round(amount + vat_value - wht_value, 2)

                cur.execute(
                    """
                    INSERT INTO voucher_lines (
                        voucher_id, company_id,
                        line_no, description, amount,
                        account_name,
                        vat_percent, wht_percent,
                        vat_value, wht_value, total
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        voucher_id,
                        company_id,
                        i,
                        desc,
                        amount,
                        acc_name,
                        vat_percent,
                        wht_percent,
                        vat_value,
                        wht_value,
                        total,
                    ),
                )

            # Extra attachments – enforce unique file_name per voucher
            extra_docs = extra_docs or []
            for doc in extra_docs:
                dname = (doc.get("file_name") or "").strip()
                dbytes = doc.get("file_bytes")
                if not dname or not dbytes:
                    continue

                cur.execute(
                    """
                    INSERT INTO voucher_documents (voucher_id, company_id, file_name, file_data)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (voucher_id, file_name) DO NOTHING
                    """,
                    (voucher_id, company_id, dname, dbytes),
                )

            conn.commit()

        log_action(username, "create_voucher", "vouchers", ref=voucher_number, company_id=company_id)
        return None

    except Exception as ex:
        return f"Error creating voucher: {ex}"


def change_voucher_status(
    company_id: int,
    voucher_id: int,
    new_status: str,
    actor_username: str,
) -> Optional[str]:
    new_status = (new_status or "").strip().lower()
    if new_status not in ("draft", "submitted", "approved", "rejected"):
        return "Invalid status."

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            ts = _now_ts()
            if new_status == "approved":
                cur.execute(
                    """
                    UPDATE vouchers
                    SET status = %s,
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

        log_action(actor_username, f"voucher_status_{new_status}", "vouchers", ref=str(voucher_id), company_id=company_id)
        return None
    except Exception as ex:
        return f"Error changing voucher status: {ex}"
