import json, os, sys, importlib
os.environ.update(AWS_DEFAULT_REGION="eu-west-2", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
                  TABLE="t", BUS="pt-events", FILES_BUCKET="files", SES_CONFIG_SET="pt-mailer")
cfg = json.load(open("config.json"))
os.environ["APP_CONFIG"] = json.dumps({"business": cfg["business"], "brand": cfg["brand"]})
from moto import mock_aws
import boto3

@mock_aws
def run():
    ddb = boto3.client("dynamodb")
    ddb.create_table(TableName="t", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}])
    boto3.client("s3").create_bucket(Bucket="files", CreateBucketConfiguration={"LocationConstraint": "eu-west-2"})
    boto3.client("events").create_event_bus(Name="pt-events")
    ses = boto3.client("sesv2")
    ses.create_email_identity(EmailIdentity=cfg["business"]["from_address"])
    ses.create_configuration_set(ConfigurationSetName="pt-mailer")

    sys.path.insert(0, "lambdas/api"); api = importlib.import_module("handler"); sys.path.pop(0)
    sys.modules.pop("handler")
    sys.path.insert(0, "lambdas/mailer"); mailer = importlib.import_module("handler")

    def call(route, body=None, params=None):
        r = api.handler({"routeKey": route, "body": json.dumps(body) if body else None, "pathParameters": params}, None)
        return r["statusCode"], json.loads(r["body"])

    s, c = call("POST /customers", {"name": "Jo Bloggs", "email": "Jo@Example.com", "address": "1 High St"}); assert s == 200, c
    assert call("POST /customers", {"name": "Dup", "email": "jo@example.com"})[0] == 409
    assert call("POST /customers", {"name": "X", "email": "nope"})[0] == 400
    assert call("PUT /customers/{id}", {"name": "Jo B", "email": "jo@example.com"}, {"id": c["id"]})[0] == 200
    assert [x["name"] for x in call("GET /customers")[1]] == ["Jo B"]

    lines = [{"date": "2026-10-08", "type": "PT session", "durationMins": 60, "amount": 45, "notes": "Strength <b>block</b>"},
             {"date": "2026-10-02", "type": "Coaching session", "durationMins": None, "amount": "30.50", "notes": ""}]
    s, inv = call("POST /invoices", {"customerId": c["id"], "issueDate": "2026-10-09", "lines": lines}); assert s == 200, inv
    assert inv["number"] == "INV-0001" and inv["total"] == 7550 and inv["dueDate"] == "2026-10-16"
    assert inv["lines"][0]["date"] == "2026-10-02" and inv["emailQueued"] is True
    assert call("POST /invoices", {"customerId": c["id"], "lines": []})[0] == 400
    assert call("POST /invoices", {"customerId": c["id"], "lines": [dict(lines[0], amount=-1)]})[0] == 400
    assert call("POST /invoices", {"customerId": "nope", "lines": lines})[0] == 404
    assert call("POST /invoices", {"customerId": c["id"], "lines": lines})[1]["number"] == "INV-0002"
    assert [i["number"] for i in call("GET /invoices")[1]] == ["INV-0002", "INV-0001"]
    assert call("POST /invoices/{number}/status", {"status": "paid"}, {"number": "INV-0001"})[0] == 200
    assert call("GET /invoices/{number}/pdf", None, {"number": "INV-0001"})[0] == 404  # not rendered yet

    # Mailer: feed it the event the API published
    from email import message_from_bytes
    sent = {}
    orig = mailer.SES.send_email
    def spy(**kw): sent.update(kw); return orig(**kw)
    mailer.SES.send_email = spy
    detail = {"template": "invoice", "ref": inv["number"], "to": [inv["customer"]["email"]],
              "bcc": [cfg["business"]["owner_email"]], "data": {"invoice": inv}}
    r = mailer.handler({"Records": [{"messageId": "1", "body": json.dumps(detail)}]}, None)
    assert r["batchItemFailures"] == [], r
    assert sent["Destination"]["ToAddresses"] == ["jo@example.com"] and sent["Destination"]["BccAddresses"] == [cfg["business"]["owner_email"]]
    m = message_from_bytes(sent["Content"]["Raw"]["Data"])
    parts = {p.get_content_type(): p for p in m.walk()}
    html = parts["text/html"].get_payload(decode=True).decode()
    assert "&lt;b&gt;block&lt;/b&gt;" in html and "£75.50" in html and "Bcc" not in str(m.keys())
    pdf_bytes = parts["application/pdf"].get_payload(decode=True); assert pdf_bytes[:4] == b"%PDF"
    open("/tmp/sample.pdf", "wb").write(pdf_bytes); open("/tmp/sample.html", "w").write(html)
    print("subject:", m["Subject"])
    # PDF now available via API
    st, pdfres = call("GET /invoices/{number}/pdf", None, {"number": "INV-0001"})
    import base64; assert st == 200 and base64.b64decode(pdfres["pdf"])[:4] == b"%PDF"

    # ---- edit: paid invoices are locked, unpaid ones can change
    new_lines = [dict(lines[0], amount=50)]
    assert call("PUT /invoices/{number}", {"customerId": c["id"], "lines": new_lines}, {"number": "INV-0001"})[0] == 409  # paid
    assert call("PUT /invoices/{number}", {"customerId": c["id"], "lines": new_lines}, {"number": "INV-0099"})[0] == 404
    s, ed = call("PUT /invoices/{number}", {"customerId": c["id"], "issueDate": "2026-10-10", "lines": new_lines, "send": False}, {"number": "INV-0002"})
    assert s == 200 and ed["total"] == 5000 and ed["dueDate"] == "2026-10-17" and ed["amendedAt"] and ed["pdfQueued"] is True and "emailQueued" not in ed, ed
    assert call("GET /invoices/{number}/pdf", None, {"number": "INV-0002"})[0] == 404  # stale PDF removed until rebuilt
    sent.clear()
    detail2 = {"template": "invoice", "ref": "INV-0002", "deliver": False, "to": [ed["customer"]["email"]], "data": {"invoice": ed}}
    assert mailer.handler({"Records": [{"messageId": "9", "body": json.dumps(detail2)}]}, None)["batchItemFailures"] == []
    assert not sent, "deliver=false must not email"
    assert call("GET /invoices/{number}/pdf", None, {"number": "INV-0002"})[0] == 200  # rebuilt
    # edit + send produces an 'Updated invoice' email
    s, ed2 = call("PUT /invoices/{number}", {"customerId": c["id"], "lines": new_lines, "send": True}, {"number": "INV-0002"})
    assert ed2["emailQueued"] is True
    detail3 = {"template": "invoice", "ref": "INV-0002", "to": ["other@example.com"], "data": {"invoice": ed2}}
    mailer.handler({"Records": [{"messageId": "10", "body": json.dumps(detail3)}]}, None)
    m2 = message_from_bytes(sent["Content"]["Raw"]["Data"])
    assert m2["Subject"].startswith("Updated invoice INV-0002"), m2["Subject"]
    assert "replaces the earlier INV-0002" in {p.get_content_type(): p for p in m2.walk()}["text/html"].get_payload(decode=True).decode()

    # ---- delete: paid invoices are locked
    assert call("DELETE /invoices/{number}", None, {"number": "INV-0001"})[0] == 409
    assert call("DELETE /invoices/{number}", None, {"number": "INV-0002"})[0] == 200
    assert call("DELETE /invoices/{number}", None, {"number": "INV-0002"})[0] == 404
    assert [i["number"] for i in call("GET /invoices")[1]] == ["INV-0001"]
    # suppression test below needs a fresh sent-log entry
    sent.clear()

    # Feedback: hard bounce suppresses the address, later sends skip it
    msg_id = ddb.scan(TableName="t", FilterExpression="pk = :p", ExpressionAttributeValues={":p": {"S": "EMAIL"}})["Items"][0]["sk"]["S"]
    note = {"eventType": "Bounce", "mail": {"messageId": msg_id}, "bounce": {"bounceType": "Permanent", "bouncedRecipients": [{"emailAddress": "jo@example.com"}]}}
    mailer.handler({"Records": [{"EventSource": "aws:sns", "Sns": {"Message": json.dumps(note)}}]}, None)
    sent.clear(); mailer.handler({"Records": [{"messageId": "2", "body": json.dumps(detail)}]}, None)
    assert not sent, "suppressed address should not be emailed"
    # Unknown template is dropped, not retried
    assert mailer.handler({"Records": [{"messageId": "3", "body": json.dumps({"template": "nope", "data": {}})}]}, None)["batchItemFailures"] == []
    print("ALL OK")
run()
