#!/usr/bin/env python3
"""Compare JSON and media contracts of two running API implementations."""
import argparse
import json
import urllib.parse
from verify_api import request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--reference-url", default="http://localhost:8080")
    args = parser.parse_args()
    base, reference = args.base_url.rstrip("/"), args.reference_url.rstrip("/")
    spec = json.loads(request(base, "/openapi.json")[2])
    paths = {v["get"]["x-smoke-path"] for v in spec["paths"].values()}
    cards = json.loads(request(reference, "/api/v1/products?per_page=100")[2])["data"]
    for card in cards:
        detail = card["detail_url"]
        paths.update((detail, detail + "/availability", detail + "/calendar", detail + "/reviews?rating=5",
                      detail + "/related?per_page=3", card["cover"]["url"]))
        resource = {"stay": "rooms", "cruise": "cabins"}.get(card["type"], "plans")
        paths.add(detail + "/" + resource)
        if card["type"] in ("cruise", "tour"):
            paths.update((detail + "/itinerary", detail + "/departures?status=guaranteed"))
    for key, values in {
        "type": ["stay", "cruise", "tour", "activity", "transfer", "esim", "sport", "rail", "", "unknown"],
        "sort": ["popular", "price_asc", "price_desc", "rating", "duration"],
        "country": ["TW", "JP", "KR", "TH", "SG"], "q": ["東京", "wifi", "no-result", "' OR 1=1 --"],
        "amenity": ["wifi", "breakfast"], "tag": ["熱門推薦", "親子友善"],
        "property_type": ["hotel", "camping", "hot_spring", "entire_house"],
        "bbox": ["121,24,122,26", "0,0,0,0", "NaN,0,10,10", "20,0,10,10"],
        "page": ["0", "2", "999999", "1_0"], "min_rating": ["4.5", "NaN", "Inf"],
        "free_cancellation": ["true", "false", "1"], "destination_id": ["taipei", "missing"],
        "locale": ["en", "zh-TW"], "currency": ["USD", "TWD"],
    }.items():
        paths.update("/api/v1/products?" + urllib.parse.urlencode({key: value}) for value in values)
    reference_date = json.loads(request(reference, "/healthz")[2])["meta"]["reference_date"]
    from datetime import date, timedelta
    start = date.fromisoformat(reference_date) + timedelta(days=12)
    dates = urllib.parse.urlencode({"start_date": str(start), "end_date": str(start + timedelta(days=2))})
    for kind in ["", "stay", "cruise", "tour", "activity"]:
        paths.add("/api/v1/products?per_page=100&type=" + kind + "&" + dates if kind else "/api/v1/products?per_page=100&" + dates)
    paths.update(("/api/v1/products?page=1&page=2", "/api/v1/products?extra=x", "/missing",
                  "/api/v1/products/stay-001/cabins", "/api/v1/products/cruise-001/availability?quantity=2"))
    for path in sorted(paths):
        actual, expected = request(base, path), request(reference, path)
        assert actual[0] == expected[0], (path, actual[0], expected[0])
        if "application/json" in actual[1].get("Content-Type", ""):
            assert json.loads(actual[2]) == json.loads(expected[2]), (path, actual[2][:300], expected[2][:300])
        else:
            assert actual[2] == expected[2], path
    print(f"Compared {len(paths)} matching JSON/media responses across both implementations.")


if __name__ == "__main__":
    main()
