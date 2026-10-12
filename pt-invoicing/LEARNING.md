# Learning guide

A plan for understanding this project well enough to change it, debug it and build the next one without help.

## 1. Read in this order

Run `python tests/test_local.py` after any change. It uses fake AWS, so it's a safe place to experiment.

1. **`config.json`, then `app.py`**: what's configurable, and how the stack gets created.
2. **`lambdas/api/handler.py`**: plain Python. Follow `create_invoice` from top to bottom: validate, get the next number, save, publish an event.
3. **`lambdas/mailer/handler.py`**: `send()` is the core. Note what happens on failure (the record goes back to the queue) and why `deliver: false` exists.
4. **`lambdas/mailer/renderers.py` and `templates/`**: how one email type is defined, and where a new one would be registered.
5. **`stacks/invoicing_stack.py`**: read it last. By now you know what each Lambda does, so this file is just the wiring: which resource exists, who may call it, and what triggers what.
6. **`frontend/app.js`**: login first (the PKCE section), then `api()`, then the render functions.

## 2. What to skim and what to learn

**Boilerplate: recognise it, look it up when needed**
- `cdk.json`, `requirements.txt`, `.gitignore`, and the shape of `app.py`
- Property lists in the stack: block-public-access, log retention, removal policies, CORS, throttling, CloudFront origin access control
- PWA files: `manifest.webmanifest`, `sw.js`, icons, meta tags
- The PKCE login recipe in `app.js`
- Lambda response formatting and the route table
- SDK call syntax (DynamoDB expressions, `put_events`, `send_email`). Everyone looks these up.

**Concepts: learn these properly, because they transfer**
1. **IAM and least privilege.** Each Lambda gets only what it needs through `grant_*` calls. Most real AWS errors are `AccessDenied`.
2. **The CDK cycle.** Stack, synth, `cdk diff`, deploy, CloudFormation events. Always run `cdk diff` before `cdk deploy`.
3. **Which service for which job.** DynamoDB for records, S3 and Athena for analytics, SQS to decouple slow work, Cognito instead of building login.
4. **Asynchronous design.** Queues, retries, dead-letter queues, and idempotency (what happens if a message is processed twice).
5. **DynamoDB key design.** You design keys around the questions you'll ask. Look at how `pk`/`sk` are used here for customers, invoices and counters.
6. **Debugging in the cloud.** CloudWatch logs and tracing a request across services.
7. **Trust boundaries.** The API validates everything even though the UI does too, and templates escape text. Never trust the client.

## 3. Habits

```bash
cdk diff                 # what would change? read this before every deploy
cdk synth > /tmp/t.yaml  # see the CloudFormation your Python generates
```

Rebuild the vendored libraries (`./build.sh`) only when `lambdas/mailer/requirements.txt` changes or `lambdas/mailer/vendor` is missing. Otherwise `cdk deploy` is all you need.

## 4. Exercises

Do them without asking for generated code first. Ask for a review afterwards.

### A. Add a "location" field to sessions
This touches every layer, so it's the best test of whether you understand the system.
- `frontend/app.js`: `newLine()`, `lineCard()`, and the mapping in `startEdit()`
- `lambdas/api/handler.py`: `_parse_lines()` (validate the new field)
- `lambdas/mailer/pdf.py` and `templates/invoice.html` / `invoice.txt`: show it
- `tests/test_local.py`: extend a test

Watch out: invoices created before the change won't have the field, so use `.get()` in Python and expect it to be missing in templates.

### B. Add a payment-reminder email
- Add `render_payment_reminder` in `renderers.py`, a template, and register it in `REGISTRY`.
- You don't need to change the stack. The EventBridge rule matches on `detail-type: email.requested`, not on templates.
- Trigger it by hand from Git Bash with `aws events put-events --entries file://entry.json` (the `Detail` field is a JSON string inside the JSON).

### C. Break it on purpose
- Remove `table.grant_read_write_data(api_fn)` from the stack. Run `cdk diff`, read it, deploy.
- Use the app, then find the `AccessDeniedException` in the API Lambda's logs.
- Put the line back and deploy again. You'll never forget what IAM errors look like.

### D. Trace one invoice
```bash
aws logs tail <api log group> --since 10m --region eu-west-2
aws logs tail <mailer log group> --since 10m --region eu-west-2
aws sqs list-queues --region eu-west-2                       # find the email queue and DLQ
aws dynamodb scan --table-name <table> --region eu-west-2    # see the raw rows
```
Find your invoice number in each place. List the log groups with `aws logs describe-log-groups`.

### E. Write one new API route from scratch
For example `GET /summary`, returning the total outstanding and the paid total.
- Add the function and its `ROUTES` entry in `handler.py`.
- Add the path to the routes dict in `invoicing_stack.py`.
- Show it in the front end.
- Hint: query `pk = INVOICE` and sum in Python.

### F. Something that isn't in this project
Add a second email type that goes to *you* (a weekly summary), triggered by an EventBridge schedule. It uses the same mailer, and you'll add a new trigger in the stack.

## 5. Reviewing generated code

When AI writes infrastructure or handlers for you, check these before deploying:
- **Stack:** what's public, what permissions are granted (`*` resources?), what's retained or deleted on teardown, and what has deletion protection.
- **Handlers:** is input validated, what happens on a retry or duplicate, what's logged, and what does the user see on failure.
- **Secrets:** nothing sensitive hard-coded or logged. Bank details here live in `config.json` on purpose, because they appear on invoices.
- **Cost:** what runs continuously, and what has no limit (a queue consumer, a Lambda that can fan out).

## 6. Glossary

| Term | Meaning here |
|---|---|
| Stack | Everything CloudFormation manages together as `PtInvoicing` |
| CDK | Python that describes the stack; synthesises to CloudFormation |
| Construct | A CDK building block, e.g. `s3.Bucket` |
| Bootstrap | One-time staging bucket and roles CDK uses to upload code |
| Lambda | A function AWS runs on demand |
| Event bus / rule | EventBridge routing: "when X is published, send it to Y" |
| DLQ | Dead-letter queue: where messages go after repeated failures |
| SES sandbox | Restricted mode where only verified addresses can receive |
| PKCE | The login method that lets a browser app sign in safely without a secret |
| PWA | A website that installs to the home screen |
