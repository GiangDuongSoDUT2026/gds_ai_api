from __future__ import annotations

import json
from typing import AsyncGenerator

import structlog
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_openai import ChatOpenAI
from langchain.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from chatbot.config import get_settings
from chatbot.agent.prompts import get_system_prompt
from chatbot.agent.tools.search_lectures import SearchLecturesTool
from chatbot.agent.tools.query_database import QueryDatabaseTool
from chatbot.agent.tools.manage_lectures import ManageLecturesTool
from chatbot.agent.tools.admin_tools import AdminStatsTool
from chatbot.agent.tools.stats_tool import StatsTool
from chatbot.agent.tools.learning_tool import LearningTool
from chatbot.agent.tools.graph_rag import GraphRAGTool

logger = structlog.get_logger(__name__)

ROLE_HIERARCHY = {
    "SUPER_ADMIN": 5,
    "SCHOOL_ADMIN": 4,
    "FACULTY_ADMIN": 3,
    "TEACHER": 2,
    "STUDENT": 1,
    None: 0,
}


def _build_tools(user_context: dict | None) -> list[BaseTool]:
    role = user_context.get("role") if user_context else None
    level = ROLE_HIERARCHY.get(role, 0)
    tools: list[BaseTool] = []

    if level >= 1:
        tools.append(SearchLecturesTool(user_context=user_context))
        tools.append(QueryDatabaseTool(user_context=user_context))
        tools.append(StatsTool(user_context=user_context))
        tools.append(LearningTool(user_context=user_context))
        tools.append(GraphRAGTool(user_context=user_context))

    if level >= 2:
        tools.append(ManageLecturesTool(user_context=user_context))

    if level >= 3:
        tools.append(AdminStatsTool(user_context=user_context))

    return tools


def _build_llm() -> ChatOpenAI:
    settings = get_settings()
    if settings.llm_provider == "vllm" and settings.vllm_base_url and settings.vllm_model:
        return ChatOpenAI(
            base_url=settings.vllm_base_url,
            model=settings.vllm_model,
            api_key="vllm",
            temperature=0,
            streaming=True,
        )
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0,
        streaming=True,
    )


def _extract_citations(tool_outputs: list[str]) -> list[dict]:
    citations = []
    for output in tool_outputs:
        try:
            data = json.loads(output)
            # Direct citations list
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and "lecture_id" in item:
                        citations.append({
                            "lecture_title": item.get("lecture_title", ""),
                            "chapter_title": item.get("chapter_title", ""),
                            "timestamp_start": item.get("timestamp_start", 0),
                            "timestamp_end": item.get("timestamp_end", 0),
                            "keyframe_url": item.get("keyframe_url"),
                            "deep_link": f"/lectures/{item.get('lecture_id')}?t={int(item.get('timestamp_start', 0))}",
                            "lecture_id": item.get("lecture_id"),
                        })
            # Citations embedded in card response
            elif isinstance(data, dict) and "citations" in data:
                for item in data["citations"]:
                    if "lecture_id" in item:
                        citations.append({
                            "lecture_title": item.get("lecture_title", ""),
                            "chapter_title": item.get("chapter_title", ""),
                            "timestamp_start": item.get("timestamp_start", 0),
                            "timestamp_end": item.get("timestamp_end", 0),
                            "keyframe_url": item.get("keyframe_url"),
                            "deep_link": item.get("deep_link", f"/lectures/{item.get('lecture_id')}"),
                            "lecture_id": item.get("lecture_id"),
                        })
        except (json.JSONDecodeError, TypeError):
            pass
    return citations


def _extract_cards(tool_outputs: list[str]) -> list[dict]:
    cards = []
    for output in tool_outputs:
        try:
            parsed = json.loads(output)
            if isinstance(parsed, dict) and "__card_type" in parsed:
                cards.append(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
    return cards


class LectureAgent:
    def __init__(self, user_context: dict | None = None, settings=None) -> None:
        self.user_context = user_context
        self.llm = _build_llm()
        self.tools = _build_tools(user_context)
        self.system_prompt = get_system_prompt(
            user_context.get("role") if user_context else None
        )

    def _make_graph(self):
        """Build LangGraph ReAct agent (uses tool calling, not text templates)."""
        if not self.tools:
            return None
        return create_react_agent(
            self.llm,
            self.tools,
            messages_modifier=SystemMessage(content=self.system_prompt),
        )

    def _build_messages(self, message: str, history: list[dict] | None) -> list[BaseMessage]:
        """Convert history dict list to LangChain message objects."""
        messages = []
        if history:
            for msg in history[-10:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))
        messages.append(HumanMessage(content=message))
        return messages

    async def stream(self, message: str, history: list[dict] | None = None) -> AsyncGenerator[dict, None]:
        """Yields dicts: {type: 'token'|'tool_call'|'citations'|'card'|'done', ...}"""
        graph = self._make_graph()
        messages = self._build_messages(message, history)

        # Anonymous user — no tools, direct LLM
        if graph is None:
            prompt = f"{self.system_prompt}\n\nUser: {message}\nAssistant:"
            async for chunk in self.llm.astream(prompt):
                if chunk.content:
                    yield {"type": "token", "content": chunk.content}
            yield {"type": "done"}
            return

        tool_outputs: list[str] = []
        tool_calls_used: list[str] = []

        try:
            async for event in graph.astream_events(
                {"messages": messages},
                version="v2",
            ):
                kind = event.get("event")
                name = event.get("name", "")

                if kind == "on_tool_start":
                    tool_calls_used.append(name)
                    yield {"type": "tool_call", "tool": name}

                elif kind == "on_tool_end":
                    output = event.get("data", {}).get("output", "")
                    if isinstance(output, str):
                        tool_outputs.append(output)

                elif kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        # Only yield tokens from the final answer (not tool-call planning)
                        if not getattr(chunk, "tool_calls", None):
                            yield {"type": "token", "content": chunk.content}

        except Exception as e:
            logger.error("agent_stream_error", error=str(e))
            yield {"type": "token", "content": f"\n\n[Lỗi: {e}]"}

        # Emit structured data after streaming
        cards = _extract_cards(tool_outputs)
        for card in cards:
            yield {"type": "card", "data": card}

        citations = _extract_citations(tool_outputs)
        if citations:
            yield {"type": "citations", "citations": citations}

        yield {"type": "done", "tool_calls_used": tool_calls_used}

    async def chat(self, message: str, history: list[dict]) -> object:
        """Non-streaming chat for HTTP endpoint backward compatibility."""
        from chatbot.schemas.chat import ChatMessageResponse, Citation

        full_content = ""
        citations_data = []
        tool_calls_used = []

        async for event in self.stream(message, history):
            if event["type"] == "token":
                full_content += event["content"]
            elif event["type"] == "citations":
                citations_data = event["citations"]
            elif event["type"] == "done":
                tool_calls_used = event.get("tool_calls_used", [])

        citations = [Citation(**c) for c in citations_data]
        return ChatMessageResponse(
            role="assistant",
            content=full_content,
            citations=citations,
            tool_calls_used=tool_calls_used,
        )
