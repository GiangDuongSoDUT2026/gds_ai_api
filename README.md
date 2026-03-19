# Giảng Đường Số — AI Backend (gds_ai_api)

Hệ thống backend xử lý video bài giảng tự động: scene detection, ASR, OCR, vector embedding, tìm kiếm ngữ nghĩa và chatbot AI phân quyền.

---

## Kiến trúc tổng thể

```
Upload Video (TEACHER+)
       │
       ▼
[Nginx :80] → [FastAPI API :8000]
                    │
                    ├── Stream file ──────→ [MinIO] (videos / frames)
                    ├── Insert DB  ──────→ [PostgreSQL 16 + pgvector]
                    └── Dispatch   ──────→ [RabbitMQ] → [Celery Worker (A100 GPU)]
                                                              │
                                          ┌───────────────────┼──────────────────┐
                                          ▼                   ▼                  ▼
                                   TransNetV2           faster-whisper        EasyOCR
                                 (scene detect)        (ASR large-v3)      (slide text)
                                          │                   │                  │
                                          └───────────────────┼──────────────────┘
                                                              ▼
                                                     open_clip ViT-L/14
                                                    (image embed 768-dim)
                                                              │
                                                              ▼
                                                   [PostgreSQL + pgvector]
                                                   HNSW index (cosine)
                                                              │
                                              ┌───────────────┴───────────────┐
                                              ▼                               ▼
                                     [Search API]                    [Chatbot :8001]
                               keyword (FTS) + semantic          LangChain ReAct Agent
                                                                  Role-aware tools
```

---

## Stack công nghệ

| Layer | Technology |
|---|---|
| API | FastAPI 0.115 + uvicorn |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| Task Queue | Celery 5.3 + RabbitMQ 3.13 |
| Object Storage | MinIO (S3-compatible) |
| Database | PostgreSQL 16 + pgvector 0.7 |
| ORM / Migration | SQLAlchemy 2.0 async + Alembic |
| Scene Detection | TransNetV2 (TensorFlow) |
| ASR | faster-whisper large-v3 (float16) |
| OCR | EasyOCR (vi + en) |
| Image Embedding | open_clip ViT-L/14 (768-dim) |
| Text Embedding | multilingual-e5-large (1024-dim) |
| LLM Agent | LangGraph + OpenAI / vLLM |
| Knowledge Graph | FalkorDB (GraphRAG) |
| Packaging | uv workspace (monorepo) |
| Infra | Docker Compose + Nginx |

---

## Cấu trúc thư mục

```
gds_ai_api/
├── src/
│   ├── shared/                  # Package dùng chung
│   │   └── shared/
│   │       ├── config.py        # Pydantic BaseSettings
│   │       ├── logging.py       # structlog (JSON prod / console dev)
│   │       └── database/
│   │           ├── models.py    # 11 ORM models
│   │           └── connection.py
│   ├── api/                     # FastAPI REST service (port 8000)
│   │   └── api/
│   │       ├── main.py
│   │       ├── config.py
│   │       ├── dependencies.py
│   │       ├── core/
│   │       │   └── security.py  # JWT + bcrypt
│   │       ├── dependencies/
│   │       │   └── auth.py      # get_current_user, role guards
│   │       ├── routers/
│   │       │   ├── auth.py
│   │       │   ├── organizations.py
│   │       │   ├── programs.py
│   │       │   ├── lectures.py
│   │       │   ├── upload.py    # single + bulk upload
│   │       │   ├── search.py
│   │       │   └── jobs.py
│   │       └── schemas/
│   ├── worker/                  # Celery GPU worker
│   │   └── worker/
│   │       ├── app.py
│   │       ├── celeryconfig.py
│   │       ├── tasks/
│   │       │   ├── pipeline.py       # orchestration
│   │       │   ├── scene_detection.py
│   │       │   ├── asr.py
│   │       │   ├── ocr.py
│   │       │   ├── clip_embed.py
│   │       │   └── indexing.py
│   │       ├── models/
│   │       │   ├── loader.py         # singleton GPU models
│   │       │   ├── transnetv2_model.py
│   │       │   ├── whisper_model.py
│   │       │   ├── ocr_model.py
│   │       │   ├── clip_model.py
│   │       │   └── text_embed_model.py
│   │       └── utils/
│   └── chatbot/                 # LangChain agent + WebSocket (port 8001)
│       └── chatbot/
│           ├── main.py
│           ├── config.py
│           ├── core/
│           │   └── auth.py      # decode JWT từ WS token
│           ├── agent/
│           │   ├── router.py    # LectureAgent — LangGraph (thay AgentExecutor)
│           │   ├── state.py     # AgentState TypedDict
│           │   ├── prompts.py   # system prompts theo role
│           │   └── tools/
│           │       ├── search_lectures.py  # vector search (scoped by role)
│           │       ├── query_database.py   # read-only SQL
│           │       ├── manage_lectures.py  # TEACHER+
│           │       ├── admin_tools.py      # FACULTY_ADMIN+
│           │       ├── stats_tool.py       # thống kê → card
│           │       ├── learning_tool.py    # learning progress + recommendations
│           │       └── graph_rag.py        # GraphRAG tool (FalkorDB)
│           ├── graph_db/
│           │   ├── client.py    # FalkorDB singleton connection
│           │   ├── schema.py    # CREATE INDEX (idempotent)
│           │   ├── sync.py      # PostgreSQL → FalkorDB MERGE sync
│           │   └── queries.py   # Cypher query helpers
│           └── schemas/
├── migrations/
│   └── versions/
│       ├── 001_initial_schema.py
│       ├── 002_add_auth_rbac.py
│       └── 003_add_upload_batch.py
├── code/                        # TransNetV2 source + weights
├── docker-compose.yml
├── Makefile
└── pyproject.toml               # uv workspace root
```

---

## Database Schema (13 bảng)

```
organizations
    └── programs ──────────────→ courses ──→ chapters ──→ lecture_videos ──→ scenes
                                    │                            │                └── scene_embeddings
                                    ├── course_enrollments       └── upload_batches
                                    └── course_teachers
users
    ├── (owns) lecture_videos
    ├── course_enrollments (STUDENT)
    ├── course_teachers (TEACHER)
    └── upload_batches

chat_sessions ──→ chat_messages

student_video_progress  (student_id + lecture_id UNIQUE)
student_learning_events (append-only log)
```

---

## RBAC — Phân quyền

| Role | Phạm vi dữ liệu | Quyền hạn |
|---|---|---|
| `SUPER_ADMIN` | Toàn bộ hệ thống | Tất cả CRUD, tạo organization |
| `SCHOOL_ADMIN` | Trường của mình | CRUD programs, quản lý users |
| `FACULTY_ADMIN` | Khoa của mình | CRUD courses/chapters, xem stats |
| `TEACHER` | Courses được phân công | Upload/sửa/xóa lecture của mình |
| `STUDENT` | **Toàn bộ** (open access) | Chỉ xem, tìm kiếm, chat |

### CRUD theo role

| Resource | Tạo | Sửa | Xóa |
|---|---|---|---|
| Organization | SUPER_ADMIN | SUPER_ADMIN | SUPER_ADMIN |
| Program | SCHOOL_ADMIN+ | SCHOOL_ADMIN+ (own org) | SCHOOL_ADMIN+ |
| Course | FACULTY_ADMIN+ | FACULTY_ADMIN+ (own faculty) | FACULTY_ADMIN+ |
| Chapter | TEACHER+ | TEACHER+ | TEACHER+ |
| Lecture | TEACHER+ | TEACHER (own) / FACULTY_ADMIN+ | TEACHER (own) / FACULTY_ADMIN+ |

---

## API Endpoints

### Auth (`/api/v1/auth/`)
| Method | Path | Quyền |
|---|---|---|
| POST | `/register` | Public (STUDENT/TEACHER tự đăng ký) |
| POST | `/login` | Public |
| POST | `/refresh` | Public |
| GET | `/me` | Authenticated |
| POST | `/admin/users` | SCHOOL_ADMIN+ |
| POST | `/courses/{id}/enroll` | Authenticated |
| POST | `/courses/{id}/teachers` | SCHOOL_ADMIN+ |

### Organizations (`/api/v1/organizations/`)
| Method | Path | Quyền |
|---|---|---|
| GET | `/` | Public |
| POST | `/` | SUPER_ADMIN |

### Programs, Courses, Chapters (`/api/v1/`)
| Method | Path | Quyền |
|---|---|---|
| GET | `/programs` | Public |
| POST | `/programs` | SCHOOL_ADMIN+ |
| PATCH | `/programs/{id}` | SCHOOL_ADMIN+ |
| DELETE | `/programs/{id}` | SCHOOL_ADMIN+ |
| GET/POST/PATCH/DELETE | `/courses/{id}` | FACULTY_ADMIN+ |
| GET/POST/PATCH/DELETE | `/chapters/{id}` | TEACHER+ |

### Lectures (`/api/v1/lectures/`)
| Method | Path | Quyền |
|---|---|---|
| GET | `/` | Public |
| GET | `/{id}` | Public |
| PATCH | `/{id}` | TEACHER (own) / FACULTY_ADMIN+ |
| DELETE | `/{id}` | TEACHER (own) / FACULTY_ADMIN+ |

### Upload (`/api/v1/upload/`)
| Method | Path | Mô tả |
|---|---|---|
| POST | `/video` | Upload 1 video, trả `{lecture_id, task_id}` |
| POST | `/videos` | Bulk upload tối đa 20 video, trả `{batch_id, items[]}` |
| GET | `/batches/{batch_id}` | Poll trạng thái batch (succeeded/failed/processing) |
| GET | `/batches` | Danh sách batches của user |

### Search (`/api/v1/search/`)
- `?q=...&mode=keyword` — FTS PostgreSQL (`fts_vector @@ plainto_tsquery`)
- `?q=...&mode=semantic` — Vector cosine (`multilingual-e5-large` → pgvector)
- `?course_id=...` — filter theo course

### Jobs (`/api/v1/jobs/{task_id}`)
- Poll Celery task status: `PENDING | STARTED | SUCCESS | FAILURE | RETRY`

---

## Chatbot Agent — Tools theo role

| Tool | Level tối thiểu | Chức năng |
|---|---|---|
| `search_lectures` | STUDENT (1) | Tìm kiếm scene theo nội dung — scoped by role |
| `query_database` | STUDENT (1) | SELECT SQL read-only |
| `get_statistics` | STUDENT (1) | Thống kê → trả về card JSON (`__card_type`) |
| `learning_progress` | STUDENT (1) | Tiến độ học, lịch sử, đề xuất bài tiếp theo |
| `graph_knowledge` | STUDENT (1) | GraphRAG: đề xuất có giải thích, bản đồ khái niệm |
| `manage_lectures` | TEACHER (2) | list/pending/stats/status bài giảng của mình |
| `admin_stats` | FACULTY_ADMIN (3) | overview/courses/users/enrollments/lectures_by_course |

**Card response**: các tool trả về `{"__card_type": "stats"|"table", ...}` — frontend render thành visual card.

**GraphRAG actions** (`graph_knowledge`):
- `recommend` — đề xuất có lý do cụ thể (tiếp tục / bài tiếp / cùng concept / mới)
- `explain <id_a> <id_b>` — giải thích tại sao 2 bài giảng liên quan
- `knowledge_map` — bản đồ concept đã học / chưa học
- `concept <tên>` — tìm bài giảng theo chủ đề
- `teacher_coverage <id>` — giảng viên dạy những topic gì

**WebSocket**: `?token=<jwt>` query param
**REST**: `Authorization: Bearer <jwt>` header

---

## Worker Pipeline

```
run_pipeline(lecture_id)
    ├── Download từ MinIO → /tmp/
    ├── detect_scenes (TransNetV2) → Scene rows + keyframes → MinIO
    └── chord(
        asr_transcribe,    # faster-whisper → transcript per scene
        ocr_extract,       # EasyOCR → slide text per scene
        clip_embed         # open_clip → Vector(768) per scene
    ) → run_indexing
            ├── SceneEmbedding (text 1024-dim + image 768-dim)
            ├── fts_vector update (transcript + OCR → tsvector)
            └── LectureVideo.status = COMPLETED
```

**GPU Models** (singleton, thread-safe double-check locking):

| Model | VRAM | Queue |
|---|---|---|
| TransNetV2 | ~2 GB | `gpu.high` |
| faster-whisper large-v3 | ~3 GB | `gpu.medium` |
| EasyOCR | ~1 GB | `gpu.medium` |
| open_clip ViT-L/14 | ~2 GB | `gpu.medium` |
| multilingual-e5-large | ~2 GB | `db` |

---

## Bulk Upload Flow

```
Frontend chọn N file (max 20)
    │
    ▼
POST /api/v1/upload/videos
    ├── Tạo UploadBatch record (JSONB items[])
    ├── Upload từng file → MinIO
    ├── Tạo LectureVideo rows
    └── Dispatch N Celery pipeline tasks
    │
    ▼  trả về {batch_id, items[]}

Frontend poll GET /upload/batches/{batch_id} mỗi 5s
    ├── Check AsyncResult cho từng task_id
    ├── Cập nhật succeeded/failed counts
    └── Khi is_done=true → Toast notification
```

---

## Hướng dẫn Deploy & Chạy Local

### Yêu cầu

| Tool | Phiên bản | Ghi chú |
|---|---|---|
| Docker + Docker Compose | ≥ 24 | Bắt buộc |
| Python | ≥ 3.11 | Chỉ cần cho local dev |
| [uv](https://docs.astral.sh/uv/) | latest | Package manager |
| NVIDIA GPU + CUDA | ≥ 11.8 | Chỉ cần cho Worker (có thể bỏ qua khi test) |
| OpenAI API key | — | Hoặc vLLM local |

---

### Cách 1 — Full Docker (khuyên dùng cho production / demo)

Chạy toàn bộ stack trong Docker: PostgreSQL, RabbitMQ, MinIO, FalkorDB, API, Worker, Chatbot, Nginx.

```bash
# 1. Clone và vào thư mục
git clone <repo>
cd gds_ai_api

# 2. Tạo file env
cp .env.example .env
# Sửa .env: điền OPENAI_API_KEY, đổi password nếu cần

# 3. Build và khởi động toàn bộ
docker compose up -d --build

# 4. Chạy migrations (chỉ lần đầu hoặc khi có migration mới)
docker compose run --rm db-migrate

# 5. Kiểm tra trạng thái
docker compose ps
```

**Các service sau khi up:**

| Service | URL | Ghi chú |
|---|---|---|
| API | http://localhost:8000 | REST API |
| Chatbot | http://localhost:8001 | WebSocket + REST |
| Nginx | http://localhost:80 | Reverse proxy |
| MinIO Console | http://localhost:9001 | user: minioadmin / minioadmin |
| RabbitMQ Console | http://localhost:15672 | user: gds_user / changeme |
| FalkorDB Browser | http://localhost:3000 | Visualize knowledge graph |
| PostgreSQL | localhost:5432 | gds_ai / gds_user / changeme |

```bash
# Xem logs theo service
docker compose logs -f api
docker compose logs -f chatbot
docker compose logs -f worker

# Dừng
docker compose down

# Dừng và xóa volumes (reset hoàn toàn)
docker compose down -v
```

---

### Cách 2 — Infra bằng Docker, code chạy local (cho development)

Chỉ chạy database và các service phụ trong Docker, code API và chatbot chạy trực tiếp trên máy để hot-reload.

#### Bước 1: Khởi động infra

```bash
# Chỉ chạy các service infrastructure
docker compose up -d postgres rabbitmq minio minio-init falkordb

# Chờ healthy
docker compose ps
```

#### Bước 2: Cài dependencies

```bash
# Cài uv nếu chưa có
curl -LsSf https://astral.sh/uv/install.sh | sh

# Cài tất cả packages trong workspace
uv sync --all-packages
```

#### Bước 3: Tạo và cấu hình .env

```bash
cp .env.example .env
```

Sửa `.env` cho local dev (thay host từ tên service Docker thành `localhost`):

```env
# PostgreSQL — kết nối local
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=gds_ai
POSTGRES_USER=gds_user
POSTGRES_PASSWORD=changeme

# RabbitMQ — kết nối local
RABBITMQ_HOST=localhost
CELERY_BROKER_URL=amqp://gds_user:changeme@localhost:5672/gds_vhost

# MinIO — kết nối local
MINIO_ENDPOINT=localhost:9000
MINIO_PUBLIC_URL=http://localhost:9000

# FalkorDB — kết nối local
FALKORDB_HOST=localhost
FALKORDB_PORT=6379

# LLM
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# API
API_SECRET_KEY=dev-secret-key-change-in-prod
```

#### Bước 4: Chạy database migrations

```bash
cd migrations
alembic upgrade head
cd ..
```

Hoặc qua make:

```bash
make migrate
```

#### Bước 5: Chạy API (terminal 1)

```bash
uv run --package api uvicorn api.main:app \
  --host 0.0.0.0 --port 8000 --reload
```

Kiểm tra: http://localhost:8000/docs

#### Bước 6: Chạy Chatbot (terminal 2)

```bash
uv run --package chatbot uvicorn chatbot.main:app \
  --host 0.0.0.0 --port 8001 --reload
```

Kiểm tra: http://localhost:8001/docs

#### Bước 7: Chạy Worker (terminal 3 — cần GPU)

```bash
uv run --package worker celery -A worker.app worker \
  --loglevel=info \
  -Q gpu.high,gpu.medium,db \
  -c 4
```

> **Không có GPU?** Worker có thể bỏ qua khi test API/Chatbot. Upload video vẫn hoạt động nhưng sẽ không xử lý (status mãi là PENDING).

---

### Cài đặt Database từ đầu (local không dùng Docker)

Nếu muốn PostgreSQL và FalkorDB chạy native trên máy:

#### PostgreSQL + pgvector

```bash
# macOS
brew install postgresql@16
brew services start postgresql@16

# Cài pgvector
git clone --branch v0.7.0 https://github.com/pgvector/pgvector.git
cd pgvector && make && make install

# Tạo DB
psql -U postgres -c "CREATE USER gds_user WITH PASSWORD 'changeme';"
psql -U postgres -c "CREATE DATABASE gds_ai OWNER gds_user;"
psql -U postgres -d gds_ai -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -U postgres -d gds_ai -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;"
psql -U postgres -d gds_ai -c "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
```

#### FalkorDB (via Docker — cách đơn giản nhất)

```bash
docker run -d --name falkordb \
  -p 6379:6379 -p 3000:3000 \
  -v falkordb_data:/data \
  falkordb/falkordb:latest
```

Mở FalkorDB Browser: http://localhost:3000

#### MinIO (via Docker)

```bash
docker run -d --name minio \
  -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  -v minio_data:/data \
  minio/minio:latest server /data --console-address ":9001"

# Tạo buckets
pip install minio
python -c "
from minio import Minio
c = Minio('localhost:9000', 'minioadmin', 'minioadmin', secure=False)
c.make_bucket('videos')
c.make_bucket('frames')
print('Buckets created')
"
```

#### RabbitMQ (via Docker)

```bash
docker run -d --name rabbitmq \
  -p 5672:5672 -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=gds_user \
  -e RABBITMQ_DEFAULT_PASS=changeme \
  -e RABBITMQ_DEFAULT_VHOST=gds_vhost \
  rabbitmq:3.13-management
```

---

### FalkorDB Graph Sync

Sau khi API và chatbot đang chạy, sync dữ liệu từ PostgreSQL vào FalkorDB:

```bash
# Lấy access token SUPER_ADMIN
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"yourpassword"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Trigger full sync
curl -X POST http://localhost:8001/graph/sync \
  -H "Authorization: Bearer $TOKEN"
```

Sau khi sync, mở FalkorDB Browser (http://localhost:3000) và chạy:

```cypher
// Xem tất cả node types
MATCH (n) RETURN labels(n), count(n) ORDER BY count(n) DESC

// Xem bài giảng và khái niệm
MATCH (l:Lecture)-[:HAS_CONCEPT]->(c:Concept)
RETURN l.title, collect(c.name) LIMIT 10

// Xem graph đề xuất cho 1 student
MATCH (s:Student)-[:WATCHED]->(l:Lecture)-[:HAS_CONCEPT]->(c:Concept)
RETURN s.name, l.title, collect(c.name) LIMIT 5
```

---

### Tạo tài khoản đầu tiên

```bash
# Tạo SUPER_ADMIN (chỉ khi DB mới)
curl -X POST http://localhost:8000/api/v1/auth/admin/users \
  -H "Content-Type: application/json" \
  -d '{
    "email": "admin@gds.edu.vn",
    "password": "Admin@123456",
    "full_name": "System Admin",
    "role": "SUPER_ADMIN"
  }'

# Tự đăng ký STUDENT
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "student@gds.edu.vn",
    "password": "Student@123",
    "full_name": "Nguyễn Văn A",
    "role": "STUDENT",
    "student_code": "SV001",
    "major": "Công nghệ thông tin"
  }'

# Đăng nhập
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@gds.edu.vn","password":"Admin@123456"}'
```

---

### Makefile shortcuts

```bash
make setup     # uv sync --all-packages
make up        # docker compose up -d
make down      # docker compose down
make migrate   # alembic upgrade head
make api       # chạy API local với --reload
make worker    # chạy Celery worker local
make test      # pytest tests/
make lint      # ruff check src/
make format    # ruff format src/
```

---

### Troubleshooting

**FalkorDB không kết nối được:**
```bash
# Kiểm tra FalkorDB đang chạy
docker compose ps falkordb
redis-cli -p 6379 ping  # phải trả về PONG

# Chatbot vẫn hoạt động khi FalkorDB down
# GraphRAG tool sẽ trả về thông báo "graph not available" thay vì crash
```

**Migrations fail:**
```bash
# Xem lịch sử migration
cd migrations && alembic history

# Rollback 1 bước
alembic downgrade -1

# Reset hoàn toàn (xóa và tạo lại DB)
docker compose down -v postgres
docker compose up -d postgres
docker compose run --rm db-migrate
```

**Worker không nhận task:**
```bash
# Kiểm tra RabbitMQ queues
docker compose exec rabbitmq rabbitmqctl list_queues

# Kiểm tra Celery workers đang active
docker compose exec worker celery -A worker.app inspect active
```

**MinIO presigned URL không hoạt động:**
```bash
# Đảm bảo MINIO_PUBLIC_URL trỏ đến địa chỉ có thể truy cập từ browser
# Local dev: MINIO_PUBLIC_URL=http://localhost:9000
# Production: MINIO_PUBLIC_URL=http://<server-ip>:9000
```
