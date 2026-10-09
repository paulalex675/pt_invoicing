#!/usr/bin/env bash
# Vendors the mailer's Python dependencies for the Lambda runtime (Linux, Python 3.12).
# Works from Git Bash on Windows - no Docker needed.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf lambdas/mailer/vendor
python -m pip install \
  -r lambdas/mailer/requirements.txt \
  --target lambdas/mailer/vendor \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary=:all: \
  --upgrade
echo "Mailer dependencies vendored."
