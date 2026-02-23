"""
unsubscribe/handler.py — GET /unsubscribe?token=<token>

Looks up the subscription by its unsubscribe token, marks it inactive,
and returns a self-contained HTML confirmation page.

No authentication is needed because the token itself acts as a secret.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3

from email_utils import build_unsubscribe_page, build_already_unsubscribed_page

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")

_HTML_HEADERS = {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
}


def _html_resp(status: int, html: str) -> dict:
    return {"statusCode": status, "headers": _HTML_HEADERS, "body": html}


def lambda_handler(event: dict, context) -> dict:
    token = (event.get("queryStringParameters") or {}).get("token", "").strip()

    if not token:
        return _html_resp(400, "<h1>Missing unsubscribe token.</h1>")

    subs_table = dynamodb.Table(os.environ["SUBSCRIPTIONS_TABLE"])

    # ── Look up subscription by token ─────────────────────────────────────────
    resp = subs_table.query(
        IndexName="unsubscribeToken-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("unsubscribeToken").eq(token),
        Limit=1,
    )
    items = resp.get("Items", [])

    if not items:
        logger.warning("Unsubscribe token not found: %s", token)
        return _html_resp(404, build_already_unsubscribed_page())

    item = items[0]

    if not item.get("active"):
        logger.info("Token already used (subscription inactive): %s", token)
        return _html_resp(200, build_already_unsubscribed_page())

    # ── Mark subscription inactive ────────────────────────────────────────────
    subs_table.update_item(
        Key={"subscriptionId": item["subscriptionId"]},
        UpdateExpression="SET active = :f, unsubscribedAt = :ts",
        ExpressionAttributeValues={
            ":f": False,
            ":ts": datetime.now(timezone.utc).isoformat(),
        },
    )

    product_name = None
    # Optionally look up product name for the confirmation page
    if "productUrl" in item:
        products_table = dynamodb.Table(os.environ["PRODUCTS_TABLE"])
        prod_resp = products_table.get_item(Key={"productUrl": item["productUrl"]})
        prod = prod_resp.get("Item")
        if prod:
            product_name = prod.get("productName")

    logger.info("Unsubscribed: %s from %s", item.get("email"), item.get("productUrl"))
    return _html_resp(200, build_unsubscribe_page(product_name))
