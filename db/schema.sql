PRAGMA foreign_keys = ON;
PRAGMA journal_mode = DELETE;
PRAGMA user_version = 1;
PRAGMA application_id = 1414676822;

CREATE TABLE media (
    url TEXT PRIMARY KEY,
    mime_type TEXT NOT NULL,
    content BLOB NOT NULL
) STRICT;
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL CHECK(json_valid(value))
) STRICT;
CREATE TABLE categories (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
    image TEXT NOT NULL CHECK(json_valid(image)), position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE TABLE destinations (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, country_code TEXT NOT NULL,
    country_name TEXT NOT NULL, description TEXT NOT NULL,
    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
    image TEXT NOT NULL CHECK(json_valid(image)), position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE TABLE products (
    id TEXT PRIMARY KEY, type TEXT NOT NULL REFERENCES categories(id),
    title TEXT NOT NULL, summary TEXT NOT NULL,
    destination_id TEXT NOT NULL REFERENCES destinations(id),
    location_name TEXT NOT NULL, latitude REAL NOT NULL, longitude REAL NOT NULL,
    cover TEXT NOT NULL CHECK(json_valid(cover)),
    tags TEXT NOT NULL CHECK(json_valid(tags)),
    amenities TEXT NOT NULL CHECK(json_valid(amenities)),
    duration_days INTEGER NOT NULL CHECK(duration_days BETWEEN 1 AND 30),
    popularity INTEGER NOT NULL, free_cancellation INTEGER NOT NULL CHECK(free_cancellation IN (0,1)),
    confirmation TEXT NOT NULL CHECK(confirmation IN ('instant','request')),
    images TEXT NOT NULL CHECK(json_valid(images)), description TEXT NOT NULL, address TEXT NOT NULL,
    highlights TEXT NOT NULL CHECK(json_valid(highlights)),
    included TEXT NOT NULL CHECK(json_valid(included)), excluded TEXT NOT NULL CHECK(json_valid(excluded)),
    cancellation_policy TEXT NOT NULL, notices TEXT NOT NULL CHECK(json_valid(notices)),
    position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE INDEX products_type_destination ON products(type, destination_id);
CREATE INDEX products_destination ON products(destination_id);
CREATE TABLE options (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity > 0), description TEXT NOT NULL,
    amenities TEXT NOT NULL CHECK(json_valid(amenities)), image TEXT NOT NULL CHECK(json_valid(image)),
    amount INTEGER NOT NULL CHECK(amount >= 0), original_amount INTEGER NOT NULL CHECK(original_amount >= 0),
    currency TEXT NOT NULL CHECK(currency = 'TWD'),
    unit TEXT NOT NULL CHECK(unit IN ('night','person','vehicle','package')),
    tax_included INTEGER NOT NULL CHECK(tax_included IN (0,1)),
    position INTEGER NOT NULL, UNIQUE(product_id, position)
) STRICT;
CREATE INDEX options_product_price ON options(product_id, amount, id);
CREATE TABLE stay_details (
    product_id TEXT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    property_type TEXT NOT NULL CHECK(property_type IN ('hotel','hot_spring','camping','entire_house')),
    check_in_time TEXT NOT NULL, check_out_time TEXT NOT NULL
) STRICT;
CREATE TABLE cruise_details (
    product_id TEXT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    ship_name TEXT NOT NULL, cruise_line TEXT NOT NULL, departure_port TEXT NOT NULL, route TEXT NOT NULL
) STRICT;
CREATE TABLE tour_details (
    product_id TEXT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    departure_city TEXT NOT NULL, transport TEXT NOT NULL,
    minimum_travelers INTEGER NOT NULL CHECK(minimum_travelers > 0)
) STRICT;
CREATE TABLE activity_details (
    product_id TEXT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    meeting_point TEXT NOT NULL, voucher_type TEXT NOT NULL,
    usage_instructions TEXT NOT NULL CHECK(json_valid(usage_instructions))
) STRICT;
CREATE TABLE itinerary_days (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    day INTEGER NOT NULL CHECK(day > 0), title TEXT NOT NULL, description TEXT NOT NULL,
    meals TEXT NOT NULL CHECK(json_valid(meals)), accommodation TEXT NOT NULL,
    PRIMARY KEY(product_id, day)
) STRICT;
CREATE TABLE reviews (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    author TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    content TEXT NOT NULL, travel_date TEXT NOT NULL, created_at TEXT NOT NULL,
    position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE INDEX reviews_product_rating ON reviews(product_id, rating);
CREATE TABLE collections (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
    image TEXT NOT NULL CHECK(json_valid(image)), position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE TABLE collection_products (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, PRIMARY KEY(collection_id, product_id), UNIQUE(collection_id, position)
) STRICT;
CREATE TABLE campaigns (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
    image TEXT NOT NULL CHECK(json_valid(image)), coupon_code TEXT NOT NULL,
    discount_amount INTEGER NOT NULL CHECK(discount_amount >= 0), minimum_spend INTEGER NOT NULL CHECK(minimum_spend >= 0),
    collection_id TEXT NOT NULL REFERENCES collections(id),
    terms TEXT NOT NULL CHECK(json_valid(terms)), position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE TABLE articles (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
    destination_id TEXT NOT NULL REFERENCES destinations(id), image TEXT NOT NULL CHECK(json_valid(image)),
    content TEXT NOT NULL CHECK(json_valid(content)), position INTEGER NOT NULL UNIQUE
) STRICT;
CREATE TABLE article_products (
    article_id TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL, PRIMARY KEY(article_id, product_id), UNIQUE(article_id, position)
) STRICT;
CREATE TABLE faqs (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL,
    position INTEGER NOT NULL UNIQUE
) STRICT;
