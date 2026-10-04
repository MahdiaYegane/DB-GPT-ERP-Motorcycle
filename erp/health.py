"""
ERP Database Health Check - distinguishes success / unreachable / timeout /
auth failure / database unavailable / lost connection.

Executes (read-only, low-cost):
  SELECT 1
  SELECT VERSION()
  SELECT DATABASE()
  SELECT USER(), CURRENT_USER()

Designed to be safe for GUI startup (call via thread pool, not main thread).
Never logs credentials.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, DBAPIError

from erp.config import erp_settings, get_database_url_safe
from erp.database import _classify_db_error, get_engine

logger = logging.getLogger(__name__)


@dataclass
class HealthResult:
    ok: bool
    category: str  # success | server_unavailable | timeout | authentication_failure | database_unavailable | lost_connection | query_failure
    server_version: Optional[str] = None
    database: Optional[str] = None
    user: Optional[str] = None
    current_user: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    safe_url: Optional[str] = None

    def to_dict(self):
        return asdict(self)

    def summary(self) -> str:
        if self.ok:
            return (
                f"OK ({self.category}) version={self.server_version} "
                f"db={self.database} user={self.user} current_user={self.current_user} "
                f"latency={self.latency_ms}ms [{self.safe_url}]"
            )
        return f"FAIL ({self.category}) error={self.error} [{self.safe_url}] latency={self.latency_ms}ms"


def _run_health_check_sync() -> HealthResult:
    """
    Run a full health check (4 SELECTs). Distinguishes categories.
    Safe for startup: short, read-only, pooled with pre_ping.
    """
    safe_url = get_database_url_safe()
    t0 = time.monotonic()
    try:
        engine = get_engine()
        with engine.connect() as conn:
            # Use a short statement-level execution; rely on connect_timeout for network
            conn.execute(text("SELECT 1"))
            version = conn.execute(text("SELECT VERSION()")).scalar()
            database = conn.execute(text("SELECT DATABASE()")).scalar()
            user, current_user = conn.execute(text("SELECT USER(), CURRENT_USER()")).fetchone()

        latency = int((time.monotonic() - t0) * 1000)
        result = HealthResult(
            ok=True,
            category="success",
            server_version=str(version),
            database=str(database) if database else None,
            user=str(user) if user else None,
            current_user=str(current_user) if current_user else None,
            latency_ms=latency,
            safe_url=safe_url,
        )
        logger.info("ERP health OK: %s", result.summary())
        return result

    except OperationalError as exc:
        # OperationalError wraps DBAPI errors (connect, auth, unknown DB)
        latency = int((time.monotonic() - t0) * 1000)
        orig = getattr(exc, "orig", exc)
        category = _classify_db_error(orig)
        # Fallback classify on the outer message too
        if category == "query_failure":
            category = _classify_db_error(exc)
        logger.warning("ERP health OperationalError (%s): %s", category, exc)
        return HealthResult(
            ok=False,
            category=category,
            latency_ms=latency,
            error=str(orig)[:900] if orig else str(exc)[:900],
            safe_url=safe_url,
        )

    except DBAPIError as exc:
        latency = int((time.monotonic() - t0) * 1000)
        category = _classify_db_error(exc)
        logger.warning("ERP health DBAPIError (%s): %s", category, exc)
        return HealthResult(ok=False, category=category, latency_ms=latency, error=str(exc)[:900], safe_url=safe_url)

    except Exception as exc:
        latency = int((time.monotonic() - t0) * 1000)
        category = _classify_db_error(exc)
        logger.exception("ERP health unexpected error (%s)", category)
        return HealthResult(ok=False, category=category, latency_ms=latency, error=str(exc)[:900], safe_url=safe_url)


def run_health_check(timeout: int | None = None) -> HealthResult:
    """Run a full health check (4 SELECTs).

    Args:
        timeout: overall seconds budget. When given, the check runs in a
            worker thread and a ``timeout`` category is returned instead of
            blocking forever on a black-holed network. ``None`` (default)
            runs inline with no extra budget beyond the DB connect_timeout.

    Note: the worker thread cannot be killed; on timeout it is left to
    finish in the background while the caller gets the FAIL result.
    """
    if timeout is None:
        return _run_health_check_sync()

    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FutureTimeoutError

    safe_url = get_database_url_safe()
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="erp-health") as ex:
        future = ex.submit(_run_health_check_sync)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            latency = int((time.monotonic() - t0) * 1000)
            logger.warning("ERP health timed out after %ss", timeout)
            return HealthResult(
                ok=False,
                category="timeout",
                latency_ms=latency,
                error=f"health check exceeded {timeout}s budget",
                safe_url=safe_url,
            )


def run_health_check_async(executor=None):
    """
    Helper for GUI: submit health check to a thread pool so UI doesn't freeze.

    Example:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = run_health_check_async(ex)
            result = future.result(timeout=10)
    """
    from concurrent.futures import ThreadPoolExecutor

    ex = executor or ThreadPoolExecutor(max_workers=1)
    close_after = executor is None
    future = ex.submit(run_health_check)
    if close_after:
        # Caller should still await future; we shutdown only after they consumed it.
        # So don't shutdown here - let caller manage.
        pass
    return future
