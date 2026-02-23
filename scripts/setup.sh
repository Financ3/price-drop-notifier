#!/usr/bin/env bash
# setup.sh — One-time AWS pre-requisites for the Price Drop Notifier
#
# Automates SES domain verification for codebystory.com via Route 53:
#   1. Checks prerequisites (aws, sam, docker)
#   2. Verifies the domain in SES (generates a TXT token)
#   3. Adds the SES verification TXT record to Route 53
#   4. Enables DKIM signing and adds the 3 CNAME records to Route 53
#   5. Sets up a custom MAIL FROM domain (improves deliverability)
#   6. Polls until AWS confirms the domain is verified
#
# Usage:
#   chmod +x scripts/setup.sh scripts/deploy.sh
#   ./scripts/setup.sh

set -euo pipefail

DOMAIN="${DOMAIN:-codebystory.com}"
SENDER_EMAIL="${SENDER_EMAIL:-noreply@codebystory.com}"
AWS_REGION="${AWS_REGION:-us-east-1}"
MAIL_FROM_SUBDOMAIN="mail.${DOMAIN}"   # envelope sender domain

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║        Price Drop Notifier — Initial Setup           ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "  Domain:  ${DOMAIN}"
echo "  Sender:  ${SENDER_EMAIL}"
echo "  Region:  ${AWS_REGION}"
echo ""

# ── Prerequisites ─────────────────────────────────────────────────────────────
check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo "  ✗ $1 not found. $2"
    exit 1
  fi
  echo "  ✓ $1"
}

echo "Checking prerequisites…"
check_cmd aws    "Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
check_cmd sam    "Install: pip install aws-sam-cli"
check_cmd docker "Install: https://docs.docker.com/get-docker/"
echo ""

echo "AWS identity:"
aws sts get-caller-identity --output table
echo ""

# ── Get Route 53 hosted zone ID ───────────────────────────────────────────────
echo "Looking up Route 53 hosted zone for ${DOMAIN}…"
ZONE_ID=$(aws route53 list-hosted-zones-by-name \
  --dns-name "${DOMAIN}." \
  --max-items 1 \
  --query "HostedZones[?Name=='${DOMAIN}.'].Id" \
  --output text | sed 's|/hostedzone/||')

if [ -z "${ZONE_ID}" ]; then
  echo "  ✗ No Route 53 hosted zone found for ${DOMAIN}."
  echo "    Make sure the domain is hosted in Route 53 in this AWS account."
  exit 1
fi
echo "  ✓ Hosted zone ID: ${ZONE_ID}"
echo ""

# ── SES domain verification TXT record ───────────────────────────────────────
echo "Requesting SES domain verification for ${DOMAIN}…"
VERIFICATION_TOKEN=$(aws ses verify-domain-identity \
  --domain "${DOMAIN}" \
  --region "${AWS_REGION}" \
  --query "VerificationToken" \
  --output text)

echo "  ✓ Verification token: ${VERIFICATION_TOKEN}"
echo "  Adding _amazonses TXT record to Route 53…"

aws route53 change-resource-record-sets \
  --hosted-zone-id "${ZONE_ID}" \
  --change-batch "{
    \"Comment\": \"SES domain verification\",
    \"Changes\": [{
      \"Action\": \"UPSERT\",
      \"ResourceRecordSet\": {
        \"Name\": \"_amazonses.${DOMAIN}\",
        \"Type\": \"TXT\",
        \"TTL\": 300,
        \"ResourceRecords\": [{\"Value\": \"\\\"${VERIFICATION_TOKEN}\\\"\"}]
      }
    }]
  }" > /dev/null

echo "  ✓ TXT record added"
echo ""

# ── DKIM (3 CNAME records) ────────────────────────────────────────────────────
echo "Enabling DKIM for ${DOMAIN}…"
DKIM_TOKENS=$(aws ses verify-domain-dkim \
  --domain "${DOMAIN}" \
  --region "${AWS_REGION}" \
  --query "DkimTokens" \
  --output text)

# Build the JSON change batch for all 3 DKIM CNAMEs
CHANGES="["
FIRST=true
for TOKEN in ${DKIM_TOKENS}; do
  if [ "${FIRST}" = true ]; then FIRST=false; else CHANGES+=","; fi
  CHANGES+="{
    \"Action\": \"UPSERT\",
    \"ResourceRecordSet\": {
      \"Name\": \"${TOKEN}._domainkey.${DOMAIN}\",
      \"Type\": \"CNAME\",
      \"TTL\": 300,
      \"ResourceRecords\": [{\"Value\": \"${TOKEN}.dkim.amazonses.com\"}]
    }
  }"
done
CHANGES+="]"

aws route53 change-resource-record-sets \
  --hosted-zone-id "${ZONE_ID}" \
  --change-batch "{\"Comment\": \"SES DKIM records\", \"Changes\": ${CHANGES}}" > /dev/null

echo "  ✓ 3 DKIM CNAME records added"
echo ""

# ── Custom MAIL FROM domain (improves spam score / deliverability) ────────────
echo "Setting up custom MAIL FROM domain (${MAIL_FROM_SUBDOMAIN})…"
aws ses set-identity-mail-from-domain \
  --identity "${DOMAIN}" \
  --mail-from-domain "${MAIL_FROM_SUBDOMAIN}" \
  --region "${AWS_REGION}" > /dev/null

# MX record for MAIL FROM
aws route53 change-resource-record-sets \
  --hosted-zone-id "${ZONE_ID}" \
  --change-batch "{
    \"Comment\": \"SES MAIL FROM MX\",
    \"Changes\": [{
      \"Action\": \"UPSERT\",
      \"ResourceRecordSet\": {
        \"Name\": \"${MAIL_FROM_SUBDOMAIN}\",
        \"Type\": \"MX\",
        \"TTL\": 300,
        \"ResourceRecords\": [{\"Value\": \"10 feedback-smtp.${AWS_REGION}.amazonses.com\"}]
      }
    },{
      \"Action\": \"UPSERT\",
      \"ResourceRecordSet\": {
        \"Name\": \"${MAIL_FROM_SUBDOMAIN}\",
        \"Type\": \"TXT\",
        \"TTL\": 300,
        \"ResourceRecords\": [{\"Value\": \"\\\"v=spf1 include:amazonses.com ~all\\\"\"}]
      }
    }]
  }" > /dev/null

echo "  ✓ MAIL FROM MX + SPF records added"
echo ""

# ── Poll for verification ─────────────────────────────────────────────────────
echo "Waiting for SES to verify ${DOMAIN} (DNS propagation takes ~1 min)…"
MAX_ATTEMPTS=20
ATTEMPT=0
while [ "${ATTEMPT}" -lt "${MAX_ATTEMPTS}" ]; do
  STATUS=$(aws ses get-identity-verification-attributes \
    --identities "${DOMAIN}" \
    --region "${AWS_REGION}" \
    --query "VerificationAttributes.\"${DOMAIN}\".VerificationStatus" \
    --output text)

  if [ "${STATUS}" = "Success" ]; then
    echo "  ✓ Domain verified!"
    break
  fi

  ATTEMPT=$((ATTEMPT + 1))
  echo "  … ${STATUS} (attempt ${ATTEMPT}/${MAX_ATTEMPTS}) — waiting 15s"
  sleep 15
done

if [ "${STATUS}" != "Success" ]; then
  echo ""
  echo "  ⚠ Domain not yet verified after $((MAX_ATTEMPTS * 15))s."
  echo "    This is normal — DNS can take a few minutes to propagate globally."
  echo "    Re-check with:"
  echo "      aws ses get-identity-verification-attributes \\"
  echo "        --identities ${DOMAIN} --region ${AWS_REGION}"
fi

# ── SES sandbox reminder ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  IMPORTANT: SES Sandbox Mode                        ║"
echo "║                                                      ║"
echo "║  New AWS accounts can only send to verified          ║"
echo "║  addresses. To send to anyone (subscribers), request ║"
echo "║  production access:                                  ║"
echo "║  AWS Console → SES → Account dashboard              ║"
echo "║  → Request production access                        ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Setup complete! Now run:"
echo "  SENDER_EMAIL=${SENDER_EMAIL} ./scripts/deploy.sh"
echo ""
