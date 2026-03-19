"""LangGraph agent state definition."""
from __future__ import annotations

from typing import Annotated, TypedDict
import operator
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """State flowing through the LangGraph agent."""
    messages: Annotated[list[BaseMessage], operator.add]
    user_context: dict | None
    tool_outputs: list[str]
    citations: list[dict]
    cards: list[dict]
    tool_calls_used: list[str]
