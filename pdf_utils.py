"""
pdf_utils.py - Voucher PDF + Company Settings helpers (VoucherPro-style layout)

This module is designed for your financeapp. It:
- Stores and loads company settings from the company_settings table.
- Provides embed_file() and excel_download_link_multi() helpers.
- Generates voucher PDFs with build_voucher_pdf_bytes(company_id, voucher_id)
  using a layout similar to your original VoucherPro app.
"""

import base64
from contextlib import closing
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from db_config import connect


# ======================= GENERAL HELPERS =======================

def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ======================= COMPANY SETTINGS =======================

def get_company_settings() -> Dict[str, Any]:
    """
    Return the single company_settings row (id = 1).

    This matches your original behaviour: one global company header that
    appears on the voucher PDF (name, RC, TIN, address, title, etc).
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT name, rc, tin, addr, title,
                   authorizer_label, approval_label,
                   authorizer_name, approver_name,
                   department_default,
                   company_doc_name, company_doc_data
            FROM company_settings
            WHERE id = 1
            """
        )
        row = cur.fetchone()
    if not row:
        return {}
    keys = [
        "name",
        "rc",
        "tin",
        "addr",
        "title",
        "authorizer_label",
        "approval_label",
        "authorizer_name",
        "approver_name",
        "department_default",
        "company_doc_name",
        "company_doc_data",
    ]
    return dict(zip(keys, row))


def save_company_settings(settings: Dict[str, Any]) -> None:
    """
    Persist company header/footer/settings to the company_settings table.

    Called from the Company Settings tab so that the UI and voucher PDF
    share the same configuration.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE company_settings
            SET name=%s, rc=%s, tin=%s, addr=%s, title=%s,
                authorizer_label=%s, approval_label=%s,
                authorizer_name=%s, approver_name=%s,
                department_default=%s,
                company_doc_name=%s, company_doc_data=%s
            WHERE id = 1
            """,
            (
                (settings.get("name") or "").strip(),
                (settings.get("rc") or "").strip(),
                (settings.get("tin") or "").strip(),
                (settings.get("addr") or "").strip(),
                (settings.get("title") or "").strip(),
                (settings.get("authorizer_label") or "").strip(),
                (settings.get("approval_label") or "").strip(),
                (settings.get("authorizer_name") or "").strip(),
                (settings.get("approver_name") or "").strip(),
                (settings.get("department_default") or "").strip(),
                settings.get("company_doc_name"),
                settings.get("company_doc_data"),
            ),
        )
        conn.commit()


# ======================= STREAMLIT HELPERS =======================

def embed_file(name: str, data: Optional[bytes]) -> None:
    """
    Show an uploaded file inline in Streamlit.

    PDF  -> iframe preview
    JPG/PNG -> st.image
    """
    if not data or not name:
        return

    b64 = base64.b64encode(data).decode("utf-8")

    lower = name.lower()
    if lower.endswith(".pdf"):
        # Inline PDF preview
        html = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="600"></iframe>'
        st.markdown(html, unsafe_allow_html=True)
    elif lower.endswith((".jpg", ".jpeg", ".png")):
        try:
            st.image(BytesIO(data), use_column_width=True)
        except Exception:
            href = f"data:application/octet-stream;base64,{b64}"
            st.markdown(
                f'<a href="{href}" download="{name}">Download file</a>',
                unsafe_allow_html=True,
            )
    else:
        href = f"data:application/octet-stream;base64,{b64}"
        st.markdown(
            f'<a href="{href}" download="{name}">Download file</a>',
            unsafe_allow_html=True,
        )


def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    """Remove timezone info to avoid Excel writer issues."""
    try:
        df = df.copy()
        dt_tz_cols = df.select_dtypes(include=["datetimetz"]).columns
        for col in dt_tz_cols:
            df[col] = df[col].dt.tz_convert(None)
    except Exception:
        pass
    return df


def excel_download_link_multi(
    df_invoices: pd.DataFrame,
    df_vouchers: pd.DataFrame,
    df_lines: pd.DataFrame,
    df_journal: pd.DataFrame,
    df_audit: pd.DataFrame,
    filename: str = "VoucherPro_Report",
) -> str:
    """
    Build a single Excel file with multiple sheets and return an HTML download button.
    Sheets: Invoices, Vouchers, Line_Items, General_Journal, Audit_Trail.
    """
    df_invoices = _strip_tz(df_invoices)
    df_vouchers = _strip_tz(df_vouchers)
    df_lines = _strip_tz(df_lines)
    df_journal = _strip_tz(df_journal)
    df_audit = _strip_tz(df_audit)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_invoices.to_excel(writer, index=False, sheet_name="Invoices")
        df_vouchers.to_excel(writer, index=False, sheet_name="Vouchers")
        df_lines.to_excel(writer, index=False, sheet_name="Line_Items")
        df_journal.to_excel(writer, index=False, sheet_name="General_Journal")
        df_audit.to_excel(writer, index=False, sheet_name="Audit_Trail")
    output.seek(0)

    b64 = base64.b64encode(output.read()).decode("utf-8")
    stamp = datetime.now().strftime("%Y%m%d")
    return (
        f'<a href="data:application/octet-stream;base64,{b64}" '
        f'download="{filename}_{stamp}.xlsx">'
        f'<button>Download Excel (Invoices/Vouchers/Lines/Journal/Audit)</button></a>'
    )


# ======================= DB HELPERS FOR VOUCHERS =======================

def _fetch_voucher(company_id: int, voucher_id: int) -> Dict[str, Any]:
    """Fetch a single voucher row as a dict. Very defensive about column names."""
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            "SELECT * FROM vouchers WHERE company_id = %s AND id = %s",
            (company_id, voucher_id),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Voucher {voucher_id} not found for company {company_id}")
        cols = [c[0] for c in cur.description]
    return dict(zip(cols, row))


def _fetch_voucher_lines(company_id: int, voucher_id: int) -> List[Dict[str, Any]]:
    """Fetch all voucher_lines for a voucher as a list of dicts."""
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            SELECT *
            FROM voucher_lines
            WHERE company_id = %s AND voucher_id = %s
            ORDER BY line_no, id
            """,
            (company_id, voucher_id),
        )
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def _fetch_main_voucher_attachment(company_id: int, voucher_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetch the most recent attachment for this voucher, if any.
    Returns {"file_name": ..., "file_data": ...} or None.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        try:
            cur.execute(
                """
                SELECT file_name, file_data
                FROM voucher_documents
                WHERE company_id = %s AND voucher_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (company_id, voucher_id),
            )
            row = cur.fetchone()
        except Exception:
            row = None
    if not row:
        return None
    return {"file_name": row[0], "file_data": row[1]}


# ======================= AMOUNT TO WORDS =======================

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
TEENS = [
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
THOUSANDS = ["", "thousand", "million", "billion", "trillion"]


def _chunk_to_words(n: int) -> str:
    parts: List[str] = []
    h, rem = divmod(n, 100)
    if h:
        parts.append(ONES[h] + " hundred")
        if rem:
            parts.append("and")
    if rem >= 20:
        t, o = divmod(rem, 10)
        parts.append(TENS[t])
        if o:
            parts.append(ONES[o])
    elif rem >= 10:
        parts.append(TEENS[rem - 10])
    elif rem > 0:
        parts.append(ONES[rem])
    return " ".join(parts) if parts else "zero"


def _int_to_words(n: int) -> str:
    if n == 0:
        return "zero"
    words: List[str] = []
    i = 0
    while n > 0 and i < len(THOUSANDS):
        n, chunk = divmod(n, 1000)
        if chunk:
            label = THOUSANDS[i]
            chunk_words = _chunk_to_words(chunk)
            if label:
                words.append(f"{chunk_words} {label}")
            else:
                words.append(chunk_words)
        i += 1
    return " ".join(reversed(words))


def amount_to_words(amount: float, currency: str = "NGN") -> str:
    """Convert a numeric amount to words for the given currency."""
    try:
        amt = round(float(amount) + 1e-9, 2)
    except Exception:
        amt = 0.0
    major = int(amt)
    minor = int(round((amt - major) * 100))

    cur = (currency or "NGN").upper()
    if cur == "NGN":
        major_name, minor_name = "naira", "kobo"
    elif cur == "USD":
        major_name, minor_name = "dollars", "cents"
    elif cur == "GBP":
        major_name, minor_name = "pounds", "pence"
    elif cur == "EUR":
        major_name, minor_name = "euros", "cents"
    else:
        major_name, minor_name = cur.lower(), "cents"

    words = _int_to_words(major)
    words = words[0].upper() + words[1:] if words else "Zero"

    if minor > 0:
        minor_words = _int_to_words(minor)
        minor_words = minor_words[0].upper() + minor_words[1:]
        return f"{words} {major_name}, {minor_words} {minor_name} only."
    else:
        return f"{words} {major_name} only."


# ======================= VOUCHER PDF CORE =======================

def _clean_currency_text(text: Any) -> str:
    """Replace Naira symbol with 'NGN ' so it renders on systems without that glyph."""
    try:
        s = str(text if text is not None else "")
    except Exception:
        s = ""
    return s.replace("₦", "NGN ")


def _build_voucher_pdf_from_struct(
    settings: Dict[str, Any],
    voucher_meta: Dict[str, Any],
    line_rows: List[Dict[str, Any]],
    attachment: Optional[Dict[str, Any]] = None,
) -> bytes:
    """
    Internal core: expect fully prepared settings, voucher_meta and line_rows.
    attachment is {"file_name": ..., "file_data": ...} or None.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )

    # Fonts
    def _register_fonts() -> Dict[str, str]:
        import os

        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "C:\\Windows\\Fonts\\DejaVuSans.ttf",
            "C:\\Windows\\Fonts\\DejaVuSans-Bold.ttf",
            "/Library/Fonts/DejaVuSans.ttf",
            "/Library/Fonts/DejaVuSans-Bold.ttf",
        ]
        reg = bold = None
        for p in candidates:
            if os.path.exists(p) and p.lower().endswith("dejavusans.ttf"):
                reg = p
            if os.path.exists(p) and p.lower().endswith("dejavusans-bold.ttf"):
                bold = p
        try:
            if reg:
                pdfmetrics.registerFont(TTFont("DejaVuSans", reg))
                if bold:
                    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
                    return {"regular": "DejaVuSans", "bold": "DejaVuSans-Bold"}
                return {"regular": "DejaVuSans", "bold": "DejaVuSans"}
        except Exception:
            pass
        return {"regular": "Helvetica", "bold": "Helvetica-Bold"}

    fonts = _register_fonts()

    # Layout
    buffer = BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=10 * mm,
        rightMargin=10 * mm,
        topMargin=4 * mm,
        bottomMargin=4 * mm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HeadBig", fontName=fonts["bold"], fontSize=13, leading=16, alignment=1))
    styles.add(ParagraphStyle(name="HeadBold", fontName=fonts["bold"], fontSize=12, leading=14))
    styles.add(ParagraphStyle(name="Small", fontName=fonts["regular"], fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="SmallBold", fontName=fonts["bold"], fontSize=9, leading=11))

    story: List[Any] = []

    # Header block
    name = settings.get("name") or ""
    addr_lines = (settings.get("addr") or "").splitlines()
    rc = settings.get("rc") or ""
    tin = settings.get("tin") or ""
    right_block_parts = [rc, tin] + addr_lines
    right_block = "<br/>".join([p for p in right_block_parts if p])

    top_table = Table(
        [
            [
                Paragraph(f"<b>{name}</b>", styles["HeadBig"]),
                Paragraph(right_block, styles["Small"]),
            ]
        ],
        colWidths=[0.6 * doc.width, 0.4 * doc.width],
    )
    top_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(top_table)
    story.append(Spacer(1, 4))

    # Title + voucher number
    title = settings.get("title") or "EFT/CHEQUE/CASH REQUISITION"
    story.append(Paragraph(title, styles["HeadBold"]))

    voucher_no = voucher_meta.get("voucher_number") or ""
    if voucher_no:
        story.append(Paragraph(f"VOUCHER NO: <b>{voucher_no}</b>", styles["Small"]))
    story.append(Spacer(1, 4))

    # Block 1: Date / Amount / Requested / Department
    date_str = voucher_meta.get("date_str") or ""
    amount_str = _clean_currency_text(voucher_meta.get("amount_str") or "")
    requested_by = voucher_meta.get("requested_by") or ""
    department = voucher_meta.get("department") or ""

    block1 = Table(
        [
            ["DATE", date_str, "AMOUNT", amount_str],
            ["REQUESTED BY", requested_by, "DEPARTMENT", department],
        ],
        colWidths=[90, 320, 100, doc.width - 90 - 320 - 100],
    )
    block1.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), fonts["regular"]),
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("ALIGN", (3, 0), (3, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(block1)
    story.append(Spacer(1, 4))

    # Block 2: Payee / Bank / Account
    payable_to = voucher_meta.get("payable_to") or ""
    bank = voucher_meta.get("bank") or ""
    acc_no = voucher_meta.get("acc_no") or ""

    block2 = Table(
        [
            [
                "PAYABLE TO",
                payable_to,
                "BANK NAME",
                bank,
                "ACCOUNT NUMBER",
                acc_no,
            ]
        ],
        colWidths=[95, 220, 95, 200, 120, doc.width - 95 - 220 - 95 - 200 - 120],
    )
    block2.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), fonts["regular"]),
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(block2)
    story.append(Spacer(1, 6))

    # Line items table
    header = ["INV NO.", "DETAILS", "AMOUNT", "VAT AMT", "WHT AMT", "PAYABLE AMT"]
    rows: List[List[Any]] = [header]

    sum_amount = 0.0
    sum_vat = 0.0
    sum_wht = 0.0
    sum_payable = 0.0

    for r in line_rows:
        amt = float(r.get("amount", r.get("_amount", 0.0)) or 0.0)
        vat = float(r.get("vat", r.get("_vat", 0.0)) or 0.0)
        wht = float(r.get("wht", r.get("_wht", 0.0)) or 0.0)
        payable = float(r.get("total", r.get("payable", (amt + vat - wht))) or 0.0)

        sum_amount += amt
        sum_vat += vat
        sum_wht += wht
        sum_payable += payable

        inv_no_text = r.get("inv_no") or ""
        details_text = r.get("details") or ""

        rows.append(
            [
                str(inv_no_text),
                Paragraph(str(details_text), styles["Small"]),
                _clean_currency_text(r.get("amount_str", f"{amt:,.2f}")),
                _clean_currency_text(r.get("vat_str", f"{vat:,.2f}")),
                _clean_currency_text(r.get("wht_str", f"{wht:,.2f}" if wht else "-")),
                _clean_currency_text(r.get("payable_str", f"{payable:,.2f}")),
            ]
        )

    # Ensure at least a few blank rows for neatness
    while len(rows) < 10:
        rows.append(["", "", "", "", "", ""])

    rows.append(
        [
            "",
            "TOTALS",
            _clean_currency_text(f"{sum_amount:,.2f}"),
            _clean_currency_text(f"{sum_vat:,.2f}"),
            _clean_currency_text(f"{sum_wht:,.2f}" if sum_wht else "-"),
            _clean_currency_text(f"{sum_payable:,.2f}"),
        ]
    )

    line_table = Table(rows, colWidths=[140, 300, 70, 70, 70, doc.width - 140 - 300 - 70 - 70 - 70])
    line_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), fonts["bold"]),
                ("FONTNAME", (0, 1), (-1, -1), fonts["regular"]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (2, 1), (5, -1), "RIGHT"),
            ]
        )
    )
    story.append(line_table)
    story.append(Spacer(1, 4))

    # Amount in words
    currency = voucher_meta.get("currency") or "NGN"
    amt_words = amount_to_words(sum_payable, currency)
    story.append(Paragraph(f"<b>Payable amount (in words):</b> {amt_words}", styles["Small"]))
    story.append(Spacer(1, 6))

    # Signatures
    requested_by = voucher_meta.get("requested_by") or ""
    authorised_name = voucher_meta.get("authorizer") or settings.get("authorizer_name") or ""
    approved_name = voucher_meta.get("approver") or settings.get("approver_name") or ""
    date_val = voucher_meta.get("date_str") or ""

    sig_rows = [
        ["Activity", "Name", "Date", "Signature"],
        ["Requested by", requested_by, date_val, ""],
        ["Authorised by", authorised_name, date_val, ""],
        ["Approved by", approved_name, date_val, ""],
    ]
    sig_table = Table(sig_rows, colWidths=[120, 360, 120, doc.width - 120 - 360 - 120])
    sig_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), fonts["bold"]),
                ("FONTNAME", (0, 1), (-1, -1), fonts["regular"]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (3, 1), (3, -1), 18),
                ("BOTTOMPADDING", (3, 1), (3, -1), 18),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(sig_table)

    # Optional: simple extra page showing the first attachment as-is (no conversion)
    if attachment and attachment.get("file_name") and attachment.get("file_data"):
        story.append(PageBreak())
        story.append(
            Paragraph(
                f"Attached document stored in the system: {attachment['file_name']}",
                styles["Small"],
            )
        )

    doc.build(story)
    return buffer.getvalue()


# ======================= PUBLIC API =======================

def build_voucher_pdf_bytes(
    company_id: int,
    voucher_id: int,
) -> bytes:
    """
    Public function used by app_main.py:

        pdf_bytes = build_voucher_pdf_bytes(company_id, voucher_id)

    It loads all data from the database, builds the voucher_meta and line_rows
    and then calls the core _build_voucher_pdf_from_struct().
    """
    settings = get_company_settings()
    voucher = _fetch_voucher(company_id, voucher_id)
    lines = _fetch_voucher_lines(company_id, voucher_id)
    attachment = _fetch_main_voucher_attachment(company_id, voucher_id)

    # Build voucher_meta
    date_val = (
        voucher.get("date")
        or voucher.get("voucher_date")
        or voucher.get("created_at")
        or voucher.get("last_modified")
    )
    if isinstance(date_val, datetime):
        date_str = date_val.strftime("%Y-%m-%d")
    else:
        date_str = str(date_val or "")

    # Compute total payable from lines if not stored
    if lines:
        total_payable = 0.0
        for ln in lines:
            amt = float(ln.get("amount") or 0.0)
            vat = float(ln.get("vat_value") or 0.0)
            wht = float(ln.get("wht_value") or 0.0)
            total_payable += (amt + vat - wht)
    else:
        try:
            total_payable = float(voucher.get("payable_total") or 0.0)
        except Exception:
            total_payable = 0.0

    currency = voucher.get("currency") or "NGN"

    voucher_meta: Dict[str, Any] = {
        "voucher_number": voucher.get("voucher_number") or f"V{voucher_id}",
        "date_str": date_str,
        "amount_str": f"{total_payable:,.2f}",
        "requested_by": voucher.get("requester") or "",
        "department": voucher.get("department") or "",
        "payable_to": voucher.get("vendor") or "",
        "bank": voucher.get("bank_name") or "",
        "acc_no": voucher.get("bank_account") or "",
        "authorizer": voucher.get("authorizer") or "",
        "approver": voucher.get("approver") or "",
        "currency": currency,
    }

    # Build line_rows
    line_rows: List[Dict[str, Any]] = []
    for ln in lines:
        amt = float(ln.get("amount") or 0.0)
        vat = float(ln.get("vat_value") or 0.0)
        wht = float(ln.get("wht_value") or 0.0)
        total = float(ln.get("total") or (amt + vat - wht))

        inv_no = (
            ln.get("invoice")
            or ln.get("invoice_ref")
            or ln.get("invoice_number")
            or ""
        )

        line_rows.append(
            {
                "inv_no": inv_no,
                "details": ln.get("description") or "",
                "amount": amt,
                "vat": vat,
                "wht": wht,
                "total": total,
                "amount_str": f"{amt:,.2f}",
                "vat_str": f"{vat:,.2f}",
                "wht_str": f"{wht:,.2f}" if wht else "-",
                "payable_str": f"{total:,.2f}",
            }
        )

    return _build_voucher_pdf_from_struct(
        settings=settings,
        voucher_meta=voucher_meta,
        line_rows=line_rows,
        attachment=attachment,
    )
