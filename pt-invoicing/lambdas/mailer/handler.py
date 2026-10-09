"""Generic mailer.

Consumes `email.requested` events (via SQS) and SES bounce/complaint feedback (via SNS).

Event detail shape:
  {
    "template": "invoice",          # key in renderers.REGISTRY
    "ref": "INV-0001",              # your own reference, stored in the send log
    "to": ["client@example.com"],
    "bcc": ["you@example.com"],     # optional
    "data": { ... }                 # whatever the renderer needs
  }

To add a new email type (progress report, payment reminder...), add a renderer
to renderers.py and register it. Nothing else changes.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import boto3  # noqa: E402

import renderers  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CONFIG = json.loads(os.environ["APP_CONFIG"])
BIZ = CONFIG["business"]
TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE"])
SES = boto3.client("sesv2")
S3 = boto3.client("s3")
FILES_BUCKET = os.environ["FILES_BUCKET"]
SES_CONFIG_SET = os.environ.get("SES_CONFIG_SET")


def handler(event, _context):
    failures = []
    for rec in event["Records"]:
        try:
            if rec.get("EventSource") == "aws:sns":
                handle_feedback(json.loads(rec["Sns"]["Message"]))
            else:
                send(json.loads(rec["body"]))
        except Exception:
            logger.exception("Record failed")
            if "messageId" in rec:
                failures.append({"itemIdentifier": rec["messageId"]})
            else:
                raise
    return {"batchItemFailures": failures}


def is_suppressed(address):
    return "Item" in TABLE.get_item(Key={"pk": "SUPPRESSED", "sk": address.lower()})


def send(msg):
    renderer = renderers.REGISTRY.get(msg.get("template"))
    if not renderer:
        # Bad event: retrying will never help, so log and drop it
        logger.error("Unknown template %r - dropping", msg.get("template"))
        return

    to = [a.lower() for a in msg.get("to", []) if a]
    bcc = [a.lower() for a in msg.get("bcc", []) if a]
    suppressed = [a for a in to if is_suppressed(a)]
    to = [a for a in to if a not in suppressed]
    if suppressed:
        logger.warning("Skipping suppressed recipients: %s", suppressed)
    if not to:
        log_send(f"skipped-{datetime.now(timezone.utc).timestamp()}", msg, "suppressed")
        return

    out = renderer(msg["data"], CONFIG)

    for att in out.get("attachments", []):
        if att.get("s3_key"):
            S3.put_object(Bucket=FILES_BUCKET, Key=att["s3_key"], Body=att["data"], ContentType=att["content_type"])

    mime = EmailMessage()
    mime["Subject"] = out["subject"]
    mime["From"] = formataddr((BIZ["name"], BIZ["from_address"]))
    mime["To"] = ", ".join(to)
    mime["Reply-To"] = BIZ.get("reply_to") or BIZ["owner_email"]
    mime.set_content(out["text"])
    mime.add_alternative(out["html"], subtype="html")
    for att in out.get("attachments", []):
        maintype, subtype = att["content_type"].split("/", 1)
        mime.add_attachment(att["data"], maintype=maintype, subtype=subtype, filename=att["filename"])

    params = {
        "FromEmailAddress": BIZ["from_address"],
        "Destination": {"ToAddresses": to, **({"BccAddresses": bcc} if bcc else {})},
        "Content": {"Raw": {"Data": mime.as_bytes()}},
    }
    if SES_CONFIG_SET:
        params["ConfigurationSetName"] = SES_CONFIG_SET
    message_id = SES.send_email(**params)["MessageId"]
    log_send(message_id, msg, "sent")
    logger.info("Sent %s %s as %s", msg["template"], msg.get("ref"), message_id)


def log_send(message_id, msg, status):
    TABLE.put_item(Item={
        "pk": "EMAIL", "sk": message_id,
        "template": msg.get("template"), "ref": msg.get("ref", ""),
        "to": msg.get("to", []), "status": status,
        "at": datetime.now(timezone.utc).isoformat(),
    })


def handle_feedback(note):
    """SES bounce/complaint notifications. Hard bounces and complaints get suppressed."""
    kind = note.get("eventType")
    mail_id = note.get("mail", {}).get("messageId")
    addresses = []
    if kind == "Bounce" and note["bounce"].get("bounceType") == "Permanent":
        addresses = [r["emailAddress"] for r in note["bounce"]["bouncedRecipients"]]
    elif kind == "Complaint":
        addresses = [r["emailAddress"] for r in note["complaint"]["complainedRecipients"]]
    else:
        return
    for a in addresses:
        TABLE.put_item(Item={
            "pk": "SUPPRESSED", "sk": a.lower(), "reason": kind.lower(),
            "at": datetime.now(timezone.utc).isoformat(),
        })
    if mail_id:
        TABLE.update_item(
            Key={"pk": "EMAIL", "sk": mail_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": kind.lower()},
        )
    logger.warning("Suppressed %s after %s", addresses, kind)
