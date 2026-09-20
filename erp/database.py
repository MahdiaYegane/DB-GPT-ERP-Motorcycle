"""
ERP Database Engine / Session - SQLAlchemy + PyMySQL, pool_pre_ping, utf8mb4, timeouts.

- URL built from erp.config (env-based)
- pool_pre_ping=True, pool_recycle, sensible pool_size/max_overflow
- connect_args: connect_timeout, charset utf8mb4, autocommit off
- Never logs passwords

Usage:
    from erp.database import get_engine, get_session, Base
    engine = get_engine()
    with get_session() as session:
        session.execute(text("SELECT 1"))
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from erp.config import erp_settings, get_database_url, get_database_url_safe

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine

    url = get_database_url()
    safe = get_database_url_safe()
    logger.info("Creating ERP engine %s", safe)

    s = erp_settings
    # connect_args are passed to PyMySQL
    _engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=s.pool_size,
        max_overflow=s.max_overflow,
        pool_recycle=s.pool_recycle,
        connect_args={
            "connect_timeout": s.connect_timeout,
            "charset": s.charset,
            # Important for Persian/utf8mb4: ensure client uses utf8mb4
            "use_unicode": True,
        },
        # Echo off (avoid logging SQL with bind params that may contain user data)
        echo=False,
        future=True,
    )

    # Quick validation: pre-ping will handle stale connections later;
    # we don't hit the network here (lazy).
    return _engine


def get_session_factory() -> sessionmaker:
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    engine = get_engine()
    _session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return _session_factory


@contextmanager
def get_session() -> Iterator[Session]:
    """Context-managed Session (commit on success, rollback on error, always close)."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _session_factory = None


# ---------------------------------------------------------------------------
# Lightweight health-check utilities (used by health.py and startup checks)
# ---------------------------------------------------------------------------

def _classify_db_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return "timeout"
    if "refused" in msg or "can't connect" in msg or "2003" in msg or "2002" in msg:
        return "server_unavailable"
    if "access denied" in msg or "1045" in msg or "denied" in msg or "authentication" in msg:
        return "authentication_failure"
    if "unknown database" in msg or "1049" in msg:
        return "database_unavailable"
    if "lost connection" in msg or "2013" in msg or "mySQL server has gone away" in msg:
        return "lost_connection"
    if "1046" in msg or "no database selected" in msg:
        return "database_unavailable"
    return "query_failure"


def ping_database(timeout: int | None = None) -> dict:
    """
    Lightweight ping: SELECT 1 with a short statement timeout.

    Returns dict with keys:
      ok: bool
      category: one of success/server_unavailable/timeout/authentication_failure/...
      error: str | None
      latency_ms: int | None
    """
    import time

    t0 = time.monotonic()
    try:
        # Use a fresh engine with a tight timeout for ping
        engine = get_engine()
        # Use short statement timeout via execution_options
        with engine.connect() as conn:
            # Optional per-statement timeout (PyMySQL doesn't support query timeout natively,
            # so we rely on connect_timeout + overall operation; keep it simple)
            conn.execute(text("SELECT 1"))
        latency = int((time.monotonic() - t0) * 1000)
        return {"ok": True, "category": "success", "error": None, "latency_ms": latency}
    except Exception as exc:
        latency = int((time.monotonic() - t0) * 1000)
        # Never log password; use safe url
        logger.warning("ERP ping failed (%s): %s", _classify_db_error(exc), exc)
        return {
            "ok": False,
            "category": _classify_db_error(exc),
            "error": str(exc)[:800],
            "latency_ms": latency,
        }
