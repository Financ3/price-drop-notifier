"""
notifier/handler.py — SNS-triggered fan-out email sender

Triggered by the 'price-drop-events' SNS topic.
For each price-drop event, queries DynamoDB for all active subscribers
of that product and sends a personalised price-drop email to each one.

This is the fan-out layer: one SNS message → N individual SES emails.
"""

import json
import logging
import os

import boto3
from botocore.exceptions import ClientError

from email_utils import build_price_drop_email

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
ses = boto3.client("ses")


def _get_active_subscribers(subs_table, product_url: str) -> list[dict]:
    """Query the productUrl-index GSI and filter for active subscriptions."""
    subscribers = []
    query_kwargs = {
        "IndexName": "productUrl-index",
        "KeyConditionExpression": boto3.dynamodb.conditions.Key("productUrl").eq(product_url),
        "FilterExpression": boto3.dynamodb.conditions.Attr("active").eq(True),
    }
    while True:
        resp = subs_table.query(**query_kwargs)
        subscribers.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key
    return subscribers


def lambda_handler(event: dict, context) -> dict:
    subs_table = dynamodb.Table(os.environ["SUBSCRIPTIONS_TABLE"])
    sender = os.environ["SENDER_EMAIL"]

    sent_count = 0
    failed_count = 0

    for record in event.get("Records", []):
        try:
            payload = json.loads(record["Sns"]["Message"])
        except (KeyError, json.JSONDecodeError) as exc:
            logger.error("Could not parse SNS message: %s — %s", record, exc)
            continue

        product_url = payload.get("productUrl", "")
        product_name = payload.get("productName", "Unknown Product")
        old_price = float(payload.get("oldPrice", 0))
        new_price = float(payload.get("newPrice", 0))
        currency = payload.get("currency", "USD")

        logger.info(
            "Processing price drop for '%s': %.2f → %.2f",
            product_name, old_price, new_price,
        )

        subscribers = _get_active_subscribers(subs_table, product_url)
        logger.info("Found %d active subscriber(s) for %s", len(subscribers), product_url)

        for sub in subscribers:
            email = sub.get("email", "")
            # unsubscribeUrl is stored at subscription time by the subscribe Lambda,
            # avoiding the need for this function to know the API base URL.
            unsubscribe_url = sub.get("unsubscribeUrl", "")

            if not email:
                continue

            template = build_price_drop_email(
                product_name=product_name,
                old_price=old_price,
                new_price=new_price,
                currency=currency,
                product_url=product_url,
                unsubscribe_url=unsubscribe_url,
            )

            try:
                ses.send_email(
                    Source=sender,
                    Destination={"ToAddresses": [email]},
                    Message={
                        "Subject": {"Data": template["subject"], "Charset": "UTF-8"},
                        "Body": {
                            "Html": {"Data": template["html"], "Charset": "UTF-8"},
                            "Text": {"Data": template["text"], "Charset": "UTF-8"},
                        },
                    },
                )
                sent_count += 1
                logger.info("Sent price-drop email to %s", email)
            except ClientError as exc:
                failed_count += 1
                logger.error("Failed to send to %s: %s", email, exc)

    logger.info("Notification run complete: sent=%d failed=%d", sent_count, failed_count)
    return {
        "statusCode": 200,
        "body": json.dumps({"sent": sent_count, "failed": failed_count}),
    }
