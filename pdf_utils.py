# pdf_utils.py
# Simple PDF generation for vouchers (multi-tenant safe)

from io import BytesIO
from decimal import Decimal

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from vouchers_module import get_voucher_with_lines


def _to_float(v):
    if v is None:
        return 0.0
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except Exception:
        return 0.0


def build_voucher_pdf_bytes(company_id: int, voucher_id: int) -> bytes:
    header, lines = get_voucher_with_lines(company_id, voucher_id)
    if not header:
        raise ValueError(f"Voucher {voucher_id} not found for this company.")

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # --------------- Header ----------------
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, height - 25 * mm, "Payment Voucher")

    c.setFont("Helvetica", 10)
    y = height - 35 * mm

    voucher_number = header.get("voucher_number") or ""
    vendor = header.get("vendor") or ""
    requester = header.get("requester") or ""
    invoice_ref = header.get("invoice_ref") or ""
    status = header.get("status") or ""
    currency = header.get("currency") or "NGN"

    c.drawString(20 * mm, y, f"Voucher No: {voucher_number}")
    c.drawString(100 * mm, y, f"Status: {status}")
    y -= 6 * mm

    c.drawString(20 * mm, y, f"Vendor: {vendor}")
    y -= 6 * mm

    if requester:
        c.drawString(20 * mm, y, f"Requester: {requester}")
        y -= 6 * mm

    if invoice_ref:
        c.drawString(20 * mm, y, f"Invoice/Ref: {invoice_ref}")
        y -= 6 * mm

    c.drawString(20 * mm, y, f"Currency: {currency}")
    y -= 10 * mm

    # --------------- Lines table ----------------
    data = [
        ["Description", "Amount", "Account", "VAT %", "WHT %", "Total"],
    ]

    total_sum = 0.0
    for line in lines:
        desc = line.get("description") or ""
        amount = _to_float(line.get("amount"))
        account_name = line.get("account_name") or ""
        vat_percent = _to_float(line.get("vat_percent"))
        wht_percent = _to_float(line.get("wht_percent"))
        total = _to_float(line.get("total"))

        total_sum += total

        data.append(
            [
                desc,
                f"{amount:,.2f}",
                account_name,
                f"{vat_percent:,.2f}%",
                f"{wht_percent:,.2f}%",
                f"{total:,.2f}",
            ]
        )

    data.append(["", "", "", "", "Grand Total", f"{total_sum:,.2f}"])

    table = Table(
        data,
        colWidths=[60 * mm, 25 * mm, 40 * mm, 20 * mm, 20 * mm, 30 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )

    table.wrapOn(c, width - 40 * mm, y - 20 * mm)
    table_height = len(data) * 8 * mm
    table.drawOn(c, 20 * mm, max(20 * mm, y - table_height))

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.read()





# reports_module.py
# Reporting utilities for the multi-tenant finance app.

from contextlib import closing
from typing import Optional

import pandas as pd

from db_config import connect


def voucher_register(company_id: int) -> pd.DataFrame:
    """
    Voucher register (header + totals per voucher).
    Inspired by old app's register logic.
    """
    sql = """
    SELECT
        v.voucher_number,
        v.vendor,
        v.requester,
        v.invoice_ref,
        v.currency,
        v.status,
        v.approved_by,
        v.approved_at,
        v.last_modified,

        COALESCE(SUM(l.amount), 0) AS base_amount,
        COALESCE(SUM(l.vat_value), 0) AS total_vat,
        COALESCE(SUM(l.wht_value), 0) AS total_wht,
        COALESCE(SUM(l.total), 0) AS total_payable
    FROM vouchers v
    LEFT JOIN voucher_lines l
      ON v.id = l.voucher_id
    WHERE v.company_id = %s
    GROUP BY v.id
    ORDER BY v.last_modified DESC NULLS LAST, v.voucher_number
    """
    with closing(connect()) as conn:
        return pd.read_sql_query(sql, conn, params=(company_id,))


def invoice_register(company_id: int) -> pd.DataFrame:
    """
    Invoice register (header with totals).
    """
    sql = """
    SELECT
        invoice_number,
        vendor_invoice_number,
        vendor,
        summary,
        currency,
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
        last_modified
    FROM invoices
    WHERE company_id = %s
    ORDER BY last_modified DESC NULLS LAST, invoice_number
    """
    with closing(connect()) as conn:
        return pd.read_sql_query(sql, conn, params=(company_id,))


def vendor_summary(company_id: int) -> pd.DataFrame:
    """
    Vendor-level summary combining invoices + vouchers.
    Total billed, total vouchers, etc.
    """
    sql = """
    WITH inv AS (
        SELECT
            vendor,
            COALESCE(SUM(total_amount), 0) AS total_invoiced
        FROM invoices
        WHERE company_id = %s
        GROUP BY vendor
    ),
    vch AS (
        SELECT
            vendor,
            COALESCE(SUM(l.total), 0) AS total_vouchered
        FROM vouchers v
        LEFT JOIN voucher_lines l ON v.id = l.voucher_id
        WHERE v.company_id = %s
        GROUP BY vendor
    )
    SELECT
        COALESCE(inv.vendor, vch.vendor) AS vendor,
        COALESCE(total_invoiced, 0) AS total_invoiced,
        COALESCE(total_vouchered, 0) AS total_vouchered,
        COALESCE(total_invoiced, 0) - COALESCE(total_vouchered, 0) AS balance
    FROM inv
    FULL OUTER JOIN vch
      ON inv.vendor = vch.vendor
    ORDER BY vendor
    """
    with closing(connect()) as conn:
        return pd.read_sql_query(sql, conn, params=(company_id, company_id))


def account_activity(company_id: int, account_name: Optional[str] = None) -> pd.DataFrame:
    """
    Simple account activity for expense/asset accounts based on voucher lines.
    (You can expand this to a full GL later.)
    """
    params = [company_id]
    where_account = ""
    if account_name:
        where_account = "AND l.account_name = %s"
        params.append(account_name)

    sql = f"""
    SELECT
        v.voucher_number,
        v.vendor,
        v.requester,
        v.invoice_ref,
        v.currency,
        v.status,
        v.last_modified,
        l.account_name,
        l.description,
        l.amount,
        l.vat_value,
        l.wht_value,
        l.total
    FROM vouchers v
    JOIN voucher_lines l
      ON v.id = l.voucher_id
    WHERE v.company_id = %s
      {where_account}
    ORDER BY v.last_modified DESC NULLS LAST, v.voucher_number, l.line_no
    """
    with closing(connect()) as conn:
        return pd.read_sql_query(sql, conn, params=tuple(params))




requirements.txt
streamlit>=1.28
pandas>=1.5
numpy
psycopg2-binary
reportlab
python-dotenv
openpyxl>=3.0.10
pillow
plotly
pdf2image
pymupdf
bcrypt





VoucherPro – Module Diagram (Text Version)

[PostgreSQL Database]
    ├── users
    ├── vouchers
    ├── voucher_lines
    ├── invoices
    ├── vendors
    ├── accounts
    └── audit_log

[db_config.py]
    ├── connect()
    ├── init_schema()
    └── log_action()

[auth_module.py]
    ├── create_user()
    ├── verify_user()
    ├── get_user_record()
    ├── list_users()
    ├── update_user_permissions()
    ├── current_user()
    ├── require_login()
    ├── require_admin()
    └── require_permission()

[crm_gateway.py]
    ├── list_vendors()
    ├── upsert_vendor()
    ├── list_accounts(type)
    └── upsert_account()

[vouchers_module.py]
    ├── list_vouchers()
    ├── get_voucher()
    ├── list_voucher_lines()
    └── create_voucher()

[invoices_module.py]
    ├── list_invoices()
    ├── get_invoice()
    └── create_invoice()

[pdf_utils.py]
    └── build_voucher_pdf_bytes(voucher_id)

[reporting_utils.py]
    ├── money(x)
    └── excel_download_link_multi()

[app_main.py]  (Streamlit UI)
    ├── app_vouchers()
    ├── app_invoices()
    ├── app_crm()
    ├── app_user_management()
    ├── app_db_browser()
    └── main()

Data Flow (simplified):

User ──> Streamlit UI (app_main.py)
           ├── Auth via auth_module -> db_config -> PostgreSQL (users)
           ├── Vouchers via vouchers_module -> db_config -> PostgreSQL (vouchers, voucher_lines)
           ├── Invoices via invoices_module -> db_config -> PostgreSQL (invoices)
           ├── CRM via crm_gateway -> db_config -> PostgreSQL (vendors, accounts)
           ├── PDF export via pdf_utils -> vouchers_module
           └── Audit logging via db_config.log_action() -> PostgreSQL (audit_log)







VoucherPro – Module Diagram (Text Version)

[PostgreSQL Database]
    ├── users
    ├── vouchers
    ├── voucher_lines
    ├── invoices
    ├── vendors
    ├── accounts
    └── audit_log

[db_config.py]
    ├── connect()
    ├── init_schema()
    └── log_action()

[auth_module.py]
    ├── create_user()
    ├── verify_user()
    ├── get_user_record()
    ├── list_users()
    ├── update_user_permissions()
    ├── current_user()
    ├── require_login()
    ├── require_admin()
    └── require_permission()

[crm_gateway.py]
    ├── list_vendors()
    ├── upsert_vendor()
    ├── list_accounts(type)
    └── upsert_account()

[vouchers_module.py]
    ├── list_vouchers()
    ├── get_voucher()
    ├── list_voucher_lines()
    └── create_voucher()

[invoices_module.py]
    ├── list_invoices()
    ├── get_invoice()
    └── create_invoice()

[pdf_utils.py]
    └── build_voucher_pdf_bytes(voucher_id)

[reporting_utils.py]
    ├── money(x)
    └── excel_download_link_multi()

[app_main.py]  (Streamlit UI)
    ├── app_vouchers()
    ├── app_invoices()
    ├── app_crm()
    ├── app_user_management()
    ├── app_db_browser()
    └── main()

Data Flow (simplified):

User ──> Streamlit UI (app_main.py)
           ├── Auth via auth_module -> db_config -> PostgreSQL (users)
           ├── Vouchers via vouchers_module -> db_config -> PostgreSQL (vouchers, voucher_lines)
           ├── Invoices via invoices_module -> db_config -> PostgreSQL (invoices)
           ├── CRM via crm_gateway -> db_config -> PostgreSQL (vendors, accounts)
           ├── PDF export via pdf_utils -> vouchers_module
           └── Audit logging via db_config.log_action() -> PostgreSQL (audit_log)







