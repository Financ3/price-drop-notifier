"""
scraper/handler.py — Scheduled price-check Lambda (EventBridge trigger)

Flow per run:
  1. Collect all unique product URLs that have at least one active subscriber
  2. For each URL, scrape the current price
  3. Compare against the stored price in DynamoDB
  4. If the price dropped → publish a message to the SNS topic
  5. Update the stored price regardless of direction
"""

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3

from scraper_utils import scrape_product

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")


def lambda_handler(event: dict, context) -> dict:
    products_table = dynamodb.Table(os.environ["PRODUCTS_TABLE"])
    subs_table = dynamodb.Table(os.environ["SUBSCRIPTIONS_TABLE"])
    topic_arn = os.environ["SNS_TOPIC_ARN"]

    # ── Gather active subscriptions ───────────────────────────────────────────
    active_urls: set[str] = set()
    scan_kwargs: dict = {
        "FilterExpression": boto3.dynamodb.conditions.Attr("active").eq(True),
        "ProjectionExpression": "productUrl",
    }
    while True:
        resp = subs_table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            active_urls.add(item["productUrl"])
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    logger.info("Checking %d actively-tracked product(s)", len(active_urls))

    now = datetime.now(timezone.utc).isoformat()
    results = {"checked": 0, "price_drops": 0, "errors": 0}

    for url in active_urls:
        results["checked"] += 1
        logger.info("Scraping: %s", url)

        # Fetch current stored price
        stored_resp = products_table.get_item(Key={"productUrl": url})
        stored_item = stored_resp.get("Item")
        if not stored_item:
            logger.warning("No product record for %s — skipping", url)
            continue

        stored_price = float(stored_item.get("currentPrice", 0))
        product_name = stored_item.get("productName", "Unknown Product")
        currency = stored_item.get("currency", "USD")

        # Scrape fresh price — pass stored name as anchor for proximity search
        product = scrape_product(url, product_name=product_name)
        if not product or not product.get("price"):
            logger.warning("Could not scrape price for %s", url)
            results["errors"] += 1
            continue

        new_price = float(product["price"])
        logger.info(
            "%s: stored=%.2f new=%.2f (%s)",
            product_name, stored_price, new_price, currency,
        )

        # ── Price drop detected ───────────────────────────────────────────────
        if new_price < stored_price:
            results["price_drops"] += 1
            logger.info("PRICE DROP: %s %.2f → %.2f", product_name, stored_price, new_price)

            message = json.dumps({
                "productUrl": url,
                "productName": product.get("name", product_name),
                "oldPrice": stored_price,
                "newPrice": new_price,
                "currency": currency,
            })

            sns.publish(
                TopicArn=topic_arn,
                Message=message,
                Subject=f"Price Drop: {product_name}",
                MessageAttributes={
                    "eventType": {
                        "DataType": "String",
                        "StringValue": "PRICE_DROP",
                    }
                },
            )

        # ── Always update the stored price ────────────────────────────────────
        products_table.update_item(
            Key={"productUrl": url},
            UpdateExpression=(
                "SET currentPrice = :price, "
                "productName = :name, "
                "lastChecked = :ts"
            ),
            ExpressionAttributeValues={
                ":price": Decimal(str(new_price)),
                ":name": product.get("name", product_name),
                ":ts": now,
            },
        )

    logger.info("Run complete: %s", results)
    return {"statusCode": 200, "body": json.dumps(results)}
