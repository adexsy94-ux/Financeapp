"""
pdf_utils.py - Voucher PDF + Company Settings + Excel helpers

Drop-in module for your financeapp.

- Uses the same company_settings table (id = 1) as your old app.
- Exposes:
    get_company_settings()
    save_company_settings()
    embed_file()
    excel_download_link_multi()
    build_voucher_pdf_bytes()

build_voucher_pdf_bytes supports BOTH call styles:

  1) OLD STYLE (manual):
        pdf_bytes = build_voucher_pdf_bytes(settings, voucher_meta)
        pdf_bytes = build_voucher_pdf_bytes(settings, voucher_meta, line_rows)
        pdf_bytes = build_voucher_pdf_bytes(settings, voucher_meta, line_rows, attachment)

  2) NEW STYLE (simple DB-based):
        pdf_bytes = build_voucher_pdf_bytes(company_id, voucher_id)
        pdf_bytes = build_voucher_pdf_bytes(company_id=..., voucher_id=...)
"""

import base64
from contextlib import closing
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from db_config import connect

# ----------------------------------------------------------------------
# Optional PDF rendering libs for attachments
# ----------------------------------------------------------------------

# 1) PyMuPDF (preferred)
try:
    import fitz  # type: ignore
    PYMUPDF_OK = True
except Exception:
    PYMUPDF_OK = False

# 2) pdf2image fallback
try:
    from pdf2image import convert_from_bytes  # type: ignore
    PDF2IMAGE_OK = True
except Exception:
    PDF2IMAGE_OK = False


# ======================= GENERAL HELPERS =======================

def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ======================= COMPANY SETTINGS =======================

def get_company_settings() -> Dict[str, Any]:
    """Return the single company_settings row (id = 1)."""
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
    """Persist company header/footer/settings to the company_settings table."""
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
    """Show an uploaded file inline in Streamlit (PDF or image)."""
    if not data or not name:
        return

    b64 = base64.b64encode(data).decode("utf-8")
    lower = name.lower()

    if lower.endswith(".pdf"):
        html = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="600"></iframe>'
        st.markdown(html, unsafe_allow_html=True)
    elif lower.endswith((".jpg", ".jpeg", ".png")):
        try:
            st.image(BytesIO(data), use_column_width=True)
        except Exception:
            href = f"data:application/octet-stream;base64,{b64}"
            st.markdown(f'<a href="{href}" download="{name}">Download file</a>', unsafe_allow_html=True)
    else:
        href = f"data:application/octet-stream;base64,{b64}"
        st.markdown(f'<a href="{href}" download="{name}">Download file</a>', unsafe_allow_html=True)


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
    """Build a single Excel file (.xlsx) with multiple sheets using openpyxl (no xlsxwriter)."""
    df_invoices = _strip_tz(df_invoices)
    df_vouchers = _strip_tz(df_vouchers)
    df_lines = _strip_tz(df_lines)
    df_journal = _strip_tz(df_journal)
    df_audit = _strip_tz(df_audit)

    output = BytesIO()
    # openpyxl is bundled with pandas on Streamlit Cloud
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if not df_invoices.empty:
            df_invoices.to_excel(writer, sheet_name="Invoices", index=False)
        if not df_vouchers.empty:
            df_vouchers.to_excel(writer, sheet_name="Vouchers", index=False)
        if not df_lines.empty:
            df_lines.to_excel(writer, sheet_name="Line_Items", index=False)
        if not df_journal.empty:
            df_journal.to_excel(writer, sheet_name="General_Journal", index=False)
        if not df_audit.empty:
            df_audit.to_excel(writer, sheet_name="Audit_Trail", index=False)

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
    """Fetch a single voucher row as a dict."""
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


def _fetch_main_voucher_attachment(voucher_id: int) -> Optional[Tuple[str, bytes]]:
    """Fetch the most recent attachment for this voucher (if any).

    NOTE: your current DB error said voucher_documents has NO company_id column.
    So this SELECT deliberately does NOT use company_id – only voucher_id.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        try:
            cur.execute(
                """
                SELECT file_name, file_data
                FROM voucher_documents
                WHERE voucher_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (voucher_id,),
            )
            row = cur.fetchone()
        except Exception:
            row = None
    if not row:
        return None
    return row[0], row[1]


# ======================= MONEY + AMOUNT TO WORDS =======================

def money(amount: float, currency: str = "NGN") -> str:
    """Format amount with currency symbol/code (used for PDF)."""
    cur = (currency or "NGN").upper()
    if cur == "NGN":
        prefix = "₦"
    elif cur == "USD":
        prefix = "$"
    elif cur == "GBP":
        prefix = "£"
    elif cur == "EUR":
        prefix = "€"
    else:
        prefix = cur + " "
    try:
        amt = float(amount)
    except Exception:
        amt = 0.0
    return f"{prefix}{amt:,.2f}"


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
    """Convert a numeric amount to words with currency names."""
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


# ======================= ATTACHMENT HELPERS =======================

def _render_pdf_pages_to_pngs(file_bytes: bytes, max_pages: int = 4, dpi: int = 150) -> List[bytes]:
    """
    Try PyMuPDF first; if unavailable, try pdf2image.
    Returns a list of PNG bytes for up to max_pages pages.
    """
    images: List[bytes] = []

    if PYMUPDF_OK:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")  # type: ignore
            count = min(max_pages, len(doc))
            for i in range(count):
                page = doc.load_page(i)
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)  # type: ignore
                pix = page.get_pixmap(matrix=mat, alpha=False)
                images.append(pix.tobytes("png"))
            return images
        except Exception:
            pass

    if PDF2IMAGE_OK:
        try:
            pil_imgs = convert_from_bytes(file_bytes, dpi=dpi, fmt="png")  # type: ignore
            for img in pil_imgs[:max_pages]:
                buf = BytesIO()
                img.save(buf, format="PNG")
                images.append(buf.getvalue())
            return images
        except Exception:
            pass

    return images  # empty if neither works


def _normalize_to_pages(file_name: Optional[str], file_bytes: Optional[bytes]) -> List[bytes]:
    """
    Returns up to 4 PNG bytes representing pages 1..4.
    For JPG/PNG uploads: returns one image (page 1); remaining slots omitted.
    For PDFs: returns rendered pages (requires PyMuPDF or pdf2image).
    """
    if not file_bytes or not file_name:
        return []

    lower = file_name.lower()
    if lower.endswith(".pdf"):
        return _render_pdf_pages_to_pngs(file_bytes, max_pages=4, dpi=160)

    # image types
    if lower.endswith((".jpg", ".jpeg", ".png")):
        return [file_bytes]  # single "page"
    return []


# ======================= VOUCHER PDF CORE =======================

def _clean_currency_text(text: Any) -> str:
    """
    Replace '₦' with 'NGN ' for fonts that don't support the naira symbol.
    """
    try:
        s = str(text if text is not None else "")
    except Exception:
        s = ""
    return s.replace("₦", "NGN ")


def _register_fonts() -> Dict[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
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


def _build_voucher_pdf_from_struct(
    settings: Dict[str, Any],
    voucher_meta: Dict[str, Any],
    line_rows: List[Dict[str, Any]],
    attachment: Optional[Tuple[str, bytes]] = None,
) -> bytes:
    """Internal core used by BOTH calling styles."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
        Image,
    )

    fonts = _register_fonts()

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

    content_w = doc.width
    content_h = doc.height - (doc.topMargin + doc.bottomMargin)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HeadBig", fontName=fonts["bold"], fontSize=13, leading=16, alignment=1))
    styles.add(ParagraphStyle(name="HeadBold", fontName=fonts["bold"], fontSize=12, leading=14))
    styles.add(ParagraphStyle(name="Small", fontName=fonts["regular"], fontSize=9, leading=11))

    story: List[Any] = []

    # Header block
    name = settings.get("name") or ""
    addr_lines = (settings.get("addr") or "").splitlines()
    rc = settings.get("rc") or ""
    tin = settings.get("tin") or ""
    right_parts = [rc, tin] + addr_lines
    right_block = "<br/>".join([p for p in right_parts if p])

    from reportlab.platypus import Table  # already imported above but to keep linters happy

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

    # Title + voucher no
    title = settings.get("title") or "EFT/CHEQUE/CASH REQUISITION"
    story.append(Paragraph(title, styles["HeadBold"]))

    voucher_no = voucher_meta.get("voucher_number") or ""
    if voucher_no:
        story.append(Paragraph(f"VOUCHER NO: <b>{voucher_no}</b>", styles["Small"]))
    story.append(Spacer(1, 4))

    # Block 1: date / amount / requested / department
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
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(block1)
    story.append(Spacer(1, 4))

    # Block 2: payee / bank / account
    payable_to = voucher_meta.get("payable_to") or ""
    bank = voucher_meta.get("bank") or ""
    acc_no = voucher_meta.get("acc_no") or ""

    block2 = Table(
        [
            ["PAYABLE TO", payable_to, "BANK NAME", bank, "ACCOUNT NUMBER", acc_no],
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
        total = float(r.get("total", r.get("payable", (amt + vat - wht))) or 0.0)

        sum_amount += amt
        sum_vat += vat
        sum_wht += wht
        sum_payable += total

        inv_no_text = r.get("inv_no") or ""
        details_text = r.get("details") or ""

        rows.append(
            [
                str(inv_no_text),
                Paragraph(str(details_text), styles["Small"]),
                _clean_currency_text(r.get("amount_str", f"{amt:,.2f}")),
                _clean_currency_text(r.get("vat_str", f"{vat:,.2f}")),
                _clean_currency_text(r.get("wht_str", f"{wht:,.2f}" if wht else "-")),
                _clean_currency_text(r.get("payable_str", f"{total:,.2f}")),
            ]
        )

    # pad to at least 10 rows
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

    line_table = Table(
        rows,
        colWidths=[140, 300, 70, 70, 70, content_w - 140 - 300 - 70 - 70 - 70],
    )
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
    sig_table = Table(
        sig_rows,
        colWidths=[120, 360, 120, content_w - 120 - 360 - 120],
    )
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

    # Attachments → convert to pages and append up to 4 pages
    page_imgs: List[bytes] = []
    if attachment:
        try:
            # Single (name, bytes) tuple
            att_name, att_bytes = attachment
            if att_name and att_bytes:
                page_imgs = _normalize_to_pages(att_name, att_bytes)[:4]
        except Exception:
            page_imgs = []

    if page_imgs:
        avail_w = content_w
        # small headroom
        avail_h = content_h - 12

        for img_bytes in page_imgs:
            story.append(PageBreak())
            img = _scaled_image_flowable(img_bytes, max_w=avail_w, max_h=avail_h)
            story.append(img)

    doc.build(story)
    return buffer.getvalue()


def _scaled_image_flowable(img_bytes: bytes, max_w: float, max_h: float):
    """
    Scale an image to fit within max_w x max_h (points), preserving aspect ratio.
    Adds a small safety margin to avoid rare 0.1pt rounding overflows that cause LayoutError.
    """
    from reportlab.platypus import Image

    safety = 2.0  # points
    max_w_eff = max(1.0, max_w - safety)
    max_h_eff = max(1.0, max_h - safety)

    img = Image(BytesIO(img_bytes))
    iw, ih = img.drawWidth, img.drawHeight

    if iw <= 0 or ih <= 0:
        return img

    scale_w = max_w_eff / iw
    scale_h = max_h_eff / ih
    scale = min(scale_w, scale_h, 1.0)  # never upscale

    dw = iw * scale
    dh = ih * scale

    if dw > max_w_eff:
        dw = max_w_eff
    if dh > max_h_eff:
        dh = max_h_eff

    img.drawWidth = dw
    img.drawHeight = dh
    img.hAlign = "CENTER"
    return img


# ======================= PUBLIC WRAPPER =======================

def build_voucher_pdf_bytes(*args, **kwargs) -> bytes:
    """Flexible wrapper to avoid 'missing line_rows' TypeError.

    Supports:
      - build_voucher_pdf_bytes(company_id, voucher_id)
      - build_voucher_pdf_bytes(company_id=..., voucher_id=...)
      - build_voucher_pdf_bytes(settings, voucher_meta)
      - build_voucher_pdf_bytes(settings, voucher_meta, line_rows)
      - build_voucher_pdf_bytes(settings=..., voucher_meta=..., line_rows=..., attachment=...)
    """

    # Case 1: keyword style with company_id/voucher_id
    if "company_id" in kwargs and "voucher_id" in kwargs:
        company_id = int(kwargs["company_id"])
        voucher_id = int(kwargs["voucher_id"])

        settings = get_company_settings()
        voucher = _fetch_voucher(company_id, voucher_id)
        lines = _fetch_voucher_lines(company_id, voucher_id)
        attachment = _fetch_main_voucher_attachment(voucher_id)

        return _build_from_db_struct(settings, voucher, lines, attachment)

    # Case 2: positional (company_id, voucher_id)
    if len(args) == 2 and all(isinstance(a, int) for a in args):
        company_id, voucher_id = int(args[0]), int(args[1])

        settings = get_company_settings()
        voucher = _fetch_voucher(company_id, voucher_id)
        lines = _fetch_voucher_lines(company_id, voucher_id)
        attachment = _fetch_main_voucher_attachment(voucher_id)

        return _build_from_db_struct(settings, voucher, lines, attachment)

    # Case 3: old-style (settings, voucher_meta, [line_rows], [attachment])
    if "settings" in kwargs:
        settings = kwargs["settings"]
    else:
        if not args:
            raise TypeError("build_voucher_pdf_bytes: missing settings in old-style call")
        settings = args[0]

    if "voucher_meta" in kwargs:
        voucher_meta = kwargs["voucher_meta"]
    else:
        if len(args) < 2:
            raise TypeError("build_voucher_pdf_bytes: missing voucher_meta in old-style call")
        voucher_meta = args[1]

    if "line_rows" in kwargs:
        line_rows = kwargs["line_rows"]
    else:
        # THIS avoids your 'missing line_rows' crash:
        line_rows = args[2] if len(args) >= 3 else []

    if "attachment" in kwargs:
        attachment = kwargs["attachment"]
    else:
        attachment = args[3] if len(args) >= 4 else None

    return _build_voucher_pdf_from_struct(settings, voucher_meta, line_rows, attachment)


def _build_from_db_struct(
    settings: Dict[str, Any],
    voucher: Dict[str, Any],
    lines: List[Dict[str, Any]],
    attachment: Optional[Tuple[str, bytes]],
) -> bytes:
    """Convert DB rows into the struct expected by _build_voucher_pdf_from_struct.

    Here we also:
      - compute total payable
      - format amount_str with currency (so PDF shows currency)
      - propagate currency to lines if needed
    """

    # Date
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

    # Total payable
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
    amount_str = money(total_payable, currency)  # <<< CURRENCY SHOWN HERE

    voucher_meta: Dict[str, Any] = {
        "voucher_number": voucher.get("voucher_number") or f"V{voucher.get('id')}",
        "date_str": date_str,
        "amount_str": amount_str,
        "requested_by": voucher.get("requester") or "",
        "department": voucher.get("department") or "",
        "payable_to": voucher.get("vendor") or "",
        "bank": voucher.get("bank_name") or "",
        "acc_no": voucher.get("bank_account") or "",
        "authorizer": voucher.get("authorizer") or "",
        "approver": voucher.get("approver") or "",
        "currency": currency,
    }

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
                "amount_str": money(amt, currency),
                "vat_str": money(vat, currency) if vat else "0.00",
                "wht_str": money(wht, currency) if wht else "-",
                "payable_str": money(total, currency),
            }
        )

    return _build_voucher_pdf_from_struct(settings, voucher_meta, line_rows, attachment)
