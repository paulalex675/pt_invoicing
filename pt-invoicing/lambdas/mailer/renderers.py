"""One renderer per email type. Each returns subject/html/text/attachments."""
import os
from datetime import date

from jinja2 import Environment, FileSystemLoader, select_autoescape

import pdf

_HERE = os.path.dirname(os.path.abspath(__file__))
_env = Environment(
    loader=FileSystemLoader(os.path.join(_HERE, "templates")),
    autoescape=select_autoescape(["html"]),
)


def money(pence):
    return f"£{int(pence) / 100:,.2f}"


def nice_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


_env.filters["money"] = money
_env.filters["nice_date"] = nice_date


def render_invoice(data, config):
    inv = data["invoice"]
    biz, brand = config["business"], config["brand"]
    ctx = {"inv": inv, "biz": biz, "brand": brand}
    pdf_bytes = pdf.build_invoice_pdf(inv, biz, brand)
    return {
        "subject": f"Invoice {inv['number']} from {biz['name']} - {money(inv['total'])}",
        "html": _env.get_template("invoice.html").render(**ctx),
        "text": _env.get_template("invoice.txt").render(**ctx),
        "attachments": [{
            "filename": f"{inv['number']}.pdf",
            "content_type": "application/pdf",
            "data": pdf_bytes,
            "s3_key": f"invoices/{inv['number']}.pdf",
        }],
    }


# Register new email types here, e.g. "progress_report": render_progress_report
REGISTRY = {
    "invoice": render_invoice,
}
