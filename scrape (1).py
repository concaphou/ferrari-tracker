"""
Playwright scraper for Ferrari Approved (preowned.ferrari.com), Cars.com and
CarGurus.

Listings are pulled from three places, most reliable first:
  1. JSON responses the page fetches in the background
  2. schema.org JSON-LD embedded in the page
  3. the rendered listing cards (site-specific text parsing)
Detail pages are then visited (with a cache) to fill in colors, dealer city
and photos when the search results don't include them.
"""

import asyncio
import json
import os
import random
import re
from urllib.parse import urlencode, urljoin

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
PHOTO_KEYS = ("images", "photos", "pictures", "photoUrls", "imageUrls", "image",
              "media.photos", "primaryPhotoUrl", "mainPictureUrl", "pictureUrl",
              "photoUrl", "imageUrl", "thumbnailUrl", "originalPictureData.url")


def _photo_urls(val, base=""):
    out = []
    items = val if isinstance(val, list) else [val]
    for it in items:
        if isinstance(it, dict):
            it = it.get("url") or it.get("src") or it.get("href") or it.get("contentUrl") or ""
        if isinstance(it, str) and it:
            u = urljoin(base, it.strip()) if base else it.strip()
            if u.startswith("//"):
                u = "https:" + u
            if u.startswith("http") and not re.search(r"\.svg|logo|icon|sprite|placeholder", u, re.I):
                out.append(u)
    return out


def photos_from(d, base=""):
    out = []
    for k in PHOTO_KEYS:
        v = _get(d, k) if "." in k else (d.get(k) if isinstance(d, dict) else None)
        if v:
            out += _photo_urls(v, base)
    return dedupe(out)


def dedupe(urls):
    seen, out = set(), []
    for u in urls:
        key = re.sub(r"[?#].*$", "", u)
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


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


def blank(source):
    return {"source": source, "title": "", "year": "", "make": "", "model": "",
            "trim": "", "body": "", "price": "", "mileage": "", "vin": "",
            "exterior_color": "", "interior_color": "", "city": "", "state": "",
            "location": "", "dealer": "", "url": "", "photos": []}


def from_dict(d, source, base):
    url = str(first(d, "url", "vdpUrl", "detailUrl", "listingUrl", "link",
                    "href", "vehicleDetailUrl") or "")
    if url.startswith("/"):
        url = base.rstrip("/") + url
    r = blank(source)
    r.update({
        "title": str(first(d, "title", "name", "listingTitle", "heading") or ""),
        "year": str(first(d, *YEAR) or ""),
        "make": str(first(d, "make", "makeName", "brand", "manufacturer") or ""),
        "model": str(first(d, "model", "modelName", "carModel", "modelDescription") or ""),
        "trim": str(first(d, "trim", "trimName", "version", "vehicleConfiguration") or ""),
        "body": str(first(d, "bodyType", "bodyStyle", "bodyTypeName") or ""),
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
                            "serviceProviderName") or ""),
        "url": url,
        "photos": photos_from(d, base),
    })
    return r


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
    "cargurus": "[data-testid='srp-tile'], [data-cg-ft='car-blade'], "
                "[data-testid='srp-listing-tile'], div[class*='listing-tile']",
    "ferrari": "[class*='CarCard'], [class*='car-card'], [class*='VehicleCard'], "
               "[class*='vehicle-card'], [data-testid*='card'], li:has(a[href*='/used-ferrari/'])",
}

NEXT_SELECTORS = {
    "carscom": None,  # paginated via URL
    "cargurus": "button[data-testid='srp-desktop-page-navigation-next-page'], "
                "button[aria-label='Next page'], a[aria-label='Next page']",
    "ferrari": "button:has-text('Load more'), button:has-text('Show more'), "
               "button:has-text('View more'), a[aria-label='Next page'], "
               "button[aria-label='Next page']",
}

# "2025 1,911 mi FERRARI APPROVED 296 GTS $450,411 exterior color Argento
#  Nurburgring interior color Nero Available at Continental Autosports"
FERRARI_RE = re.compile(
    r"\b(20\d\d)\s+([\d,]+)\s*mi\b\s+(?:FERRARI APPROVED\s+)?(.+?)\s+"
    r"(\$\s?[\d,]+|Price on request)\s+exterior(?:\s+colou?r)?\s+(.+?)\s+"
    r"interior(?:\s+colou?r)?\s+(.+?)\s+Available at\s+(.+?)"
    r"(?=\s+(?:NEW\s+)?(?:tailor made\s+)?20\d\d\s+[\d,]+\s*mi\b|\s*$)", re.I)


def parse_ferrari_text(text, rec):
    m = FERRARI_RE.search(re.sub(r"\s+", " ", text))
    if not m:
        return False
    y, mi, model, price, ext, intr, dealer = m.groups()
    rec.update(year=y, mileage=mi, title=f"{y} Ferrari {model}", model=model,
               price="" if "request" in price.lower() else price,
               exterior_color=ext, interior_color="" if intr.strip() == "-" else intr,
               dealer=dealer.strip(), make="Ferrari")
    return True


async def card_photos(card, base):
    try:
        srcs = await card.eval_on_selector_all(
            "img, source",
            """els => els.map(e => e.currentSrc || e.getAttribute('src') ||
                     e.getAttribute('data-src') ||
                     (e.getAttribute('srcset')||e.getAttribute('data-srcset')||'').split(' ')[0] || '')""")
    except Exception:
        return []
    return _photo_urls([s for s in srcs if s and not s.startswith("data:")], base)


async def extract_cards(page, key, source, base):
    out = []
    for c in await page.query_selector_all(CARD_SELECTORS[key]):
        try:
            text = (await c.inner_text()).strip()
        except Exception:
            continue
        if not text or not re.search(r"\b20\d\d\b", text):
            continue
        link = await c.query_selector("a[href]")
        href = (await link.get_attribute("href")) if link else ""
        href = urljoin(base, href) if href else ""

        rec = None
        details = await c.get_attribute("data-vehicle-details")  # Cars.com
        if details:
            try:
                rec = from_dict(json.loads(details), source, base)
            except Exception:
                rec = None
        rec = rec or blank(source)
        if key == "ferrari":
            parse_ferrari_text(text, rec)

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not rec["title"]:
            rec["title"] = next((ln for ln in lines if re.search(r"\b20\d\d\b.*(ferrari|488|296|f8)", ln, re.I)),
                                lines[0])
        if not rec["year"]:
            m = re.search(r"\b(20[0-2]\d)\b", rec["title"])
            rec["year"] = m.group(1) if m else ""
        if not rec["price"] and "request" not in text.lower():
            m = re.search(r"\$\s?([\d,]{6,})", text)
            rec["price"] = m.group(1) if m else ""
        if not rec["mileage"]:
            m = re.search(r"([\d,]+)\s*(mi\.?|miles)\b", text, re.I)
            rec["mileage"] = m.group(1) if m else ""
        if not rec["location"]:
            m = re.search(r"([A-Z][A-Za-z .'\-]+,\s*[A-Z]{2})\b", text)
            rec["location"] = m.group(1) if m else ""
        for field, pat in (("exterior_color", r"Exterior colou?r:*\s*([^\n]+)"),
                           ("interior_color", r"Interior colou?r:*\s*([^\n]+)")):
            if not rec[field]:
                m = re.search(pat, text, re.I)
                if m:
                    rec[field] = m.group(1)
        rec["url"] = rec["url"] or href
        rec["photos"] = dedupe(rec["photos"] + await card_photos(c, base))
        out.append(rec)
    return out


async def extract_ferrari_page_text(page, source, base):
    """Fallback for Ferrari Approved when card selectors miss: parse page text."""
    try:
        text = re.sub(r"\s+", " ", await page.inner_text("body"))
    except Exception:
        return []
    out = []
    for m in FERRARI_RE.finditer(text):
        rec = blank(source)
        parse_ferrari_text(m.group(0), rec)
        rec["search_url"] = page.url  # no per-car link found; link to the search page
        out.append(rec)
    return out


async def enrich_from_detail(page, rec):
    """Open a listing's detail page and fill in colors, city, VIN and photos."""
    await page.goto(rec["url"], wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(2500)
    for d in await extract_jsonld(page, rec["source"], rec["url"]):
        for k in ("exterior_color", "interior_color", "city", "state", "vin", "dealer"):
            if not rec.get(k) and d.get(k):
                rec[k] = d[k]
        rec["photos"] = dedupe(rec.get("photos", []) + d.get("photos", []))
    text = await page.inner_text("body")
    pats = {
        "exterior_color": r"Exterior\s*colou?r\s*[:\n]*\s*([^\n]+)",
        "interior_color": r"Interior\s*colou?r\s*[:\n]*\s*([^\n]+)",
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
    if len(rec.get("photos", [])) < 3:
        try:
            og = await page.eval_on_selector_all(
                "meta[property='og:image'], meta[name='twitter:image']",
                "els => els.map(e => e.content)")
            big = await page.eval_on_selector_all(
                "img",
                "els => els.filter(e => (e.naturalWidth||e.width) >= 400)"
                ".map(e => e.currentSrc || e.src)")
            rec["photos"] = dedupe(rec.get("photos", []) + _photo_urls(og + big, rec["url"]))
        except Exception:
            pass
    rec["photos"] = rec.get("photos", [])[:config.MAX_PHOTOS]


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


def cargurus_url(entity):
    return ("https://www.cargurus.com/Cars/inventorylisting/"
            "viewDetailsFilterViewInventoryListing.action?" +
            urlencode({"zip": config.HOME_ZIP, "distance": 50000,
                       "entitySelectingHelper.selectedEntity": entity,
                       "sortDir": "ASC", "sortType": "PRICE"}))


def cargurus_urls():
    urls = [cargurus_url(e) for e in config.CARGURUS_MODEL_IDS.values() if e]
    return urls + list(config.CARGURUS_EXTRA_URLS)


def ferrari_urls():
    return [f"https://preowned.ferrari.com/en-US/r/north-america/used-ferrari/usa/{s}/rfcm"
            for s in config.FERRARI_MODEL_SLUGS] + list(config.FERRARI_EXTRA_URLS)


SITES = {
    "ferrari":  ("Ferrari Approved", "https://preowned.ferrari.com", ferrari_urls),
    "carscom":  ("Cars.com", "https://www.cars.com", carscom_urls),
    "cargurus": ("CarGurus", "https://www.cargurus.com", cargurus_urls),
}


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""

BLOCK_WORDS = re.compile(r"access denied|pardon our interruption|are you a human|verify you are|"
                         r"captcha|unusual traffic|request blocked|forbidden|bot detection|"
                         r"just a moment|enable javascript and cookies", re.I)

DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "debug")


async def diagnose(page, resp, key, n, log):
    """Log what the site actually showed, and save a small screenshot."""
    status = resp.status if resp else "?"
    try:
        title = (await page.title())[:80]
        body = re.sub(r"\s+", " ", await page.inner_text("body"))
    except Exception:
        title, body = "?", ""
    blocked = bool(BLOCK_WORDS.search(title + " " + body[:2000])) or status in (401, 403, 429)
    log(f"  status {status} | title: {title!r} | {'LOOKS BLOCKED' if blocked else 'page loaded'}")
    log(f"  page text starts: {body[:160]!r}")
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        await page.screenshot(path=os.path.join(DEBUG_DIR, f"{key}-{n}.jpg"), type="jpeg", quality=45)
    except Exception:
        pass


async def pause(rng):
    await asyncio.sleep(random.uniform(*rng))


async def discover_cargurus(page, log):
    """Find CarGurus model IDs left as None in config (e.g. 296 GTS)."""
    found = []
    missing = [name for name, e in config.CARGURUS_MODEL_IDS.items() if not e]
    if not missing:
        return found
    try:
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return found
    for name in missing:
        for h in hrefs:
            m = re.search(rf"-Ferrari-{re.escape(name)}-(?:[A-Za-z\-]+-)?(d\d+)", h, re.I)
            if m:
                config.CARGURUS_MODEL_IDS[name] = m.group(1)
                log(f"  discovered CarGurus ID for {name}: {m.group(1)}")
                found.append(cargurus_url(m.group(1)))
                break
    return found


async def scrape_site(browser, key, log):
    source, base, url_fn = SITES[key]
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width": 1366, "height": 900},
                                    timezone_id=config.TIMEZONE,
                                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"})
    await ctx.add_init_script(STEALTH_JS)
    page = await ctx.new_page()
    api_hits = []
    shot = 0

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
    queue = list(url_fn())

    while queue:
        url = queue.pop(0)
        log(f"[{source}] {url[:130]}")
        before = len(records) + len(api_hits)
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(6000)
            shot += 1
            await diagnose(page, resp, key, shot, log)
            pages_done = 0
            while True:
                for _ in range(8):
                    await page.mouse.wheel(0, 2500)
                    await page.wait_for_timeout(600)
                records += await extract_jsonld(page, source, base)
                cards = await extract_cards(page, key, source, base)
                if key == "ferrari" and not cards:
                    cards = await extract_ferrari_page_text(page, source, base)
                records += cards
                if key == "cargurus":
                    queue += await discover_cargurus(page, log)
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
        added = len(records) + len(api_hits) - before
        log(f"  +{added} records")
        await pause(config.PAGE_DELAY_SECONDS)
        if key == "carscom" and added == 0:  # past the last results page
            break

    records = [from_dict(d, source, base) for d in api_hits] + records
    await ctx.close()
    log(f"[{source}] {len(records)} raw records")
    return records


async def enrich(browser, records, log):
    ctx = await browser.new_context(user_agent=UA, locale="en-US")
    await ctx.add_init_script(STEALTH_JS)
    page = await ctx.new_page()
    for n, rec in enumerate(records):
        if n >= config.MAX_DETAIL_PAGES_PER_RUN:
            log("  detail-page cap reached; the rest are filled on later runs")
            break
        try:
            await enrich_from_detail(page, rec)
        except Exception as e:
            log(f"  ! detail {rec['url'][:80]}: {str(e)[:100]}")
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
            log(f"Visiting up to {min(len(todo), config.MAX_DETAIL_PAGES_PER_RUN)} detail pages")
            await enrich(browser, todo, log)
        await browser.close()
    return allrecs
