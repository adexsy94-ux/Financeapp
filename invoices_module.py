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


# ------------------------
# Schema init
# ------------------------

def init_invoice_schema() -> None:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(INVOICE_TABLE_SQL)
        conn.commit()


# ------------------------
# Helpers
# ------------------------

def _now_ts() -> datetime:
    return datetime.utcnow()


def generate_invoice_number(company_id: int) -> str:
    """
    Generate a unique invoice number based on timestamp.
    """
    now = datetime.utcnow()
    return now.strftime("INV-%Y%m%d%H%M%S")


def compute_invoice_totals(
    vatable_amount: float,
    non_vatable_amount: float,
    vat_rate: float,
    wht_rate: float,
) -> Dict[str, float]:
    """
    Compute VAT, WHT, subtotal, total.
    """
    vatable_amount = float(vatable_amount or 0.0)
    non_vatable_amount = float(non_vatable_amount or 0.0)
    vat_rate = float(vat_rate or 0.0)
    wht_rate = float(wht_rate or 0.0)

    vat_amount = round(vatable_amount * vat_rate / 100.0, 2)
    wht_amount = round(vatable_amount * wht_rate / 100.0, 2)

    subtotal = vatable_amount + non_vatable_amount
    total_amount = subtotal + vat_amount - wht_amount

    return {
        "vat_amount": vat_amount,
        "wht_amount": wht_amount,
        "subtotal": subtotal,
        "total_amount": total_amount,
    }


# ------------------------
# Create
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
    currency: str,
    file_name: Optional[str],
    file_bytes: Optional[bytes],
) -> Optional[str]:
    """
    Create a new invoice.

    If invoice_number is blank, an auto timestamp-based number is generated.
    """
    vendor = (vendor or "").strip()
    vendor_invoice_number = (vendor_invoice_number or "").strip() or None
    summary = (summary or "").strip() or None
    terms = (terms or "").strip() or None
    payable_account = (payable_account or "").strip() or None
    expense_asset_account = (expense_asset_account or "").strip() or None
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        return "Vendor is required."

    # Validate vendor exists in CRM
    vendor_list = get_vendor_name_list(company_id)
    if vendor not in vendor_list:
        return f"Vendor '{vendor}' not found in CRM. Please create it first in the CRM tab."

    # Soft validate accounts (if provided)
    payables = get_payable_account_options(company_id)
    expenses = get_expense_asset_account_options(company_id)

    if payable_account and payable_account not in payables:
        return f"Payable account '{payable_account}' is not in Chart of Accounts for this company."
    if expense_asset_account and expense_asset_account not in expenses:
        return f"Expense/Asset account '{expense_asset_account}' is not in Chart of Accounts for this company."

    if not invoice_number:
        invoice_number = generate_invoice_number(company_id)

    totals = compute_invoice_totals(
        vatable_amount=vatable_amount,
        non_vatable_amount=non_vatable_amount,
        vat_rate=vat_rate,
        wht_rate=wht_rate,
    )

    try:
        ts = _now_ts()
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO invoices (
                    company_id,
                    parent_id,
                    version,
                    invoice_number,
                    vendor_invoice_number,
                    vendor,
                    summary,
                    vatable_amount,
                    non_vatable_amount,
                    vat_rate,
                    wht_rate,
                    vat_amount,
                    wht_amount,
                    subtotal,
                    total_amount,
                    terms,
                    payable_account,
                    expense_asset_account,
                    currency,
                    file_name,
                    file_bytes,
                    created_at,
                    last_modified
                ) VALUES (
                    %s, NULL, 1,
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
                    %s, %s
                )
                """,
                (
                    company_id,
                    invoice_number,
                    vendor_invoice_number,
                    vendor,
                    summary,
                    vatable_amount,
                    non_vatable_amount,
                    vat_rate,
                    wht_rate,
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


# ------------------------
# Update / delete
# ------------------------

def update_invoice(
    company_id: int,
    invoice_id: int,
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
    currency: str,
    username: str,
) -> Optional[str]:
    """
    Update invoice header for a given company + invoice id.
    Recomputes VAT/WHT/Subtotal/Total using current amounts and rates.
    """

    vendor = (vendor or "").strip()
    vendor_invoice_number = (vendor_invoice_number or "").strip() or None
    summary = (summary or "").strip() or None
    terms = (terms or "").strip() or None
    payable_account = (payable_account or "").strip() or None
    expense_asset_account = (expense_asset_account or "").strip() or None
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        return "Vendor is required."

    vendor_opts = get_vendor_name_list(company_id)
    if vendor not in vendor_opts:
        return f"Vendor '{vendor}' not found in CRM. Please create it first in the CRM tab."

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
        ts = _now_ts()
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE invoices
                SET
                    vendor_invoice_number = %s,
                    vendor                = %s,
                    summary               = %s,
                    vatable_amount        = %s,
                    non_vatable_amount    = %s,
                    vat_rate              = %s,
                    wht_rate              = %s,
                    vat_amount            = %s,
                    wht_amount            = %s,
                    subtotal              = %s,
                    total_amount          = %s,
                    terms                 = %s,
                    payable_account       = %s,
                    expense_asset_account = %s,
                    currency              = %s,
                    last_modified         = %s
                WHERE company_id = %s
                  AND id         = %s
                """,
                (
                    vendor_invoice_number,
                    vendor,
                    summary,
                    vatable_amount,
                    non_vatable_amount,
                    vat_rate,
                    wht_rate,
                    totals["vat_amount"],
                    totals["wht_amount"],
                    totals["subtotal"],
                    totals["total_amount"],
                    terms,
                    payable_account,
                    expense_asset_account,
                    currency,
                    ts,
                    company_id,
                    invoice_id,
                ),
            )
            conn.commit()

        log_action(
            username,
            "update_invoice",
            "invoices",
            ref=str(invoice_id),
            company_id=company_id,
        )
        return None
    except Exception as ex:
        return f"Error updating invoice: {ex}"


def delete_invoice(
    company_id: int,
    invoice_id: int,
    username: str,
) -> Optional[str]:
    """
    Hard-delete an invoice row.
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                DELETE FROM invoices
                WHERE company_id = %s
                  AND id = %s
                """,
                (company_id, invoice_id),
            )
            conn.commit()

        log_action(
            username,
            "delete_invoice",
            "invoices",
            ref=str(invoice_id),
            company_id=company_id,
        )
        return None
    except Exception as ex:
        return f"Error deleting invoice: {ex}"


# ------------------------
# Queries
# ------------------------

def list_invoices(company_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT
                id,
                parent_id,
                version,
                invoice_number,
                vendor_invoice_number,
                vendor,
                summary,
                vatable_amount,
                non_vatable_amount,
                vat_rate,
                wht_rate,
                vat_amount,
                wht_amount,
                subtotal,
                total_amount,
                payable_account,
                expense_asset_account,
                currency,
                file_name,
                created_at,
                last_modified
            FROM invoices
            WHERE company_id = %s
            ORDER BY last_modified DESC, id DESC
            """,
            (company_id,),
        )
        rows = cur.fetchall()

    result: List[Dict] = []
    for r in rows:
        (
            iid,
            parent_id,
            version,
            invoice_number,
            vendor_invoice_number,
            vendor,
            summary,
            vatable_amount,
            non_vatable_amount,
            vat_rate,
            wht_rate,
            vat_amount,
            wht_amount,
            subtotal,
            total_amount,
            payable_account,
            expense_asset_account,
            currency,
            file_name,
            created_at,
            last_modified,
        ) = r
        result.append(
            {
                "id": iid,
                "parent_id": parent_id,
                "version": version,
                "invoice_number": invoice_number,
                "vendor_invoice_number": vendor_invoice_number,
                "vendor": vendor,
                "summary": summary,
                "vatable_amount": vatable_amount,
                "non_vatable_amount": non_vatable_amount,
                "vat_rate": vat_rate,
                "wht_rate": wht_rate,
                "vat_amount": vat_amount,
                "wht_amount": wht_amount,
                "subtotal": subtotal,
                "total_amount": total_amount,
                "payable_account": payable_account,
                "expense_asset_account": expense_asset_account,
                "currency": currency,
                "file_name": file_name,
                "created_at": created_at,
                "last_modified": last_modified,
            }
        )
    return result
