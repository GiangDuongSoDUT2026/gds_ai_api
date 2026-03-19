# Tổng kết Hệ thống Giảng Đường Số — Backend

> Cập nhật: 2026-03-17

---

## Tổng quan

Backend xử lý video bài giảng tự động (scene detection, ASR, OCR, embedding), tìm kiếm ngữ nghĩa, chatbot AI phân quyền. Được xây dựng theo kiến trúc microservice: API, Worker GPU, Chatbot chạy độc lập trong Docker.

---

## Services

| Service | Port | Công nghệ | Vai trò |
|---|---|---|---|
| `api` | 8000 | FastAPI + uvicorn | REST API, auth, upload, search |
| `worker` | — | Celery + CUDA | GPU pipeline xử lý video |
| `chatbot` | 8001 | FastAPI + LangChain | ReAct agent + WebSocket |
| `postgres` | 5432 | PostgreSQL 16 + pgvector | DB chính + vector store |
| `rabbitmq` | 5672 | RabbitMQ 3.13 | Celery broker |
| `minio` | 9000 | MinIO | Object storage video + frames |
| `nginx` | 80 | Nginx | Reverse proxy |

---

## Database — 13 bảng

```
organizations
    └── programs (organization_id)
            └── courses (faculty)
                    ├── chapters
                    │       └── lecture_videos (owner_id, video_hash)
                    │               ├── scenes
                    │               │     └── scene_embeddings (Vector 768 + 1024)
                    │               └── upload_batches (owner_id)
                    ├── course_enrollments (student_id)
                    └── course_teachers (teacher_id)

users (role, organization_id, faculty, student_code, major, teacher_code, department)

chat_sessions → chat_messages (JSONB citations, tool_calls)

student_video_progress (student_id + lecture_id UNIQUE, position_sec, watched_seconds, completed, scenes_viewed JSONB)
student_learning_events (student_id, lecture_id, event_type, payload JSONB) — append-only log
```

### Indexes
- HNSW `m=16, ef_construction=64` trên `text_embedding` và `image_embedding`
- GIN index trên `fts_vector` (tsvector)
- Trigram index trên `transcript`, `ocr_text`
- B-tree trên `users.email`

---

## RBAC — 5 cấp bậc

| Role | Level | Phạm vi | Quyền |
|---|---|---|---|
| `SUPER_ADMIN` | 5 | Toàn hệ thống | Tất cả |
| `SCHOOL_ADMIN` | 4 | Trường của mình | CRUD programs, quản lý users |
| `FACULTY_ADMIN` | 3 | Khoa của mình | CRUD courses/chapters, xem stats |
| `TEACHER` | 2 | Courses được phân công | Upload, sửa/xóa lecture của mình |
| `STUDENT` | 1 | **Toàn bộ** (open access) | Chỉ xem, tìm kiếm, chat |

**JWT payload:** `{sub, role, org, faculty, exp, type}`

---

## Worker Pipeline

```
run_pipeline(lecture_id)
    ├── Download video từ MinIO
    ├── detect_scenes → TransNetV2 → Scenes + keyframes → MinIO
    └── chord(
        asr_transcribe    → faster-whisper large-v3 (float16)
        ocr_extract       → EasyOCR (vi + en)
        clip_embed        → open_clip ViT-L/14 → Vector(768)
    ) → run_indexing
            ├── SceneEmbedding: text Vector(1024) + image Vector(768)
            ├── fts_vector = to_tsvector(transcript || ocr_text)
            └── LectureVideo.status = COMPLETED
```

**GPU models** (singleton, thread-safe):

| Model | VRAM | Queue |
|---|---|---|
| TransNetV2 | ~2 GB | `gpu.high` |
| faster-whisper large-v3 | ~3 GB | `gpu.medium` |
| EasyOCR | ~1 GB | `gpu.medium` |
| open_clip ViT-L/14 | ~2 GB | `gpu.medium` |
| multilingual-e5-large | ~2 GB | `db` |

---

## Bulk Upload

```
POST /api/v1/upload/videos (files[], chapter_id)
    ├── Tạo UploadBatch {batch_id, total, items[]}
    ├── Mỗi file: upload MinIO → LectureVideo → Celery task
    └── Trả {batch_id, items[{lecture_id, task_id, filename}]}

GET /api/v1/upload/batches/{batch_id}
    ├── Check AsyncResult cho từng task_id
    └── Trả {succeeded, failed, processing, is_done}
```

Frontend poll mỗi 5 giây → toast khi `is_done=true`.

---

## Chatbot Agent

### Tools theo role level

| Tool | Level | Scoping |
|---|---|---|
| `search_lectures` | ≥1 | STUDENT=all, TEACHER=assigned courses, FACULTY=faculty, SCHOOL=org |
| `query_database` | ≥1 | SELECT only, FORBIDDEN keywords blocked |
| `get_statistics` | ≥1 | Returns `__card_type` JSON → frontend card |
| `learning_progress` | ≥1 | Student learning stats, recommendations, history |
| `manage_lectures` | ≥2 | Lecture của chính TEACHER đó |
| `admin_stats` | ≥3 | Scoped by faculty/org/all |

### Learning Progress Tool (actions)

| Action | Mô tả |
|---|---|
| `continue` | Danh sách video đang xem, sắp xếp theo lần xem gần nhất |
| `stats` | Tổng giờ xem, số video hoàn thành, streak học tập |
| `completed` | Danh sách video đã xem xong |
| `recommendations` | 4-tier: in-progress → next-in-chapter → vector similarity → newest |
| `history <N>` | N sự kiện gần nhất (mặc định 10) |

### Card response format
```json
{
  "__card_type": "stats",
  "title": "Thống kê hệ thống",
  "metrics": [
    {"label": "Tổng bài giảng", "value": 150, "icon": "video"},
    {"label": "Hoàn thành", "value": 120, "icon": "check", "color": "green"}
  ]
}
```
hoặc `"__card_type": "table"` với `columns[]` và `rows[][]`.

### WebSocket flow
```
Client → {content, history[]}
Agent  → {type: "tool_call", tool: "search_lectures"}
       → {type: "token", content: "..."}  (streaming)
       → {type: "card", data: {...}}      (if stats tool)
       → {type: "citations", citations: [...]}
       → {type: "done", tool_calls_used: [...]}
```

---

## Learning Analytics

### API Endpoints (`/api/v1/progress/`)

```
POST   /{lecture_id}    — Upsert watch position (30s interval từ player)
POST   /events          — Log learning event (watch, complete, seek, pause)
GET    /               — Tất cả progress records của student hiện tại
GET    /stats           — Aggregate: giờ học, streak, video hoàn thành
GET    /recommendations — 4-tier recommendation engine
```

### 4-tier Recommendation Engine

```
Tier 1 — In-progress: videos đang xem, sorted by last_watched desc
Tier 2 — Next-in-chapter: video kế tiếp sau video đã hoàn thành trong cùng chapter
Tier 3 — Vector similarity: avg embedding của watched content → cosine search pgvector
Tier 4 — Fallback: newest completed lectures (cho user mới, không có lịch sử)
```

---

## Bugs đã fix

| # | File | Lỗi | Fix |
|---|---|---|---|
| 1 | api/chatbot Dockerfile | `pip install -e .` trước COPY source | COPY source trước |
| 2 | `utils/db.py` | `status.value == "COMPLETED"` luôn False | `status == VideoStatus.COMPLETED` |
| 3 | `search.py` | Tạo SentenceTransformer mỗi request | Module-level singleton + Lock |
| 4 | `api/pyproject.toml` | Thiếu `sentence-transformers` | Thêm dependency |
| 5 | `indexing.py` | `sa_text("NOW()")` trong `.values()` | `func.now()` |
| 6 | `vector_search.py` | SentenceTransformer mỗi `_run()` | Singleton + Lock |
| 7 | `transnetv2_model.py` | sys.path hardcode 4 cấp | `PYTHONPATH=/app/code` |
| 8 | `LectureResponse` | Thiếu `video_url` field | Thêm + `_build_presigned_url()` |
