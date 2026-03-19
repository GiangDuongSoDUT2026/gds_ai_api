import json
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database.connection import get_db
from shared.database.models import ChatMessage, ChatRole, ChatSession
from shared.logging import setup_logging

from chatbot.agent.router import LectureAgent
from chatbot.config import get_chatbot_settings
from chatbot.core.auth import decode_user_context
from chatbot.graph_db.schema import init_schema
from chatbot.graph_db.sync import sync_all, sync_lecture
from chatbot.schemas.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    SessionCreate,
    SessionResponse,
)

settings = get_chatbot_settings()
setup_logging(settings.log_level, settings.environment)

logger = structlog.get_logger(__name__)

bearer = HTTPBearer(auto_error=False)


def get_user_context_from_header(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict | None:
    if not credentials:
        return None
    return decode_user_context(credentials.credentials)


@asynccontextmanager
async def lifespan(application: FastAPI):
    logger.info("chatbot_starting")
    # Initialize FalkorDB schema (indexes). Gracefully skipped if FalkorDB is not running.
    try:
        init_schema()
    except Exception as _e:
        logger.warning("falkordb_init_failed", error=str(_e))
    yield
    logger.info("chatbot_shutting_down")


app = FastAPI(
    title="GDS Chatbot",
    version="1.0.0",
    description="AI Lecture Agent — Hệ thống Giảng Đường Số",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "1.0.0", "service": "chatbot"}


# ─── Stateless chat (no session) ──────────────────────────────────────────────


@app.post("/chat", response_model=ChatMessageResponse)
async def chat_stateless(
    request: ChatMessageRequest,
    user_ctx: dict | None = Depends(get_user_context_from_header),
) -> ChatMessageResponse:
    agent = LectureAgent(user_context=user_ctx)
    return await agent.chat(request.content, [])


# ─── Sessions ─────────────────────────────────────────────────────────────────


@app.post("/chat/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    data: SessionCreate,
    user_ctx: dict | None = Depends(get_user_context_from_header),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    user_id = (user_ctx.get("user_id") if user_ctx else None) or data.user_id
    session = ChatSession(
        id=uuid.uuid4(),
        user_id=user_id,
        course_id=data.course_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionResponse.model_validate(session)


@app.post("/chat/sessions/{session_id}/messages", response_model=ChatMessageResponse)
async def chat_with_history(
    session_id: uuid.UUID,
    request: ChatMessageRequest,
    user_ctx: dict | None = Depends(get_user_context_from_header),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageResponse:
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    existing_messages = result.scalars().all()
    history = [{"role": msg.role.value, "content": msg.content} for msg in existing_messages]

    user_msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role=ChatRole.user,
        content=request.content,
    )
    db.add(user_msg)
    await db.flush()

    agent = LectureAgent(user_context=user_ctx)
    response = await agent.chat(request.content, history)

    assistant_msg = ChatMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role=ChatRole.assistant,
        content=response.content,
        citations=[c.model_dump() for c in response.citations] if response.citations else None,
        tool_calls=[{"tool": t} for t in response.tool_calls_used]
        if response.tool_calls_used
        else None,
    )
    db.add(assistant_msg)
    await db.commit()

    return response


# ─── Graph sync endpoints ─────────────────────────────────────────────────────


def _require_super_admin(user_ctx: dict | None = Depends(get_user_context_from_header)) -> dict:
    """Dependency that enforces SUPER_ADMIN role."""
    if not user_ctx or user_ctx.get("role") != "SUPER_ADMIN":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return user_ctx


@app.post("/graph/sync")
async def trigger_graph_sync(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(_require_super_admin),
) -> dict:
    """Trigger a full PostgreSQL → FalkorDB sync. Super admin only."""
    counts = await db.run_sync(sync_all)
    return {"status": "ok", "synced": counts}


@app.post("/graph/sync/lecture/{lecture_id}")
async def sync_single_lecture_endpoint(
    lecture_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Called by the worker after lecture processing completes. No auth required (internal)."""
    await db.run_sync(sync_lecture, lecture_id)
    return {"status": "ok", "lecture_id": lecture_id}


# ─── WebSocket ────────────────────────────────────────────────────────────────


@app.websocket("/ws/chat/{session_id}")
async def websocket_chat(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str | None = Query(None),
) -> None:
    await websocket.accept()
    user_ctx = decode_user_context(token)
    log = logger.bind(session_id=str(session_id))
    log.info("websocket_connected", role=user_ctx.get("role") if user_ctx else "anonymous")

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                message = payload.get("content", "")
                history = payload.get("history", [])
            except json.JSONDecodeError:
                message = data
                history = []

            if not message.strip():
                continue

            agent = LectureAgent(user_context=user_ctx)

            async for event in agent.stream(message, history):
                await websocket.send_text(json.dumps(event))
                if event.get("type") == "done":
                    break

    except WebSocketDisconnect:
        log.info("websocket_disconnected")
    except Exception as exc:
        log.error("websocket_error", error=str(exc))
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(exc)}))
        except Exception:
            pass
        await websocket.close()
