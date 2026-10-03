"""
Playwright scraper for Ferrari Approved, Cars.com and CarGurus.

Listings are pulled from three places, most reliable first:
  1. JSON responses the page fetches in the background
  2. schema.org JSON-LD embedded in the page
  3. the rendered listing cards (regex fallback)
Detail pages are then visited (with a cache) to fill in interior/exterior
color and dealer city when the search results don't include them.
"""

import asyncio
import json
import random
import re
from urllib.parse import urlencode

from playwright.async_api import async_playwright

from . import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _get(d, path):
    cur = d
    for part in path.split("."):
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is None:
            return None
    if isinstance(cur, dict):
        cur = cur.get("name") or cur.get("value") or cur.get("displayName")
    return cur


def first(d, *paths):
    for p in paths:
        v = _get(d, p)
        if v not in (None, "", [], {}):
            return v
    return ""


PRICE = ("price", "listPrice", "priceValue", "askingPrice", "price.value",
         "offers.price", "pricing.price", "salePrice", "expectedPrice",
         "priceUnformatted")
YEAR = ("year", "modelYear", "carYear", "vehicleModelDate", "productionYear",
        "registrationYear")
MILES = ("mileage", "mileageValue", "odometer", "mileageFromOdometer.value",
         "mileageFromOdometer", "odometerReading", "kilometers")
VIN = ("vin", "VIN", "vehicleIdentificationNumber", "chassisNumber")
EXT = ("exteriorColor", "exteriorColorName", "colorExterior", "color",
       "exterior_color", "bodyColor", "paintColor", "normalizedExteriorColor")
INT = ("interiorColor", "interiorColorName", "colorInterior",
       "vehicleInteriorColor", "interior_color", "upholsteryColor",
       "normalizedInteriorColor")
CITY = ("dealerCity", "sellerCity", "city", "dealer.city", "dealer.address.city",
        "seller.city", "offers.seller.address.addressLocality",
        "address.addressLocality", "dealerAddress.city", "location.city")
STATE = ("dealerState", "sellerRegion", "state", "dealer.state",
         "dealer.address.state", "offers.seller.address.addressRegion",
         "address.addressRegion", "dealerAddress.state", "location.state")


def looks_like_listing(d):
    if not isinstance(d, dict):
        return False
    has_price = any(_get(d, k) not in (None, "") for k in PRICE)
    has_car = any(_get(d, k) not in (None, "") for k in YEAR + MILES + VIN)
    has_name = any(_get(d, k) for k in ("model", "modelName", "name", "title",
                                          "listingTitle", "trimName"))
    return has_car and (has_price or has_name)


def walk_json(obj, found, depth=0):
    if depth > 12:
        return
    if isinstance(obj, dict):
        if looks_like_listing(obj):
            found.append(obj)
            return
        for v in obj.values():
            walk_json(v, found, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            walk_json(v, found, depth + 1)


def from_dict(d, source, base):
    url = str(first(d, "url", "vdpUrl", "detailUrl", "listingUrl", "link",
                    "href", "vehicleDetailUrl") or "")
    if url.startswith("/"):
        url = base.rstrip("/") + url
    return {
        "source": source,
        "title": str(first(d, "title", "name", "listingTitle", "heading") or ""),
        "year": str(first(d, *YEAR) or ""),
        "make": str(first(d, "make", "makeName", "brand", "manufacturer") or ""),
        "model": str(first(d, "model", "modelName", "carModel", "modelDescription") or ""),
        "trim": str(first(d, "trim", "trimName", "version", "vehicleConfiguration") or ""),
        "price": first(d, *PRICE),
        "mileage": first(d, *MILES),
        "vin": str(first(d, *VIN) or ""),
        "exterior_color": str(first(d, *EXT) or ""),
        "interior_color": str(first(d, *INT) or ""),
        "city": str(first(d, *CITY) or ""),
        "state": str(first(d, *STATE) or ""),
        "location": str(first(d, "location", "sellerLocation", "dealerLocation") or ""),
        "dealer": str(first(d, "dealerName", "sellerName", "dealer.name",
                            "seller.name", "offers.seller.name",
                            "serviceProviderName", "dealer") or ""),
        "url": url,
    }


# ---------------------------------------------------------------------------
# Page-level extraction
# ---------------------------------------------------------------------------

async def extract_jsonld(page, source, base):
    out = []
    try:
        blocks = await page.eval_on_selector_all(
            "script[type='application/ld+json']", "els => els.map(e => e.textContent)")
    except Exception:
        return out
    for raw in blocks:
        try:
            data = json.loads(raw)
        except Exception:
            continue
        found = []
        walk_json(data, found)
        out += [from_dict(d, source, base) for d in found]
    return out


CARD_SELECTORS = {
    "carscom": "div.vehicle-card, fuse-card",
    "cargurus": "[data-testid='srp-tile'], [data-cg-ft='car-blade'], [data-testid='srp-listing-tile']",
    "ferrari": "[class*='CarCard'], [class*='car-card'], [class*='VehicleCard'], [data-testid*='card']",
}

NEXT_SELECTORS = {
    "carscom": None,  # paginated via URL
    "cargurus": "button[data-testid='srp-desktop-page-navigation-next-page'], "
                "button[aria-label='Next page'], a[aria-label='Next page']",
    "ferrari": "button:has-text('Load more'), button:has-text('Show more'), "
               "button:has-text('View more')",
}


async def extract_cards(page, key, source, base):
    out = []
    for c in await page.query_selector_all(CARD_SELECTORS[key]):
        try:
            text = (await c.inner_text()).strip()
        except Exception:
            continue
        if not text:
            continue
        link = await c.query_selector("a[href]")
        href = (await link.get_attribute("href")) if link else ""
        if href and href.startswith("/"):
            href = base + href

        details = await c.get_attribute("data-vehicle-details")  # Cars.com
        rec = None
        if details:
            try:
                rec = from_dict(json.loads(details), source, base)
            except Exception:
                rec = None
        if rec is None:
            rec = {"source": source, "title": "", "year": "", "make": "", "model": "",
                   "trim": "", "price": "", "mileage": "", "vin": "",
                   "exterior_color": "", "interior_color": "", "city": "",
                   "state": "", "location": "", "dealer": "", "url": ""}
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not rec["title"]:
            rec["title"] = next((ln for ln in lines if re.search(r"\b(19|20)\d{2}\b.*ferrari", ln, re.I)),
                                lines[0])
        if not rec["year"]:
            m = re.search(r"\b(20[0-2]\d)\b", rec["title"])
            rec["year"] = m.group(1) if m else ""
        if not rec["price"]:
            m = re.search(r"\$\s?([\d,]{5,})", text)
            rec["price"] = m.group(1) if m else ""
        if not rec["mileage"]:
            m = re.search(r"([\d,]+)\s*(mi\.?|miles)\b", text, re.I)
            rec["mileage"] = m.group(1) if m else ""
        if not rec["location"]:
            m = re.search(r"([A-Z][A-Za-z .'\-]+,\s*[A-Z]{2})\b", text)
            rec["location"] = m.group(1) if m else ""
        m = re.search(r"Ext(?:erior)?\.?\s*colou?r:?\s*([^\n]+)", text, re.I)
        if m and not rec["exterior_color"]:
            rec["exterior_color"] = m.group(1)
        m = re.search(r"Int(?:erior)?\.?\s*colou?r:?\s*([^\n]+)", text, re.I)
        if m and not rec["interior_color"]:
            rec["interior_color"] = m.group(1)
        rec["url"] = rec["url"] or href or ""
        out.append(rec)
    return out


async def enrich_from_detail(page, rec):
    """Open a listing's detail page and fill in colors / city / VIN."""
    await page.goto(rec["url"], wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(2500)
    for d in await extract_jsonld(page, rec["source"], ""):
        for k in ("exterior_color", "interior_color", "city", "state", "vin", "dealer"):
            if not rec.get(k) and d.get(k):
                rec[k] = d[k]
    text = await page.inner_text("body")
    pats = {
        "exterior_color": r"Exterior\s*colou?r\s*[:\n]\s*([^\n]+)",
        "interior_color": r"Interior\s*colou?r\s*[:\n]\s*([^\n]+)",
        "vin": r"\bVIN\s*[:#\n]?\s*([A-HJ-NPR-Z0-9]{17})\b",
    }
    for k, p in pats.items():
        if not rec.get(k):
            m = re.search(p, text, re.I)
            if m:
                rec[k] = m.group(1).strip()
    if not rec.get("city") and not rec.get("location"):
        m = re.search(r"\b([A-Z][A-Za-z .'\-]{2,}),\s*([A-Z]{2})\s+\d{5}\b", text)
        if m:
            rec["city"], rec["state"] = m.group(1), m.group(2)


# ---------------------------------------------------------------------------
# Search URLs
# ---------------------------------------------------------------------------

def carscom_urls():
    base = [("stock_type", "used"), ("makes[]", "ferrari"),
            ("maximum_distance", "all"), ("zip", config.HOME_ZIP),
            ("page_size", "100"), ("sort", "list_price")]
    base += [("models[]", s) for s in config.CARSCOM_MODEL_SLUGS]
    return ["https://www.cars.com/shopping/results/?" + urlencode(base + [("page", p)])
            for p in range(1, config.MAX_PAGES_PER_SEARCH + 1)]


SITES = {
    "ferrari":  ("Ferrari Approved", "https://preowned.ferrari.com", lambda: config.FERRARI_SEARCH_URLS),
    "carscom":  ("Cars.com", "https://www.cars.com", carscom_urls),
    "cargurus": ("CarGurus", "https://www.cargurus.com", lambda: config.CARGURUS_SEARCH_URLS),
}


async def pause(rng):
    await asyncio.sleep(random.uniform(*rng))


async def scrape_site(browser, key, log):
    source, base, url_fn = SITES[key]
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width": 1366, "height": 900},
                                    timezone_id=config.TIMEZONE)
    page = await ctx.new_page()
    api_hits = []

    async def on_response(resp):
        if "json" not in resp.headers.get("content-type", ""):
            return
        try:
            data = await resp.json()
        except Exception:
            return
        found = []
        walk_json(data, found)
        api_hits.extend(found)

    page.on("response", on_response)
    records = []

    for url in url_fn():
        log(f"[{source}] {url[:120]}")
        before = len(records) + len(api_hits)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            pages_done = 0
            while True:
                for _ in range(8):
                    await page.mouse.wheel(0, 2500)
                    await page.wait_for_timeout(600)
                records += await extract_jsonld(page, source, base)
                records += await extract_cards(page, key, source, base)
                pages_done += 1
                nxt = NEXT_SELECTORS[key]
                if not nxt or pages_done >= config.MAX_PAGES_PER_SEARCH:
                    break
                btn = await page.query_selector(nxt)
                if not btn or not await btn.is_enabled():
                    break
                await btn.click()
                await pause(config.PAGE_DELAY_SECONDS)
        except Exception as e:
            log(f"  ! {e.__class__.__name__}: {str(e)[:150]}")
        await pause(config.PAGE_DELAY_SECONDS)
        # Cars.com: stop paging once a page adds nothing new
        if key == "carscom" and len(records) + len(api_hits) == before:
            break

    records = [from_dict(d, source, base) for d in api_hits] + records
    await ctx.close()
    log(f"[{source}] {len(records)} raw records")
    return records


async def enrich(browser, records, log):
    ctx = await browser.new_context(user_agent=UA, locale="en-US")
    page = await ctx.new_page()
    n = 0
    for rec in records:
        if n >= config.MAX_DETAIL_PAGES_PER_RUN:
            log("  detail-page cap reached; the rest are filled on later runs")
            break
        try:
            await enrich_from_detail(page, rec)
        except Exception as e:
            log(f"  ! detail {rec['url'][:80]}: {str(e)[:100]}")
        n += 1
        await pause(config.DETAIL_DELAY_SECONDS)
    await ctx.close()


async def run_scrape(sites, log, needs_detail):
    """needs_detail(records) -> list of records to enrich (lets caller use a cache)."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True,
                                          args=["--disable-blink-features=AutomationControlled"])
        allrecs = []
        for key in sites:
            try:
                allrecs += await scrape_site(browser, key, log)
            except Exception as e:
                log(f"[{key}] failed: {e}")
        todo = needs_detail(allrecs)
        if todo:
            log(f"Visiting {min(len(todo), config.MAX_DETAIL_PAGES_PER_RUN)} detail pages")
            await enrich(browser, todo, log)
        await browser.close()
    return allrecs
