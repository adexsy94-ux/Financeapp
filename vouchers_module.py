"""
vouchers_module.py
Updated voucher module with manual voucher number support and simple reporting-friendly helpers.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2

from db_config import connect
from auth_module import log_action


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _now_ts() -> str:
    """Return current UTC timestamp in ISO format suitable for DB."""
    return datetime.utcnow().isoformat(timespec="seconds")


def generate_voucher_number(company_id: int) -> str:
    """
    Generate a new voucher number like VCH-2025-0001 for the given company.
    Assumes there is a vouchers table with company_id and voucher_number.
    """
    year = datetime.utcnow().year
    prefix = f"VCH-{year}-"

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
            (company_id, prefix + "%"),
        )
        row = cur.fetchone()

        if not row or not row[0]:
            next_seq = 1
        else:
            last_num = row[0]
            try:
                seq_part = last_num.split("-")[-1]
                next_seq = int(seq_part) + 1
            except Exception:
                next_seq = 1

    return f"{prefix}{next_seq:04d}"


# ---------------------------------------------------------------------
# Schema initialisation (optional – safe to call multiple times)
# ---------------------------------------------------------------------

def init_voucher_schema() -> None:
    """
    Ensures the vouchers and voucher_lines tables exist.
    This is conservative: if the tables already exist, nothing breaks.
    """
    ddl_vouchers = """
    CREATE TABLE IF NOT EXISTS vouchers (
        id SERIAL PRIMARY KEY,
        company_id INTEGER NOT NULL,
        parent_id INTEGER NULL,
        version INTEGER NOT NULL DEFAULT 1,
        voucher_number TEXT NOT NULL,
        vendor TEXT NOT NULL,
        requester TEXT NOT NULL,
        invoice_ref TEXT,
        currency TEXT NOT NULL DEFAULT 'NGN',
        status TEXT NOT NULL DEFAULT 'draft',
        created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        last_modified TIMESTAMP WITHOUT TIME ZONE NOT NULL,
        approved_by TEXT NULL,
        approved_at TIMESTAMP WITHOUT TIME ZONE NULL,
        file_name TEXT NULL,
        file_data BYTEA NULL
    );
    """

    ddl_voucher_lines = """
    CREATE TABLE IF NOT EXISTS voucher_lines (
        id SERIAL PRIMARY KEY,
        company_id INTEGER NOT NULL,
        voucher_id INTEGER NOT NULL REFERENCES vouchers(id) ON DELETE CASCADE,
        line_no INTEGER NOT NULL,
        description TEXT NOT NULL,
        account_name TEXT NOT NULL,
        amount NUMERIC(18, 2) NOT NULL DEFAULT 0,
        vat_percent NUMERIC(5, 2) NOT NULL DEFAULT 0,
        wht_percent NUMERIC(5, 2) NOT NULL DEFAULT 0,
        vat_value NUMERIC(18, 2) NOT NULL DEFAULT 0,
        wht_value NUMERIC(18, 2) NOT NULL DEFAULT 0,
        total NUMERIC(18, 2) NOT NULL DEFAULT 0
    );
    """

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(ddl_vouchers)
        cur.execute(ddl_voucher_lines)
        conn.commit()


# ---------------------------------------------------------------------
# Core CRUD helpers
# ---------------------------------------------------------------------

def list_vouchers(company_id: int) -> List[Dict[str, Any]]:
    """
    Return all vouchers for a company as a list of dicts.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
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
                approved_at,
                file_name
            FROM vouchers
            WHERE company_id = %s
            ORDER BY id DESC
            """,
            (company_id,),
        )
        rows = cur.fetchall()
        colnames = [c.name for c in cur.description]

    results: List[Dict[str, Any]] = []
    for row in rows:
        results.append({colnames[i]: row[i] for i in range(len(colnames))})
    return results


def list_voucher_lines(company_id: int, voucher_id: int) -> List[Dict[str, Any]]:
    """
    Return all voucher_lines for a voucher as a list of dicts.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                company_id,
                voucher_id,
                line_no,
                description,
                account_name,
                amount,
                vat_percent,
                wht_percent,
                vat_value,
                wht_value,
                total
            FROM voucher_lines
            WHERE company_id = %s
              AND voucher_id = %s
            ORDER BY line_no
            """,
            (company_id, voucher_id),
        )
        rows = cur.fetchall()
        colnames = [c.name for c in cur.description]

    results: List[Dict[str, Any]] = []
    for row in rows:
        results.append({colnames[i]: row[i] for i in range(len(colnames))})
    return results


def create_voucher(
    company_id: int,
    vendor: str,
    requester: str,
    invoice_ref: str,
    currency: str,
    username: str,
    lines: List[Dict[str, Any]],
    file_name: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
    voucher_number: Optional[str] = None,
) -> Optional[str]:
    """
    Create a new voucher with optional manual voucher_number.
    If voucher_number is None or blank, a new one is generated.
    Ensures voucher_number is unique per company.
    Returns None on success, or an error message on failure.
    """

    if not vendor:
        return "Vendor is required."
    if not requester:
        return "Requester is required."
    if not currency:
        currency = "NGN"
    if not lines:
        return "Voucher must have at least one line."

    # Validate that at least one line has a positive total
    positive_exists = False
    for ln in lines:
        try:
            total_val = float(ln.get("total") or 0.0)
        except Exception:
            total_val = 0.0
        if total_val > 0:
            positive_exists = True
            break

    if not positive_exists:
        return "At least one line must have a positive payable amount."

    # Determine / normalise voucher number
    vnum = (voucher_number or "").strip().upper()
    if not vnum:
        vnum = generate_voucher_number(company_id)

    # Compute total payable (for logging only)
    total_payable = 0.0
    for ln in lines:
        try:
            total_payable += float(ln.get("total") or 0.0)
        except Exception:
            pass

    now_ts = _now_ts()

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            # Check uniqueness of voucher_number within this company
            cur.execute(
                """
                SELECT 1
                FROM vouchers
                WHERE company_id = %s
                  AND voucher_number = %s
                """,
                (company_id, vnum),
            )
            if cur.fetchone():
                return f"Voucher number '{vnum}' already exists. Please choose another number."

            # Insert header
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
                    approved_at,
                    file_name,
                    file_data
                )
                VALUES (
                    %s, NULL, 1,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'draft',
                    %s,
                    %s,
                    NULL,
                    NULL,
                    %s,
                    %s
                )
                RETURNING id
                """,
                (
                    company_id,
                    vnum,
                    vendor,
                    requester,
                    invoice_ref or "",
                    currency,
                    now_ts,
                    now_ts,
                    file_name,
                    psycopg2.Binary(file_bytes) if file_bytes else None,
                ),
            )
            voucher_id = cur.fetchone()[0]

            # Insert lines
            line_no = 1
            for ln in lines:
                desc = (ln.get("description") or "").strip()
                acct_name = (ln.get("account_name") or "").strip()

                try:
                    amt = float(ln.get("amount") or 0.0)
                except Exception:
                    amt = 0.0
                try:
                    vat_percent = float(ln.get("vat_percent") or 0.0)
                except Exception:
                    vat_percent = 0.0
                try:
                    wht_percent = float(ln.get("wht_percent") or 0.0)
                except Exception:
                    wht_percent = 0.0

                vat_val = amt * vat_percent / 100.0
                wht_val = amt * wht_percent / 100.0
                total_val = amt + vat_val - wht_val

                cur.execute(
                    """
                    INSERT INTO voucher_lines (
                        company_id,
                        voucher_id,
                        line_no,
                        description,
                        account_name,
                        amount,
                        vat_percent,
                        wht_percent,
                        vat_value,
                        wht_value,
                        total
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    """,
                    (
                        company_id,
                        voucher_id,
                        line_no,
                        desc,
                        acct_name,
                        amt,
                        vat_percent,
                        wht_percent,
                        vat_val,
                        wht_val,
                        total_val,
                    ),
                )
                line_no += 1

            # Audit
            log_action(
                conn=conn,
                company_id=company_id,
                username=username,
                action="CREATE",
                entity="voucher",
                ref=vnum,
                details=f"Created voucher with total payable {total_payable:.2f}",
            )

            conn.commit()
        return None
    except Exception as e:
        return f"Error creating voucher: {e}"


def update_voucher(
    company_id: int,
    voucher_id: int,
    username: str,
    vendor: Optional[str] = None,
    requester: Optional[str] = None,
    invoice_ref: Optional[str] = None,
    currency: Optional[str] = None,
    status: Optional[str] = None,
    lines: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """
    Update header + (optionally) lines for an existing voucher.
    Lines are replaced if a non-empty list is passed.
    """
    now_ts = _now_ts()

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            # Update header
            fields = []
            params: List[Any] = []

            if vendor is not None:
                fields.append("vendor = %s")
                params.append(vendor)
            if requester is not None:
                fields.append("requester = %s")
                params.append(requester)
            if invoice_ref is not None:
                fields.append("invoice_ref = %s")
                params.append(invoice_ref)
            if currency is not None:
                fields.append("currency = %s")
                params.append(currency)
            if status is not None:
                fields.append("status = %s")
                params.append(status)

            fields.append("last_modified = %s")
            params.append(now_ts)

            if fields:
                set_sql = ", ".join(fields)
                params.extend([company_id, voucher_id])
                cur.execute(
                    f"""
                    UPDATE vouchers
                    SET {set_sql}
                    WHERE company_id = %s
                      AND id = %s
                    """,
                    params,
                )

            # Replace lines if provided
            if lines is not None:
                cur.execute(
                    """
                    DELETE FROM voucher_lines
                    WHERE company_id = %s
                      AND voucher_id = %s
                    """,
                    (company_id, voucher_id),
                )

                line_no = 1
                for ln in lines:
                    desc = (ln.get("description") or "").strip()
                    acct_name = (ln.get("account_name") or "").strip()

                    try:
                        amt = float(ln.get("amount") or 0.0)
                    except Exception:
                        amt = 0.0
                    try:
                        vat_percent = float(ln.get("vat_percent") or 0.0)
                    except Exception:
                        vat_percent = 0.0
                    try:
                        wht_percent = float(ln.get("wht_percent") or 0.0)
                    except Exception:
                        wht_percent = 0.0

                    vat_val = amt * vat_percent / 100.0
                    wht_val = amt * wht_percent / 100.0
                    total_val = amt + vat_val - wht_val

                    cur.execute(
                        """
                        INSERT INTO voucher_lines (
                            company_id,
                            voucher_id,
                            line_no,
                            description,
                            account_name,
                            amount,
                            vat_percent,
                            wht_percent,
                            vat_value,
                            wht_value,
                            total
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )
                        """,
                        (
                            company_id,
                            voucher_id,
                            line_no,
                            desc,
                            acct_name,
                            amt,
                            vat_percent,
                            wht_percent,
                            vat_val,
                            wht_val,
                            total_val,
                        ),
                    )
                    line_no += 1

            # Fetch voucher_number for logging
            cur.execute(
                """
                SELECT voucher_number
                FROM vouchers
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, voucher_id),
            )
            row = cur.fetchone()
            vnum = row[0] if row else f"ID {voucher_id}"

            log_action(
                conn=conn,
                company_id=company_id,
                username=username,
                action="UPDATE",
                entity="voucher",
                ref=vnum,
                details="Updated voucher header/lines",
            )

            conn.commit()
        return None
    except Exception as e:
        return f"Error updating voucher: {e}"


def change_voucher_status(
    company_id: int,
    voucher_id: int,
    new_status: str,
    username: str,
) -> Optional[str]:
    """
    Change the status of a voucher (e.g. draft -> approved / void).
    """
    now_ts = _now_ts()
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE vouchers
                SET status = %s,
                    last_modified = %s
                WHERE company_id = %s
                  AND id = %s
                """,
                (new_status, now_ts, company_id, voucher_id),
            )

            cur.execute(
                """
                SELECT voucher_number
                FROM vouchers
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, voucher_id),
            )
            row = cur.fetchone()
            vnum = row[0] if row else f"ID {voucher_id}"

            log_action(
                conn=conn,
                company_id=company_id,
                username=username,
                action="STATUS",
                entity="voucher",
                ref=vnum,
                details=f"Changed status to {new_status}",
            )

            conn.commit()
        return None
    except Exception as e:
        return f"Error updating voucher status: {e}"


def delete_voucher(
    company_id: int,
    voucher_id: int,
    username: str,
) -> Optional[str]:
    """
    Hard-delete a voucher and its lines.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            # Get voucher number for audit before delete
            cur.execute(
                """
                SELECT voucher_number
                FROM vouchers
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, voucher_id),
            )
            row = cur.fetchone()
            vnum = row[0] if row else f"ID {voucher_id}"

            # Delete lines then header
            cur.execute(
                """
                DELETE FROM voucher_lines
                WHERE company_id = %s
                  AND voucher_id = %s
                """,
                (company_id, voucher_id),
            )
            cur.execute(
                """
                DELETE FROM vouchers
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, voucher_id),
            )

            log_action(
                conn=conn,
                company_id=company_id,
                username=username,
                action="DELETE",
                entity="voucher",
                ref=vnum,
                details="Deleted voucher and all lines",
            )

            conn.commit()
        return None
    except Exception as e:
        return f"Error deleting voucher: {e}"
