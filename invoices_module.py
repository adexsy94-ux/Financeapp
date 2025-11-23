# invoices_module.py
# Multi-tenant invoices: totals, CRM vendor linking, accounts.

from contextlib import closing
from datetime import datetime
from typing import Dict, List, Optional

from db_config import connect, INVOICE_TABLE_SQL, log_action
from crm_gateway import (
    get_vendor_name_list,
    get_payable_account_options,
    get_expense_asset_account_options,
)


def init_invoice_schema() -> None:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(INVOICE_TABLE_SQL)
        conn.commit()


# ------------------------
# Helpers
# ------------------------

def _now_ts() -> datetime:
    return datetime.utcnow()


def compute_invoice_totals(
    vatable_amount: float,
    non_vatable_amount: float,
    vat_rate: float,
    wht_rate: float,
) -> Dict[str, float]:
    vatable = float(vatable_amount or 0.0)
    non_vatable = float(non_vatable_amount or 0.0)
    vat_rate = float(vat_rate or 0.0)
    wht_rate = float(wht_rate or 0.0)

    vat_amount = round(vatable * vat_rate / 100.0, 2)
    wht_amount = round(vatable * wht_rate / 100.0, 2)
    subtotal = vatable + non_vatable
    total_amount = subtotal + vat_amount - wht_amount

    return {
        "vat_amount": vat_amount,
        "wht_amount": wht_amount,
        "subtotal": subtotal,
        "total_amount": total_amount,
    }


# ------------------------
# Queries
# ------------------------

def list_invoices(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id, parent_id, version,
                invoice_number, vendor_invoice_number,
                vendor, summary,
                vatable_amount, non_vatable_amount,
                vat_rate, wht_rate,
                vat_amount, wht_amount,
                subtotal, total_amount,
                payable_account, expense_asset_account,
                currency, last_modified
            FROM invoices
            WHERE company_id = %s
            ORDER BY last_modified DESC NULLS LAST, id DESC
            """,
            (company_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_invoice(company_id: int, invoice_id: int) -> Optional[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT *
            FROM invoices
            WHERE company_id = %s
              AND id = %s
            """,
            (company_id, invoice_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


# ------------------------
# Creation
# ------------------------

def create_invoice(
    company_id: int,
    username: str,
    invoice_number: str,
    vendor_invoice_number: Optional[str],
    vendor: str,
    summary: Optional[str],
    vatable_amount: float,
    non_vatable_amount: float,
    vat_rate: float,
    wht_rate: float,
    terms: Optional[str],
    payable_account: Optional[str],
    expense_asset_account: Optional[str],
    currency: str = "NGN",
    file_name: Optional[str] = None,
    file_bytes: Optional[bytes] = None,
) -> Optional[str]:
    """
    Create an invoice.

    NOTE: invoice_number is auto-generated if blank, using UTC timestamp:
          INV-YYYYMMDDHHMMSS
    """
    # --- Auto-generate invoice number if blank, using date/time stamp ---
    invoice_number = (invoice_number or "").strip()
    if not invoice_number:
        ts_code = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        invoice_number = f"INV-{ts_code}"

    vendor_invoice_number = (vendor_invoice_number or "").strip() or None
    vendor = (vendor or "").strip()
    summary = (summary or "").strip() or None
    terms = (terms or "").strip() or None
    payable_account = (payable_account or "").strip() or None
    expense_asset_account = (expense_asset_account or "").strip() or None
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        return "Vendor is required."

    # Check vendor exists in CRM
    vendor_opts = get_vendor_name_list(company_id)
    if vendor not in vendor_opts:
        return f"Vendor '{vendor}' not found in CRM. Please create it first in the CRM tab."

    # Soft validate accounts using CRM chart of accounts
    payables = get_payable_account_options(company_id)
    expenses = get_expense_asset_account_options(company_id)

    if payable_account and payable_account not in payables:
        return f"Payable account '{payable_account}' is not in Chart of Accounts for this company."

    if expense_asset_account and expense_asset_account not in expenses:
        return f"Expense/Asset account '{expense_asset_account}' is not in Chart of Accounts for this company."

    totals = compute_invoice_totals(
        vatable_amount=vatable_amount,
        non_vatable_amount=non_vatable_amount,
        vat_rate=vat_rate,
        wht_rate=wht_rate,
    )

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            ts = _now_ts()
            cur.execute(
                """
                INSERT INTO invoices (
                    parent_id, version, company_id,
                    invoice_number, vendor_invoice_number,
                    vendor, summary,
                    vatable_amount, non_vatable_amount,
                    vat_rate, wht_rate,
                    vat_amount, wht_amount,
                    subtotal, total_amount,
                    terms,
                    payable_account,
                    expense_asset_account,
                    currency,
                    file_name,
                    file_data,
                    last_modified
                )
                VALUES (
                    NULL, 1, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s,
                    %s, %s,
                    %s,
                    %s, %s,
                    %s
                )
                """,
                (
                    company_id,
                    invoice_number,
                    vendor_invoice_number,
                    vendor,
                    summary,
                    float(vatable_amount or 0.0),
                    float(non_vatable_amount or 0.0),
                    float(vat_rate or 0.0),
                    float(wht_rate or 0.0),
                    totals["vat_amount"],
                    totals["wht_amount"],
                    totals["subtotal"],
                    totals["total_amount"],
                    terms,
                    payable_account,
                    expense_asset_account,
                    currency,
                    file_name,
                    file_bytes,
                    ts,
                ),
            )
            conn.commit()

        log_action(
            username,
            "create_invoice",
            "invoices",
            ref=invoice_number,
            company_id=company_id,
        )
        return None
    except Exception as ex:
        return f"Error creating invoice: {ex}"
