"""
ERP environment configuration - env-based, no hard codes.

Respects:
  DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
  DB_POOL_SIZE, DB_MAX_OVERFLOW, DB_POOL_RECYCLE,
  DB_CONNECT_TIMEOUT, DB_CHARSET

Connection URL is built dynamically (SQLAlchemy + PyMySQL).
Never log passwords.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus


def _load_env_file() -> None:
    """Load .env without extra deps (fallback if python-dotenv missing)."""
    # Try python-dotenv first
    try:
        from dotenv import load_dotenv  # type: ignore

        # Search upward from project root
        root = Path(__file__).resolve().parent.parent
        env_path = root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass

    # Fallback: manual .env parse (simple KEY=VALUE, ignore comments)
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class ERPSettings:
    host: str
    port: int
    name: str
    user: str
    password: str
    charset: str
    pool_size: int
    max_overflow: int
    pool_recycle: int
    connect_timeout: int

    @property
    def safe_url(self) -> str:
        """URL with password masked for logging."""
        return f"mysql+pymysql://{self.user}:***@{self.host}:{self.port}/{self.name}?charset={self.charset}"


def load_erp_settings() -> ERPSettings:
    return ERPSettings(
        host=_env("DB_HOST", "172.16.1.131"),
        port=_env_int("DB_PORT", 3306),
        name=_env("DB_NAME", "erp_motorcycle"),
        user=_env("DB_USER", "a.mansourpour"),
        password=_env("DB_PASSWORD", ""),
        charset=_env("DB_CHARSET", "utf8mb4"),
        pool_size=_env_int("DB_POOL_SIZE", 10),
        max_overflow=_env_int("DB_MAX_OVERFLOW", 20),
        pool_recycle=_env_int("DB_POOL_RECYCLE", 3600),
        connect_timeout=_env_int("DB_CONNECT_TIMEOUT", 5),
    )


erp_settings = load_erp_settings()


def get_database_url(settings: ERPSettings | None = None) -> str:
    s = settings or erp_settings
    # Empty password => "user:@host"  (PyMySQL accepts this)
    if s.password:
        auth = f"{quote_plus(s.user)}:{quote_plus(s.password)}"
    else:
        # Use manual construction to keep ":@" explicit (more compatible)
        auth = f"{quote_plus(s.user)}:"
    return (
        f"mysql+pymysql://{auth}@{s.host}:{s.port}/{s.name}"
        f"?charset={s.charset}"
    )


def get_database_url_safe(settings: ERPSettings | None = None) -> str:
    s = settings or erp_settings
    return s.safe_url
