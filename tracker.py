#!/usr/bin/env python3
"""Pull the Moses Toyota of Morgantown inventory feed and record changes.

Data lives in docs/data/inventory.json. The file is only rewritten when
something actually changed (a new car, a detail change, a price change, or a car
leaving/returning), so git history shows exactly when prices moved.

Run one sync:  python3 tracker.py
Uses only the Python standard library.
"""

import base64
import html
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HOST = "www.mosestoyotaofmorgantown.com"
DEALER_ID, PAGE_ID = 26254, 2682380
FEED = (f"https://{HOST}/api/vhcliaa/vehicle-pages/cosmos/srp/vehicles/"
        f"{DEALER_ID}/{PAGE_ID}?host={HOST}&pn=96&pt={{page}}")
ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "docs" / "data" / "inventory.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"

# Car detail fields compared on every sync (prices are tracked in `history`).
DETAIL_FIELDS = [
    "stock", "name", "year", "make", "model", "trim", "condition", "mileage",
    "ext_color", "int_color", "body", "engine", "fuel", "transmission",
    "mpg_city", "mpg_hwy", "url", "photo", "inventory_date", "in_transit", "status",
]


def clean(v):
    """Strip HTML the dealer embeds in text (e.g. disclaimer footnote links) and tidy spacing."""
    if not isinstance(v, str):
        return v
    v = re.sub(r"<a\b[^>]*>.*?</a>", " ", v, flags=re.S)  # footnote links: drop their text too
    v = html.unescape(re.sub(r"<[^>]+>", " ", v))
    return re.sub(r"\s+", " ", v).strip() or None


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_page(page):
    req = urllib.request.Request(FEED.format(page=page), headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def parse_prices(card):
    """VehiclePriceLibrary is base64 of 'Key:Value;Key:Value;...'."""
    prices = {}
    try:
        raw = base64.b64decode(card.get("VehiclePriceLibrary") or "").decode()
        for part in raw.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                prices[k] = float(v)
    except Exception:
        pass
    msrp = prices.get("MSRP") or card.get("VehicleMsrp") or None
    price = prices.get("Selling Price") or prices.get("Internet Price") or None
    return (round(msrp, 2) if msrp else None), (round(price, 2) if price else None)


def normalize(card):
    photo = (card.get("VehicleImageModel") or {}).get("VehiclePhotoSrc") or ""
    if photo.startswith("/"):
        photo = f"https://{HOST}{photo}"
    msrp, price = parse_prices(card)
    status = (card.get("VehicleStatusModel") or {}).get("StatusText")
    if not status:
        status = "In Transit" if card.get("VehicleInTransit") else "In Production" if card.get("VehicleInProduction") else "In Stock"
    car = {
        "vin": card["VehicleVin"],
        "stock": card.get("VehicleStockNumber"),
        "name": card.get("VehicleName"),
        "year": card.get("VehicleYear"),
        "make": card.get("VehicleMake"),
        "model": card.get("VehicleModel"),
        "trim": card.get("VehicleTrim"),
        "condition": card.get("VehicleType"),
        "mileage": card.get("VehicleMileage"),
        "ext_color": card.get("ExteriorColorLabel"),
        "int_color": card.get("InteriorColorLabel"),
        "body": card.get("VehicleBodyType"),
        "engine": card.get("VehicleEngine"),
        "fuel": card.get("VehicleFuelType"),
        "transmission": card.get("VehicleTransmission"),
        "mpg_city": card.get("VehicleMpgCity") or None,
        "mpg_hwy": card.get("VehicleMpgHwy") or None,
        "url": card.get("VehicleDetailUrl"),
        "photo": photo,
        "inventory_date": (card.get("VehicleTaggingInventoryDate") or "").replace("/", "-") or None,
        "in_transit": int(bool(card.get("VehicleInTransit"))),
        "status": status,
        "msrp": msrp,
        "price": price,
    }
    return {k: clean(v) for k, v in car.items()}


def fetch_inventory():
    first = fetch_page(1)
    paging = first["Paging"]["PaginationDataModel"]
    results = [first]
    if paging["TotalPages"] > 1:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results += list(pool.map(fetch_page, range(2, paging["TotalPages"] + 1)))
    cars = {}
    for res in results:
        for dc in res.get("DisplayCards", []):
            card = dc.get("VehicleCard")
            if card and card.get("VehicleVin"):
                c = normalize(card)
                cars[c["vin"]] = c
    return cars, paging["TotalCount"]


def load_data():
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())
    return {"last_change": None, "cars": [], "events": []}


def save_data(data):
    data["cars"].sort(key=lambda c: c["vin"])
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_PATH.with_suffix(".tmp")
    # One car per line keeps git diffs small and readable.
    lines = ",\n".join(json.dumps(c, sort_keys=True) for c in data["cars"])
    events = ",\n".join(json.dumps(e, sort_keys=True) for e in data["events"])
    tmp.write_text(f'{{"last_change": {json.dumps(data["last_change"])},\n'
                   f'"cars": [\n{lines}\n],\n"events": [\n{events}\n]}}\n')
    tmp.replace(DATA_PATH)


def sync():
    """Pull the live feed and record only the differences. Returns a summary."""
    live, expected = fetch_inventory()
    # Guard against a partial/failed feed marking everything as sold.
    if not live or len(live) < expected * 0.9:
        raise RuntimeError(f"Feed returned {len(live)} of {expected} cars; skipping sync")

    ts = now()
    summary = {"added": 0, "price_changes": 0, "detail_changes": 0, "removed": 0, "returned": 0, "live": len(live)}
    data = load_data()
    existing = {c["vin"]: c for c in data["cars"]}
    events = data["events"]
    backfilled = False

    for vin, car in live.items():
        old = existing.get(vin)
        if old is None:
            new = {f: car[f] for f in DETAIL_FIELDS}
            new.update(vin=vin, msrp=car["msrp"], price=car["price"], first_seen=ts, updated_at=ts,
                       removed_at=None, history=[{"at": ts, "msrp": car["msrp"], "price": car["price"]}],
                       pending_at=ts if "Sale Pending" in (car["status"] or "") else None)
            data["cars"].append(new)
            events.append({"vin": vin, "at": ts, "kind": "added", "price": car["price"]})
            summary["added"] += 1
            continue

        if old.get("removed_at"):
            old["removed_at"] = None
            old["updated_at"] = ts
            events.append({"vin": vin, "at": ts, "kind": "returned"})
            summary["returned"] += 1

        for f in DETAIL_FIELDS:
            if f not in old:  # field added to the tracker after this car was first seen
                old[f] = car[f]
                backfilled = True
        if car["status"] and "Sale Pending" in car["status"] and not old.get("pending_at"):
            old["pending_at"] = ts
            events.append({"vin": vin, "at": ts, "kind": "pending"})
            summary["pending"] = summary.get("pending", 0) + 1
        changed = {f: [old.get(f), car[f]] for f in DETAIL_FIELDS if car[f] != old.get(f)}
        if changed:
            old.update({f: v[1] for f, v in changed.items()}, updated_at=ts)
            events.append({"vin": vin, "at": ts, "kind": "details", "changes": changed})
            summary["detail_changes"] += 1

        if car["price"] != old["price"] or car["msrp"] != old["msrp"]:
            events.append({"vin": vin, "at": ts, "kind": "price", "from": old["price"], "to": car["price"]})
            old.update(msrp=car["msrp"], price=car["price"], updated_at=ts)
            old["history"].append({"at": ts, "msrp": car["msrp"], "price": car["price"]})
            summary["price_changes"] += 1

    for vin, old in existing.items():
        if vin not in live and not old.get("removed_at"):
            old["removed_at"] = old["updated_at"] = ts
            events.append({"vin": vin, "at": ts, "kind": "removed"})
            summary["removed"] += 1

    if any(summary[k] for k in summary if k != "live"):
        data["last_change"] = ts
        save_data(data)
    elif backfilled:
        save_data(data)
    return summary


if __name__ == "__main__":
    try:
        print(json.dumps(sync()))
    except Exception as e:
        print(f"Sync failed: {e}", file=sys.stderr)
        sys.exit(1)
