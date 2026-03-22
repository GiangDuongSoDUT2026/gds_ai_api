from functools import lru_cache
from uuid import UUID

from pydantic_settings import SettingsConfigDict

from shared.config import Settings


class ChatbotSettings(Settings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    openai_base_url: str | None = None  # override endpoint (e.g. Google AI Studio)
    vllm_base_url: str | None = None
    vllm_model: str | None = None
    max_tool_iterations: int = 5
    api_secret_key: str = "change-me-in-production"

    # FalkorDB (Graph Database for GraphRAG)
    falkordb_host: str = "falkordb"
    falkordb_port: int = 6379
    falkordb_graph_name: str = "gds_knowledge"
    graph_sync_interval_seconds: int = 300  # sync every 5 min


@lru_cache
def get_chatbot_settings() -> ChatbotSettings:
    return ChatbotSettings()


# Alias used by chatbot internals
def get_settings() -> ChatbotSettings:
    return get_chatbot_settings()
