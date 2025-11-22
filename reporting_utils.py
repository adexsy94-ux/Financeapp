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
