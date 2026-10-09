"""HTTP API for customers and invoices.

Invoices are stored in DynamoDB and an `email.requested` event is published on
the event bus. The mailer Lambda does the rendering and sending, so this code
never talks to SES directly.
"""
import base64
import datetime as dt
import json
import logging
import os
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CONFIG = json.loads(os.environ["APP_CONFIG"])
BIZ = CONFIG["business"]
TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE"])
EVENTS = boto3.client("events")
S3 = boto3.client("s3")
BUS = os.environ["BUS"]
FILES_BUCKET = os.environ["FILES_BUCKET"]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LINE_TYPES = {"PT session", "Coaching session", "Small group", "Online coaching", "Other"}
MAX_LINES = 50


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------- helpers ----------

def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError


def resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def clean(value, field, max_len, required=True):
    value = (value or "").strip() if isinstance(value, str) or value is None else str(value).strip()
    if required and not value:
        raise ApiError(400, f"{field} is required")
    if len(value) > max_len:
        raise ApiError(400, f"{field} must be {max_len} characters or fewer")
    return value


def valid_date(value, field):
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ApiError(400, f"{field} must be a date (YYYY-MM-DD)")


def to_pence(value):
    try:
        pounds = Decimal(str(value))
    except Exception:
        raise ApiError(400, "Price must be a number")
    if pounds <= 0 or pounds > 100000:
        raise ApiError(400, "Price must be between 0.01 and 100000")
    return int((pounds * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def today_uk():
    # Lambda runs in UTC; good enough to default to "today" in the UK
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


# ---------- customers ----------

def list_customers(_body, _params):
    items = TABLE.query(KeyConditionExpression=Key("pk").eq("CUSTOMER"))["Items"]
    customers = [
        {"id": i["sk"], "name": i["name"], "email": i["email"], "address": i.get("address", "")}
        for i in items
    ]
    return sorted(customers, key=lambda c: c["name"].lower())


def _customer_fields(body):
    name = clean(body.get("name"), "Name", 100)
    email = clean(body.get("email"), "Email", 200).lower()
    if not EMAIL_RE.match(email):
        raise ApiError(400, "Enter a valid email address")
    address = clean(body.get("address"), "Address", 300, required=False)
    return name, email, address


def create_customer(body, _params):
    name, email, address = _customer_fields(body)
    if any(c["email"] == email for c in list_customers(None, None)):
        raise ApiError(409, "A customer with that email already exists")
    cid = uuid.uuid4().hex[:12]
    TABLE.put_item(Item={
        "pk": "CUSTOMER", "sk": cid, "name": name, "email": email, "address": address,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return {"id": cid, "name": name, "email": email, "address": address}


def update_customer(body, params):
    cid = params["id"]
    name, email, address = _customer_fields(body)
    try:
        TABLE.update_item(
            Key={"pk": "CUSTOMER", "sk": cid},
            UpdateExpression="SET #n = :n, email = :e, address = :a",
            ConditionExpression="attribute_exists(sk)",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={":n": name, ":e": email, ":a": address},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ApiError(404, "Customer not found")
        raise
    return {"id": cid, "name": name, "email": email, "address": address}


# ---------- invoices ----------

def next_invoice_number():
    r = TABLE.update_item(
        Key={"pk": "COUNTER", "sk": "INVOICE"},
        UpdateExpression="ADD n :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return f"{BIZ.get('invoice_prefix', 'INV-')}{int(r['Attributes']['n']):04d}"


def _parse_lines(raw):
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "Add at least one session")
    if len(raw) > MAX_LINES:
        raise ApiError(400, f"An invoice can have at most {MAX_LINES} sessions")
    lines = []
    for i, line in enumerate(raw, 1):
        kind = clean(line.get("type"), f"Session {i} type", 40)
        if kind not in LINE_TYPES:
            raise ApiError(400, f"Session {i} has an unknown type")
        duration = line.get("durationMins")
        if duration in (None, ""):
            duration = None
        else:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                raise ApiError(400, f"Session {i} duration must be a whole number of minutes")
            if not 1 <= duration <= 600:
                raise ApiError(400, f"Session {i} duration must be 1-600 minutes")
        lines.append({
            "date": valid_date(line.get("date"), f"Session {i} date"),
            "type": kind,
            "durationMins": duration,
            "notes": clean(line.get("notes"), f"Session {i} notes", 300, required=False),
            "amount": to_pence(line.get("amount")),
        })
    return sorted(lines, key=lambda l: l["date"])


def create_invoice(body, _params):
    cid = clean(body.get("customerId"), "Customer", 40)
    cust = TABLE.get_item(Key={"pk": "CUSTOMER", "sk": cid}).get("Item")
    if not cust:
        raise ApiError(404, "Customer not found")
    issue = valid_date(body.get("issueDate") or today_uk(), "Issue date")
    lines = _parse_lines(body.get("lines"))
    due = (dt.date.fromisoformat(issue) + dt.timedelta(days=int(BIZ.get("payment_terms_days", 7)))).isoformat()
    invoice = {
        "pk": "INVOICE",
        "sk": next_invoice_number(),
        "issueDate": issue,
        "dueDate": due,
        "customer": {"id": cid, "name": cust["name"], "email": cust["email"], "address": cust.get("address", "")},
        "lines": lines,
        "total": sum(l["amount"] for l in lines),
        "status": "issued",
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    TABLE.put_item(Item=invoice, ConditionExpression="attribute_not_exists(sk)")
    result = _public(invoice)
    result["emailQueued"] = publish_email(result)
    return result


def _public(item):
    inv = dict(item)
    inv.pop("pk", None)
    inv["number"] = inv.pop("sk")
    return json.loads(json.dumps(inv, default=_json_default))


def list_invoices(_body, _params):
    r = TABLE.query(KeyConditionExpression=Key("pk").eq("INVOICE"), ScanIndexForward=False, Limit=60)
    return [_public(i) for i in r["Items"]]


def _get_invoice(number):
    item = TABLE.get_item(Key={"pk": "INVOICE", "sk": number}).get("Item")
    if not item:
        raise ApiError(404, "Invoice not found")
    return item


def resend_invoice(_body, params):
    inv = _public(_get_invoice(params["number"]))
    return {"emailQueued": publish_email(inv)}


def set_status(body, params):
    status = body.get("status")
    if status not in ("issued", "paid"):
        raise ApiError(400, "Status must be issued or paid")
    _get_invoice(params["number"])
    paid_at = dt.datetime.now(dt.timezone.utc).isoformat() if status == "paid" else None
    TABLE.update_item(
        Key={"pk": "INVOICE", "sk": params["number"]},
        UpdateExpression="SET #s = :s, paidAt = :p",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":p": paid_at},
    )
    return {"status": status}


def invoice_pdf(_body, params):
    number = params["number"]
    _get_invoice(number)
    key = f"invoices/{number}.pdf"
    try:
        S3.head_object(Bucket=FILES_BUCKET, Key=key)
    except ClientError:
        raise ApiError(404, "The PDF isn't ready yet - try again in a moment")
    url = S3.generate_presigned_url(
        "get_object",
        Params={"Bucket": FILES_BUCKET, "Key": key, "ResponseContentType": "application/pdf"},
        ExpiresIn=300,
    )
    return {"url": url}


# ---------- events ----------

def publish_email(invoice):
    """Ask the mailer to send this invoice. Returns True if the event was accepted."""
    detail = {
        "template": "invoice",
        "ref": invoice["number"],
        "to": [invoice["customer"]["email"]],
        "bcc": [BIZ["owner_email"]],
        "data": {"invoice": invoice},
    }
    try:
        r = EVENTS.put_events(Entries=[{
            "EventBusName": BUS,
            "Source": "pt.invoicing",
            "DetailType": "email.requested",
            "Detail": json.dumps(detail),
        }])
        return r.get("FailedEntryCount", 1) == 0
    except Exception:
        logger.exception("Failed to publish email event for %s", invoice["number"])
        return False


# ---------- routing ----------

ROUTES = {
    "GET /customers": list_customers,
    "POST /customers": create_customer,
    "PUT /customers/{id}": update_customer,
    "GET /invoices": list_invoices,
    "POST /invoices": create_invoice,
    "POST /invoices/{number}/resend": resend_invoice,
    "POST /invoices/{number}/status": set_status,
    "GET /invoices/{number}/pdf": invoice_pdf,
}


def handler(event, _context):
    try:
        fn = ROUTES.get(event["routeKey"])
        if not fn:
            raise ApiError(404, "Not found")
        raw = event.get("body") or ""
        if event.get("isBase64Encoded") and raw:
            raw = base64.b64decode(raw).decode("utf-8")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise ApiError(400, "Request body must be JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "Request body must be a JSON object")
        return resp(200, fn(body, event.get("pathParameters") or {}))
    except ApiError as e:
        return resp(e.status, {"error": str(e)})
    except Exception:
        logger.exception("Unhandled error")
        return resp(500, {"error": "Something went wrong. Please try again."})
