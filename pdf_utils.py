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
