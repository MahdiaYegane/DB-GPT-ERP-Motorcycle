"""
ERP Motorcycle - MariaDB Integration Package

Layered architecture:
  UI / API
    -> Services (business logic)
      -> Repositories (data access)
        -> Database Layer (engine/session)
          -> MariaDB 172.16.1.131:3306 / erp_motorcycle

All connections are env-based (.env), UTF-8/utf8mb4, pooled with pre_ping,
and never hard-code credentials.
"""

from erp.config import erp_settings, get_database_url
from erp.database import get_engine, get_session_factory, get_session, Base

__all__ = ["erp_settings", "get_database_url", "get_engine", "get_session_factory", "get_session", "Base"]
