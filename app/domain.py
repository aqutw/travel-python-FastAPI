"""Deterministic inventory and pricing shared by search and detail endpoints."""
from collections import Counter
from datetime import timedelta

from .database import Catalog


def days(start, end):
    for offset in range((end - start).days):
        yield start + timedelta(days=offset)


def options(product):
    kind = product["type"]
    detail = product[kind if kind in ("stay", "cruise", "tour") else "activity"]
    return detail[{"stay": "rooms", "cruise": "cabins"}.get(kind, "plans")]


def inventory(id, day):
    value = 2166136261
    for byte in (id + day.isoformat()).encode("utf-8"):
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value % 9


def availability(product, dates, adults=2, children=0, quantity=1):
    start, end = dates
    nights = (end - start).days
    quotes = []
    for option in options(product):
        left = inventory(option["id"], start)
        if product["type"] == "stay":
            left = min(inventory(option["id"], day) for day in days(start, end))
        multiplier = quantity
        if product["type"] == "stay":
            multiplier *= nights
        elif option["price"]["unit"] == "person":
            multiplier = adults + children
        available = left >= quantity and option["capacity"] * quantity >= adults + children
        if option["price"]["unit"] == "person":
            available = left >= adults + children and option["capacity"] >= adults + children
        quotes.append({
            "option_id": option["id"], "name": option["name"], "capacity": option["capacity"],
            "remaining": left, "available": available, "unit_price": option["price"],
            "multiplier": multiplier, "total_amount": option["price"]["amount"] * multiplier,
        })
    return {
        "product_id": product["id"], "start_date": start.isoformat(), "end_date": end.isoformat(),
        "nights": nights, "adults": adults, "children": children, "quantity": quantity,
        "available": any(q["available"] for q in quotes), "currency": "TWD", "options": quotes,
        "disclaimer": "模擬報價，不保留庫存，也不建立訂單。",
    }


def calendar(product, dates):
    rows = []
    for day in days(*dates):
        stock = [(inventory(o["id"], day), o["price"]) for o in options(product)]
        left = max((n for n, _ in stock), default=0)
        price = min((price for n, price in stock if n > 0), key=lambda p: p["amount"], default=product["price"])
        status = "sold_out" if left == 0 else "available"
        if left and product["type"] == "tour":
            status = "guaranteed" if day.day % 2 == 0 else "forming"
        rows.append({"date": day.isoformat(), "available": left > 0, "remaining": left,
                     "price": price, "status": status})
    return rows


def facets(cards):
    mappings = {
        "types": Counter(p["type"] for p in cards),
        "destinations": Counter(p["destination_id"] for p in cards),
        "tags": Counter(v for p in cards for v in p["tags"]),
        "amenities": Counter(v for p in cards for v in p["amenities"]),
    }
    result = {key: [{"value": value, "count": counts[value]} for value in sorted(counts)]
              for key, counts in mappings.items()}
    prices = [p["price"]["amount"] for p in cards]
    return {**result, "min_price": min(prices, default=0), "max_price": max(prices, default=0)}


def search(catalog: Catalog, s: dict):
    clauses = ["c.price_amount>=?", "c.price_amount<=?", "c.rating_average>=?",
               "c.duration_days>=?", "c.duration_days<=?"]
    args = [s[k] for k in ("min_price", "max_price", "min_rating", "min_days", "max_days")]
    for key, column in (
        ("type", "c.type"), ("destination_id", "c.destination_id"), ("country", "d.country_code"),
        ("departure_port", "c.departure_port"), ("departure_city", "c.departure_city"),
        ("cruise_line", "c.cruise_line"), ("property_type", "c.property_type"),
    ):
        if s[key]:
            clauses.append(column + "=?")
            args.append(s[key])
    if s["q"]:
        clauses.append("instr(lower(c.title||' '||c.summary||' '||d.name||' '||d.country_name||' '||"
                       "coalesce((SELECT group_concat(value,' ') FROM json_each(c.tags)),'')),lower(?))>0")
        args.append(s["q"])
    for key, column in (("tag", "tags"), ("amenity", "amenities")):
        if s[key]:
            clauses.append(f"EXISTS(SELECT 1 FROM json_each(c.{column}) WHERE value=?)")
            args.append(s[key])
    if s["free_cancellation"]:
        clauses.append("c.free_cancellation=1")
    if "bbox" in s:
        west, south, east, north = s["bbox"]
        clauses.append("c.longitude BETWEEN ? AND ? AND c.latitude BETWEEN ? AND ?")
        args.extend((west, east, south, north))
    order = {"price_asc": "c.price_amount ASC", "price_desc": "c.price_amount DESC",
             "rating": "c.rating_average DESC", "duration": "c.duration_days ASC"}.get(s["sort"], "c.popularity DESC")
    cards = catalog.query(
        "SELECT c.body FROM product_cards c JOIN destinations d ON d.id=c.destination_id WHERE "
        + " AND ".join(clauses) + " ORDER BY " + order + ",c.id", args,
    )
    if "dates" not in s:
        return cards
    matches = []
    for card in cards:
        product = catalog.one("products", card["id"])
        if product["price"]["unit"] == "person" and s["quantity"] != 1:
            continue
        intervals = [s["dates"]] if product["type"] == "stay" else (
            (day, day + timedelta(days=product["duration_days"])) for day in days(*s["dates"])
        )
        if any(availability(product, interval, s["adults"], s["children"], s["quantity"])["available"]
               for interval in intervals):
            matches.append(card)
    return matches
