# Guideline Management Platform — Backend API

FastAPI backend cho hệ thống quản lý hướng dẫn y tế (Guideline Management Platform).

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 (async) |
| Database driver | asyncpg (async), psycopg2 (Alembic) |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Server | Uvicorn |

## Cấu trúc project

```
chatbot-document/
├── app/
│   ├── main.py                     # Entry point, khởi tạo FastAPI app
│   ├── core/
│   │   ├── config.py               # Settings (pydantic-settings, đọc từ .env)
│   │   ├── database.py             # Async SQLAlchemy engine & session
│   │   └── exceptions.py           # Custom HTTP exceptions
│   ├── api/
│   │   ├── deps.py                 # FastAPI dependencies (DB session, ...)
│   │   └── v1/
│   │       ├── router.py           # Tổng hợp tất cả routers của v1
│   │       └── endpoints/
│   │           └── health.py       # GET /api/v1/health
│   ├── models/                     # SQLAlchemy ORM models (mapping DB schema)
│   │   ├── base.py
│   │   ├── guideline.py
│   │   ├── guideline_version.py
│   │   ├── document.py
│   │   ├── section.py
│   │   ├── chunk.py
│   │   └── chunk_embedding.py
│   └── schemas/                    # Pydantic schemas (request/response)
│       ├── base.py
│       └── health.py
├── .env                            # Biến môi trường (không commit)
├── .env.example                    # Mẫu biến môi trường
├── .gitignore
├── requirements.txt
└── README.md
```

## Yêu cầu

- Python 3.11+
- PostgreSQL đang chạy trên Docker (xem mục Database)

## Cài đặt

### 1. Clone & tạo virtual environment

```bash
git clone <repo-url>
cd chatbot-document

python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / macOS
source .venv/bin/activate
```

### 2. Cài dependencies

```bash
pip install -r requirements.txt
```

### 3. Cấu hình biến môi trường

```bash
cp .env.example .env
# Chỉnh sửa .env nếu cần (DB host/port/user/pass đã được điền sẵn theo server)
```

### 4. Kiểm tra Docker database

```bash
# Nếu container chưa chạy:
docker start chatbot-y-te

# Kiểm tra kết nối:
# host: localhost  port: 5436  user: phuongnh  pass: 1234  db: chatbot_healthcare
```

## Chạy server

### Development (auto-reload)

```bash
python -m app.main
```

Hoặc dùng uvicorn trực tiếp:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Production

```bash
APP_ENV=production DEBUG=false uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Docs

Sau khi server chạy, truy cập:

| URL | Mô tả |
|---|---|
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/api/v1/health` | Health check |

### Health Check

```
GET /api/v1/health
```

Response mẫu:

```json
{
  "status": "ok",
  "app_name": "Guideline Management Platform API",
  "version": "0.1.0",
  "environment": "development",
  "timestamp": "2026-03-04T07:00:00.000Z",
  "database": "connected"
}
```

Nếu DB không kết nối được, `status` sẽ là `"degraded"` và `database` sẽ là `"disconnected"`.

## Database Schema (tóm tắt)

```
guidelines          — metadata guideline (title, publisher, chuyen_khoa)
guideline_versions  — các phiên bản của guideline (version_label, status, ...)
documents           — file PDF gốc gắn với version (storage_uri, ...)
sections            — cây mục lục TOC (heading, level, parent_id, ...)
chunks              — đoạn văn bản đã chia nhỏ cho AI retrieval
chunk_embeddings    — vector embedding cho mỗi chunk
```
