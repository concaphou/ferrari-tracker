#!/usr/bin/env python3
"""
Run one scheduled snapshot:  python run.py
Force a run right now:       python run.py --force
Rebuild dashboard only:      python run.py --analyze-only
"""

import argparse
import asyncio
import csv
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from scraper import config
from scraper.normalize import classify_model, is_red, clean_color, split_city_state, to_int

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY = os.path.join(ROOT, "data", "history.csv")
CACHE = os.path.join(ROOT, "data", "vehicle_cache.json")
FIELDS = ["snapshot", "scraped_at", "model_group", "year", "price", "mileage",
          "exterior_color", "interior_color", "city", "state", "dealer",
          "vin", "url", "sources", "title"]


def log(msg):
    print(msg, flush=True)


def current_slot(force):
    now = datetime.now(ZoneInfo(config.TIMEZONE))
    for name, (start, end) in config.SLOTS.items():
        if start <= now.hour < end:
            return now, f"{now:%Y-%m-%d}_{name}"
    if force:
        return now, f"{now:%Y-%m-%d}_{now:%H%M}"
    return now, None


def existing_snapshots():
    if not os.path.exists(HISTORY):
        return set()
    with open(HISTORY, newline="") as f:
        return {r["snapshot"] for r in csv.DictReader(f)}


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def ident(rec):
    vin = str(rec.get("vin") or "").strip().upper()
    if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        return vin
    url = str(rec.get("url") or "").split("?")[0].split("#")[0].rstrip("/")
    return url or None


def prepare(rec, cache):
    """Classify + apply cached details. Returns None if not a target model."""
    group = classify_model(rec.get("title"), rec.get("model"), rec.get("trim"))
    if not group:
        return None
    make_text = f"{rec.get('make','')} {rec.get('title','')}".lower()
    if rec.get("make") and "ferrari" not in make_text:
        return None
    rec["model_group"] = group
    key = ident(rec)
    if key and key in cache:
        for k, v in cache[key].items():
            if v and not rec.get(k):
                rec[k] = v
    return rec


def needs_detail_factory(cache):
    def needs_detail(records):
        todo, seen = [], set()
        for r in records:
            r2 = prepare(dict(r), cache)
            if not r2 or not r.get("url") or r["url"] in seen:
                continue
            missing = not (r2.get("exterior_color") and r2.get("interior_color")
                           and (r2.get("city") or r2.get("location")))
            if missing:
                seen.add(r["url"])
                todo.append(r)  # enrich the original record in place
        return todo
    return needs_detail


def finalize(records, cache, snapshot, now):
    merged = {}
    for raw in records:
        rec = prepare(raw, cache)
        if not rec:
            continue
        key = ident(rec)
        if not key:
            continue
        if key in merged:  # same car seen twice (or on two sites): merge
            m = merged[key]
            for k, v in rec.items():
                if v and not m.get(k):
                    m[k] = v
            m["_sources"].add(rec["source"])
            p_new, p_old = to_int(rec.get("price")), to_int(m.get("price"))
            if p_new and (not p_old or p_new < p_old):
                m["price"] = rec["price"]
        else:
            rec["_sources"] = {rec["source"]}
            merged[key] = rec

    rows, dropped_red = [], 0
    for key, r in merged.items():
        ext, intr = clean_color(r.get("exterior_color")), clean_color(r.get("interior_color"))
        city, state = split_city_state(r.get("city"), r.get("state"), r.get("location"))
        cache[key] = {"exterior_color": ext, "interior_color": intr, "city": city,
                      "state": state, "dealer": r.get("dealer", ""), "vin": r.get("vin", "")}
        if is_red(ext) or is_red(intr):
            dropped_red += 1
            continue
        year = to_int(r.get("year"))
        price = to_int(r.get("price"))
        miles = to_int(r.get("mileage"))
        if year and not 2014 <= year <= now.year + 1:
            year = None
        if price and not 50_000 <= price <= 2_000_000:
            price = None
        rows.append({
            "snapshot": snapshot, "scraped_at": now.isoformat(timespec="minutes"),
            "model_group": r["model_group"], "year": year or "", "price": price or "",
            "mileage": miles if miles is not None else "",
            "exterior_color": ext or "Unknown", "interior_color": intr or "Unknown",
            "city": city, "state": state, "dealer": str(r.get("dealer", ""))[:80],
            "vin": key if len(key) == 17 else "", "url": r.get("url", ""),
            "sources": "|".join(sorted(r["_sources"])), "title": str(r.get("title", ""))[:120],
        })
    log(f"Kept {len(rows)} cars; dropped {dropped_red} red/near-red")
    return rows


def append_history(rows):
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    new_file = not os.path.exists(HISTORY)
    with open(HISTORY, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run even outside the 11am/5pm windows")
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--sites", nargs="+", default=["ferrari", "carscom", "cargurus"])
    args = ap.parse_args()

    from scraper import analyze
    if args.analyze_only:
        analyze.build(HISTORY, os.path.join(ROOT, "docs", "data.json"))
        return

    now, snapshot = current_slot(args.force)
    if not snapshot:
        log(f"{now:%H:%M} {config.TIMEZONE} is outside the run windows; nothing to do.")
        return
    if snapshot in existing_snapshots() and not args.force:
        log(f"Snapshot {snapshot} already captured; skipping.")
        return

    from scraper.scrape import run_scrape
    cache = load_cache()
    records = asyncio.run(run_scrape(args.sites, log, needs_detail_factory(cache)))
    rows = finalize(records, cache, snapshot, now)

    with open(CACHE, "w") as f:
        json.dump(cache, f, indent=0)
    if rows:
        append_history(rows)
    else:
        log("No matching cars found this run; history not changed.")
    analyze.build(HISTORY, os.path.join(ROOT, "docs", "data.json"))
    if not rows:
        sys.exit(2)  # makes the failure visible in GitHub Actions


if __name__ == "__main__":
    main()
