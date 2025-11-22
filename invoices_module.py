# invoices_module.py
# Invoice CRUD and queries (multi-tenant)

from contextlib import closing
from typing import List, Dict, Optional

from db_config import connect, log_action


def list_invoices(company_id: int, limit: int = 100) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT *
            FROM invoices
            WHERE company_id = %s
            ORDER BY id DESC
            LIMIT %s
            """
            ,
            (company_id, limit),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_invoice(company_id: int, invoice_id: int) -> Optional[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT * FROM invoices WHERE id = %s AND company_id = %s",
            (invoice_id, company_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def create_invoice(
    company_id: int,
    invoice_number: str,
    vendor_invoice_number: str,
    vendor: str,
    summary: str,
    vatable_amount: float,
    vat_rate: float,
    wht_rate: float,
    non_vatable_amount: float,
    terms: str,
    payable_account: str,
    expense_asset_account: str,
    currency: str,
    username: str = "",
    file_name: Optional[str] = None,
    file_data: Optional[bytes] = None,
) -> int:
    vat_amount = vatable_amount * vat_rate / 100.0
    wht_amount = vatable_amount * wht_rate / 100.0
    subtotal = vatable_amount + non_vatable_amount
    total_amount = subtotal + vat_amount - wht_amount

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO invoices (
                company_id,
                invoice_number, vendor_invoice_number, vendor, summary,
                vatable_amount, vat_rate, wht_rate,
                vat_amount, wht_amount,
                non_vatable_amount, subtotal, total_amount,
                terms, last_modified,
                payable_account, expense_asset_account,
                currency, file_name, file_data
            )
            VALUES (
                %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, CURRENT_TIMESTAMP,
                %s, %s,
                %s, %s, %s
            )
            RETURNING id
            """
            ,
            (
                company_id,
                invoice_number,
                vendor_invoice_number,
                vendor,
                summary,
                vatable_amount,
                vat_rate,
                wht_rate,
                vat_amount,
                wht_amount,
                non_vatable_amount,
                subtotal,
                total_amount,
                terms,
                payable_account,
                expense_asset_account,
                currency,
                file_name,
                file_data,
            ),
        )
        iid = cur.fetchone()["id"]
        conn.commit()

    log_action(username, "create_invoice", "invoices", ref=str(iid),
               details=f"company_id={company_id}")
    return iid
