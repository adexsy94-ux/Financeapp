# pdf_utils.py
# Simple PDF generation for vouchers (multi-tenant safe)

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

from vouchers_module import list_voucher_lines, get_voucher


def build_voucher_pdf_bytes(company_id: int, voucher_id: int) -> bytes:
    v = get_voucher(company_id, voucher_id)
    if not v:
        raise ValueError("Voucher not found")

    lines = list_voucher_lines(voucher_id)

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 30 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, "PAYMENT VOUCHER")
    y -= 10 * mm

    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, y, f"Voucher No: {v.get('voucher_number') or ''}")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Vendor: {v.get('vendor') or ''}")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Requester: {v.get('requester') or ''}")
    y -= 6 * mm
    c.drawString(20 * mm, y, f"Invoice: {v.get('invoice') or ''}")
    y -= 12 * mm

    data = [["Description", "Amount", "Expense Account", "VAT", "WHT", "Total"]]
    total_sum = 0.0
    for line in lines:
        amount = float(line.get("amount") or 0)
        vat_value = float(line.get("vat_value") or 0)
        wht_value = float(line.get("wht_value") or 0)
        total = float(line.get("total") or amount + vat_value - wht_value)
        total_sum += total
        data.append(
            [
                line.get("description") or "",
                f"{amount:,.2f}",
                line.get("expense_account") or "",
                f"{line.get('vat_percent') or 0}%",
                f"{line.get('wht_percent') or 0}%",
                f"{total:,.2f}",
            ]
        )

    data.append(["", "", "", "", "Grand Total", f"{total_sum:,.2f}"])

    table = Table(data, colWidths=[60 * mm, 25 * mm, 40 * mm, 20 * mm, 20 * mm, 25 * mm])
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
    table.drawOn(c, 20 * mm, y - len(data) * 8 * mm)

    c.showPage()
    c.save()

    buffer.seek(0)
    return buffer.read()
