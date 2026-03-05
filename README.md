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
| AuthN/AuthZ | JWT Bearer + RBAC (roles: admin/editor/viewer) |
| Server | Uvicorn |

## Cấu trúc project

```
chatbot-document/
├── app/
│   ├── main.py                     # Entry point, khởi tạo FastAPI app
│   ├── core/
│   │   ├── config.py               # Settings (pydantic-settings, đọc từ .env)
│   │   ├── database.py             # Async SQLAlchemy engine & session
│   │   ├── security.py             # Password hash + JWT helpers
│   │   ├── bootstrap.py            # Seed default admin khi startup
│   │   └── exceptions.py           # Custom HTTP exceptions
│   ├── api/
│   │   ├── deps.py                 # FastAPI dependencies (DB session, ...)
│   │   └── v1/
│   │       ├── router.py           # Tổng hợp tất cả routers của v1
│   │       └── endpoints/
│   │           ├── health.py       # GET /api/v1/health
│   │           └── auth.py         # Login, me, user/role management
│   ├── models/                     # SQLAlchemy ORM models (mapping DB schema)
│   │   ├── base.py
│   │   ├── guideline.py
│   │   ├── guideline_version.py
│   │   ├── document.py
│   │   ├── section.py
│   │   ├── chunk.py
│   │   ├── chunk_embedding.py
│   │   └── user.py
│   ├── schemas/                    # Pydantic schemas (request/response)
│       ├── base.py
│       ├── health.py
│       └── auth.py
│   └── services/
│       └── auth_service.py         # Business logic cho auth và phân quyền
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

## Auth & RBAC

### Mặc định khi startup

- App tự `create table` nếu `AUTO_CREATE_TABLES=true`.
- Role được lưu trực tiếp trong `users.role` với 3 giá trị: `admin`, `editor`, `viewer`.
- Nếu `SEED_AUTH_DATA=true`, app sẽ đảm bảo có tài khoản admin mặc định từ `.env`.

Biến môi trường liên quan:

```env
JWT_SECRET_KEY="change-this-secret"
JWT_ALGORITHM="HS256"
ACCESS_TOKEN_EXPIRE_MINUTES=60
AUTO_CREATE_TABLES=true
SEED_AUTH_DATA=true
DEFAULT_ADMIN_EMAIL="admin@example.com"
DEFAULT_ADMIN_PASSWORD="ChangeMe123!"
DEFAULT_ADMIN_FULL_NAME="System Admin"
LOCAL_STORAGE_ROOT=uploads
```

### Luồng sử dụng nhanh

1. Đăng nhập bằng tài khoản admin mặc định ở `.env`.
2. Lấy `access_token` từ `POST /api/v1/auth/login`.
3. Truyền token vào header: `Authorization: Bearer <token>`.
4. Quản lý user/role qua các endpoint admin.

Swagger `Authorize`:
- Gọi `POST /api/v1/auth/login` để lấy `access_token`
- Bấm `Authorize` và nhập token theo dạng: `Bearer <access_token>`

### Auth Endpoints

| Method | Path | Quyền |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Public |
| `GET` | `/api/v1/auth/me` | Đã đăng nhập |
| `GET` | `/api/v1/auth/roles` | `admin` |
| `GET` | `/api/v1/auth/users` | `admin` |
| `POST` | `/api/v1/auth/users` | `admin` |
| `PATCH` | `/api/v1/auth/users/{user_id}/role` | `admin` |

Ví dụ login:

```json
{
  "email": "admin@example.com",
  "password": "ChangeMe123!"
}
```

### Guideline Endpoints (Current)

| Method | Path | Quyền |
|---|---|---|
| `GET` | `/api/v1/guidelines` | `viewer/editor/admin` |
| `GET` | `/api/v1/guidelines/{guideline_id}/versions` | `viewer/editor/admin` |
| `POST` | `/api/v1/guidelines` | `editor/admin` |
| `POST` | `/api/v1/guidelines/{guideline_id}/versions` | `editor/admin` |
| `GET` | `/api/v1/versions/{version_id}/workspace` | `viewer/editor/admin` |
| `GET` | `/api/v1/documents/{document_id}/file` | `viewer/editor/admin` |

`POST /api/v1/guidelines` dùng `multipart/form-data` với các field:

- `title` (required)
- `file` (required, PDF)
- `publisher`, `chuyen_khoa`, `version_label`, `release_date`, `effective_from`, `effective_to`, `status` (optional)

`POST /api/v1/guidelines/{guideline_id}/versions` dùng `multipart/form-data` với các field:

- `version_label`, `release_date`, `effective_from`, `effective_to`, `status` (optional)
- `file` (optional, nếu gửi thì phải là PDF)
- Rule status: nếu version mới có `status` thuộc nhóm active (`active`, `dang_hieu_luc`, `đang hiệu lực`) thì các version active cũ của guideline đó sẽ tự chuyển sang `inactive`

`GET /api/v1/documents/{document_id}/file`:

- Trả stream file theo `storage_uri` trong DB
- Hỗ trợ header `Range` (ví dụ `Range: bytes=0-1023`) để PDF viewer tải mượt
- Chỉ cho role `viewer/editor/admin`

## Database Schema (tóm tắt)

```
guidelines          — metadata guideline (title, publisher, chuyen_khoa)
guideline_versions  — các phiên bản của guideline (version_label, status, ...)
documents           — file PDF gốc gắn với version (storage_uri, ...)
sections            — cây mục lục TOC (heading, level, parent_id, ...)
chunks              — đoạn văn bản đã chia nhỏ cho AI retrieval
chunk_embeddings    — vector embedding cho mỗi chunk
users               — tài khoản đăng nhập CMS + cột role (admin/editor/viewer)
```
