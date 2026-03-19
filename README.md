# Guideline Management Platform — Backend API

FastAPI backend cho hệ thống quản lý hướng dẫn y tế (Guideline Management Platform).

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 (async) |
| Database driver | asyncpg (async), psycopg2 (Alembic), pgvector |
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

Lưu ý:
- Nếu dùng schema có cột `chunks.embedding halfvec(3072)`, PostgreSQL cần có extension/vector type tương ứng trước khi app startup với `AUTO_CREATE_TABLES=true`.
- Backend hiện build `chunks` sau khi persist `sections`, tạo `text_abstract` bằng OpenAI và lưu `embedding` vào chính bảng `chunks`.

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

## Chạy bằng Docker (để FE gọi trực tiếp)

### 1) Chuẩn bị biến môi trường

- Dùng `.env` hiện tại.
- Nếu BE chạy trong docker và DB chạy trên host máy:
  - giữ `DB_HOST=localhost` cho local python run
  - thêm/đổi `DB_HOST_DOCKER=host.docker.internal`
- Nếu DB ở server khác/container khác thì set `DB_HOST_DOCKER=<db-host-thực-tế>`.

### 2) Build và chạy

```bash
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend
```

### 3) URL FE dùng

- Swagger: `http://<server-ip>:8000/docs`
- API base: `http://<server-ip>:8000/api/v1`

Ví dụ health check từ máy FE:

```bash
curl http://<server-ip>:8000/api/v1/health
```

### 4) Cập nhật bản BE mới

```bash
docker compose build --no-cache backend
docker compose up -d backend
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
SCORE_THRESHOLD=0.65
CHUNK_MAX_CHARS=3000
LANDINGAI_API_KEY=
LANDINGAI_API_URL="https://api.va.landing.ai/v1/ade/parse"
LANDINGAI_MODEL_NAME="dpt-2-latest"
OPENAI_API_KEY=
OPENAI_API_URL="https://api.openai.com/v1"
OPENAI_MODEL_NAME="gpt-4.1"
OPENAI_EMBEDDING_MODEL_NAME="text-embedding-3-large"
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
| `DELETE` | `/api/v1/guidelines/{guideline_id}` | `admin` |
| `POST` | `/api/v1/guidelines/{guideline_id}/versions` | `editor/admin` |
| `GET` | `/api/v1/versions/{version_id}/workspace` | `viewer/editor/admin` |
| `PATCH` | `/api/v1/versions/{version_id}/sections/content` | `editor/admin` |
| `DELETE` | `/api/v1/versions/{version_id}` | `editor/admin` |
| `GET` | `/api/v1/documents/{document_id}/file` | `viewer/editor/admin` |

`POST /api/v1/guidelines` dùng `multipart/form-data` với các field:

- `title` (required)
- `file` (required, PDF)
- `ten_benh`, `publisher`, `chuyen_khoa`, `version_label`, `release_date`, `effective_from`, `effective_to`, `status` (optional)
- API sẽ tự chạy pipeline nội bộ: OCR (LandingAI) -> TOC (OpenAI) -> chunking -> clean markdown -> ghi `sections`
- Artifact debug như `chunks.json` vẫn được lưu ở local storage, nhưng hiện tại chưa ghi dữ liệu vào bảng `chunks`

`POST /api/v1/guidelines/{guideline_id}/versions` dùng `multipart/form-data` với các field:

- `version_label`, `release_date`, `effective_from`, `effective_to`, `status` (optional)
- `file` (required, PDF)
- Rule status: nếu version mới có `status` thuộc nhóm active (`active`, `dang_hieu_luc`, `đang hiệu lực`) thì các version active cũ của guideline đó sẽ tự chuyển sang `inactive`
- API sẽ tự chạy pipeline nội bộ giống luồng tạo guideline mới

`GET /api/v1/documents/{document_id}/file`:

- Trả stream file theo `storage_uri` trong DB
- Hỗ trợ header `Range` (ví dụ `Range: bytes=0-1023`) để PDF viewer tải mượt
- Chỉ cho role `viewer/editor/admin`

`PATCH /api/v1/versions/{version_id}/sections/content`:

- Sửa đồng thời nhiều section của cùng một version (save một lần từ FE)
- Cho phép sửa `content`, `heading`, hoặc cả hai trong mỗi phần tử `updates`
- Input JSON:

```json
{
  "updates": [
    { "section_id": 101, "content": "Nội dung mới A" },
    { "section_id": 102, "heading": "Mục tiêu điều trị" },
    { "section_id": 103, "heading": "Chẩn đoán", "content": "Nội dung mới C" }
  ]
}
```

- BE cập nhật trực tiếp `sections.content`/`sections.heading`
- Không tạo lịch sử edit
- Không re-index lại `start_char/end_char/page/score`
- Không xử lý bảng `chunks` ở bước này

`DELETE /api/v1/versions/{version_id}`:

- Xóa một version và dữ liệu liên quan
- Xóa luôn thư mục local storage của version: `uploads/guidelines/{guideline_id}/{version_id}`
- Nếu version bị xóa đang `active`, hệ thống tự promote version gần nhất còn lại lên `active`

`DELETE /api/v1/guidelines/{guideline_id}`:

- Xóa toàn bộ guideline cùng tất cả versions liên quan
- Xóa luôn thư mục local storage: `uploads/guidelines/{guideline_id}`
- Chỉ `admin` được phép gọi

`GET /api/v1/versions/{version_id}/workspace`:

- TOC node trả thêm `page_start`, `page_end`, `score`, `is_suspect` để FE điều hướng + highlight nghi ngờ
- Trả thêm `suspect_score_threshold` và `suspect_section_count` ở cấp response
- Có thể override ngưỡng qua query param `suspect_threshold` (0.0 < x < 1.0)

### Pipeline Test (Upload/OCR/TOC)

Pipeline implementation (refactored):

- Orchestrator: `app/services/document_ingestion_pipeline_service.py`
- OCR adapter: `app/services/pipeline/ocr_service.py`
- Markdown preprocess: `app/services/pipeline/markdown_service.py`
- TOC builder: `app/services/pipeline/toc_service.py`
- Fuzzy chunking: `app/services/pipeline/chunking_service.py`
- DB/artifact persistence: `app/services/pipeline/persistence_service.py`
- Prompt templates: `app/services/pipeline/prompts.py`

File test mới: `tests/test_upload_pipeline_flow.py`

- Test preflight config (`LANDINGAI_API_KEY`, `OPENAI_API_KEY`, model name, threshold)
- Test orchestration luồng pipeline bằng file mẫu trong `examples/`
- Có test live tùy chọn với OCR/TOC thật (bật bằng env `RUN_LIVE_PIPELINE_TEST=1`)
- Có test live kiểm tra API key + model dùng được thật (bật bằng env `RUN_LIVE_KEY_CHECK=1`)

Chạy test:

```bash
DEBUG=true python -m unittest tests.test_upload_pipeline_flow -v
```

Chạy live test OCR/TOC thật:

```bash
DEBUG=true RUN_LIVE_PIPELINE_TEST=1 python -m unittest tests.test_upload_pipeline_flow.TestUploadPipelineFlow.test_live_pipeline_with_example_pdf_optional -v
```

Chạy check key/model/connectivity thật:

```bash
DEBUG=true RUN_LIVE_KEY_CHECK=1 python -m unittest tests.test_upload_pipeline_flow.TestUploadPipelineFlow.test_live_key_connectivity_optional -v
```

Chạy script chẩn đoán chi tiết (khuyến nghị để gửi log):

```bash
DEBUG=true PYTHONPATH=. python -m tests.check_pipeline_services
```

## Database Schema (tóm tắt)

```
guidelines          — metadata guideline (title, ten_benh, publisher, chuyen_khoa)
guideline_versions  — các phiên bản của guideline (version_label, status, ...)
documents           — file PDF gốc gắn với version (storage_uri, ...)
sections            — cây mục lục TOC (heading, level, parent_id, ...)
chunks              — bảng để dành cho phase sau của AI retrieval, hiện backend chưa ghi/đọc trong luồng chính
users               — tài khoản đăng nhập CMS + cột role (admin/editor/viewer)
```
