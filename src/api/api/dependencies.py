"""
Package: api.dependencies (flat module fallback)

Re-exports from api.dependencies package so both import styles work.
"""
from api.dependencies import get_db, get_celery

__all__ = ["get_db", "get_celery"]
