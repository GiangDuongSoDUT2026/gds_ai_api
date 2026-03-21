from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from shared.logging import setup_logging

from api.config import get_api_settings
from api.routers import upload, jobs, programs, lectures, search
from api.routers.auth import router as auth_router
from api.routers.organizations import router as org_router
from api.routers.progress import router as progress_router

settings = get_api_settings()
setup_logging(settings.log_level, settings.environment)

app = FastAPI(
    title="GDS AI API",
    version="0.1.0",
    description="AI Lecture Intelligence System — Hệ thống Giảng Đường Số",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage_dir = Path(settings.storage_path)
storage_dir.mkdir(parents=True, exist_ok=True)
app.mount("/files", StaticFiles(directory=str(storage_dir)), name="files")

app.include_router(upload.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(programs.router, prefix="/api/v1")
app.include_router(lectures.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(org_router, prefix="/api/v1")
app.include_router(progress_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    return {"status": "ok", "version": "0.1.0"}


@app.exception_handler(404)
async def not_found_handler(request: Request, exc) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "Not found", "path": str(request.url)},
    )


@app.exception_handler(422)
async def validation_error_handler(request: Request, exc) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "Validation error", "detail": exc.errors() if hasattr(exc, "errors") else str(exc)},
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc) -> JSONResponse:
    import structlog

    log = structlog.get_logger(__name__)
    log.error("internal_server_error", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"},
    )
