"""Branded invoice PDF (ReportLab).

Theming, all optional:
  - brand.primary / brand.accent in config.json set the colours
  - brand.pdf_style: "band" (default, coloured header band) or "plain" (white page)
  - assets/logo.png is placed in the header. In "band" style it sits on the primary
    colour, so use a light or transparent-background logo (or switch to "plain")
  - assets/fonts/regular.ttf + bold.ttf swap Helvetica for your brand font
"""
import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
LOGO = os.path.join(ASSETS, "logo.png")
BAND_H = 36 * mm
MARGIN = 20 * mm
PAD = 6  # SimpleDocTemplate frames have 6pt padding, so the band lines up with the body text


def _money(p):
    return f"£{int(p) / 100:,.2f}"


def _date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fonts():
    """Brand font if both TTFs are present, otherwise built-in Helvetica."""
    reg = os.path.join(ASSETS, "fonts", "regular.ttf")
    bold = os.path.join(ASSETS, "fonts", "bold.ttf")
    if os.path.exists(reg) and os.path.exists(bold):
        pdfmetrics.registerFont(TTFont("Brand", reg))
        pdfmetrics.registerFont(TTFont("Brand-Bold", bold))
        pdfmetrics.registerFontFamily("Brand", normal="Brand", bold="Brand-Bold", italic="Brand", boldItalic="Brand-Bold")
        return "Brand", "Brand-Bold"
    return "Helvetica", "Helvetica-Bold"


def _on(color):
    """White text on dark colours, near-black text on light ones."""
    lum = 0.299 * color.red + 0.587 * color.green + 0.114 * color.blue
    return colors.white if lum < 0.6 else colors.HexColor("#111827")


def _band_painter(inv, biz, primary, accent, regular, bold):
    ink = _on(primary)

    def paint(canv, _doc):
        w, h = A4
        canv.saveState()
        canv.setFillColor(primary)
        canv.rect(0, h - BAND_H, w, BAND_H, stroke=0, fill=1)
        canv.setFillColor(accent)
        canv.rect(0, h - BAND_H - 1.6 * mm, w, 1.6 * mm, stroke=0, fill=1)
        mid = h - BAND_H / 2
        if os.path.exists(LOGO):
            iw, ih = ImageReader(LOGO).getSize()
            scale = min(62 * mm / iw, (BAND_H - 12 * mm) / ih)
            canv.drawImage(LOGO, MARGIN + PAD, mid - ih * scale / 2, iw * scale, ih * scale, mask="auto")
        else:
            canv.setFillColor(ink)
            canv.setFont(bold, 17)
            canv.drawString(MARGIN + PAD, mid - 5, biz["name"])
        canv.setFillColor(ink)
        canv.setFont(bold, 22)
        canv.drawRightString(w - MARGIN - PAD, mid + 3, "INVOICE")
        canv.setFont(regular, 10)
        canv.drawRightString(w - MARGIN - PAD, mid - 11, inv["number"])
        if inv.get("amendedAt"):
            canv.setFont(regular, 8.5)
            canv.drawRightString(w - MARGIN - PAD, mid - 23, f"Updated {_date(inv['amendedAt'][:10])}")
        canv.restoreState()

    return paint


def build_invoice_pdf(inv, biz, brand):
    primary = colors.HexColor(brand["primary"])
    accent = colors.HexColor(brand["accent"])
    grey = colors.HexColor("#6b7280")
    band = brand.get("pdf_style", "band") != "plain"
    regular, bold = _fonts()

    base = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13, textColor=colors.HexColor("#1f2937"))
    small = ParagraphStyle("small", parent=base, fontSize=8.5, leading=12, textColor=grey)
    right = ParagraphStyle("r", parent=base, alignment=2)
    label = ParagraphStyle("label", parent=small, fontName=bold)
    boldp = ParagraphStyle("bold", parent=base, fontName=bold)
    title_r = ParagraphStyle("hr", parent=base, fontName=bold, fontSize=22, leading=26, textColor=primary, alignment=2)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=(BAND_H + 12 * mm) if band else 18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {inv['number']}", author=biz["name"],
    )
    width = A4[0] - 2 * MARGIN - 12  # frames have 6pt padding each side
    story = []

    business_block = [Paragraph(f"<b>{_esc(biz['name'])}</b>", ParagraphStyle("bn", parent=base, fontName=bold, fontSize=11, leading=15, textColor=primary))]
    business_block.append(Paragraph("<br/>".join(_esc(l) for l in biz.get("address_lines", [])), small))
    contact = [biz.get("owner_email", ""), biz.get("phone", ""), biz.get("website", "")]
    business_block.append(Paragraph("<br/>".join(_esc(c) for c in contact if c), small))

    if not band:
        left = []
        if os.path.exists(LOGO):
            left.append(Image(LOGO, width=40 * mm, height=16 * mm, kind="proportional", hAlign="LEFT"))
        left += business_block
        right_col = [Paragraph("INVOICE", title_r), Paragraph(_esc(inv["number"]), right)]
        if inv.get("amendedAt"):
            right_col.append(Paragraph(f"Updated {_date(inv['amendedAt'][:10])}", ParagraphStyle("am", parent=small, alignment=2)))
        head = Table([[left, right_col]], colWidths=[width * 0.62, width * 0.38])
        head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        rule = Table([[""]], colWidths=[width], rowHeights=[1.2 * mm])
        rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent)]))
        story += [head, Spacer(1, 6 * mm), rule, Spacer(1, 6 * mm)]

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

    total = Table([[Paragraph("Total due", boldp), Paragraph(f"<b>{_money(inv['total'])}</b>", ParagraphStyle("tt", parent=right, fontName=bold, fontSize=14, leading=18, textColor=primary))]],
                  colWidths=[width - 50 * mm, 50 * mm])
    total.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.8, primary), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    story += [Spacer(1, 3 * mm), total, Spacer(1, 10 * mm)]

    # Payment details (and, in band style, the business address, since the band has no room for it)
    bank = biz.get("bank", {})
    pay = Table([
        [Paragraph("Pay by bank transfer", label), ""],
        [Paragraph("Account name", small), Paragraph(_esc(bank.get("account_name", "")), base)],
        [Paragraph("Sort code", small), Paragraph(_esc(bank.get("sort_code", "")), base)],
        [Paragraph("Account number", small), Paragraph(_esc(bank.get("account_number", "")), base)],
        [Paragraph("Reference", small), Paragraph(_esc(inv["number"]), base)],
    ], colWidths=[34 * mm, 58 * mm], hAlign="LEFT")
    pay.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f4f6")),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, accent),
        ("SPAN", (0, 0), (1, 0)),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    if band:
        bottom = Table([[pay, business_block]], colWidths=[width - 62 * mm, 62 * mm])
        bottom.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
        story.append(bottom)
    else:
        story.append(pay)

    foot = [biz.get("vat_note"), f"Payment due within {biz.get('payment_terms_days', 7)} days of issue.", biz.get("footer_note")]
    story += [Spacer(1, 10 * mm), Paragraph("<br/>".join(_esc(f) for f in foot if f), small)]

    if band:
        painter = _band_painter(inv, biz, primary, accent, regular, bold)
        doc.build(story, onFirstPage=painter, onLaterPages=painter)
    else:
        doc.build(story)
    return buf.getvalue()
