"""FalkorDB client singleton."""
from __future__ import annotations

import threading
import structlog
from falkordb import FalkorDB, Graph

from chatbot.config import get_settings

logger = structlog.get_logger(__name__)

_client: FalkorDB | None = None
_graph: Graph | None = None
_lock = threading.Lock()


def get_graph() -> Graph | None:
    """Return the FalkorDB graph instance, or None if unavailable."""
    global _client, _graph
    if _graph is not None:
        return _graph
    with _lock:
        if _graph is not None:
            return _graph
        try:
            settings = get_settings()
            _client = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)
            _graph = _client.select_graph(settings.falkordb_graph_name)
            logger.info("falkordb_connected", graph=settings.falkordb_graph_name)
        except Exception as e:
            logger.warning("falkordb_unavailable", error=str(e))
            _graph = None
    return _graph


def reset_graph() -> None:
    """Reset connection (for testing or reconnect)."""
    global _client, _graph
    with _lock:
        _client = None
        _graph = None
