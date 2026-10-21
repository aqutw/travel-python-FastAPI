"""Request-scoped, read-only SQLite access. No entity cache."""
import json
import sqlite3
from pathlib import Path

from .errors import not_found


class Catalog:
    VIEWS = {
        "categories": "categories_json", "destinations": "destinations_json",
        "products": "product_details", "cards": "product_cards",
        "collections": "collections_json", "campaigns": "campaigns_json",
        "articles": "articles_json", "faqs": "faqs_json",
    }

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True,
            timeout=5, check_same_thread=False,
        )
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA query_only=ON")

    def close(self):
        self.connection.close()

    def validate(self):
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        app_id = self.connection.execute("PRAGMA application_id").fetchone()[0]
        if (version, app_id) != (1, 1414676822):
            raise RuntimeError("unsupported catalog schema")
        self.connection.execute("SELECT body FROM product_details LIMIT 1").fetchone()

    def query(self, sql: str, args=()) -> list[dict]:
        return [json.loads(row[0]) for row in self.connection.execute(sql, args)]

    def all(self, entity: str) -> list[dict]:
        return self.query(f"SELECT body FROM {self.VIEWS[entity]} ORDER BY position,id")

    def one(self, entity: str, id: str) -> dict:
        rows = self.query(f"SELECT body FROM {self.VIEWS[entity]} WHERE id=?", (id,))
        if not rows:
            not_found(entity.removesuffix("s"))
        return rows[0]

    def setting(self, key: str) -> dict:
        row = self.connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            raise sqlite3.DatabaseError("missing catalog setting")
        return json.loads(row[0])

    def exists(self, entity: str, id: str) -> bool:
        return bool(self.connection.execute(
            f"SELECT EXISTS(SELECT 1 FROM {self.VIEWS[entity]} WHERE id=?)", (id,)
        ).fetchone()[0])

    def media(self, path: str):
        row = self.connection.execute("SELECT mime_type,content FROM media WHERE url=?", (path,)).fetchone()
        if row is None:
            not_found("image")
        return row
