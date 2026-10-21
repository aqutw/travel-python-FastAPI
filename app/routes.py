"""FastAPI routes; query parsing and SQLite access are explicit dependencies."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from . import domain
from .database import Catalog
from .errors import APIError, invalid
from .query import COUNTRIES, KINDS, SEARCH_KEYS, Query

router = APIRouter()


def get_catalog(request: Request):
    catalog = Catalog(request.app.state.db_path)
    try:
        yield catalog
    finally:
        catalog.close()


DB = Annotated[Catalog, Depends(get_catalog)]


def reply(request, data, page=None, size=None, counts=None):
    meta = {"mock": True, "locale": "zh-TW", "currency": "TWD",
            "reference_date": request.app.state.today.isoformat()}
    if page is not None:
        total = len(data)
        start = min((page - 1) * size, total)
        data = data[start:start + size]
        meta["pagination"] = {"page": page, "per_page": size, "total": total,
                              "total_pages": (total + size - 1) // size, "has_next": start + size < total}
    if counts is not None:
        meta["facets"] = counts
    return {"data": data, "meta": meta}


def selected(items, ids):
    by_id = {p["id"]: p for p in items}
    return [by_id[id] for id in ids if id in by_id]


@router.get("/healthz")
def health(request: Request, db: DB):
    Query(request)
    db.connection.execute("SELECT 1").fetchone()
    return reply(request, {"status": "ok"})


@router.get("/api/v1/config")
def config(request: Request, db: DB):
    Query(request)
    return reply(request, {**db.setting("config"), "product_types": db.all("categories")})


@router.get("/api/v1/navigation")
def navigation(request: Request, db: DB):
    Query(request)
    return reply(request, db.setting("navigation"))


@router.get("/api/v1/categories")
def categories(request: Request, db: DB):
    Query(request)
    return reply(request, db.all("categories"))


@router.get("/api/v1/home")
def home(request: Request, db: DB):
    Query(request)
    layout = db.setting("home")
    collections, cards = db.all("collections"), db.all("cards")
    sections = [{"id": c["id"], "title": c["title"], "collection_id": c["id"],
                 "more_url": "/collections/" + c["id"],
                 "products": selected(cards, c["product_ids"])[:layout["cards_per_section"]]}
                for c in selected(collections, layout["section_collection_ids"])]
    return reply(request, {
        "hero": layout["hero"], "navigation": db.setting("navigation"),
        "categories": db.all("categories"), "campaigns": db.all("campaigns"),
        "destinations": selected(db.all("destinations"), layout["destination_ids"]),
        "sections": sections, "collections": collections,
        "articles": selected(db.all("articles"), layout["article_ids"]),
    })


@router.get("/api/v1/cruise/regions")
def cruise_regions(request: Request, db: DB):
    Query(request)
    products = [db.one("products", p["id"]) for p in db.all("cards") if p["type"] == "cruise"]
    ids = {p["destination_id"] for p in products}
    return reply(request, {
        "port_regions": sorted({p["cruise"]["departure_port"] for p in products}),
        "cruise_lines": sorted({p["cruise"]["cruise_line"] for p in products}),
        "route_regions": [{"label": d["name"], "href": d["id"]} for d in db.all("destinations") if d["id"] in ids],
    })


@router.get("/api/v1/destinations")
def destinations(request: Request, db: DB):
    q = Query(request, "q", "country", "page", "per_page")
    country = q.enum("country", "", COUNTRIES)
    page, size = q.pagination()
    items = [d for d in db.all("destinations") if (not country or d["country_code"] == country)
             and q.str("q").lower() in (d["name"] + " " + d["id"] + " " + d["country_name"]).lower()]
    return reply(request, items, page, size)


@router.get("/api/v1/destinations/{id}")
def destination(id: str, request: Request, db: DB):
    Query(request)
    d = db.one("destinations", id)
    cards = [p for p in db.all("cards") if p["destination_id"] == id]
    ids = {p["id"] for p in cards}
    return reply(request, {**d, "product_count": len(cards), "featured_products": cards[:6],
                           "collections": [c for c in db.all("collections") if ids.intersection(c["product_ids"])]})


@router.get("/api/v1/search/suggestions")
def suggestions(request: Request, db: DB):
    q = Query(request, "q", "type", "limit")
    limit = q.integer("limit", 8, 1, 20)
    kind = q.enum("type", "", KINDS)
    term = q.str("q").lower()
    items = [{"id": d["id"], "type": "destination", "label": d["name"], "subtitle": d["country_name"],
              "href": "/products?destination_id=" + d["id"], "image": d["image"]}
             for d in db.all("destinations") if term in (d["name"] + " " + d["id"] + " " + d["country_name"]).lower()]
    items.extend({"id": p["id"], "type": "product", "label": p["title"], "subtitle": p["location_name"],
                  "href": "/products/" + p["id"], "image": p["cover"]}
                 for p in db.all("cards") if (not kind or p["type"] == kind) and term in (p["title"] + " " + p["id"]).lower())
    return reply(request, items[:limit])


def search_result(request, db):
    q = Query(request, *SEARCH_KEYS)
    s = q.search(db, request.app.state.today)
    page, size = q.pagination()
    return domain.search(db, s), page, size


@router.get("/api/v1/search/filters")
def filters(request: Request, db: DB):
    items, _, _ = search_result(request, db)
    return reply(request, domain.facets(items))


@router.get("/api/v1/products")
def products(request: Request, db: DB):
    items, page, size = search_result(request, db)
    return reply(request, items, page, size, domain.facets(items))


@router.get("/api/v1/products/map")
def product_map(request: Request, db: DB):
    items, page, size = search_result(request, db)
    return reply(request, [{key: p[key] for key in ("id", "type", "title", "latitude", "longitude", "price")}
                           for p in items], page, size)


@router.get("/api/v1/products/{id}")
def product(id: str, request: Request, db: DB):
    Query(request)
    return reply(request, db.one("products", id))


@router.get("/api/v1/products/{id}/rooms")
@router.get("/api/v1/products/{id}/cabins")
@router.get("/api/v1/products/{id}/plans")
def product_options(id: str, request: Request, db: DB):
    Query(request)
    p = db.one("products", id)
    resource = request.url.path.rsplit("/", 1)[1]
    expected = {"stay": "rooms", "cruise": "cabins"}.get(p["type"], "plans")
    if resource != expected:
        raise APIError(404, "not_applicable", "resource does not apply to this product type")
    return reply(request, domain.options(p))


@router.get("/api/v1/products/{id}/itinerary")
def itinerary(id: str, request: Request, db: DB):
    Query(request)
    p = db.one("products", id)
    if p["type"] not in ("cruise", "tour"):
        raise APIError(404, "not_applicable", "itinerary applies to cruises and tours")
    return reply(request, p[p["type"]]["itinerary"])


@router.get("/api/v1/products/{id}/calendar")
def calendar(id: str, request: Request, db: DB):
    q = Query(request, "start_date", "end_date")
    dates = q.dates(request.app.state.today, 30, 90)
    return reply(request, domain.calendar(db.one("products", id), dates))


@router.get("/api/v1/products/{id}/departures")
def departures(id: str, request: Request, db: DB):
    q = Query(request, "start_date", "end_date", "page", "per_page", "status")
    dates = q.dates(request.app.state.today, 30, 90)
    page, size = q.pagination()
    status = q.enum("status", "", ("available", "guaranteed", "forming", "sold_out"))
    p = db.one("products", id)
    if p["type"] not in ("tour", "cruise"):
        raise APIError(404, "not_applicable", "departures apply to cruises and tours")
    return reply(request, [d for d in domain.calendar(p, dates) if not status or d["status"] == status], page, size)


@router.get("/api/v1/products/{id}/availability")
def availability(id: str, request: Request, db: DB):
    p = db.one("products", id)
    q = Query(request, "start_date", "end_date", "adults", "children", "quantity")
    dates = q.dates(request.app.state.today, p["duration_days"], 30)
    adults, children, quantity = (q.integer("adults", 2, 1, 16), q.integer("children", 0, 0, 16), q.integer("quantity", 1, 1, 8))
    if p["type"] != "stay" and (dates[1] - dates[0]).days != p["duration_days"]:
        invalid("end_date", f"this product has a fixed duration of {p['duration_days']} days")
    if p["price"]["unit"] == "person" and quantity != 1:
        invalid("quantity", "person-priced products require quantity=1; set adults and children instead")
    return reply(request, domain.availability(p, dates, adults, children, quantity))


@router.get("/api/v1/products/{id}/reviews")
def reviews(id: str, request: Request, db: DB):
    q = Query(request, "page", "per_page", "rating")
    page, size = q.pagination()
    rating = q.integer("rating", 0, 1, 5)
    db.one("products", id)
    items = db.query("SELECT body FROM reviews_json WHERE product_id=? AND (?=0 OR rating=?) ORDER BY position,id", (id, rating, rating))
    return reply(request, items, page, size)


@router.get("/api/v1/products/{id}/related")
def related(id: str, request: Request, db: DB):
    q = Query(request, "page", "per_page")
    page, size = q.pagination()
    p = db.one("products", id)
    return reply(request, [c for c in db.all("cards") if c["id"] != id and
                           (c["destination_id"] == p["destination_id"] or c["type"] == p["type"])], page, size)


@router.get("/api/v1/collections/{id}/products")
def collection_products(id: str, request: Request, db: DB):
    q = Query(request, *SEARCH_KEYS)
    s = q.search(db, request.app.state.today)
    page, size = q.pagination()
    ids = set(db.one("collections", id)["product_ids"])
    items = [p for p in domain.search(db, s) if p["id"] in ids]
    return reply(request, items, page, size, domain.facets(items))


@router.get("/api/v1/collections")
@router.get("/api/v1/campaigns")
@router.get("/api/v1/articles")
@router.get("/api/v1/faqs")
def content_list(request: Request, db: DB):
    entity = request.url.path.rsplit("/", 1)[1]
    extra = {"articles": ("destination_id",), "faqs": ("category", "q")}.get(entity, ())
    q = Query(request, "page", "per_page", *extra)
    page, size = q.pagination()
    items = db.all(entity)
    if entity == "articles" and q.str("destination_id"):
        items = [a for a in items if a["destination_id"] == q.str("destination_id")]
    if entity == "faqs":
        items = [f for f in items if (not q.str("category") or f["category"] == q.str("category"))
                 and q.str("q").lower() in (f["question"] + f["answer"]).lower()]
    return reply(request, items, page, size)


@router.get("/api/v1/collections/{id}")
@router.get("/api/v1/campaigns/{id}")
@router.get("/api/v1/articles/{id}")
def content_detail(id: str, request: Request, db: DB):
    Query(request)
    return reply(request, db.one(request.url.path.split("/")[-2], id))


@router.get("/images/{name}", include_in_schema=False)
def image(name: str, db: DB):
    mime, body = db.media("/images/" + name)
    return Response(content=body, media_type=mime)
