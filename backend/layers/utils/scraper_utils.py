"""
scraper_utils.py — Price extraction utilities for the Price Drop Notifier.

Strategy (applied in order):
  1. JSON-LD Schema.org data   — most reliable, used by major e-commerce sites
  2. OpenGraph / meta price tags
  3. CSS selector heuristics   — common class/id patterns
  4. Regex sweep               — last-resort pattern match on page text

Limitations:
  - JavaScript-rendered pages (React/Next.js SPAs) require a JS-capable
    fetcher. Set SCRAPER_API_KEY to route all requests through ScraperAPI
    with render=true, which executes JavaScript before returning HTML.
  - Sites with aggressive anti-bot measures (Amazon, Cloudflare-protected
    pages) will block or return empty content without ScraperAPI.
"""

import json
import os
import re
import logging
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Request headers that mimic a real browser
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# CSS selectors tried in order — more specific selectors first
_PRICE_SELECTORS = [
    # Schema.org microdata attributes
    ('[itemprop="price"]', "content"),
    ('[itemprop="price"]', "text"),
    # OpenGraph meta tags
    ('meta[property="product:price:amount"]', "content"),
    ('meta[property="og:price:amount"]', "content"),
    # Amazon
    ("#priceblock_ourprice", "text"),
    ("#priceblock_dealprice", "text"),
    (".a-price > .a-offscreen", "text"),
    ("#price_inside_buybox", "text"),
    # Best Buy
    (".priceView-hero-price span[aria-hidden='true']", "text"),
    # data-test-id / data-testid attributes (used by Wayfair, Target, many React apps)
    ('[data-test-id*="Price"]', "text"),
    ('[data-test-id*="price"]', "text"),
    ('[data-testid*="Price"]', "text"),
    ('[data-testid*="price"]', "text"),
    ('[data-name-id*="Price"]', "text"),
    # Generic e-commerce patterns
    (".product-price", "text"),
    (".price--main", "text"),
    (".price-box .price", "text"),
    (".woocommerce-Price-amount", "text"),
    ('[class*="ProductPrice"]', "text"),
    ('[class*="product-price"]', "text"),
    ('[class*="current-price"]', "text"),
    ('[class*="sale-price"]', "text"),
    ("#price", "text"),
    (".price", "text"),
]

# Regex patterns to extract numeric price values
_PRICE_PATTERNS = [
    r'\$\s*(\d{1,6}(?:,\d{3})*(?:\.\d{2})?)',       # $1,234.56
    r'(\d{1,6}(?:,\d{3})*(?:\.\d{2})?)\s*USD',       # 1234.56 USD
    r'USD\s*(\d{1,6}(?:,\d{3})*(?:\.\d{2})?)',       # USD 1234.56
    r'£\s*(\d{1,6}(?:,\d{3})*(?:\.\d{2})?)',         # £999.99
    r'€\s*(\d{1,6}(?:[.,]\d{3})*(?:[.,]\d{2})?)',    # €1.234,56 or €1,234.56
    r'(\d{1,6}(?:,\d{3})*\.\d{2})',                  # bare decimal fallback
]


def _extract_price_from_text(text: str) -> Optional[float]:
    """Pull the first recognisable price out of an arbitrary string."""
    if not text:
        return None
    # Strip whitespace and normalise newlines
    text = " ".join(text.split())
    for pattern in _PRICE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            raw = match.group(1).replace(",", "")
            # Handle European decimal comma (€1.234,56 → 1234.56)
            if raw.count(".") > 1:
                raw = raw.replace(".", "").replace(",", ".")
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def _detect_currency(text: str) -> str:
    if "£" in text:
        return "GBP"
    if "€" in text:
        return "EUR"
    return "USD"


def _find_anchor_element(soup: BeautifulSoup, product_name: str):
    """Return the element whose text most tightly matches product_name.

    Scores every element by the ratio len(product_name) / len(element_text).
    A score of 1.0 means the element's entire text IS the product name.
    Large containers that merely contain the name somewhere score much lower,
    so we end up pointing at the tightest, most specific title element on the
    page rather than a wrapper div.

    Falls back to the H1 if no substring match is found.
    """
    needle = product_name.lower().strip()
    if not needle:
        return soup.find("h1")

    _SKIP = {"script", "style", "meta", "link", "head", "noscript"}
    best_el = None
    best_score = 0.0

    for el in soup.find_all(True):
        if el.name in _SKIP:
            continue
        text = el.get_text(strip=True)
        if not text:
            continue
        text_lower = text.lower()
        if needle not in text_lower:
            continue
        score = len(needle) / len(text_lower)
        if score > best_score:
            best_score = score
            best_el = el

    return best_el or soup.find("h1")


def _dom_distance(el_a, el_b) -> int:
    """Number of edges traversed in the DOM tree between two elements.

    Builds the ancestor chain of el_a (with depth), then walks el_b's
    ancestors until a common node is found. Returns the sum of both depths
    at that point, giving the shortest path length between the two elements.
    """
    ancestors_a: dict = {}
    node = el_a
    depth = 0
    while node:
        ancestors_a[id(node)] = depth
        node = getattr(node, "parent", None)
        depth += 1
    node = el_b
    depth = 0
    while node:
        if id(node) in ancestors_a:
            return ancestors_a[id(node)] + depth
        node = getattr(node, "parent", None)
        depth += 1
    return 10_000  # disconnected


def _extract_title(soup: BeautifulSoup) -> str:
    """Best-effort product name extraction.

    Priority: JSON-LD Product name → H1 → OpenGraph title → page <title>.
    H1 is intentionally ranked above OpenGraph because og:title frequently
    includes brand prefixes and site suffixes (e.g. "Brand X Widget | Wayfair")
    whereas the H1 contains only the product title as shown on the page.
    """
    # JSON-LD Product name — most explicit when present
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") == "Product" and data.get("name"):
                return str(data["name"]).strip()
        except Exception:
            pass

    # H1 heading — the canonical visible product title on product pages
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    # OpenGraph title — often has brand/site suffixes, use as fallback
    og = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()

    # Page <title> — last resort
    title_tag = soup.find("title")
    if title_tag:
        return title_tag.get_text(strip=True)

    return "Unknown Product"


def _try_json_ld(soup: BeautifulSoup, page_url: str = "") -> Optional[dict]:
    """Extract price/name/currency from JSON-LD Schema.org data.

    Collects all Product entries, then prefers the one whose url/id path
    matches the current page URL (Option B). This correctly ignores prices
    from recommended/related products embedded on the same page.
    Falls back to the first Product found if none match.
    """
    page_path = urlparse(page_url).path.rstrip("/") if page_url else ""

    candidates = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0]
            if data.get("@type") != "Product":
                continue

            offers = data.get("offers", {})
            if isinstance(offers, list):
                offers = offers[0]

            price_raw = offers.get("price") or data.get("price")
            name = data.get("name")
            currency = offers.get("priceCurrency", "USD")

            if price_raw is None:
                continue
            try:
                price = float(str(price_raw).replace(",", ""))
            except ValueError:
                continue

            ld_url_raw = data.get("url") or data.get("@id") or ""
            ld_path = urlparse(ld_url_raw).path.rstrip("/") if ld_url_raw.startswith("http") else ld_url_raw.rstrip("/")
            url_match = bool(page_path and ld_path and page_path == ld_path)

            candidates.append({
                "name": str(name).strip() if name else None,
                "price": price,
                "currency": currency,
                "url_match": url_match,
            })
        except Exception:
            pass

    if not candidates:
        return None

    matched = [c for c in candidates if c["url_match"]]
    chosen = matched[0] if matched else candidates[0]
    return {"name": chosen["name"], "price": chosen["price"], "currency": chosen["currency"]}


def _try_selectors(soup: BeautifulSoup, anchor=None) -> Optional[dict]:
    """Try CSS selectors, scoring each candidate by DOM distance to anchor.

    anchor is the element we use as a proximity reference — typically either
    the element matching the user-supplied product name, or the H1 heading.
    The price closest to that anchor in the DOM is almost certainly the main
    product price; related-product prices live in separate subtrees.
    """
    if anchor is None:
        anchor = soup.find("h1")
    best_dist = 10_001
    best_price: Optional[float] = None
    best_currency = "USD"

    for selector, attr_type in _PRICE_SELECTORS:
        for el in soup.select(selector):
            if attr_type == "text":
                raw = el.get_text(separator=" ", strip=True)
            else:
                raw = el.get(attr_type, "")
            price = _extract_price_from_text(raw)
            if not price or price <= 0:
                continue
            dist = _dom_distance(el, anchor) if anchor else 0
            if dist < best_dist:
                best_dist = dist
                best_price = price
                best_currency = _detect_currency(raw)

    if best_price is not None:
        return {"price": best_price, "currency": best_currency}
    return None


def _try_proximity_sweep(soup: BeautifulSoup, anchor=None) -> Optional[dict]:
    """Full proximity sweep of all short-text leaf elements.

    Scans every leaf-ish element whose visible text is ≤ 30 chars, extracts
    any price pattern, and returns the candidate closest to anchor.

    This is the primary strategy for sites with obfuscated CSS class names
    (Wayfair, most React/CSS-in-JS apps) where selector-based strategies
    yield nothing, and is also the most accurate strategy when the user has
    provided a product name (anchor points directly at their product's title
    element rather than the generic H1).
    """
    if anchor is None:
        anchor = soup.find("h1")
    _SKIP_TAGS = {"script", "style", "meta", "link", "head", "noscript"}
    best_dist = 10_001
    best_price: Optional[float] = None
    best_currency = "USD"

    for el in soup.find_all(True):
        if el.name in _SKIP_TAGS:
            continue
        # Leaf-ish only: skip elements that have child tags (they're containers)
        if any(hasattr(c, "name") and c.name for c in el.children):
            continue
        text = el.get_text(separator=" ", strip=True)
        if not text or len(text) > 30:
            continue
        price = _extract_price_from_text(text)
        if not price or not (0 < price < 1_000_000):
            continue
        dist = _dom_distance(el, anchor) if anchor else 0
        if dist < best_dist:
            best_dist = dist
            best_price = price
            best_currency = _detect_currency(text)

    if best_price is not None:
        return {"price": best_price, "currency": best_currency}
    return None


def _fetch_html(url: str, render: bool = True) -> Optional[str]:
    """
    Fetch the page HTML.

    When render=True and SCRAPER_API_KEY is set, routes through ScraperAPI
    with render=true so JavaScript-rendered pages (React/Next.js) are fully
    executed before the HTML is returned. This can take 30-60 seconds.

    When render=False, skips ScraperAPI entirely and makes a direct request
    (fast, suitable for synchronous API Gateway paths with a 29s hard limit).
    """
    scraper_key = os.environ.get("SCRAPER_API_KEY", "")
    if render and scraper_key:
        from urllib.parse import quote_plus
        api_url = (
            f"https://api.scraperapi.com/?api_key={scraper_key}"
            f"&url={quote_plus(url)}&render=true"
        )
        try:
            resp = requests.get(api_url, timeout=60)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.warning("ScraperAPI render fetch failed (%s), falling back to direct", e)

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None


def scrape_product(url: str, render: bool = True, product_name: str = "") -> Optional[dict]:
    """
    Scrape a product page and return a dict with keys:
      - name     (str)
      - price    (float)
      - currency (str, ISO 4217)

    Returns None if the page cannot be fetched or no price is found.

    product_name — optional name provided by the user at sign-up time.
      When given, we locate the element on the page whose text most tightly
      matches it and use that as the proximity anchor for price extraction.
      This reliably targets the correct product even when a page has many
      other prices (recommendations, carousels, sidebars).
      Falls back to the H1 heading when not provided.

    render — pass False for fast scraping (no ScraperAPI JS rendering) when
      inside an API Gateway request (29s hard limit). The scheduled scraper
      uses render=True (default) since it runs standalone.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.error("Invalid URL: %s", url)
        return None

    html = _fetch_html(url, render=render)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Resolve the proximity anchor once — either the element whose text matches
    # the user-supplied product name, or the page H1 as a fallback.
    anchor = _find_anchor_element(soup, product_name) if product_name else soup.find("h1")

    # Strategy 1 — JSON-LD with page-URL validation (Option B)
    result = _try_json_ld(soup, page_url=url)
    if result:
        result.setdefault("name", _extract_title(soup))
        return result

    # Strategy 2 — CSS selectors anchored to the product title (Option A)
    result = _try_selectors(soup, anchor=anchor)
    if result:
        result["name"] = _extract_title(soup)
        return result

    # Strategy 3 — Full proximity sweep (handles obfuscated class names)
    result = _try_proximity_sweep(soup, anchor=anchor)
    if result:
        result["name"] = _extract_title(soup)
        return result

    logger.warning("No price found on %s", url)
    return None
