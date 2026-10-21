CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('wishlist','trip')),
    content TEXT NOT NULL CHECK(json_valid(content)),
    version INTEGER NOT NULL CHECK(version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS documents_kind_created ON documents(kind,created_at,id);
CREATE TABLE IF NOT EXISTS document_products (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    product_id TEXT NOT NULL REFERENCES products(id),
    position INTEGER NOT NULL CHECK(position >= 0),
    PRIMARY KEY(document_id,position)
) STRICT;
CREATE TABLE IF NOT EXISTS managed_reviews (
    review_id TEXT PRIMARY KEY REFERENCES reviews(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version > 0)
) STRICT;
CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status INTEGER NOT NULL,
    data TEXT NOT NULL CHECK(json_valid(data)),
    location TEXT NOT NULL,
    PRIMARY KEY(scope,key)
) STRICT;
PRAGMA user_version = 2;
