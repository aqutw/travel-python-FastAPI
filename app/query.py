"""Strict HTTP query boundary shared by all routes."""
import math
import re
from datetime import date, timedelta

from fastapi import Request

from .database import Catalog
from .errors import invalid

KINDS = ("stay", "cruise", "tour", "activity", "transfer", "esim", "sport", "rail")
COUNTRIES = ("TW", "JP", "KR", "TH", "SG")
SEARCH_KEYS = (
    "q", "type", "destination_id", "country", "tag", "amenity", "sort",
    "min_price", "max_price", "min_rating", "min_days", "max_days",
    "free_cancellation", "bbox", "start_date", "end_date", "adults", "children",
    "quantity", "page", "per_page", "departure_port", "departure_city",
    "cruise_line", "property_type",
)


class Query:
    def __init__(self, request: Request, *allowed: str):
        raw = request.scope["query_string"]
        if len(raw) > 4096:
            invalid("query", "must be at most 4096 bytes")
        # Match URL query grammar before Starlette's permissive decoding.
        if re.search(rb"%(?![0-9a-fA-F]{2})", raw) or b";" in raw:
            invalid("query", "malformed URL query")
        self.values = request.query_params
        for key in self.values:
            if len(self.values.getlist(key)) != 1:
                invalid(key, "must occur once")
            if key not in (*allowed, "locale", "currency"):
                invalid(key, "unknown query parameter")
        for key, value in (("locale", "zh-TW"), ("currency", "TWD")):
            if key in self.values and self.str(key) != value:
                invalid(key, f"only {value} is supported")

    def has(self, key: str) -> bool:
        return key in self.values

    def str(self, key: str) -> str:
        return self.values.get(key, "").strip()

    def integer(self, key: str, default: int, low: int, high: int) -> int:
        if not self.has(key):
            return default
        raw = self.str(key)
        if not re.fullmatch(r"[+-]?[0-9]+", raw) or len(raw) > 20:
            invalid(key, f"must be an integer between {low} and {high}")
        value = int(raw)
        if not low <= value <= high:
            invalid(key, f"must be an integer between {low} and {high}")
        return value

    def number(self, key: str, default: float, low: float, high: float) -> float:
        if not self.has(key):
            return default
        try:
            raw = self.str(key)
            if "_" in raw:
                raise ValueError
            value = float(raw)
            if math.isfinite(value) and low <= value <= high:
                return value
        except ValueError:
            pass
        invalid(key, f"must be a number between {low:g} and {high:g}")

    def enum(self, key: str, default: str, values: tuple[str, ...]) -> str:
        if not self.has(key):
            return default
        value = self.str(key)
        if value not in values:
            invalid(key, "must be one of " + ", ".join(values))
        return value

    def boolean(self, key: str) -> bool:
        if self.has(key) and self.str(key) not in ("true", "false"):
            invalid(key, "must be true or false")
        return self.str(key) == "true"

    def pagination(self) -> tuple[int, int]:
        return self.integer("page", 1, 1, 1_000_000), self.integer("per_page", 12, 1, 100)

    def dates(self, today: date, default_days: int, max_days: int) -> tuple[date, date]:
        def parse(key, fallback):
            if not self.has(key):
                return fallback
            try:
                raw = self.str(key)
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw):
                    raise ValueError
                return date.fromisoformat(raw)
            except ValueError:
                invalid(key, "must use YYYY-MM-DD")
        start = parse("start_date", today + timedelta(days=7))
        # Validate before adding duration to avoid overflow on extreme dates.
        if not today <= start <= today + timedelta(days=365):
            invalid("start_date", "must be between reference_date and reference_date + 365 days")
        end = parse("end_date", start + timedelta(days=default_days))
        if not 1 <= (end - start).days <= max_days:
            invalid("end_date", f"must be after start_date by 1 to {max_days} days")
        return start, end

    def search(self, catalog: Catalog, today: date) -> dict:
        s = {key: self.str(key) for key in (
            "q", "destination_id", "tag", "amenity", "departure_port", "departure_city", "cruise_line"
        )}
        s["type"] = self.enum("type", "", KINDS)
        s["country"] = self.enum("country", "", COUNTRIES)
        s["property_type"] = self.enum("property_type", "", ("hotel", "hot_spring", "camping", "entire_house"))
        s["sort"] = self.enum("sort", "popular", ("popular", "price_asc", "price_desc", "rating", "duration"))
        for key, default, low, high in (
            ("min_price", 0, 0, 10_000_000), ("max_price", 10_000_000, 0, 10_000_000),
            ("min_days", 1, 1, 30), ("max_days", 30, 1, 30),
            ("adults", 2, 1, 16), ("children", 0, 0, 16), ("quantity", 1, 1, 8),
        ):
            s[key] = self.integer(key, default, low, high)
        s["min_rating"] = self.number("min_rating", 0, 0, 5)
        s["free_cancellation"] = self.boolean("free_cancellation")
        if s["type"] and not catalog.exists("categories", s["type"]):
            invalid("type", "unknown product type")
        if s["destination_id"] and not catalog.exists("destinations", s["destination_id"]):
            invalid("destination_id", "unknown destination")
        if s["country"] and not catalog.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM destinations WHERE country_code=?)", (s["country"],)
        ).fetchone()[0]:
            invalid("country", "unknown country code")
        for low, high in (("min_price", "max_price"), ("min_days", "max_days")):
            if s[low] > s[high]:
                invalid(low, f"must not exceed {high}")
        if self.has("bbox"):
            parts = self.str("bbox").split(",")
            if len(parts) != 4:
                invalid("bbox", "must contain west,south,east,north")
            try:
                bounds = [float(p) if p == p.strip() and "_" not in p else float("nan") for p in parts]
            except ValueError:
                invalid("bbox", "invalid coordinate")
            if any(not math.isfinite(v) or abs(v) > (180 if i % 2 == 0 else 90) for i, v in enumerate(bounds)):
                invalid("bbox", "invalid coordinate")
            if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
                invalid("bbox", "coordinates must be ordered west,south,east,north; antimeridian crossing is unsupported")
            s["bbox"] = bounds
        if self.has("start_date") or self.has("end_date"):
            if not self.has("start_date") or not self.has("end_date"):
                invalid("dates", "provide both start_date and end_date")
            s["dates"] = self.dates(today, 1, 30)
        elif any(self.has(k) for k in ("adults", "children", "quantity")):
            invalid("dates", "traveler filters require start_date and end_date")
        return s
