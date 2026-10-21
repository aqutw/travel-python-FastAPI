# Travel Python FastAPI

## 專案目錄與檔案結構

```text
travel-python-FastAPI/
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI factory、啟動與 HTTP 設定
│   ├── routes.py              # 路由與 request-scoped database dependency
│   ├── query.py               # 查詢參數驗證
│   ├── database.py            # SQLite 連線與查詢
│   ├── domain.py              # 搜尋、庫存與報價計算
│   ├── errors.py              # 錯誤回應
│   ├── openapi.json           # API 契約
│   └── index.html             # API Explorer
├── data/travel.sqlite3        # 頁面資料、商品、評論與圖片
├── db/schema.sql              # SQLite schema
├── tests/test_api.py          # API 與資料庫整合測試
├── scripts/
│   ├── verify_api.py          # 實際 HTTP 回應與 schema 驗證
│   └── compare_api.py         # 兩個服務的回應比對
├── .github/workflows/ci.yml
├── .dockerignore
├── .gitignore
├── Dockerfile
├── Makefile
├── requirements.txt
├── requirements-dev.txt
├── LICENSE
└── README.md
```

## 啟動方式

### 使用 Python

需要 Python 3.12 或以上版本。在專案根目錄執行：

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- API：`http://localhost:8000`
- Swagger UI：`http://localhost:8000/docs`
- OpenAPI：`http://localhost:8000/openapi.json`
- API Explorer：`http://localhost:8000/`

預設讀取專案內的 `data/travel.sqlite3`。指定資料庫路徑、日期與連接埠：

```sh
DB_PATH=/absolute/path/travel.sqlite3 REFERENCE_DATE=2026-10-20 \
  uvicorn app.main:app --host 127.0.0.1 --port 8001
```

未指定 `REFERENCE_DATE` 時使用服務啟動當天的台北日期。

執行測試與實際 HTTP 驗證：

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
# 先啟動服務，再執行：
python scripts/verify_api.py --base-url http://localhost:8000
```

比對兩個已啟動且 `REFERENCE_DATE` 相同的服務：

```sh
python scripts/compare_api.py \
  --base-url http://localhost:8000 \
  --reference-url http://localhost:8080
```

### 使用 Docker

```sh
docker build -t travel-python-fastapi .
docker run --rm -p 8000:8000 travel-python-fastapi
```

指定主機連接埠與日期：

```sh
docker run --rm -p 8001:8000 \
  -e REFERENCE_DATE=2026-10-20 \
  travel-python-fastapi
```

掛載主機資料庫：

```sh
docker run --rm -p 8000:8000 \
  --mount type=bind,source="$(pwd)/data/travel.sqlite3",target=/app/data/travel.sqlite3,readonly \
  travel-python-fastapi
```

## Request / response examples

以下回應以 `REFERENCE_DATE=2026-10-20` 為例。

### 健康檢查

Request：

```sh
curl -i 'http://localhost:8000/healthz'
```

Response：`HTTP 200`

```json
{
  "data": {
    "status": "ok"
  },
  "meta": {
    "mock": true,
    "locale": "zh-TW",
    "currency": "TWD",
    "reference_date": "2026-10-20"
  }
}
```

### 搜尋住宿商品

Request：

```sh
curl -i 'http://localhost:8000/api/v1/products?type=stay&destination_id=taipei&per_page=1'
```

Response：`HTTP 200`

```json
{
  "data": [
    {
      "id": "stay-001",
      "type": "stay",
      "title": "台北｜木光旅宿",
      "summary": "以台北為起點，享受從容的住宿時光。所有內容皆為測試假資料。",
      "destination_id": "taipei",
      "location_name": "台灣・台北",
      "latitude": 25.033,
      "longitude": 121.565,
      "cover": {
        "url": "/images/stay.svg",
        "alt": "台北｜木光旅宿示意圖",
        "width": 1200,
        "height": 800
      },
      "price": {
        "amount": 2400,
        "original_amount": 2880,
        "currency": "TWD",
        "unit": "night",
        "tax_included": true
      },
      "rating": {
        "average": 4.6,
        "count": 5
      },
      "tags": [
        "親子友善",
        "熱門推薦",
        "飯店"
      ],
      "amenities": [
        "wifi",
        "breakfast",
        "parking"
      ],
      "duration_days": 1,
      "popularity": 1000,
      "free_cancellation": true,
      "confirmation": "instant",
      "detail_url": "/api/v1/products/stay-001"
    }
  ],
  "meta": {
    "mock": true,
    "locale": "zh-TW",
    "currency": "TWD",
    "reference_date": "2026-10-20",
    "pagination": {
      "page": 1,
      "per_page": 1,
      "total": 1,
      "total_pages": 1,
      "has_next": false
    },
    "facets": {
      "types": [
        {
          "value": "stay",
          "count": 1
        }
      ],
      "destinations": [
        {
          "value": "taipei",
          "count": 1
        }
      ],
      "tags": [
        {
          "value": "熱門推薦",
          "count": 1
        },
        {
          "value": "親子友善",
          "count": 1
        },
        {
          "value": "飯店",
          "count": 1
        }
      ],
      "amenities": [
        {
          "value": "breakfast",
          "count": 1
        },
        {
          "value": "parking",
          "count": 1
        },
        {
          "value": "wifi",
          "count": 1
        }
      ],
      "min_price": 2400,
      "max_price": 2400
    }
  }
}
```

### 查詢郵輪艙房

Request：

```sh
curl -i 'http://localhost:8000/api/v1/products/cruise-001/cabins'
```

Response：`HTTP 200`

```json
{
  "data": [
    {
      "id": "cruise-001-opt-1",
      "name": "內艙雙人房",
      "capacity": 2,
      "description": "內艙雙人房，提供測試用價格與容量。",
      "amenities": [
        "chinese_service",
        "guide"
      ],
      "image": {
        "url": "/images/cruise.svg",
        "alt": "內艙雙人房示意圖",
        "width": 1200,
        "height": 800
      },
      "price": {
        "amount": 12900,
        "original_amount": 15480,
        "currency": "TWD",
        "unit": "person",
        "tax_included": true
      }
    },
    {
      "id": "cruise-001-opt-2",
      "name": "陽台四人房",
      "capacity": 4,
      "description": "陽台四人房，提供測試用價格與容量。",
      "amenities": [
        "chinese_service",
        "guide"
      ],
      "image": {
        "url": "/images/cruise.svg",
        "alt": "陽台四人房示意圖",
        "width": 1200,
        "height": 800
      },
      "price": {
        "amount": 15400,
        "original_amount": 18480,
        "currency": "TWD",
        "unit": "person",
        "tax_included": true
      }
    }
  ],
  "meta": {
    "mock": true,
    "locale": "zh-TW",
    "currency": "TWD",
    "reference_date": "2026-10-20"
  }
}
```

### 查詢住宿庫存與報價

Request：

```sh
curl -i 'http://localhost:8000/api/v1/products/stay-001/availability?start_date=2026-11-01&end_date=2026-11-03&adults=2&quantity=1'
```

Response：`HTTP 200`

```json
{
  "data": {
    "product_id": "stay-001",
    "start_date": "2026-11-01",
    "end_date": "2026-11-03",
    "nights": 2,
    "adults": 2,
    "children": 0,
    "quantity": 1,
    "available": true,
    "currency": "TWD",
    "options": [
      {
        "option_id": "stay-001-opt-1",
        "name": "經典雙人房",
        "capacity": 2,
        "remaining": 0,
        "available": false,
        "unit_price": {
          "amount": 2400,
          "original_amount": 2880,
          "currency": "TWD",
          "unit": "night",
          "tax_included": true
        },
        "multiplier": 2,
        "total_amount": 4800
      },
      {
        "option_id": "stay-001-opt-2",
        "name": "景觀四人房",
        "capacity": 4,
        "remaining": 1,
        "available": true,
        "unit_price": {
          "amount": 3000,
          "original_amount": 3600,
          "currency": "TWD",
          "unit": "night",
          "tax_included": true
        },
        "multiplier": 2,
        "total_amount": 6000
      }
    ],
    "disclaimer": "模擬報價，不保留庫存，也不建立訂單。"
  },
  "meta": {
    "mock": true,
    "locale": "zh-TW",
    "currency": "TWD",
    "reference_date": "2026-10-20"
  }
}
```

### 無效查詢參數

Request：

```sh
curl -i 'http://localhost:8000/api/v1/products?page=0'
```

Response：`HTTP 400`

```json
{
  "error": {
    "code": "invalid_query",
    "message": "page: must be an integer between 1 and 1000000"
  }
}
```
