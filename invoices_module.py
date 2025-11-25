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
    """Ensure the invoices table exists."""
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
    """
    Compute VAT, WHT, subtotal and total based on base amounts and rates.
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


def _generate_invoice_number() -> str:
    """
    Simple timestamp-based invoice number.
    """
    now = datetime.utcnow()
    return now.strftime("INV-%Y%m%d%H%M%S")


# ------------------------
# Create invoice
# ------------------------

def create_invoice(
    company_id: int,
    invoice_number: str,
    vendor_invoice_number: Optional[str],
    vendor: str,
    summary: Optional[str],
    vatable_amount: float,
    vat_rate: float,
    wht_rate: float,
    non_vatable_amount: float,
    terms: Optional[str],
    payable_account: Optional[str],
    expense_asset_account: Optional[str],
    currency: str,
    username: str,
    file_name: Optional[str],
    file_data: Optional[bytes],
) -> int:
    """
    Create a new invoice and return its ID.

    app_main.py calls this with invoice_number="",
    so we auto-generate the invoice_number if blank.
    """

    invoice_number = (invoice_number or "").strip()
    if not invoice_number:
        invoice_number = _generate_invoice_number()

    vendor_invoice_number = (vendor_invoice_number or "").strip() or None
    vendor = (vendor or "").strip()
    summary = (summary or "").strip() or None
    terms = (terms or "").strip() or None
    payable_account = (payable_account or "").strip() or None
    expense_asset_account = (expense_asset_account or "").strip() or None
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        raise ValueError("Vendor is required.")

    # Validate vendor exists in CRM
    vendor_opts = get_vendor_name_list(company_id)
    if vendor not in vendor_opts:
        raise ValueError(
            f"Vendor '{vendor}' not found in CRM. Please create it first in the CRM tab."
        )

    # Soft-validate accounts (if provided)
    payables = get_payable_account_options(company_id)
    expenses = get_expense_asset_account_options(company_id)

    if payable_account and payable_account not in payables:
        raise ValueError(
            f"Payable account '{payable_account}' is not in Chart of Accounts for this company."
        )
    if expense_asset_account and expense_asset_account not in expenses:
        raise ValueError(
            f"Expense/Asset account '{expense_asset_account}' is not in Chart of Accounts for this company."
        )

    totals = compute_invoice_totals(
        vatable_amount=vatable_amount,
        non_vatable_amount=non_vatable_amount,
        vat_rate=vat_rate,
        wht_rate=wht_rate,
    )

    ts = _now_ts()

    try:
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
                    file_data,
                    last_modified
                ) VALUES (
                    %s,
                    NULL,
                    1,
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
                RETURNING id
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
                    file_data,
                    ts,
                ),
            )
            (iid,) = cur.fetchone()
            conn.commit()

        # Audit log
        log_action(
            username,
            "create_invoice",
            "invoices",
            ref=invoice_number,
            company_id=company_id,
        )

        return iid
    except Exception as ex:
        raise RuntimeError(f"Error creating invoice: {ex}") from ex


# ------------------------
# List / query invoices
# ------------------------

def list_invoices(company_id: int) -> List[Dict]:
    """
    List invoices for a company.

    IMPORTANT: only select columns that actually exist in the DB schema:
    there is NO 'created_at' column on the invoices table, only 'last_modified'.
    """
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
                terms,
                payable_account,
                expense_asset_account,
                currency,
                file_name,
                last_modified
            FROM invoices
            WHERE company_id = %s
            ORDER BY last_modified DESC NULLS LAST, id DESC
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
            terms,
            payable_account,
            expense_asset_account,
            currency,
            file_name,
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
                "terms": terms,
                "payable_account": payable_account,
                "expense_asset_account": expense_asset_account,
                "currency": currency,
                "file_name": file_name,
                "last_modified": last_modified,
            }
        )
    return result


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
    vat_rate: float,
    wht_rate: float,
    non_vatable_amount: float,
    terms: Optional[str],
    payable_account: Optional[str],
    expense_asset_account: Optional[str],
    currency: str,
    username: str,
    file_name: Optional[str],
    file_data: Optional[bytes],
) -> Optional[str]:
    """
    Update an existing invoice.
    Returns None on success, or an error message.
    """
    vendor_invoice_number = (vendor_invoice_number or "").strip() or None
    vendor = (vendor or "").strip()
    summary = (summary or "").strip() or None
    terms = (terms or "").strip() or None
    payable_account = (payable_account or "").strip() or None
    expense_asset_account = (expense_asset_account or "").strip() or None
    currency = (currency or "").strip() or "NGN"

    if not vendor:
        return "Vendor is required."

    # Validate vendor exists in CRM
    vendor_opts = get_vendor_name_list(company_id)
    if vendor not in vendor_opts:
        return (
            f"Vendor '{vendor}' not found in CRM. Please create it first in the CRM tab."
        )

    # Soft-validate accounts (if provided)
    payables = get_payable_account_options(company_id)
    expenses = get_expense_asset_account_options(company_id)

    if payable_account and payable_account not in payables:
        return (
            f"Payable account '{payable_account}' is not in Chart of Accounts for this company."
        )
    if expense_asset_account and expense_asset_account not in expenses:
        return (
            f"Expense/Asset account '{expense_asset_account}' is not in Chart of Accounts for this company."
        )

    totals = compute_invoice_totals(
        vatable_amount=vatable_amount,
        non_vatable_amount=non_vatable_amount,
        vat_rate=vat_rate,
        wht_rate=wht_rate,
    )

    ts = _now_ts()

    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            if file_name is not None and file_data is not None:
                cur.execute(
                    """
                    UPDATE invoices
                    SET vendor_invoice_number = %s,
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
                        file_name             = %s,
                        file_data             = %s,
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
                        file_name,
                        file_data,
                        ts,
                        company_id,
                        invoice_id,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE invoices
                    SET vendor_invoice_number = %s,
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
    Delete an invoice. (Hard delete.)
    """
    try:
        with closing(connect()) as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                DELETE FROM invoices
                WHERE company_id = %s
                  AND id         = %s
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






