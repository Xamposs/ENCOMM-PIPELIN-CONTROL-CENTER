"""Persistence layer: SQLite schema and the explicit data-access layer."""

from .database import SCHEMA_PATH, SCHEMA_VERSION, Database, PersistenceError

__all__ = ["SCHEMA_PATH", "SCHEMA_VERSION", "Database", "PersistenceError"]
