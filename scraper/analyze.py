"""Turn data/history.csv into docs/data.json for the dashboard."""

import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean, median

from . import config


def _ident(r):
    return r.get("car_id") or r["vin"] or r["url"].split("?")[0] or f'{r["title"]}|{r["mileage"]}|{r["city"]}'


def _load(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r["price"] = int(r["price"]) if r["price"] else None
            r["mileage"] = int(r["mileage"]) if r["mileage"] else None
            r["year"] = int(r["year"]) if r["year"] else None
            r["t"] = datetime.fromisoformat(r["scraped_at"])
            r["id"] = _ident(r)
            rows.append(r)
    return rows


def _stats(prices):
    prices = [p for p in prices if p]
    if not prices:
        return None
    return {"n": len(prices), "low": min(prices), "high": max(prices),
            "avg": round(mean(prices)), "med": round(median(prices))}


def _fit(cars):
    """Expected price for a model-year given mileage. Falls back to median."""
    prices = [c["price"] for c in cars]
    med = median(prices)
    pts = [(c["mileage"], c["price"]) for c in cars if c["mileage"] is not None]
    if len(pts) >= 5:
        mx, my = mean(p[0] for p in pts), mean(p[1] for p in pts)
        sxx = sum((x - mx) ** 2 for x, _ in pts)
        if sxx > 0:
            b = sum((x - mx) * (y - my) for x, y in pts) / sxx
            if b < 0:  # more miles should mean lower price
                a = my - b * mx
                return lambda m: (a + b * m) if m is not None else med
    return lambda m: med


def _city_rank(rows, snapshots, min_cars):
    """Rank cities by how far asking prices sit below the market.

    Each car is compared with the same model and year in the same snapshot,
    adjusted for mileage, so falling or rising markets don't skew history.
    """
    peers = defaultdict(list)
    for r in rows:
        if r["price"] and r["year"]:
            peers[(r["snapshot"], r["model_group"], r["year"])].append(r)
    fits = {k: _fit(v) for k, v in peers.items() if len(v) >= 2}

    cities = defaultdict(lambda: {"latest": {}, "snaps": defaultdict(list)})
    for r in rows:
        f = fits.get((r["snapshot"], r["model_group"], r["year"]))
        if not (f and r["price"] and r["city"]):
            continue
        exp = f(r["mileage"])
        if not exp or exp <= 0:
            continue
        i = r["price"] / exp - 1
        key = f'{r["city"]}, {r["state"]}'.strip(", ")
        d = cities[key]
        d["snaps"][r["snapshot"]].append(i)
        if r["id"] not in d["latest"] or r["t"] > d["latest"][r["id"]][0]:
            d["latest"][r["id"]] = (r["t"], i, r["model_group"])

    out = []
    for name, d in cities.items():
        vals = [v[1] for v in d["latest"].values()]
        if len(vals) < min_cars:
            continue
        models = defaultdict(int)
        for _, _, g in d["latest"].values():
            models[g] += 1
        snap_means = [mean(v) for v in d["snaps"].values()]
        out.append({
            "city": name,
            "cars": len(vals),
            "vs_market": round(mean(vals) * 100, 1),
            "below_share": round(100 * sum(v < 0 for v in vals) / len(vals)),
            "snaps_below": sum(m < 0 for m in snap_means),
            "snaps_present": len(snap_means),
            "snaps_total": len(snapshots),
            "models": dict(sorted(models.items(), key=lambda kv: -kv[1])),
        })
    out.sort(key=lambda x: (x["vs_market"], -x["cars"]))
    return out


def _city(r):
    return f'{r["city"]}, {r["state"]}'.strip(", ")


def _segments(car_rows, snap_idx):
    """Compress a car's history into [first_snapshot, last_snapshot, price] runs."""
    segs = []
    for r in sorted(car_rows, key=lambda r: snap_idx[r["snapshot"]]):
        i = snap_idx[r["snapshot"]]
        if segs and segs[-1][1] == i - 1 and segs[-1][2] == r["price"]:
            segs[-1][1] = i
        elif not (segs and segs[-1][1] == i):
            segs.append([i, i, r["price"]])
    return segs


def build(history_path, out_path, cache_path=None):
    rows = _load(history_path)
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    if not rows:
        with open(out_path, "w") as f:
            json.dump({"generated_at": datetime.now().isoformat(timespec="minutes"),
                       "empty": True, "models": config.MODEL_GROUPS}, f)
        return

    snap_time = {}
    for r in rows:
        snap_time.setdefault(r["snapshot"], r["t"])
    snapshots = sorted(snap_time, key=snap_time.get)
    snap_idx = {sn: i for i, sn in enumerate(snapshots)}
    latest_snap = snapshots[-1]
    first_seen, first_price = {}, {}
    for r in sorted(rows, key=lambda r: r["t"]):
        first_seen.setdefault(r["id"], r["t"])
        if r["price"]:
            first_price.setdefault(r["id"], r["price"])

    window_start = snap_time[latest_snap] - timedelta(days=config.CITY_WINDOW_DAYS)
    window_rows = [r for r in rows if r["t"] >= window_start]
    window_snaps = [s for s in snapshots if snap_time[s] >= window_start]

    by_model = {}
    for g in config.MODEL_GROUPS:
        g_rows = [r for r in rows if r["model_group"] == g]
        cur = [r for r in g_rows if r["snapshot"] == latest_snap]

        # price table: latest snapshot, by year and exterior color
        table = defaultdict(list)
        for r in cur:
            if r["year"]:
                table[(r["year"], r["exterior_color"])].append(r["price"])
        price_table = []
        for (y, color), ps in table.items():
            s = _stats(ps)
            price_table.append({"year": y, "color": color, "listed": len(ps),
                                **({"low": s["low"], "avg": s["avg"], "high": s["high"]} if s
                                   else {"low": None, "avg": None, "high": None})})
        price_table.sort(key=lambda x: (-x["year"], x["avg"] or 9e9))
        year_rows = defaultdict(list)
        for r in cur:
            if r["year"]:
                year_rows[r["year"]].append(r["price"])
        year_summary = []
        for y, ps in sorted(year_rows.items(), reverse=True):
            s = _stats(ps) or {}
            year_summary.append({"year": y, "listed": len(ps), "low": s.get("low"),
                                 "avg": s.get("avg"), "high": s.get("high")})

        # compact per-car history; the page computes trends for any year/city
        by_car = defaultdict(list)
        for r in g_rows:
            by_car[r["id"]].append(r)
        cars = []
        for cid, crs in by_car.items():
            last = max(crs, key=lambda r: r["t"])
            cars.append([last["year"], _city(last), _segments(crs, snap_idx)])
        city_counts = defaultdict(int)
        for r in cur:
            if r["city"]:
                city_counts[_city(r)] += 1

        cur_stats = _stats([r["price"] for r in cur]) or {}
        week_ago = snap_time[latest_snap] - timedelta(days=7)
        past = [s for s in snapshots if snap_time[s] <= week_ago]
        change = None
        if past and cur_stats:
            old = _stats([r["price"] for r in g_rows if r["snapshot"] == past[-1]])
            if old:
                change = round((cur_stats["med"] / old["med"] - 1) * 100, 1)
        prev_count = None
        if len(snapshots) > 1:
            prev_count = sum(1 for r in g_rows if r["snapshot"] == snapshots[-2])

        listings = []
        for r in sorted(cur, key=lambda r: (r["price"] or 9e9)):
            fp = first_price.get(r["id"])
            info = cache.get(r["id"], {})
            links = [{"s": src, "u": u} for src, u in (info.get("links") or {}).items() if u]
            if not links and r["url"]:
                links = [{"s": r["sources"].split("|")[0], "u": r["url"]}]
            listings.append({
                "year": r["year"], "price": r["price"], "miles": r["mileage"],
                "ext": r["exterior_color"], "int": r["interior_color"],
                "city": f'{r["city"]}, {r["state"]}'.strip(", "), "dealer": r["dealer"],
                "url": r["url"], "src": r["sources"],
                "days": (r["t"] - first_seen[r["id"]]).days,
                "drop": (r["price"] - fp) if (fp and r["price"] and r["price"] != fp) else 0,
                "photos": (info.get("photos") or [])[:6],
                "links": links,
            })

        by_model[g] = {
            "current": {"listed": len(cur), "prev_listed": prev_count,
                        "med": cur_stats.get("med"), "low": cur_stats.get("low"),
                        "high": cur_stats.get("high"), "change_7d": change},
            "years": year_summary,
            "price_table": price_table,
            "cars": cars,
            "cities_now": sorted(city_counts.items(), key=lambda kv: (-kv[1], kv[0])),
            "cities": _city_rank([r for r in window_rows if r["model_group"] == g], window_snaps, 2),
            "listings": listings,
        }

    data = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="minutes"),
        "latest_snapshot": latest_snap,
        "latest_time": snap_time[latest_snap].isoformat(timespec="minutes"),
        "snapshot_count": len(snapshots),
        "snaps": [snap_time[sn].strftime("%b %-d %p").replace("AM", "am").replace("PM", "pm")
                  for sn in snapshots],
        "first_time": snap_time[snapshots[0]].isoformat(timespec="minutes"),
        "city_window_days": config.CITY_WINDOW_DAYS,
        "models": config.MODEL_GROUPS,
        "by_model": by_model,
        "cities": _city_rank(window_rows, window_snaps, config.CITY_MIN_CARS),
    }
    with open(out_path, "w") as f:
        json.dump(data, f, separators=(",", ":"))
