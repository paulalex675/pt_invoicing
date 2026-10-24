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


def cm(value):
    return "-" if value is None else f"{float(value):.1f} cm"


_env.filters["money"] = money
_env.filters["nice_date"] = nice_date
_env.filters["cm"] = cm


def render_invoice(data, config):
    inv = data["invoice"]
    biz, brand = config["business"], config["brand"]
    ctx = {"inv": inv, "biz": biz, "brand": brand}
    pdf_bytes = pdf.build_invoice_pdf(inv, biz, brand)
    return {
        "subject": f"{'Updated invoice' if inv.get('amendedAt') else 'Invoice'} {inv['number']} from {biz['name']} - {money(inv['total'])}",
        "html": _env.get_template("invoice.html").render(**ctx),
        "text": _env.get_template("invoice.txt").render(**ctx),
        "attachments": [{
            "filename": f"{inv['number']}.pdf",
            "content_type": "application/pdf",
            "data": pdf_bytes,
            "s3_key": f"invoices/{inv['number']}.pdf",
        }],
    }


# ---- Juniors (sent by the pt_app juniors API via the same pt-events bus) ----

CONSENT_LABELS = {
    "training": "Taking part in coached training sessions",
    "growth_measurements": "Monthly height, sitting height and weight measurements, used to track growth and plan training safely",
    "hr_data": "Recording heart rate during sessions with a chest strap",
    "photos": "Using photos or video of training in my marketing",
}

STAGE_NOTES = {
    "pre_phv": "Growth is steady, so training focuses on skills, coordination and general fitness.",
    "circa_phv": "This is the main growth spurt. Bones can grow faster than muscles and tendons adapt, so I manage load carefully and focus on technique, mobility and recovery.",
    "post_phv": "The main growth spurt has passed, so strength and conditioning can build up progressively.",
}


def render_junior_consent_request(data, config):
    ctx = {"d": data, "biz": config["business"], "brand": config["brand"], "labels": CONSENT_LABELS}
    return {
        "subject": f"Consent for {data['child_first_name']}'s training - {config['business']['name']}",
        "html": _env.get_template("junior_consent_request.html").render(**ctx),
        "text": _env.get_template("junior_consent_request.txt").render(**ctx),
    }


def render_junior_growth_summary(data, config):
    ctx = {"d": data, "biz": config["business"], "brand": config["brand"], "stage_notes": STAGE_NOTES}
    return {
        "subject": f"{data['child_first_name']}'s growth update - {config['business']['name']}",
        "html": _env.get_template("junior_growth_summary.html").render(**ctx),
        "text": _env.get_template("junior_growth_summary.txt").render(**ctx),
    }


# Register new email types here, e.g. "progress_report": render_progress_report
REGISTRY = {
    "invoice": render_invoice,
    "junior_consent_request": render_junior_consent_request,
    "junior_growth_summary": render_junior_growth_summary,
}
