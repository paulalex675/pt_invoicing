import json
import os

import aws_cdk as cdk

from stacks.invoicing_stack import InvoicingStack

with open("config.json", encoding="utf-8") as f:
    config = json.load(f)

if "example.co.uk" in json.dumps(config["business"]) and not os.getenv("ALLOW_PLACEHOLDERS"):
    raise SystemExit("Edit config.json with your real business details before deploying.")

if not os.path.isdir("lambdas/mailer/vendor") and not os.getenv("ALLOW_PLACEHOLDERS"):
    raise SystemExit("Run ./build.sh first to bundle the mailer's dependencies.")

app = cdk.App()
InvoicingStack(
    app,
    "PtInvoicing",
    config=config,
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=config.get("region", "eu-west-2"),
    ),
    termination_protection=True,
)
app.synth()
