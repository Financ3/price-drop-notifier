"""
email_utils.py — HTML email template builder for the Price Drop Notifier.

All emails share a base template with consistent branding. Three template
types are supported:
  - welcome:    Sent after a successful subscription
  - price_drop: Sent when a tracked product's price falls
  - unsub_confirm: Returned as an HTML page from the unsubscribe endpoint
"""

from typing import Optional


# ── Base template ─────────────────────────────────────────────────────────────

_BASE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <style>
    body {{
      margin: 0; padding: 0;
      background: #0f0f1a;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #e2e8f0;
    }}
    .wrapper {{
      max-width: 580px;
      margin: 40px auto;
      background: #1a1a2e;
      border-radius: 16px;
      overflow: hidden;
      border: 1px solid rgba(99,102,241,0.3);
    }}
    .header {{
      background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
      padding: 32px 40px;
      text-align: center;
    }}
    .header .logo {{
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 3px;
      text-transform: uppercase;
      color: rgba(255,255,255,0.75);
      margin-bottom: 8px;
    }}
    .header h1 {{
      margin: 0;
      font-size: 24px;
      font-weight: 800;
      color: #fff;
    }}
    .body {{
      padding: 36px 40px;
    }}
    .body p {{
      margin: 0 0 16px;
      line-height: 1.65;
      color: #cbd5e1;
      font-size: 15px;
    }}
    .product-card {{
      background: rgba(99,102,241,0.08);
      border: 1px solid rgba(99,102,241,0.25);
      border-radius: 12px;
      padding: 20px 24px;
      margin: 24px 0;
    }}
    .product-name {{
      font-size: 16px;
      font-weight: 600;
      color: #e2e8f0;
      margin: 0 0 12px;
    }}
    .price-row {{
      display: flex;
      align-items: center;
      gap: 16px;
      flex-wrap: wrap;
    }}
    .price-badge {{
      display: inline-block;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      color: #fff;
      font-size: 22px;
      font-weight: 800;
      padding: 6px 18px;
      border-radius: 8px;
    }}
    .price-old {{
      text-decoration: line-through;
      color: #64748b;
      font-size: 18px;
    }}
    .savings-badge {{
      background: rgba(34,197,94,0.15);
      border: 1px solid rgba(34,197,94,0.4);
      color: #4ade80;
      font-size: 13px;
      font-weight: 700;
      padding: 4px 12px;
      border-radius: 20px;
    }}
    .cta-button {{
      display: inline-block;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      color: #fff !important;
      text-decoration: none;
      font-weight: 700;
      font-size: 15px;
      padding: 14px 32px;
      border-radius: 10px;
      margin: 8px 8px 8px 0;
    }}
    .footer {{
      border-top: 1px solid rgba(255,255,255,0.06);
      padding: 24px 40px;
      text-align: center;
      font-size: 12px;
      color: #475569;
      line-height: 1.6;
    }}
    .footer a {{
      color: #6366f1;
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="header">
      <div class="logo">Price Drop Notifier</div>
      <h1>{header}</h1>
    </div>
    <div class="body">
      {body}
    </div>
    <div class="footer">
      {footer}
    </div>
  </div>
</body>
</html>"""


def _format_price(price: float, currency: str = "USD") -> str:
    symbols = {"USD": "$", "GBP": "£", "EUR": "€"}
    sym = symbols.get(currency, "$")
    return f"{sym}{price:,.2f}"


# ── Welcome / confirmation email ──────────────────────────────────────────────

def build_welcome_email(
    product_name: str,
    product_url: str,
    unsubscribe_url: str,
    price: Optional[float] = None,
    currency: str = "USD",
) -> dict:
    """Returns {'subject': str, 'html': str, 'text': str}.

    price may be None for JS-rendered sites where an initial fast scrape
    couldn't extract a price. In that case the email omits the price and
    tells the subscriber we'll notify them on the first drop we detect.
    """
    if price is not None:
        price_str = _format_price(price, currency)
        price_block = f"""
        <div class="price-row">
          <span class="price-badge">{price_str}</span>
          <span style="color:#94a3b8;font-size:13px;">current price</span>
        </div>"""
        subject = f"Tracking {product_name} \u2014 Currently {price_str}"
        text_price = f"Current price: {price_str}\n"
    else:
        price_block = """
        <div style="color:#94a3b8;font-size:13px;margin-top:4px;">
          We\u2019ll check the price on our next scheduled run and email you the moment it drops.
        </div>"""
        subject = f"You\u2019re now tracking {product_name}"
        text_price = "Price will be checked on our next scheduled run.\n"

    body = f"""
      <p>You're all set! We'll email you as soon as the price drops on:</p>
      <div class="product-card">
        <div class="product-name">{_esc(product_name)}</div>
        {price_block}
      </div>
      <a href="{_esc(product_url)}" class="cta-button">View Product</a>
    """

    footer = (
        f"You subscribed to price alerts for <em>{_esc(product_name)}</em>.<br>"
        f'No longer interested? <a href="{_esc(unsubscribe_url)}">Unsubscribe</a>'
    )

    html = _BASE_HTML.format(
        title="You're tracking a product",
        header="You're now tracking this product!",
        body=body,
        footer=footer,
    )

    text = (
        f"Price Drop Notifier \u2014 Subscription confirmed\n\n"
        f"You're tracking: {product_name}\n"
        f"{text_price}"
        f"Product URL: {product_url}\n\n"
        f"We'll email you when the price drops.\n\n"
        f"Unsubscribe: {unsubscribe_url}"
    )

    return {"subject": subject, "html": html, "text": text}


# ── Price drop notification email ─────────────────────────────────────────────

def build_price_drop_email(
    product_name: str,
    old_price: float,
    new_price: float,
    currency: str,
    product_url: str,
    unsubscribe_url: str,
) -> dict:
    old_str = _format_price(old_price, currency)
    new_str = _format_price(new_price, currency)
    savings = old_price - new_price
    savings_str = _format_price(savings, currency)
    pct = round((savings / old_price) * 100)

    body = f"""
      <p>Great news — the price just dropped on a product you're watching!</p>
      <div class="product-card">
        <div class="product-name">{_esc(product_name)}</div>
        <div class="price-row">
          <span class="price-old">{old_str}</span>
          <span class="price-badge">{new_str}</span>
          <span class="savings-badge">Save {savings_str} ({pct}% off)</span>
        </div>
      </div>
      <a href="{_esc(product_url)}" class="cta-button">View Deal</a>
    """

    footer = (
        f"You subscribed to price alerts for <em>{_esc(product_name)}</em>.<br>"
        f'Want to stop receiving alerts? <a href="{_esc(unsubscribe_url)}">Unsubscribe</a>'
    )

    html = _BASE_HTML.format(
        title=f"Price Drop: {product_name}",
        header=f"Price dropped to {new_str}!",
        body=body,
        footer=footer,
    )

    text = (
        f"Price Drop Alert — {product_name}\n\n"
        f"Was: {old_str}\n"
        f"Now: {new_str}  (save {savings_str}, {pct}% off)\n\n"
        f"View product: {product_url}\n\n"
        f"Unsubscribe: {unsubscribe_url}"
    )

    return {
        "subject": f"Price Drop! {product_name} is now {new_str} (was {old_str})",
        "html": html,
        "text": text,
    }


# ── Unsubscribe confirmation page (returned as HTML from Lambda) ───────────────

def build_unsubscribe_page(product_name: Optional[str] = None) -> str:
    name_blurb = f" from <strong>{_esc(product_name)}</strong> price alerts" if product_name else ""

    body = f"""
      <p>You've been successfully unsubscribed{name_blurb}.</p>
      <p>You will no longer receive price drop notifications for this product.
         If you change your mind, just visit the app to subscribe again.</p>
    """

    footer = "© Price Drop Notifier"

    return _BASE_HTML.format(
        title="Unsubscribed",
        header="You've been unsubscribed",
        body=body,
        footer=footer,
    )


def build_already_unsubscribed_page() -> str:
    body = "<p>This unsubscribe link has already been used or is invalid.</p>"
    footer = "© Price Drop Notifier"
    return _BASE_HTML.format(
        title="Already unsubscribed",
        header="Nothing to do",
        body=body,
        footer=footer,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Minimal HTML escaping for values interpolated into templates."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
