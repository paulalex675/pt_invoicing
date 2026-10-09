"""Branded invoice PDF (ReportLab)."""
import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.png")


def _money(p):
    return f"£{int(p) / 100:,.2f}"


def _date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_invoice_pdf(inv, biz, brand):
    primary = colors.HexColor(brand["primary"])
    accent = colors.HexColor(brand["accent"])
    grey = colors.HexColor("#6b7280")

    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13, textColor=colors.HexColor("#1f2937"))
    small = ParagraphStyle("small", parent=base, fontSize=8.5, leading=12, textColor=grey)
    h_right = ParagraphStyle("hr", parent=base, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=primary, alignment=2)
    right = ParagraphStyle("r", parent=base, alignment=2)
    label = ParagraphStyle("label", parent=small, fontName="Helvetica-Bold")
    bold = ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {inv['number']}", author=biz["name"],
    )
    width = A4[0] - 40 * mm - 12  # frames have 6pt padding each side
    story = []

    # Header: business on the left, invoice title on the right
    left = []
    if os.path.exists(LOGO):
        left.append(Image(LOGO, width=40 * mm, height=16 * mm, kind="proportional", hAlign="LEFT"))
    left.append(Paragraph(f"<b>{_esc(biz['name'])}</b>", ParagraphStyle("bn", parent=base, fontSize=13, leading=17, textColor=primary)))
    left.append(Paragraph("<br/>".join(_esc(l) for l in biz.get("address_lines", [])), small))
    contact = [biz.get("owner_email", ""), biz.get("phone", ""), biz.get("website", "")]
    left.append(Paragraph("<br/>".join(_esc(c) for c in contact if c), small))
    head = Table([[left, [Paragraph("INVOICE", h_right), Paragraph(_esc(inv["number"]), right)]]], colWidths=[width * 0.62, width * 0.38])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 6 * mm)]

    # Accent rule
    rule = Table([[""]], colWidths=[width], rowHeights=[1.2 * mm])
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)]))
    story += [rule, Spacer(1, 6 * mm)]

    # Bill to / dates
    cust = inv["customer"]
    bill = [Paragraph("Billed to", label), Paragraph(f"<b>{_esc(cust['name'])}</b>", base)]
    if cust.get("address"):
        bill.append(Paragraph(_esc(cust["address"]).replace("\n", "<br/>"), base))
    bill.append(Paragraph(_esc(cust["email"]), small))
    dates = Table([
        [Paragraph("Issued", label), Paragraph(_date(inv["issueDate"]), right)],
        [Paragraph("Due", label), Paragraph(_date(inv["dueDate"]), right)],
    ], colWidths=[22 * mm, 38 * mm])
    dates.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    meta = Table([[bill, dates]], colWidths=[width - 62 * mm, 62 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 8 * mm)]

    # Line items
    rows = [[Paragraph("Date", label), Paragraph("Session", label), Paragraph("Amount", ParagraphStyle("la", parent=label, alignment=2))]]
    for l in inv["lines"]:
        title = l["type"] + (f" ({l['durationMins']} min)" if l.get("durationMins") else "")
        desc = f"<b>{_esc(title)}</b>" + (f"<br/><font color='#6b7280' size='8.5'>{_esc(l['notes'])}</font>" if l.get("notes") else "")
        rows.append([Paragraph(_date(l["date"]), base), Paragraph(desc, base), Paragraph(_money(l["amount"]), right)])
    t = Table(rows, colWidths=[28 * mm, width - 28 * mm - 30 * mm, 30 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, primary),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(t)

    total = Table([[Paragraph("Total due", bold), Paragraph(f"<b>{_money(inv['total'])}</b>", ParagraphStyle("tt", parent=right, fontSize=14, leading=18, textColor=primary))]],
                  colWidths=[width - 50 * mm, 50 * mm])
    total.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.8, primary), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 3 * mm), total, Spacer(1, 10 * mm)]

    # Payment details
    bank = biz.get("bank", {})
    pay = [
        [Paragraph("Pay by bank transfer", label), ""],
        [Paragraph("Account name", small), Paragraph(_esc(bank.get("account_name", "")), base)],
        [Paragraph("Sort code", small), Paragraph(_esc(bank.get("sort_code", "")), base)],
        [Paragraph("Account number", small), Paragraph(_esc(bank.get("account_number", "")), base)],
        [Paragraph("Reference", small), Paragraph(_esc(inv["number"]), base)],
    ]
    pt = Table(pay, colWidths=[34 * mm, 60 * mm], hAlign="LEFT")
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")),
        ("SPAN", (0, 0), (1, 0)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(pt)

    foot = [biz.get("vat_note"), f"Payment due within {biz.get('payment_terms_days', 7)} days of issue.", biz.get("footer_note")]
    story += [Spacer(1, 10 * mm), Paragraph("<br/>".join(_esc(f) for f in foot if f), small)]

    doc.build(story)
    return buf.getvalue()
