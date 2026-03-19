# Giảng Đường Số — Backend Implementation Plan

> Cập nhật: 2026-03-17

---

## Kiến trúc tổng quan

```
Upload Video (TEACHER+)
       │
[Nginx :80] → [FastAPI API :8000]
                    ├── MinIO (video + keyframes)
                    ├── PostgreSQL 16 + pgvector
                    └── RabbitMQ → Celery Worker (A100 GPU)
                                       │
                               TransNetV2 → ASR → OCR → CLIP
                                       │
                               [PostgreSQL + pgvector HNSW]
                                       │
                          ┌────────────┴────────────┐
                    [Search API]            [Chatbot :8001]
                 keyword + semantic      ReAct Agent (role-aware)
```

---

## Phase 0 — Foundation ✅ DONE

- `docker-compose.yml` — PostgreSQL (pgvector), RabbitMQ, MinIO, Nginx
- `src/shared/shared/database/models.py` — 11 ORM models
- `migrations/versions/001_initial_schema.py` — extensions, tables, HNSW indexes, FTS indexes, triggers
- uv workspace monorepo (`src/shared`, `src/api`, `src/worker`, `src/chatbot`)
- Pydantic BaseSettings với `@lru_cache`, structlog

---

## Phase 1 — Worker Pipeline ✅ DONE

- `src/worker/worker/tasks/pipeline.py` — `detect_scenes | chord(asr, ocr, clip_embed) | indexing`
- `src/worker/worker/tasks/scene_detection.py` — TransNetV2 → keyframe → MinIO
- `src/worker/worker/tasks/asr.py` — faster-whisper large-v3 (float16)
- `src/worker/worker/tasks/ocr.py` — EasyOCR (vi + en)
- `src/worker/worker/tasks/clip_embed.py` — open_clip ViT-L/14 → Vector(768)
- `src/worker/worker/tasks/indexing.py` — SceneEmbedding, fts_vector, status COMPLETED
- `src/worker/worker/models/loader.py` — thread-safe singleton GPU model loader
- `PYTHONPATH=/app/code` cho TransNetV2 (không dùng sys.path)

---

## Phase 2 — Upload & REST API ✅ DONE

- `src/api/api/routers/upload.py` — single `POST /upload/video` + bulk `POST /upload/videos`
- `src/api/api/routers/programs.py` — CRUD programs/courses/chapters với role guards
- `src/api/api/routers/lectures.py` — GET (presigned URL) + PATCH/DELETE với owner check
- `src/api/api/routers/jobs.py` — poll Celery task status
- `src/api/api/routers/search.py` — keyword FTS + semantic vector (singleton embedder)
- Bulk upload: `UploadBatch` model, JSONB items, batch polling endpoint
- `migrations/versions/003_add_upload_batch.py`

---

## Phase 3 — Search Engine ✅ DONE

- Keyword: `fts_vector @@ plainto_tsquery` + trigram fallback
- Semantic: `multilingual-e5-large` encode → pgvector cosine `<=>`
- HNSW index: `m=16, ef_construction=64`
- Filter by `course_id`
- Response: scene + timestamps + presigned keyframe URL + score
- Singleton embedder với threading.Lock (không tạo mới mỗi request)

---

## Phase 4 — RBAC + Auth ✅ DONE

### Role Hierarchy
```
SUPER_ADMIN (5) → toàn hệ thống
SCHOOL_ADMIN (4) → trường của mình
FACULTY_ADMIN (3) → khoa của mình
TEACHER (2) → courses được phân công + lecture của mình
STUDENT (1) → xem/tìm kiếm toàn bộ (open access)
```

### Models mới
- `Organization` — trường/đại học
- `User` — role enum, organization_id, faculty, hashed_password
- `CourseEnrollment` — student ↔ course
- `CourseTeacher` — teacher ↔ course
- `UploadBatch` — batch upload tracking (JSONB items)

### Auth endpoints
- `POST /auth/register` — STUDENT/TEACHER tự đăng ký
- `POST /auth/login` — trả access_token + refresh_token (JWT HS256)
- `POST /auth/refresh`
- `GET /auth/me`
- `POST /auth/admin/users` — SCHOOL_ADMIN+ tạo mọi role
- `POST /auth/courses/{id}/enroll` — ghi danh
- `POST /auth/courses/{id}/teachers` — phân công giảng viên

### CRUD guards
| Resource | Tạo/Sửa/Xóa |
|---|---|
| Organization | SUPER_ADMIN |
| Program | SCHOOL_ADMIN+ (own org) |
| Course | FACULTY_ADMIN+ (own faculty) |
| Chapter | TEACHER+ |
| Lecture | TEACHER (own) / FACULTY_ADMIN+ |
| Upload | TEACHER+ |

---

## Phase 5 — Agentic Chatbot ✅ DONE

### LectureAgent — Role-aware ReAct

**Tools theo level:**
| Tool | Min Level | Scope |
|---|---|---|
| `search_lectures` | 1 (STUDENT) | STUDENT=all, TEACHER=assigned, FACULTY=faculty, SCHOOL=org |
| `query_database` | 1 (STUDENT) | SELECT only, forbidden keywords blocked |
| `get_statistics` | 1 (STUDENT) | Returns `__card_type` JSON → frontend card |
| `manage_lectures` | 2 (TEACHER) | list/pending/stats/status — own lectures only |
| `admin_stats` | 3 (FACULTY_ADMIN) | overview/courses/users/enrollments/lectures_by_course |

**System prompts:** khác nhau cho mỗi role (vi/en bilingual)

**Token flow:**
- REST: `Authorization: Bearer <jwt>`
- WebSocket: `?token=<jwt>` query param

**Card events:** agent emit `{"type": "card", "data": {"__card_type": "stats"|"table", ...}}`

---

## Phase 6 — Hardening ⏳ TODO

- [ ] Prometheus metrics: GPU util, task duration, queue depth
- [ ] Grafana dashboard
- [ ] Nginx rate limiting cho upload
- [ ] Celery retry: `max_retries=3`, exponential backoff
- [ ] RabbitMQ dead letter queue cho failed tasks
- [ ] Integration tests: upload → pipeline → search → chat
- [ ] `scripts/benchmark_gpu.py` — đo throughput A100
- [ ] SSL/TLS termination tại Nginx

---

## Architectural Decisions

| Decision | Choice | Lý do |
|---|---|---|
| Vector DB | pgvector trong PostgreSQL | Một DB thay vì 2, HNSW đủ mạnh |
| Broker | RabbitMQ | Durable queues, không mất GPU job khi crash |
| ASR | faster-whisper large-v3 | 4x nhanh hơn, float16 trên A100 |
| Schema | Tách `scene_embeddings` khỏi `scenes` | Tránh page bloat khi query metadata |
| TransNetV2 import | `PYTHONPATH=/app/code` | Không dùng sys.path fragile |
| Auth | JWT HS256, bcrypt | Stateless, đơn giản, production-grade |
| Bulk upload | UploadBatch + JSONB | Track từng file, poll status dễ dàng |
| Stats response | `__card_type` JSON field | Frontend detect và render card tự động |

---

## Progress

| Phase | Status | Ghi chú |
|---|---|---|
| 0 — Foundation | ✅ DONE | docker-compose, schema, migrations, MinIO, RabbitMQ |
| 1 — Worker Pipeline | ✅ DONE | TransNetV2 → ASR → OCR → CLIP → indexing |
| 2 — Upload & REST API | ✅ DONE | Single + bulk upload, CRUD, job polling, presigned URLs |
| 3 — Search Engine | ✅ DONE | FTS + pgvector semantic, singleton embedder |
| 4 — RBAC + Auth | ✅ DONE | JWT, 5 roles, CRUD guards, enrollment, assignment |
| 5 — Agentic Chatbot | ✅ DONE | Role-aware tools, stats card, WS streaming |
| 6 — Hardening | ⏳ TODO | Monitoring, retry, integration tests |
