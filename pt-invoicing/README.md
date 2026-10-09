# PT Invoices

A mobile-friendly invoicing web app (PWA) for PT and coaching sessions, running entirely on AWS in `eu-west-2` (London).

- Pick a customer from a dropdown or add a new one inline
- Add as many sessions as you like to one invoice
- Sends a branded HTML email plus a PDF to the customer, with you Bcc'd
- Tracks invoices, lets you resend them, and lets you mark them paid
- Installs to your iPhone home screen from Safari, with no App Store involved

```
iPhone (PWA) -> CloudFront -> S3 (front end)
     |
     +-> Cognito login (+ authenticator-app MFA)
     +-> API Gateway (JWT) -> Lambda "Api" -> DynamoDB
                                   |
                                   +-> EventBridge "email.requested" -> SQS (+DLQ) -> Lambda "Mailer" -> SES
                                                                                         |-> S3 (PDF copies)
SES bounces/complaints -> SNS -> Mailer (hard bounces are suppressed automatically)
```

The Mailer is generic. Anything that publishes an `email.requested` event can use it (see "Reusing the mailer").

---

## 1. Prerequisites (one-off)

In Git Bash:

```bash
python --version      # 3.10+ is fine
node --version        # 18+
aws --version         # AWS CLI v2
npm install -g aws-cdk
cdk --version
```

## 2. Configure

Edit `config.json`. Deployment refuses to run while it still contains `example.co.uk`.

| Field | What it's for |
|---|---|
| `business.name` | Name on invoices and in the From line |
| `business.owner_email` | You. Bcc'd on every invoice |
| `business.from_address` | Sender address. **Must be on a domain you verify in SES (step 4)** |
| `business.reply_to` | Where customer replies go |
| `business.address_lines`, `phone`, `website` | Shown on the invoice |
| `business.bank` | Bank transfer details printed on invoices |
| `business.payment_terms_days` | Due date = issue date + this |
| `business.vat_note`, `footer_note` | Small print |
| `brand.primary`, `brand.accent` | Your two brand colours (hex). Used in the app, email and PDF |
| `brand.logo_url` | Optional public https URL of your logo for the email header |
| `domain.name` / `certificate_arn` | Optional custom domain (step 9). Leave blank at first |

Optional logo for the PDF: save a PNG as `lambdas/mailer/assets/logo.png`.

## 3. Set up and log in to AWS

```bash
python -m venv .venv
source .venv/Scripts/activate        # Git Bash on Windows
pip install -r requirements.txt

aws sso login --profile YOUR_PROFILE
export AWS_PROFILE=YOUR_PROFILE
aws sts get-caller-identity          # check it's the right account
```

Bootstrap CDK once per account and region. Skip this if `eu-west-2` is already bootstrapped from your pipeline work.

```bash
cdk bootstrap aws://YOUR_ACCOUNT_ID/eu-west-2
```

## 4. Verify your sending domain in SES

Do this early, because DNS and approval take time.

1. AWS console -> **SES** -> make sure the region is **Europe (London)**.
2. **Identities -> Create identity -> Domain** (e.g. `yourdomain.co.uk`), leave **Easy DKIM** on.
3. Add the 3 DKIM CNAME records it shows to your DNS provider. The status turns "Verified" within minutes to hours.
4. Recommended: add a DMARC record, e.g. TXT `_dmarc` = `v=DMARC1; p=none; rua=mailto:you@yourdomain.co.uk`.
5. **Account dashboard -> Request production access**. Until it's approved, SES is in *sandbox* and can only send to addresses you've individually verified (create an **Email address** identity for your own address and for any test customer). Approval is usually within a day.

## 5. Deploy

```bash
./build.sh          # bundles Jinja2 + ReportLab for the Lambda (no Docker needed)
cdk deploy
```

The first deploy takes 5-10 minutes because of CloudFront. When it finishes, note the outputs:

- `SiteUrl`: "https://d1vtps4edwcl10.cloudfront.net"
- `UserPoolId`: "eu-west-2_HTJZgbB7Q"
- `ApiUrl`: "https://6swywkegt1.execute-api.eu-west-2.amazonaws.com"
- `CloudFrontDomain`: "d1vtps4edwcl10.cloudfront.ne"
 

## 6. Create your login

```bash
aws cognito-idp admin-create-user \
  --region eu-west-2 \
  --user-pool-id eu-west-2_HTJZgbB7Q \
  --username alexrobinson.paul@outlook.com \
  --user-attributes Name=email,Value=alexrobinson.paul@outlook.com Name=email_verified,Value=true \
  --desired-delivery-mediums EMAIL
```

A temporary password is emailed to you. Open `SiteUrl`, tap **Sign in**, enter the temporary password, set a new one (12+ characters), then scan the QR code with an authenticator app. Self-sign-up is disabled, so nobody else can create an account.

## 7. Put it on your iPhone

1. Open `SiteUrl` in **Safari** (it has to be Safari for the install option).
2. Share -> **Add to Home Screen**.
3. Open it from the home screen and sign in there. Safari and the home-screen app keep separate logins, so you sign in once in each. You'll stay signed in for up to 90 days.

## 8. Send a test invoice

1. Add yourself (or a verified address, while in the SES sandbox) as a customer.
2. Create an invoice with one session and send it.
3. Check the email arrives with the PDF attached, then try **View PDF** in the Invoices tab.

## 9. Optional: your own domain (e.g. `invoices.yourdomain.co.uk`)

1. In the AWS console switch to **us-east-1** -> **Certificate Manager** -> request a public certificate for `invoices.yourdomain.co.uk` and validate it using DNS. CloudFront only accepts certificates from us-east-1.
2. Put the domain and the certificate ARN into `config.json` under `domain`.
3. Run `cdk deploy`.
4. At your DNS host, add a CNAME `invoices` pointing at the `CloudFrontDomain` output.

Cognito's callback URLs update automatically. After switching domains, install the home-screen app again from the new address.

---

## Reusing the mailer (progress reports, reminders)

Everything that sends email goes through the `pt-events` bus. To send something, publish:

```python
events.put_events(Entries=[{
    "EventBusName": "pt-events",
    "Source": "pt.pipeline",
    "DetailType": "email.requested",
    "Detail": json.dumps({
        "template": "progress_report",      # key in lambdas/mailer/renderers.py REGISTRY
        "ref": "client123-2026-10",         # your own reference, stored in the send log
        "to": ["client@example.com"],
        "bcc": ["you@yourdomain.co.uk"],
        "data": {"...": "whatever the renderer needs"},
    }),
}])
```

To add a new email type:

1. Add `templates/progress_report.html` (extend `base.html` for your branding) and a `.txt` version.
2. Add a `render_progress_report(data, config)` function in `renderers.py`. It returns `subject`, `html`, `text` and optional `attachments`.
3. Register it in `REGISTRY`, then `cdk deploy`.

Whoever publishes needs `events:PutEvents` on the bus. In CDK that's `bus.grant_put_events_to(your_lambda)`. The bus name is `pt-events`.

The mailer also handles retries (3 attempts, then the dead-letter queue), a send log (`pk = EMAIL` rows in DynamoDB), and automatic suppression of addresses that hard-bounce or complain.

## Day-to-day operations

- **Update the app or branding:** edit files, then `cdk deploy`.
- **Run the local tests:** `pip install jinja2 reportlab boto3 "moto[dynamodb,events,s3,sqs]"` then `python tests/test_local.py`. They use mocked AWS.
- **Logs:** CloudWatch log groups for the `Api` and `Mailer` functions (kept for one month).
- **Emails not arriving?** Check, in order: SES identity verified, still in sandbox (recipient unverified), `Mailer` logs, and the `EmailDlq` queue in SQS for failed messages.
- **"The PDF isn't ready yet":** the mailer creates it a few seconds after sending. Try again.
- **Cost:** pennies per month at this volume. CloudFront, Lambda, API Gateway, DynamoDB, SQS and SES all have free tiers or per-request pricing. Cognito is free for this number of users.

## Data and safety notes

- DynamoDB (invoices and customers) and the PDF bucket are set to **retain on stack deletion**. DynamoDB has point-in-time recovery on. The stack has termination protection on.
- Customer names and emails are personal data held in `eu-west-2`. There's no delete-customer button yet, so for an erasure request delete the customer row in DynamoDB by hand. Be aware that invoices usually have to be kept for tax purposes, so check your obligations before deleting those.
- The API is throttled (10 requests/second), protected by a JWT from your Cognito pool, and the pool requires MFA.
- To tear down: `aws cloudformation update-termination-protection --no-enable-termination-protection --stack-name PtInvoicing`, then `cdk destroy`. The retained table, bucket and user pool must be deleted by hand if you really want them gone.

## Not built yet (planned)

Payment links (Stripe or GoCardless), overdue reminders (an EventBridge Scheduler job that publishes `email.requested` with a `payment_reminder` template), and progress-report emails. The event bus and mailer are already shaped for them.



aws sesv2 put-account-details --region eu-west-2 \
  --production-access-enabled \
  --mail-type TRANSACTIONAL \
  --website-url "https://www.paulpt.co.uk/" \
  --contact-language EN \
  --additional-contact-email-addresses alexrobinson.paul@outlook.com \
  --use-case-description "Sole-trader personal training business. I email invoices (HTML + PDF) one at a time to my own clients after training sessions, with a copy to myself. Expected volume is under 100 emails a month, all to existing clients who have booked sessions with me. Recipients are only people I have trained, and addresses are entered by me manually. Hard bounces and complaints are handled automatically: the address is suppressed and no further mail is sent. Sending domain is verified with DKIM."