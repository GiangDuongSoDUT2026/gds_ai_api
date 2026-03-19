import hashlib
import uuid
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy import select, desc
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_minio, get_celery
from api.dependencies.auth import require_teacher
from shared.config import get_settings
from shared.database.models import LectureVideo, VideoStatus, UploadBatch, BatchStatus, User

router = APIRouter(prefix="/upload", tags=["upload"])
logger = structlog.get_logger(__name__)

ALLOWED_EXTENSIONS = {".mp4", ".mpeg", ".mpg", ".mov", ".avi", ".webm", ".mkv"}
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB
MIN_FILE_SIZE = 10 * 1024  # 10 KB minimum


def _estimate_eta_minutes(file_size_bytes: int) -> int:
    """Estimate processing time based on file size. Roughly 1 min per 100MB on A100."""
    size_mb = file_size_bytes / (1024 * 1024)
    # scene detection + ASR + OCR + embedding: ~1 min/80MB, min 2 min
    return max(2, int(size_mb / 80) + 2)


async def _compute_hash(file: UploadFile) -> tuple[str, int]:
    """Compute SHA256 hash and file size. Resets file position after."""
    sha256 = hashlib.sha256()
    total_bytes = 0
    chunk_size = 1024 * 1024  # 1MB chunks
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        sha256.update(chunk)
        total_bytes += len(chunk)
    await file.seek(0)
    return sha256.hexdigest(), total_bytes


async def _validate_file(file: UploadFile, db: AsyncSession):
    """
    Validate file. Returns (error_dict, video_hash, file_size).
    error_dict is None if valid.
    """
    # Check filename and extension
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return (
            {"error": f"Định dạng không hỗ trợ: {ext}. Chấp nhận: {', '.join(ALLOWED_EXTENSIONS)}", "code": "INVALID_FORMAT"},
            None,
            0,
        )

    # Check MIME type
    if file.content_type and not file.content_type.startswith("video/"):
        return (
            {"error": f"File không phải video (content-type: {file.content_type})", "code": "INVALID_MIME"},
            None,
            0,
        )

    # Compute hash + size
    video_hash, file_size = await _compute_hash(file)

    # Check size
    if file_size < MIN_FILE_SIZE:
        return {"error": "File quá nhỏ (tối thiểu 10KB)", "code": "FILE_TOO_SMALL"}, video_hash, file_size

    if file_size > MAX_FILE_SIZE:
        return {"error": "File quá lớn (tối đa 10GB)", "code": "FILE_TOO_LARGE"}, video_hash, file_size

    # Duplicate check
    dup = await db.execute(sa_select(LectureVideo).where(LectureVideo.video_hash == video_hash))
    existing = dup.scalar_one_or_none()
    if existing:
        return (
            {
                "error": f"Video đã tồn tại trong hệ thống: '{existing.title}'",
                "code": "DUPLICATE",
                "existing_lecture_id": str(existing.id),
                "existing_title": existing.title,
            },
            video_hash,
            file_size,
        )

    return None, video_hash, file_size


async def _upload_one(
    file: UploadFile,
    chapter_id: uuid.UUID,
    title: str,
    current_user: User,
    db: AsyncSession,
    minio,
    celery_app,
) -> dict:
    settings = get_settings()
    filename = file.filename or "video.mp4"

    # Validate + hash + duplicate check
    err, video_hash, file_size = await _validate_file(file, db)
    if err:
        return {"filename": filename, "status": "REJECTED", **err}

    lecture_id = uuid.uuid4()
    ext = Path(filename).suffix.lower() or ".mp4"
    minio_key = f"{lecture_id}/{filename}"

    try:
        minio.upload_fileobj(
            file.file,
            settings.minio_bucket_videos,
            minio_key,
            ExtraArgs={"ContentType": file.content_type or "video/mp4"},
        )
    except Exception as e:
        logger.error("minio_upload_failed", filename=filename, error=str(e))
        return {"filename": filename, "status": "FAILED", "error": f"Lỗi lưu trữ: {str(e)}", "code": "STORAGE_ERROR"}

    lecture = LectureVideo(
        id=lecture_id,
        title=title or Path(filename).stem,
        chapter_id=chapter_id,
        status=VideoStatus.PENDING,
        minio_key=minio_key,
        owner_id=current_user.id,
        video_hash=video_hash,
        file_size_bytes=file_size,
    )
    db.add(lecture)
    await db.flush()

    task = celery_app.send_task(
        "worker.tasks.pipeline.run_pipeline",
        args=[str(lecture_id)],
        queue="gpu.high",
    )

    eta_minutes = _estimate_eta_minutes(file_size)

    return {
        "lecture_id": str(lecture_id),
        "task_id": task.id,
        "filename": filename,
        "file_size_bytes": file_size,
        "status": "PENDING",
        "eta_minutes": eta_minutes,
    }


@router.post("/video")
async def upload_video(
    file: UploadFile,
    chapter_id: uuid.UUID = Form(...),
    title: str = Form(""),
    uploaded_by: str = Form(None),
    current_user: Annotated[User, Depends(require_teacher)] = None,
    db: AsyncSession = Depends(get_db),
    minio=Depends(get_minio),
    celery_app=Depends(get_celery),
):
    """Single video upload — returns {lecture_id, task_id, status, message, eta_minutes}."""
    result = await _upload_one(file, chapter_id, title, current_user, db, minio, celery_app)
    if result.get("status") in ("FAILED", "REJECTED"):
        raise HTTPException(400, result.get("error"))
    await db.commit()
    return {
        "lecture_id": result["lecture_id"],
        "task_id": result["task_id"],
        "status": "PENDING",
        "eta_minutes": result.get("eta_minutes", 5),
        "message": f"Upload thành công. Dự kiến xử lý xong trong ~{result.get('eta_minutes', 5)} phút.",
    }


@router.post("/videos")
async def upload_videos_bulk(
    files: list[UploadFile],
    chapter_id: uuid.UUID = Form(...),
    current_user: Annotated[User, Depends(require_teacher)] = None,
    db: AsyncSession = Depends(get_db),
    minio=Depends(get_minio),
    celery_app=Depends(get_celery),
):
    """Bulk video upload — returns {batch_id, total, accepted, rejected, items[], eta_minutes}."""
    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > 20:
        raise HTTPException(400, "Maximum 20 files per batch")

    batch = UploadBatch(
        id=uuid.uuid4(),
        owner_id=current_user.id,
        status=BatchStatus.PROCESSING,
        total=len(files),
        items=[],
    )
    db.add(batch)
    await db.flush()

    items = []
    for file in files:
        title = file.filename or "Untitled"
        item = await _upload_one(file, chapter_id, title, current_user, db, minio, celery_app)
        item["batch_id"] = str(batch.id)
        items.append(item)

    batch.items = items
    await db.commit()

    # Calculate summary
    total_eta = max((i.get("eta_minutes", 0) for i in items if i.get("status") == "PENDING"), default=5)
    accepted = sum(1 for i in items if i.get("status") == "PENDING")
    rejected = sum(1 for i in items if i.get("status") in ("REJECTED", "FAILED"))

    return {
        "batch_id": str(batch.id),
        "total": len(files),
        "accepted": accepted,
        "rejected": rejected,
        "items": items,
        "eta_minutes": total_eta,
        "message": f"Đã nhận {accepted}/{len(files)} video hợp lệ. Dự kiến xử lý xong trong ~{total_eta} phút. Bạn sẽ nhận thông báo khi hoàn tất.",
    }


@router.post("/chat-upload")
async def chat_upload(
    files: list[UploadFile],
    chapter_id: uuid.UUID = Form(...),
    current_user: Annotated[User, Depends(require_teacher)] = None,
    db: AsyncSession = Depends(get_db),
    minio=Depends(get_minio),
    celery_app=Depends(get_celery),
):
    """
    Upload endpoint optimized for chat interface.
    Max 10 files. Returns a human-readable message + structured data.
    """
    if not files:
        raise HTTPException(400, "Không có file nào được chọn")
    if len(files) > 10:
        raise HTTPException(400, "Tối đa 10 video mỗi lần upload qua chat")

    batch = UploadBatch(
        id=uuid.uuid4(),
        owner_id=current_user.id,
        status=BatchStatus.PROCESSING,
        total=len(files),
        items=[],
    )
    db.add(batch)
    await db.flush()

    items = []
    for file in files:
        item = await _upload_one(file, chapter_id, file.filename or "", current_user, db, minio, celery_app)
        item["batch_id"] = str(batch.id)
        items.append(item)

    batch.items = items
    await db.commit()

    accepted = [i for i in items if i.get("status") == "PENDING"]
    rejected = [i for i in items if i.get("status") in ("REJECTED", "FAILED")]

    total_eta = max((i.get("eta_minutes", 0) for i in accepted), default=5) if accepted else 0

    # Build human-readable chat response
    lines = []
    if accepted:
        lines.append(f"✅ Đã nhận {len(accepted)} video để xử lý:")
        for item in accepted:
            size_mb = round(item.get("file_size_bytes", 0) / 1024 / 1024, 1)
            lines.append(f"  • {item['filename']} ({size_mb} MB) — dự kiến ~{item.get('eta_minutes', 5)} phút")
        lines.append(f"\n⏱ Thời gian xử lý ước tính: ~{total_eta} phút")
        lines.append("📩 Bạn sẽ nhận thông báo khi từng video hoàn tất.")

    if rejected:
        lines.append(f"\n❌ {len(rejected)} file bị từ chối:")
        for item in rejected:
            lines.append(f"  • {item['filename']}: {item.get('error', 'Lỗi không xác định')}")

    return {
        "batch_id": str(batch.id),
        "message": "\n".join(lines),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "total": len(files),
        "eta_minutes": total_eta,
        "items": items,
    }


@router.get("/batches/{batch_id}")
async def get_batch_status(
    batch_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_teacher)],
    db: AsyncSession = Depends(get_db),
    celery_app=Depends(get_celery),
):
    """Poll batch processing status. Frontend calls this to get success/fail counts."""
    result = await db.execute(select(UploadBatch).where(UploadBatch.id == batch_id))
    batch = result.scalar_one_or_none()
    if not batch:
        raise HTTPException(404)
    if batch.owner_id != current_user.id and current_user.role.value not in ("FACULTY_ADMIN", "SCHOOL_ADMIN", "SUPER_ADMIN"):
        raise HTTPException(403)

    # Check actual Celery task statuses
    succeeded = 0
    failed = 0
    updated_items = []
    all_done = True

    for item in (batch.items or []):
        task_id = item.get("task_id")
        if not task_id:
            item["status"] = "FAILED"
            failed += 1
            updated_items.append(item)
            continue

        from celery.result import AsyncResult
        ar = AsyncResult(task_id, app=celery_app)

        if ar.state == "SUCCESS":
            item["status"] = "COMPLETED"
            succeeded += 1
        elif ar.state in ("FAILURE", "REVOKED"):
            item["status"] = "FAILED"
            failed += 1
        else:
            item["status"] = ar.state  # PENDING, STARTED, RETRY
            all_done = False

        updated_items.append(item)

    # Update batch record
    batch.items = updated_items
    batch.succeeded = succeeded
    batch.failed = failed
    if all_done:
        batch.status = BatchStatus.COMPLETED if failed == 0 else BatchStatus.PARTIAL
    await db.commit()

    return {
        "batch_id": str(batch.id),
        "status": batch.status.value,
        "total": batch.total,
        "succeeded": succeeded,
        "failed": failed,
        "processing": batch.total - succeeded - failed,
        "items": updated_items,
        "is_done": all_done,
    }


@router.get("/batches")
async def list_my_batches(
    current_user: Annotated[User, Depends(require_teacher)],
    db: AsyncSession = Depends(get_db),
):
    """List upload batches for the current user."""
    result = await db.execute(
        select(UploadBatch)
        .where(UploadBatch.owner_id == current_user.id)
        .order_by(desc(UploadBatch.created_at))
        .limit(20)
    )
    batches = result.scalars().all()
    return [
        {
            "batch_id": str(b.id),
            "status": b.status.value,
            "total": b.total,
            "succeeded": b.succeeded,
            "failed": b.failed,
            "created_at": b.created_at.isoformat() if b.created_at else None,
        }
        for b in batches
    ]
