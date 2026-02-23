"""
subscribe/handler.py — POST /subscribe

Flow:
  1. Validate input (url + email)
  2. Scrape the product URL for a price
  3. Upsert the product in DynamoDB
  4. Create (or reactivate) a subscription record
  5. Send a welcome email via SES
  6. Return the product info so the frontend can display confirmation
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from scraper_utils import scrape_product
from email_utils import build_welcome_email

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
ses = boto3.client("ses")

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _resp(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": _CORS_HEADERS, "body": json.dumps(body)}


def _get_api_base_url(event: dict) -> str:
    """Derive the API base URL from the API Gateway request context.
    Avoids the CloudFormation circular dependency caused by referencing
    ServerlessRestApi in environment variables.
    """
    ctx = event.get("requestContext", {})
    domain = ctx.get("domainName", "")
    stage = ctx.get("stage", "Prod")
    if domain:
        return f"https://{domain}/{stage}"
    return ""


def _build_unsubscribe_url(api_base: str, token: str) -> str:
    return f"{api_base.rstrip('/')}/unsubscribe?token={token}"


def _send_welcome(email: str, product: Optional[dict], product_url: str, unsubscribe_url: str, name_hint: str = ""):
    if product and product.get("name"):
        name = product["name"]
    elif name_hint:
        name = name_hint
    else:
        # Fall back to just the domain so we never put a raw truncated URL in the email
        from urllib.parse import urlparse as _urlparse
        name = _urlparse(product_url).netloc or product_url[:60]
    template = build_welcome_email(
        product_name=name,
        product_url=product_url,
        unsubscribe_url=unsubscribe_url,
        price=float(product["price"]) if product and product.get("price") else None,
        currency=product.get("currency", "USD") if product else "USD",
    )
    ses.send_email(
        Source=os.environ["SENDER_EMAIL"],
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": template["subject"], "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": template["html"], "Charset": "UTF-8"},
                "Text": {"Data": template["text"], "Charset": "UTF-8"},
            },
        },
    )


def lambda_handler(event: dict, context) -> dict:
    # Handle CORS preflight
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": _CORS_HEADERS, "body": ""}

    # ── Parse body ────────────────────────────────────────────────────────────
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON body"})

    url = (body.get("url") or "").strip()
    email = (body.get("email") or "").strip().lower()
    product_name = (body.get("productName") or "").strip()

    if not url or not email:
        return _resp(400, {"error": "Both 'url' and 'email' are required."})

    if not url.startswith(("http://", "https://")):
        return _resp(400, {"error": "URL must start with http:// or https://"})

    if not _EMAIL_RE.match(email):
        return _resp(400, {"error": "Please provide a valid email address."})

    api_base = _get_api_base_url(event)

    # ── Scrape product (fast, no JS rendering — must stay within 29s API GW limit) ──
    logger.info("Scraping URL: %s (product_name=%r)", url, product_name)
    product = scrape_product(url, render=False, product_name=product_name)
    if product and product.get("price"):
        logger.info("Found product: %s at %s %s", product["name"], product["currency"], product["price"])
    else:
        logger.info("No price found via fast scrape (may be JS-rendered); subscribing anyway")
        product = None

    now = datetime.now(timezone.utc).isoformat()
    products_table = dynamodb.Table(os.environ["PRODUCTS_TABLE"])
    subs_table = dynamodb.Table(os.environ["SUBSCRIPTIONS_TABLE"])

    # ── Upsert product ────────────────────────────────────────────────────────
    if product:
        products_table.put_item(Item={
            "productUrl": url,
            "productName": product["name"],
            "currentPrice": Decimal(str(product["price"])),
            "currency": product.get("currency", "USD"),
            "lastChecked": now,
        })
    else:
        # Save a stub so the scheduled scraper picks it up on its next run
        products_table.put_item(Item={
            "productUrl": url,
            "productName": product_name or url[:120],
            "currentPrice": Decimal("0"),
            "currency": "USD",
            "lastChecked": now,
        })

    # ── Check for existing subscription ───────────────────────────────────────
    existing_resp = subs_table.query(
        IndexName="productUrl-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("productUrl").eq(url),
        FilterExpression=boto3.dynamodb.conditions.Attr("email").eq(email),
    )
    existing_items = existing_resp.get("Items", [])

    if existing_items:
        item = existing_items[0]
        if item.get("active"):
            return _resp(409, {
                "error": "You're already subscribed to price alerts for this product.",
                "product": {
                    "name": product["name"],
                    "price": float(product["price"]),
                    "currency": product.get("currency", "USD"),
                } if product else None,
            })
        # Reactivate — regenerate unsubscribe URL in case API base changed
        unsub_token = item["unsubscribeToken"]
        unsubscribe_url = _build_unsubscribe_url(api_base, unsub_token)
        subs_table.update_item(
            Key={"subscriptionId": item["subscriptionId"]},
            UpdateExpression="SET active = :t, reactivatedAt = :ts, unsubscribeUrl = :url",
            ExpressionAttributeValues={":t": True, ":ts": now, ":url": unsubscribe_url},
        )
        logger.info("Reactivated subscription %s", item["subscriptionId"])
    else:
        # New subscription — store full unsubscribe URL so the notifier doesn't
        # need to know the API base URL (avoids another circular dependency)
        unsub_token = str(uuid.uuid4())
        unsubscribe_url = _build_unsubscribe_url(api_base, unsub_token)
        subs_table.put_item(Item={
            "subscriptionId": str(uuid.uuid4()),
            "email": email,
            "productUrl": url,
            "active": True,
            "subscribedAt": now,
            "unsubscribeToken": unsub_token,
            "unsubscribeUrl": unsubscribe_url,
        })

    # ── Send welcome email ────────────────────────────────────────────────────
    product_payload = {
        "name": product["name"],
        "price": float(product["price"]),
        "currency": product.get("currency", "USD"),
    } if product else None

    try:
        _send_welcome(email, product, url, unsubscribe_url, name_hint=product_name)
        logger.info("Welcome email sent to %s", email)
    except ClientError as exc:
        logger.error("SES send failed: %s", exc)
        return _resp(200, {
            "success": True,
            "emailSent": False,
            "emailError": "Email delivery failed — check SES configuration.",
            "product": product_payload,
        })

    return _resp(200, {
        "success": True,
        "emailSent": True,
        "product": product_payload,
    })
