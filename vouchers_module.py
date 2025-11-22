# vouchers_module.py
# Voucher CRUD, queries, and status updates (multi-tenant)

from contextlib import closing
from typing import List, Dict, Optional

from db_config import connect, log_action


def list_vouchers(company_id: int, limit: int = 100) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT *
            FROM vouchers
            WHERE company_id = %s
            ORDER BY id DESC
            LIMIT %s
            """
            ,
            (company_id, limit),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_voucher(company_id: int, voucher_id: int) -> Optional[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT * FROM vouchers WHERE id = %s AND company_id = %s",
            (voucher_id, company_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def list_voucher_lines(voucher_id: int) -> List[Dict]:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT *
            FROM voucher_lines
            WHERE voucher_id = %s
            ORDER BY id
            """
            ,
            (voucher_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def create_voucher(
    company_id: int,
    voucher_number: str,
    vendor: str,
    requester: str,
    invoice: str,
    lines: List[Dict],
    username: str = "",
    file_name: Optional[str] = None,
    file_data: Optional[bytes] = None,
) -> int:
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            INSERT INTO vouchers (
                company_id,
                voucher_number,
                vendor,
                requester,
                invoice,
                file_name,
                file_data,
                last_modified,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 'draft')
            RETURNING id
            """
            ,
            (company_id, voucher_number, vendor, requester, invoice, file_name, file_data),
        )
        vid = cur.fetchone()["id"]

        for line in lines:
            amount = float(line.get("amount") or 0)
            vat_percent = float(line.get("vat_percent") or 0)
            wht_percent = float(line.get("wht_percent") or 0)
            vat_value = amount * vat_percent / 100.0
            wht_value = amount * wht_percent / 100.0
            total = amount + vat_value - wht_value
            cur.execute(
                """
                INSERT INTO voucher_lines (
                    voucher_id, description, amount, expense_account,
                    vat_percent, wht_percent, vat_value, wht_value, total
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                ,
                (
                    vid,
                    line.get("description", ""),
                    amount,
                    line.get("expense_account", ""),
                    vat_percent,
                    wht_percent,
                    vat_value,
                    wht_value,
                    total,
                ),
            )

        conn.commit()

    log_action(username, "create_voucher", "vouchers", ref=str(vid),
               details=f"company_id={company_id}")
    return vid


def update_voucher_status(
    company_id: int,
    voucher_id: int,
    new_status: str,
    actor_username: str,
    as_approver: bool = False,
) -> Optional[str]:
    if new_status not in ("draft", "submitted", "approved", "rejected"):
        return "Invalid status."

    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        if as_approver and new_status == "approved":
            cur.execute(
                """
                UPDATE vouchers
                SET status = %s,
                    approved_by = %s,
                    approved_at = CURRENT_TIMESTAMP,
                    last_modified = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND company_id = %s
                """
                ,
                (new_status, actor_username, voucher_id, company_id),
            )
        else:
            cur.execute(
                """
                UPDATE vouchers
                SET status = %s,
                    last_modified = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND company_id = %s
                """
                ,
                (new_status, voucher_id, company_id),
            )
        conn.commit()

    log_action(
        actor_username,
        "update_voucher_status",
        "vouchers",
        ref=str(voucher_id),
        details=f"company_id={company_id}, status={new_status}, approver={as_approver}",
    )
    return None
