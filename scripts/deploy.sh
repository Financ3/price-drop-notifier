#!/usr/bin/env bash
# deploy.sh — Build and deploy the Price Drop Notifier to AWS using SAM
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - AWS SAM CLI installed (brew install aws-sam-cli  OR  pip install aws-sam-cli)
#   - Docker running (used by sam build --use-container)
#   - An S3 bucket for SAM artifacts (created by scripts/setup.sh)
#   - A verified SES email address for SENDER_EMAIL
#
# Usage:
#   chmod +x scripts/deploy.sh
#   SENDER_EMAIL=you@example.com ./scripts/deploy.sh

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
STACK_NAME="${STACK_NAME:-price-drop-notifier}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ENVIRONMENT="${ENVIRONMENT:-prod}"
SENDER_EMAIL="${SENDER_EMAIL:-noreply@codebystory.com}"
SCRAPER_API_KEY="${SCRAPER_API_KEY:-}"
SAM_BUCKET="${SAM_BUCKET:-}"   # Auto-created if empty

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        Price Drop Notifier — SAM Deploy              ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Stack:       ${STACK_NAME}"
echo "  Region:      ${AWS_REGION}"
echo "  Environment: ${ENVIRONMENT}"
echo "  Sender:      ${SENDER_EMAIL}"
echo ""

cd "$(dirname "$0")/../backend"

# ── Create SAM artifacts bucket if needed ────────────────────────────────────
if [ -z "${SAM_BUCKET}" ]; then
  SAM_BUCKET="${STACK_NAME}-sam-artifacts-$(aws sts get-caller-identity --query Account --output text)"
  if ! aws s3api head-bucket --bucket "${SAM_BUCKET}" 2>/dev/null; then
    echo "Creating S3 bucket for SAM artifacts: ${SAM_BUCKET}"
    if [ "${AWS_REGION}" = "us-east-1" ]; then
      aws s3api create-bucket --bucket "${SAM_BUCKET}" --region "${AWS_REGION}"
    else
      aws s3api create-bucket \
        --bucket "${SAM_BUCKET}" \
        --region "${AWS_REGION}" \
        --create-bucket-configuration LocationConstraint="${AWS_REGION}"
    fi
    aws s3api put-bucket-versioning \
      --bucket "${SAM_BUCKET}" \
      --versioning-configuration Status=Enabled
  fi
fi

# ── Build ─────────────────────────────────────────────────────────────────────
echo "▶ Building Lambda functions and layer…"
sam build \
  --use-container \
  --template-file template.yaml

# ── Deploy ────────────────────────────────────────────────────────────────────
echo ""
echo "▶ Deploying to AWS…"
sam deploy \
  --stack-name "${STACK_NAME}" \
  --s3-bucket "${SAM_BUCKET}" \
  --region "${AWS_REGION}" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "SenderEmail=${SENDER_EMAIL}" \
    "Environment=${ENVIRONMENT}" \
    "ScraperApiKey=${SCRAPER_API_KEY}" \
  --no-fail-on-empty-changeset

# ── Capture outputs ───────────────────────────────────────────────────────────
echo ""
echo "▶ Stack outputs:"
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${AWS_REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)

echo ""
echo "  API URL: ${API_URL}"
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Deployment complete!                                ║"
echo "╠══════════════════════════════════════════════════════╣"
echo "║  Next steps:                                         ║"
echo "║  1. Copy the API URL above into frontend/app.js      ║"
echo "║     (replace the REPLACE_ME placeholder)             ║"
echo "║  2. Host the frontend/ folder on S3, GitHub Pages,   ║"
echo "║     Netlify, or any static host.                     ║"
echo "║  3. noreply@codebystory.com is your verified sender. ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
