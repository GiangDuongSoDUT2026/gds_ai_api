"""Initialize FalkorDB graph schema — CREATE INDEXes and constraints."""
from __future__ import annotations

import structlog
from chatbot.graph_db.client import get_graph

logger = structlog.get_logger(__name__)

# Index creation queries
_INDEX_QUERIES = [
    "CREATE INDEX FOR (n:Student) ON (n.id)",
    "CREATE INDEX FOR (n:Teacher) ON (n.id)",
    "CREATE INDEX FOR (n:Lecture) ON (n.id)",
    "CREATE INDEX FOR (n:Lecture) ON (n.status)",
    "CREATE INDEX FOR (n:Chapter) ON (n.id)",
    "CREATE INDEX FOR (n:Course) ON (n.id)",
    "CREATE INDEX FOR (n:Program) ON (n.id)",
    "CREATE INDEX FOR (n:Organization) ON (n.id)",
    "CREATE INDEX FOR (n:Concept) ON (n.name)",
]


def init_schema() -> None:
    """Create indexes. Idempotent — safe to call multiple times."""
    graph = get_graph()
    if graph is None:
        logger.warning("falkordb_schema_skip", reason="graph not available")
        return
    for query in _INDEX_QUERIES:
        try:
            graph.query(query)
        except Exception:
            # Index may already exist — FalkorDB raises error on duplicate
            pass
    logger.info("falkordb_schema_ready")
