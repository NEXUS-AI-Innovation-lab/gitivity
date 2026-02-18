"""Database access layer"""
from app.db.client import db, get_db, DatabaseClient

__all__ = ["db", "get_db", "DatabaseClient"]
