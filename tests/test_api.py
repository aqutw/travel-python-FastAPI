import logging
import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft4Validator
from openapi_spec_validator import validate

from app.main import ROOT, create_app


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "travel.sqlite3"
        shutil.copyfile(ROOT / "data/travel.sqlite3", self.path)
        self.client = TestClient(create_app(self.path, "2026-10-20"))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def get(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_all_documented_responses_and_product_variants(self):
        spec = self.get("/openapi.json")
        validate(spec)
        self.assertEqual(len(spec["paths"]), 30)
        for path, item in spec["paths"].items():
            with self.subTest(path=path):
                operation = item["get"]
                schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
                Draft4Validator({**spec, **schema}, format_checker=Draft4Validator.FORMAT_CHECKER).validate(
                    self.get(operation["x-smoke-path"]))
        detail = spec["paths"]["/api/v1/products/{id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        cards = self.get("/api/v1/products?per_page=100")["data"]
        self.assertEqual(len(cards), 96)
        for card in cards:
            with self.subTest(id=card["id"]):
                body = self.get(card["detail_url"])
                Draft4Validator({**spec, **detail}).validate(body)
                self.assertEqual(body["data"]["price"], card["price"])

    def test_invalid_queries(self):
        queries = ["page=0", "per_page=101", "page=1&page=2", "extra=x", "type=", "locale=en",
                   "currency=USD", "min_rating=NaN", "min_rating=inf", "min_rating=1_0",
                   "min_price=10&max_price=1", "min_days=5&max_days=2", "destination_id=missing",
                   "adults=3", "start_date=2026-11-01", "start_date=2026-1-01&end_date=2026-11-03",
                   "start_date=2026-01-01&end_date=2026-01-03", "free_cancellation=1",
                   "bbox=0,1,2", "bbox=NaN,0,10,10", "bbox=20,0,10,10", "q=" + "a" * 4097,
                   "page=1.0", "page=１", "page=1_0", "q=%ZZ"]
        for query in queries:
            with self.subTest(query=query[:100]):
                response = self.client.get("/api/v1/products?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"]["code"], "invalid_query")

    def test_not_found_and_inapplicable(self):
        for path, code in [("/missing", "not_found"), ("/api/v1/products/missing", "not_found"),
                           ("/api/v1/products/stay-001/cabins", "not_applicable"),
                           ("/api/v1/products/stay-001/itinerary", "not_applicable"),
                           ("/api/v1/products/stay-001/departures", "not_applicable"),
                           ("/images/missing.svg", "not_found")]:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 404)
                self.assertEqual(r.json()["error"]["code"], code)

    def test_pagination_facets_and_sort(self):
        full = self.get("/api/v1/products?type=stay&per_page=100&sort=price_asc")
        prices = [p["price"]["amount"] for p in full["data"]]
        self.assertEqual(prices, sorted(prices))
        page = self.get("/api/v1/products?type=stay&per_page=1&page=2&sort=price_asc")
        self.assertEqual(page["data"], full["data"][1:2])
        self.assertEqual(page["meta"]["facets"], full["meta"]["facets"])
        empty = self.get("/api/v1/products?type=stay&page=999999")
        self.assertEqual(empty["data"], [])
        self.assertFalse(empty["meta"]["pagination"]["has_next"])
        self.assertEqual(self.get("/api/v1/products?q=no-match")["meta"]["pagination"]["total_pages"], 0)

    def test_inventory_price_and_search_agree(self):
        dates = "start_date=2026-11-01&end_date=2026-11-03"
        quote = self.get("/api/v1/products/stay-001/availability?" + dates)["data"]
        self.assertEqual([o["remaining"] for o in quote["options"]], [0, 1])
        self.assertEqual([o["total_amount"] for o in quote["options"]], [4800, 6000])
        cards = self.get("/api/v1/products?type=stay&per_page=100&" + dates)["data"]
        for card in cards:
            self.assertTrue(self.get(card["detail_url"] + "/availability?" + dates)["data"]["available"])
        cruise = self.get("/api/v1/products/cruise-001/availability?adults=2&children=1")["data"]
        self.assertEqual(cruise["options"][0]["multiplier"], 3)
        for query in ("quantity=2", "start_date=2026-11-01&end_date=2026-11-02"):
            self.assertEqual(self.client.get("/api/v1/products/cruise-001/availability?" + query).status_code, 400)

    def test_database_edits_visible_in_all_projections(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE products SET title='更新商品' WHERE id='stay-001'")
            db.execute("UPDATE options SET amount=123,original_amount=200 WHERE id='stay-001-opt-1'")
        detail = self.get("/api/v1/products/stay-001")["data"]
        self.assertEqual(detail["title"], "更新商品")
        self.assertEqual(detail["price"]["amount"], 123)
        self.assertEqual(self.get("/api/v1/products?q=更新商品")["data"][0]["id"], "stay-001")
        self.assertEqual(self.get("/api/v1/home")["data"]["sections"][0]["products"][0]["title"], "更新商品")
        quote = self.get("/api/v1/products/stay-001/availability")["data"]
        self.assertEqual(quote["options"][0]["total_amount"], 123)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE media SET content=? WHERE url='/images/stay.svg'", (b"<svg>updated</svg>",))
        self.assertEqual(self.client.get("/images/stay.svg").content, b"<svg>updated</svg>")

    def test_database_failures_do_not_leak_sql(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DROP VIEW product_cards")
        with self.assertLogs("app.main", logging.ERROR):
            r = self.client.get("/api/v1/products")
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json(), {"error": {"code": "database_error", "message": "unable to read catalog"}})

    def test_methods_headers_and_docs(self):
        for path in ("/api/v1/products", "/docs", "/openapi.json", "/images/stay.svg", "/missing"):
            get = self.client.get(path)
            head = self.client.head(path)
            self.assertEqual(head.status_code, get.status_code)
            self.assertEqual(head.content, b"")
            self.assertEqual(head.headers["content-length"], get.headers["content-length"])
            self.assertEqual(get.headers["access-control-allow-origin"], "*")
            self.assertEqual(get.headers["cache-control"], "no-store")
            self.assertEqual(self.client.options(path).status_code, 204)
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(self.client.request(method, path).status_code, 405)
        self.assertIn("swagger-ui", self.client.get("/docs").text)
        self.assertIn("text/html", self.client.get("/").headers["content-type"])

    def test_concurrent_requests(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.get("/api/v1/products?per_page=1"), range(24)))
        self.assertTrue(all(r["data"][0]["id"] == "stay-001" for r in results))
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), ["travel.sqlite3"])


class StartupTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.sqlite3"
            with self.assertRaises(sqlite3.OperationalError), TestClient(create_app(path)):
                pass
            self.assertFalse(path.exists())

    def test_invalid_schema_and_reference_date(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "invalid.sqlite3"
            sqlite3.connect(path).close()
            with self.assertRaisesRegex(RuntimeError, "unsupported catalog schema"), TestClient(create_app(path)):
                pass
        with self.assertRaises(ValueError), TestClient(create_app(reference_date="2026-1-01")):
            pass


if __name__ == "__main__":
    unittest.main()
