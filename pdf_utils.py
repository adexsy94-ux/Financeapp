
"""pdf_utils.py - Voucher PDF + Company Settings helpers (VoucherPro layout)

This module is designed for your new financeapp but uses the SAME PDF layout,
company settings, and embed/Excel helpers from your old VoucherPro code.
"""

import base64
from contextlib import closing
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

from db_config import connect


# ======================= COMPANY SETTINGS =======================

def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_company_settings() -> Dict[str, Any]:
    """Return the single company_settings row (id = 1).

    This matches your old VoucherPro behaviour so the same settings
    will appear in the PDF header (name, RC, TIN, address, title, etc).
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
    """Persist company header/footer/settings to the company_settings table.

    This is copied from your old code so the UI + PDF share the same configuration.
    """
    with closing(connect()) as conn, closing(conn.cursor()) as cur:
        cur.execute(
            """
            UPDATE company_settings
            SET name=%s, rc=%s, tin=%s, addr=%s, title=%s,
                authorizer_label=%s, approval_label=%s,
                authorizer_name=%s, approver_name=%s, department_default=%s,
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


# ======================= EMBED + EXCEL HELPERS =======================

def embed_file(name: str, data: Optional[bytes]) -> None:
    """Show an uploaded file inline in Streamlit.

    PDF  -> iframe preview
    JPG/PNG -> st.image
    """
    if not data or not name:
        return

    b64 = base64.b64encode(data).decode()

    if name.lower().endswith(".pdf"):
        # Inline PDF preview
        html = f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="600"></iframe>'
        st.markdown(html, unsafe_allow_html=True)
    else:
        # Assume image
        try:
            st.image(BytesIO(data), use_column_width=True)
        except Exception:
            # Fallback: simple download link if image rendering fails
            href = f"data:application/octet-stream;base64,{b64}"
            st.markdown(f'<a href="{href}" download="{name}">Download file</a>', unsafe_allow_html=True)


def excel_download_link_multi(
    df_invoices: pd.DataFrame,
    df_vouchers: pd.DataFrame,
    df_lines: pd.DataFrame,
    df_journal: pd.DataFrame,
    df_audit: pd.DataFrame,
    filename: str = "VoucherPro_Report",
) -> str:
    """Build a single Excel file with multiple sheets and return an HTML download button.

    This is the same helper you used before, now using openpyxl as the engine.
    """

    def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
        try:
            df = df.copy()
            dt_tz_cols = df.select_dtypes(include=["datetimetz"]).columns
            for col in dt_tz_cols:
                df[col] = df[col].dt.tz_convert(None)
        except Exception:
            pass
        return df

    df_invoices = _strip_tz(df_invoices)
    df_vouchers = _strip_tz(df_vouchers)
    df_lines = _strip_tz(df_lines)
    df_journal = _strip_tz(df_journal)
    df_audit = _strip_tz(df_audit)

    output = BytesIO()
    # openpyxl is already part of your requirements from the old app
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_invoices.to_excel(writer, index=False, sheet_name="Invoices")
        df_vouchers.to_excel(writer, index=False, sheet_name="Vouchers")
        df_lines.to_excel(writer, index=False, sheet_name="Line_Items")
        df_journal.to_excel(writer, index=False, sheet_name="General_Journal")
        df_audit.to_excel(writer, index=False, sheet_name="Audit_Trail")
    output.seek(0)
    b64 = base64.b64encode(output.read()).decode()
    stamp = datetime.now().strftime("%Y%m%d")
    return (
        f'<a href="data:application/octet-stream;base64,{b64}" '
        f'download="{filename}_{stamp}.xlsx">'
        f'<button>Download Excel (Invoices/Vouchers/Lines/Journal/Audit)</button></a>'
    )


# ======================= OPTIONAL: CRM LOOKUP (for bank details) =======================

def _crm_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Read CRM tables from the same PostgreSQL DB using connect().

    This is only used to enrich voucher PDFs with vendor bank details.
    If the query fails for any reason, it just returns an empty DataFrame.
    """
    try:
        with closing(connect()) as conn:
            return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


# ======================= ATTACHMENT IMAGE HELPERS =======================

try:
    import fitz  # PyMuPDF
    PYMUPDF_OK = True
except Exception:  # pragma: no cover - optional dependency
    PYMUPDF_OK = False

try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_OK = True
except Exception:  # pragma: no cover - optional dependency
    PDF2IMAGE_OK = False


def _render_pdf_pages_to_pngs(file_bytes: bytes, max_pages: int = 4, dpi: int = 150) -> List[bytes]:
    """Try PyMuPDF first; if unavailable, try pdf2image.

    Returns a list of PNG bytes for up to max_pages pages.
    """
    images: List[bytes] = []

    if PYMUPDF_OK:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            count = min(max_pages, len(doc))
            for i in range(count):
                page = doc.load_page(i)
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                images.append(pix.tobytes("png"))
            doc.close()
            return images
        except Exception:
            pass

    if PDF2IMAGE_OK:
        try:
            pil_imgs = convert_from_bytes(file_bytes, dpi=dpi, fmt="png")
            for img in pil_imgs[:max_pages]:
                buf = BytesIO()
                img.save(buf, format="PNG")
                images.append(buf.getvalue())
            return images
        except Exception:
            pass

    return images


def _normalize_to_pages(file_name: Optional[str], file_bytes: Optional[bytes]) -> List[bytes]:
    """Normalize a PDF or image upload into up to 4 page images (PNG bytes)."""
    if not file_bytes or not file_name:
        return []

    lower = file_name.lower()
    if lower.endswith(".pdf"):
        return _render_pdf_pages_to_pngs(file_bytes, max_pages=4, dpi=160)

    if lower.endswith((".jpg", ".jpeg", ".png")):
        return [file_bytes]
    return []


from reportlab.platypus import Image as RLImage  # for _scaled_image_flowable


def _scaled_image_flowable(img_bytes: bytes, max_w: float, max_h: float) -> RLImage:
    """Scale an image to fit within max_w x max_h (points), preserving aspect ratio."""
    safety = 2.0  # points
    max_w_eff = max(1.0, max_w - safety)
    max_h_eff = max(1.0, max_h - safety)

    img = RLImage(BytesIO(img_bytes))
    iw = float(getattr(img, "imageWidth", 0) or 0)
    ih = float(getattr(img, "imageHeight", 0) or 0)

    if iw <= 0 or ih <= 0:
        img.hAlign = "CENTER"
        return img

    scale = min(max_w_eff / iw, max_h_eff / ih)
    if not (0 < scale < 10000):
        scale = 1.0

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


# ======================= VOUCHER PDF BUILDER (VoucherPro layout) =======================

def build_voucher_pdf_bytes(
    settings: Dict[str, Any],
    voucher_meta: Dict[str, Any],
    line_rows: List[Dict[str, Any]],
    attachment: Optional[Tuple[Optional[str], Optional[bytes]]] = None,
) -> bytes:
    """Generate a voucher PDF using the exact layout from your old VoucherPro app.

    * Page 1: Header (company settings), voucher details, line items, totals, amount in words,
      and signatures.
    * Pages 2–5: At most 4 pages from the attached document (PDF or image), one per page.

    The `settings` dict must come from `get_company_settings()` so that whatever you
    configure on the Company Settings tab appears automatically in the PDF header.
    """

    # --- Enrich voucher_meta with vendor bank details from CRM (optional) ---
    try:
        payee = voucher_meta.get("payable_to")
        if payee:
            vend_row = _crm_df(
                "SELECT website, contact_person, bank_name, bank_account, notes FROM vendors WHERE name = %s",
                (str(payee),),
            )
            if not vend_row.empty:
                row0 = vend_row.iloc[0]
                voucher_meta["website"] = (row0.get("website") or "") or ""
                voucher_meta["contact_person"] = (row0.get("contact_person") or "") or ""
                voucher_meta["bank"] = (row0.get("bank_name") or "") or ""
                voucher_meta["acc_no"] = (row0.get("bank_account") or "") or ""
                voucher_meta["vendor_notes"] = (row0.get("notes") or "") or ""
    except Exception:
        # Fail silently; PDF will just omit bank details if lookup fails
        pass

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        Flowable,
        PageBreak,
        KeepTogether,
    )
    from reportlab.lib import colors

    # ---------- helpers ----------
    def scale_widths(widths: List[float], content_w: float) -> List[float]:
        s = sum(widths) or 1.0
        return [w * content_w / s for w in widths]

    def _clean_currency_text(text: Any) -> str:
        """Replace Naira symbol with 'NGN ' so it renders on systems without that glyph."""
        try:
            s = str(text if text is not None else "")
        except Exception:
            s = ""
        return s.replace("₦", "NGN ")

    def register_unicode_font() -> Dict[str, str]:
        import os as _os

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
            if _os.path.exists(p) and p.lower().endswith("dejavusans.ttf"):
                reg = p
            if _os.path.exists(p) and p.lower().endswith("dejavusans-bold.ttf"):
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

    # ---------- number to words ----------
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
    THOUSANDS = ["", "thousand", "million", "billion", "trillion", "quadrillion"]

    def chunk_to_words(n: int) -> str:
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

    def int_to_words(n: int) -> str:
        if n == 0:
            return "zero"
        words: List[str] = []
        i = 0
        while n > 0 and i < len(THOUSANDS):
            n, chunk = divmod(n, 1000)
            if chunk:
                label = THOUSANDS[i]
                chunk_words = chunk_to_words(chunk)
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

        words = int_to_words(major)
        words = words[0].upper() + words[1:] if words else "Zero"

        if minor > 0:
            minor_words = int_to_words(minor)
            minor_words = minor_words[0].upper() + minor_words[1:]
            return f"{words} {major_name}, {minor_words} {minor_name} only."
        else:
            return f"{words} {major_name} only."

    # ---------- fonts & layout ----------
    font_names = register_unicode_font()
    MM = 72.0 / 25.4
    left_margin = 10 * MM
    right_margin = 10 * MM
    top_margin = 4 * MM
    bottom_margin = 4 * MM

    page_size = landscape(A4)
    PAGE_W, PAGE_H = page_size
    content_w = PAGE_W - left_margin - right_margin
    content_h = PAGE_H - top_margin - bottom_margin

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        leftMargin=left_margin,
        rightMargin=right_margin,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
    )

    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HeadBold", fontName=font_names["bold"], fontSize=12, leading=14))
    styles.add(ParagraphStyle(name="HeadBig", fontName=font_names["bold"], fontSize=13, leading=16, alignment=1))
    styles.add(ParagraphStyle(name="Small", fontName=font_names["regular"], fontSize=9, leading=11))
    styles.add(ParagraphStyle(name="SmallBold", fontName=font_names["bold"], fontSize=9, leading=11))

    story: List[Flowable] = []

    # ---------- Header block ----------
    addr_lines = (settings.get("addr") or "").splitlines()
    right_block = "<br/>".join(filter(None, [settings.get("rc", ""), settings.get("tin", ""), *addr_lines]))
    t0_colw = scale_widths([0.58, 0.42], content_w)
    top_table = Table(
        [
            [
                Paragraph(f"<b>{settings.get('name','')}</b>", styles["HeadBig"]),
                Paragraph(right_block, styles["Small"]),
            ]
        ],
        colWidths=t0_colw,
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

    # Title + Voucher No
    story.append(Paragraph(settings.get("title", "EFT/CHEQUE/CASH REQUISITION"), styles["HeadBold"]))
    voucher_no = voucher_meta.get("voucher_number", "")
    if voucher_no:
        story.append(Paragraph(f"VOUCHER NO: <b>{voucher_no}</b>", styles["Small"]))
    story.append(Spacer(1, 4))

    # ---------- Table 1: Date/Amount + Requested/Dept ----------
    t1_colw = scale_widths([90, 320, 100, 218], content_w)
    block1 = Table(
        [
            ["DATE", voucher_meta.get("date_str", ""), "AMOUNT", _clean_currency_text(voucher_meta.get("amount_str", ""))],
            ["REQUESTED. BY", voucher_meta.get("requested_by", ""), "DEPARTMENT", voucher_meta.get("department", "")],
        ],
        colWidths=t1_colw,
    )
    block1.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), font_names["regular"]),
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("ALIGN", (1, 0), (1, 0), "LEFT"),
                ("ALIGN", (3, 0), (3, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(block1)
    story.append(Spacer(1, 4))

    # ---------- Table 2: Bank/payee strip ----------
    t2_colw = scale_widths([95, 220, 95, 200, 120, 98], content_w)
    block2 = Table(
        [
            [
                "PAYABLE TO",
                voucher_meta.get("payable_to", ""),
                "BANK NAME",
                voucher_meta.get("bank", ""),
                "ACCOUNT NUMBER",
                voucher_meta.get("acc_no", ""),
            ]
        ],
        colWidths=t2_colw,
    )
    block2.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("FONTNAME", (0, 0), (-1, -1), font_names["regular"]),
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(block2)
    story.append(Spacer(1, 6))

    # ---------- Table 3: Line items ----------
    t3_colw = scale_widths([140, 300, 70, 70, 70, 58], content_w)

    header = ["INV NO.", "DETAILS", "AMOUNT", "VAT AMT", "WHT AMT", "PAYABLE AMT"]
    rows: List[List[Any]] = [header]

    sum_amount = 0.0
    sum_vat = 0.0
    sum_wht = 0.0
    sum_payable = 0.0

    for r in line_rows:
        amt = float(r.get("_amount", 0.0) or 0.0)
        vat = float(r.get("_vat", 0.0) or 0.0)
        wht = float(r.get("_wht", 0.0) or 0.0)
        payable = (amt + vat) - wht

        sum_amount += amt
        sum_vat += vat
        sum_wht += wht
        sum_payable += payable

        inv_no_text = r.get("inv_no", "")
        details_text = r.get("details", "")

        from reportlab.platypus import Paragraph as RLParagraph

        inv_no_para = RLParagraph(str(inv_no_text), styles["Small"])
        details_para = RLParagraph(str(details_text), styles["Small"])

        rows.append(
            [
                inv_no_para,
                details_para,
                _clean_currency_text(r.get("amount_str", f"{amt:,.2f}")),
                _clean_currency_text(r.get("vat_str", f"{vat:,.2f}")),
                _clean_currency_text(r.get("wht_str", f"{wht:,.2f}" if wht else "-")),
                _clean_currency_text(r.get("payable_str", f"{payable:,.2f}")),
            ]
        )

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

    line_table = Table(rows, colWidths=t3_colw, repeatRows=1)
    line_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), font_names["bold"]),
                ("FONTNAME", (0, 1), (-1, -1), font_names["regular"]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("ALIGN", (2, 1), (5, -1), "RIGHT"),
            ]
        )
    )
    story.append(line_table)
    story.append(Spacer(1, 4))

    voucher_currency = (voucher_meta.get("currency") or "NGN") if isinstance(voucher_meta, dict) else "NGN"
    amt_words = amount_to_words(sum_payable, voucher_currency)
    story.append(Paragraph(f"<b>Payable amount (in words):</b> {amt_words}", styles["Small"]))
    story.append(Spacer(1, 6))

    # ---------- Signatures ----------
    sig_headers = ["Activity", "Name", "Date", "Signature"]
    requested_by = voucher_meta.get("requested_by", "")
    authorised_name = voucher_meta.get("authorizer", settings.get("authorizer_name", ""))
    approved_name = voucher_meta.get("approver", settings.get("approver_name", ""))
    date_val = voucher_meta.get("date_str", "")

    t4_colw = scale_widths([120, 360, 120, 128], content_w)
    sig_rows = [
        sig_headers,
        ["Requested by", requested_by, date_val, ""],
        ["Authorised by", authorised_name, date_val, ""],
        ["Approved by", approved_name, date_val, ""],
    ]
    sig_table = Table(sig_rows, colWidths=t4_colw)
    sig_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), font_names["bold"]),
                ("FONTNAME", (0, 1), (-1, -1), font_names["regular"]),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (3, 1), (3, -1), 18),
                ("BOTTOMPADDING", (3, 1), (3, -1), 18),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
            ]
        )
    )
    story.append(KeepTogether(sig_table))

    # ---------- Attachment pages ----------
    page_imgs: List[bytes] = []

    if attachment:
        try:
            if isinstance(attachment, (list, tuple)) and attachment and isinstance(attachment[0], (list, tuple)):
                for name_bytes in attachment:
                    if not name_bytes or len(name_bytes) < 2:
                        continue
                    att_name, att_bytes = name_bytes[0], name_bytes[1]
                    if not att_name or not att_bytes:
                        continue
                    new_pages = _normalize_to_pages(att_name, att_bytes)
                    for p in new_pages:
                        page_imgs.append(p)
                        if len(page_imgs) >= 4:
                            break
                    if len(page_imgs) >= 4:
                        break
            else:
                att_name, att_bytes = attachment  # type: ignore[misc]
                if att_name and att_bytes:
                    page_imgs = _normalize_to_pages(att_name, att_bytes)[:4]
        except Exception:
            page_imgs = []

    if page_imgs:
        avail_w = content_w
        avail_h = content_h - 12

        for img_bytes in page_imgs:
            story.append(PageBreak())
            img = _scaled_image_flowable(img_bytes, max_w=avail_w, max_h=avail_h)
            story.append(img)

    doc.build(story)
    return buffer.getvalue()
