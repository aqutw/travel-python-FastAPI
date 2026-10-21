# Travel Python FastAPI

## 專案目錄與檔案結構

```text
travel-python-FastAPI/
├── app/
│   ├── main.py                # FastAPI factory、啟動與錯誤處理
│   ├── routes.py              # 公開 GET 路由與 database dependency
│   ├── writes.py              # POST、PUT 與私人資料 GET 路由
│   ├── models.py              # Pydantic JSON 綁定與驗證
│   ├── middleware.py          # API key、CORS、限流、body 上限與 request log
│   ├── workspace.py           # SQLite 寫入交易、冪等重試與版本控制
│   ├── migration.sql          # v1 → v2 schema migration
│   ├── query.py               # 查詢參數驗證
│   ├── database.py            # SQLite 商品資料查詢
│   ├── domain.py              # 搜尋、庫存與報價計算
│   ├── errors.py              # 結構化錯誤
│   ├── openapi.json           # 完整 API 契約
│   └── index.html             # API Explorer
├── data/travel.sqlite3        # 商品、評論、圖片、收藏與行程
├── db/schema.sql              # 基礎 schema
├── docs/examples/*.http       # 各 API 的完整 request / response
├── tests/
│   ├── test_api.py            # 公開 API 與資料庫測試
│   └── test_writes.py         # 綁定、寫入、併發、migration 與 middleware 測試
├── scripts/
│   ├── generate_spec.py       # 由 Pydantic models 更新 request schemas
│   ├── generate_examples.py   # 以獨立 DB 副本產生回應範例與 README 表格
│   ├── verify_examples.py     # 範例、連結與 schema 驗證
│   ├── verify_api.py          # 實際 HTTP 公開 API 驗證
│   ├── verify_writes.py       # 實際 HTTP 讀寫流程驗證
│   ├── compare_api.py         # 兩個服務的公開回應比對
│   └── compare_writes.py      # 共用 DB 的跨服務寫入與重試比對
├── .github/workflows/ci.yml
├── Dockerfile
├── Makefile
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## 啟動方式

### 使用 Python

需要 Python 3.12 或以上版本。在專案根目錄執行：

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export API_WRITE_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers --no-access-log
```

- API：`http://localhost:8000`
- Swagger UI：`http://localhost:8000/docs`
- OpenAPI：`http://localhost:8000/openapi.json`
- API Explorer：`http://localhost:8000/`

`API_WRITE_KEY` 至少 32 bytes；私人 GET、收藏／行程／評價寫入須帶 `X-API-Key`。建立資料另須帶 `Idempotency-Key`，PUT 須帶目前的 `version`。未設定服務金鑰時，私人 API 回傳 503，公開查詢與報價仍可使用。

預設使用 `data/travel.sqlite3`，寫入時資料庫與所在目錄須可寫入。舊版 v1 DB 在啟用服務金鑰後自動遷移至 v2。指定資料庫、日期、允許的瀏覽器來源與連接埠：

```sh
DB_PATH=/absolute/path/travel.sqlite3 REFERENCE_DATE=2026-10-20 \
  CORS_ORIGINS=http://localhost:3000,http://localhost:5173 \
  uvicorn app.main:app --host 127.0.0.1 --port 8001 --no-proxy-headers --no-access-log
```

未指定 `REFERENCE_DATE` 時使用服務啟動當天的台北日期。`CORS_ORIGINS` 預設為 `*`。寫入及報價每個連線來源 IP 每分鐘補充 60 次額度，最多累積 20 次；JSON body 上限 64 KiB。

執行測試、更新範例與驗證文件：

```sh
pip install -r requirements-dev.txt
make test
make generate
make verify-examples
```

實際 HTTP 驗證請使用測試 DB 副本；`verify-writes` 會新增及修改資料。在另一個終端啟動測試服務，並讓兩個終端使用相同的 `API_WRITE_KEY`：

```sh
cp data/travel.sqlite3 /tmp/travel-test.sqlite3
DB_PATH=/tmp/travel-test.sqlite3 REFERENCE_DATE=2026-10-20 make run
# 服務啟動後，在原終端執行：
make verify verify-writes
```

比對兩個服務時，請設定相同的 `REFERENCE_DATE`。`compare_writes.py` 另要求兩個服務共用同一份測試 DB 與 `API_WRITE_KEY`，會新增及修改資料：

```sh
python scripts/compare_api.py --base-url http://localhost:8000 --reference-url http://localhost:8080
python scripts/compare_writes.py --base-url http://localhost:8000 --reference-url http://localhost:8080
```

### 使用 Docker

保留上述 `API_WRITE_KEY` 環境變數。使用 volume 保存 DB：

```sh
docker build -t travel-python-fastapi .
docker volume create travel-python-data
docker run --rm --name travel-python-api --read-only -p 8000:8000 \
  -e API_WRITE_KEY -e REFERENCE_DATE=2026-10-20 \
  --mount type=volume,source=travel-python-data,target=/app/data \
  travel-python-fastapi
```

也可掛載主機資料夾；容器的 UID 10001 須可寫入該資料夾及 DB：

```sh
docker run --rm -p 8001:8000 -e API_WRITE_KEY \
  --mount type=bind,source="$(pwd)/data",target=/app/data \
  travel-python-fastapi
```

## Request / response examples

共 42 個 API：35 GET、4 POST、3 PUT。每一列對應一組 method 與 endpoint，範例包含完整 JSON 回應。

範例日期為 `REFERENCE_DATE=2026-10-20`。`{{API_WRITE_KEY}}` 請替換為服務金鑰；`{{wishlist_id}}`、`{{trip_id}}`、`{{review_id}}` 請使用對應 POST 回傳的 ID。回應中的時間為固定示例值，PUT 範例使用建立後的 `version=1`。

| Method | Endpoint | 說明 | Request 範例 | Response 範例 |
| --- | --- | --- | --- | --- |
| `GET` | `/api/v1/articles` | 旅遊內容列表 | [Request](docs/examples/get-api-v1-articles.request.http) | [Response](docs/examples/get-api-v1-articles.response.http) |
| `GET` | `/api/v1/articles/{id}` | 旅遊文章詳情 | [Request](docs/examples/get-api-v1-articles-id.request.http) | [Response](docs/examples/get-api-v1-articles-id.response.http) |
| `GET` | `/api/v1/campaigns` | 優惠活動列表 | [Request](docs/examples/get-api-v1-campaigns.request.http) | [Response](docs/examples/get-api-v1-campaigns.response.http) |
| `GET` | `/api/v1/campaigns/{id}` | 優惠條件與折扣碼 | [Request](docs/examples/get-api-v1-campaigns-id.request.http) | [Response](docs/examples/get-api-v1-campaigns-id.response.http) |
| `GET` | `/api/v1/categories` | 旅遊品類捷徑 | [Request](docs/examples/get-api-v1-categories.request.http) | [Response](docs/examples/get-api-v1-categories.response.http) |
| `GET` | `/api/v1/collections` | 主題集合列表 | [Request](docs/examples/get-api-v1-collections.request.http) | [Response](docs/examples/get-api-v1-collections.response.http) |
| `GET` | `/api/v1/collections/{id}` | 主題集合內容 | [Request](docs/examples/get-api-v1-collections-id.request.http) | [Response](docs/examples/get-api-v1-collections-id.response.http) |
| `GET` | `/api/v1/collections/{id}/products` | 集合內的商品搜尋 | [Request](docs/examples/get-api-v1-collections-id-products.request.http) | [Response](docs/examples/get-api-v1-collections-id-products.response.http) |
| `GET` | `/api/v1/config` | 前端設定與選項 | [Request](docs/examples/get-api-v1-config.request.http) | [Response](docs/examples/get-api-v1-config.response.http) |
| `GET` | `/api/v1/cruise/regions` | 郵輪港口、航區與品牌 | [Request](docs/examples/get-api-v1-cruise-regions.request.http) | [Response](docs/examples/get-api-v1-cruise-regions.response.http) |
| `GET` | `/api/v1/destinations` | 目的地列表 | [Request](docs/examples/get-api-v1-destinations.request.http) | [Response](docs/examples/get-api-v1-destinations.response.http) |
| `GET` | `/api/v1/destinations/{id}` | 目的地探索頁 | [Request](docs/examples/get-api-v1-destinations-id.request.http) | [Response](docs/examples/get-api-v1-destinations-id.response.http) |
| `GET` | `/api/v1/faqs` | 常見問題搜尋 | [Request](docs/examples/get-api-v1-faqs.request.http) | [Response](docs/examples/get-api-v1-faqs.response.http) |
| `GET` | `/api/v1/home` | 首頁區塊聚合 | [Request](docs/examples/get-api-v1-home.request.http) | [Response](docs/examples/get-api-v1-home.response.http) |
| `GET` | `/api/v1/navigation` | 主導覽與頁尾 | [Request](docs/examples/get-api-v1-navigation.request.http) | [Response](docs/examples/get-api-v1-navigation.response.http) |
| `GET` | `/api/v1/products` | 商品搜尋結果 | [Request](docs/examples/get-api-v1-products.request.http) · [400](docs/examples/get-api-v1-products.invalid.request.http) | [Response](docs/examples/get-api-v1-products.response.http) · [400](docs/examples/get-api-v1-products.invalid.response.http) |
| `GET` | `/api/v1/products/map` | 商品地圖點位 | [Request](docs/examples/get-api-v1-products-map.request.http) | [Response](docs/examples/get-api-v1-products-map.response.http) |
| `GET` | `/api/v1/products/{id}` | 商品完整詳情 | [Request](docs/examples/get-api-v1-products-id.request.http) | [Response](docs/examples/get-api-v1-products-id.response.http) |
| `GET` | `/api/v1/products/{id}/availability` | 方案容量、庫存與報價 | [Request](docs/examples/get-api-v1-products-id-availability.request.http) | [Response](docs/examples/get-api-v1-products-id-availability.response.http) |
| `GET` | `/api/v1/products/{id}/cabins` | 郵輪艙房 | [Request](docs/examples/get-api-v1-products-id-cabins.request.http) | [Response](docs/examples/get-api-v1-products-id-cabins.response.http) |
| `GET` | `/api/v1/products/{id}/calendar` | 每日起價與庫存日曆 | [Request](docs/examples/get-api-v1-products-id-calendar.request.http) | [Response](docs/examples/get-api-v1-products-id-calendar.response.http) |
| `GET` | `/api/v1/products/{id}/departures` | 郵輪與跟團出發日期 | [Request](docs/examples/get-api-v1-products-id-departures.request.http) | [Response](docs/examples/get-api-v1-products-id-departures.response.http) |
| `GET` | `/api/v1/products/{id}/itinerary` | 郵輪與跟團每日行程 | [Request](docs/examples/get-api-v1-products-id-itinerary.request.http) | [Response](docs/examples/get-api-v1-products-id-itinerary.response.http) |
| `GET` | `/api/v1/products/{id}/plans` | 跟團、體驗與交通方案 | [Request](docs/examples/get-api-v1-products-id-plans.request.http) | [Response](docs/examples/get-api-v1-products-id-plans.response.http) |
| `POST` | `/api/v1/products/{id}/quotes` | 以 JSON 查詢方案容量與報價 | [Request](docs/examples/post-api-v1-products-id-quotes.request.http) | [Response](docs/examples/post-api-v1-products-id-quotes.response.http) |
| `GET` | `/api/v1/products/{id}/related` | 相關商品推薦 | [Request](docs/examples/get-api-v1-products-id-related.request.http) | [Response](docs/examples/get-api-v1-products-id-related.response.http) |
| `GET` | `/api/v1/products/{id}/reviews` | 商品旅客評價 | [Request](docs/examples/get-api-v1-products-id-reviews.request.http) | [Response](docs/examples/get-api-v1-products-id-reviews.response.http) |
| `POST` | `/api/v1/products/{id}/reviews` | 建立商品評價 | [Request](docs/examples/post-api-v1-products-id-reviews.request.http) | [Response](docs/examples/post-api-v1-products-id-reviews.response.http) |
| `GET` | `/api/v1/products/{id}/reviews/{review_id}` | 取得可編輯評價與版本 | [Request](docs/examples/get-api-v1-products-id-reviews-review_id.request.http) | [Response](docs/examples/get-api-v1-products-id-reviews-review_id.response.http) |
| `PUT` | `/api/v1/products/{id}/reviews/{review_id}` | 完整取代 API 建立的評價 | [Request](docs/examples/put-api-v1-products-id-reviews-review_id.request.http) | [Response](docs/examples/put-api-v1-products-id-reviews-review_id.response.http) |
| `GET` | `/api/v1/products/{id}/rooms` | 住宿房型 | [Request](docs/examples/get-api-v1-products-id-rooms.request.http) | [Response](docs/examples/get-api-v1-products-id-rooms.response.http) |
| `GET` | `/api/v1/search/filters` | 目前查詢結果的篩選計數 | [Request](docs/examples/get-api-v1-search-filters.request.http) | [Response](docs/examples/get-api-v1-search-filters.response.http) |
| `GET` | `/api/v1/search/suggestions` | 目的地與商品自動完成 | [Request](docs/examples/get-api-v1-search-suggestions.request.http) | [Response](docs/examples/get-api-v1-search-suggestions.response.http) |
| `GET` | `/api/v1/trips` | 查詢旅遊行程 | [Request](docs/examples/get-api-v1-trips.request.http) | [Response](docs/examples/get-api-v1-trips.response.http) |
| `POST` | `/api/v1/trips` | 建立旅遊行程 | [Request](docs/examples/post-api-v1-trips.request.http) · [400](docs/examples/post-api-v1-trips.invalid.request.http) | [Response](docs/examples/post-api-v1-trips.response.http) · [400](docs/examples/post-api-v1-trips.invalid.response.http) |
| `GET` | `/api/v1/trips/{id}` | 取得旅遊行程 | [Request](docs/examples/get-api-v1-trips-id.request.http) | [Response](docs/examples/get-api-v1-trips-id.response.http) |
| `PUT` | `/api/v1/trips/{id}` | 完整取代旅遊行程 | [Request](docs/examples/put-api-v1-trips-id.request.http) | [Response](docs/examples/put-api-v1-trips-id.response.http) |
| `GET` | `/api/v1/wishlists` | 查詢收藏清單 | [Request](docs/examples/get-api-v1-wishlists.request.http) | [Response](docs/examples/get-api-v1-wishlists.response.http) |
| `POST` | `/api/v1/wishlists` | 建立收藏清單 | [Request](docs/examples/post-api-v1-wishlists.request.http) | [Response](docs/examples/post-api-v1-wishlists.response.http) |
| `GET` | `/api/v1/wishlists/{id}` | 取得收藏清單 | [Request](docs/examples/get-api-v1-wishlists-id.request.http) | [Response](docs/examples/get-api-v1-wishlists-id.response.http) |
| `PUT` | `/api/v1/wishlists/{id}` | 完整取代收藏清單 | [Request](docs/examples/put-api-v1-wishlists-id.request.http) | [Response](docs/examples/put-api-v1-wishlists-id.response.http) |
| `GET` | `/healthz` | 健康檢查 | [Request](docs/examples/get-healthz.request.http) | [Response](docs/examples/get-healthz.response.http) |
