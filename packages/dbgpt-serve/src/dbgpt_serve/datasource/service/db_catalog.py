"""Automatic database catalog, built once when a datasource is connected.

The catalog is the assistant's knowledge of a database. It is discovered from
the database itself — nothing is hand-written per database or per ERP:

1. Metadata: tables, row counts, columns + types, comments, primary keys,
   declared foreign keys.
2. Code: views / procedures / functions defined in the database. Used to find
   which tables are used together and how they are joined (most ERPs declare
   almost no foreign keys).
3. Profiling (non-empty tables only): per-column null ratio, distinct values,
   value distribution of low-cardinality columns, numeric / date ranges and
   string date formats (e.g. ``1405/06/18``).
4. Relationships: declared FKs + joins found in database code + shared
   identifier-like column names.
5. Descriptions: an LLM writes a short description and search keywords for
   every non-empty table, strictly from the evidence above.
6. Embeddings: each non-empty table (name + description + keywords + columns)
   is embedded with the local embedding model (e.g. bge-m3, multilingual, no
   API cost) so a question in any language finds its tables by meaning.

The result is stored as JSON in ``<pilot>/meta_data/db_catalog/<db>.json`` and
reused for every question. Rebuilds are incremental: tables whose columns did
not change keep their description (no LLM call).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import decimal
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

CATALOG_VERSION = 1

# Tunables (environment variables, all optional).
SAMPLE_ROWS = int(os.getenv("DBGPT_CATALOG_SAMPLE_ROWS", "2000"))
EXACT_PROFILE_MAX_ROWS = int(os.getenv("DBGPT_CATALOG_EXACT_MAX_ROWS", "1000000"))
LOW_CARDINALITY = int(os.getenv("DBGPT_CATALOG_LOW_CARDINALITY", "25"))
MAX_PROFILED_TABLES = int(os.getenv("DBGPT_CATALOG_MAX_TABLES", "600"))
LLM_BATCH_SIZE = int(os.getenv("DBGPT_CATALOG_LLM_BATCH", "6"))
LLM_CONCURRENCY = int(os.getenv("DBGPT_CATALOG_LLM_CONCURRENCY", "3"))
LLM_TIMEOUT_SECONDS = float(os.getenv("DBGPT_CATALOG_LLM_TIMEOUT", "240"))
# Cost controls for the (paid) LLM description step.
DESCRIBE_ENABLED = os.getenv("DBGPT_CATALOG_DESCRIBE", "1").strip().lower() not in ("0", "false", "no")
DESCRIBE_MAX_TABLES = int(os.getenv("DBGPT_CATALOG_DESCRIBE_MAX_TABLES", "200"))
EVIDENCE_MAX_COLUMNS = int(os.getenv("DBGPT_CATALOG_EVIDENCE_MAX_COLUMNS", "60"))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("DBGPT_CATALOG_LLM_MAX_TOKENS", "3000"))
_USAGE: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
STARTUP_DELAY_SECONDS = float(os.getenv("DBGPT_CATALOG_STARTUP_DELAY", "60"))
# Semantic table search with the local embedding model (free, multilingual).
EMBED_ENABLED = os.getenv("DBGPT_CATALOG_EMBED", "1").strip().lower() not in ("0", "false", "no")
EMBED_BATCH = int(os.getenv("DBGPT_CATALOG_EMBED_BATCH", "16"))
EMBED_QUERY_TIMEOUT = float(os.getenv("DBGPT_CATALOG_EMBED_QUERY_TIMEOUT", "8"))

# Large-object types (not profiled). "text" is a LOB only on SQL Server; on
# SQLite / PostgreSQL / MySQL it is an ordinary string type.
_MSSQL_LOB_TYPES = {"text", "ntext", "image"}
_SKIP_TYPES = {
    "xml", "varbinary", "binary", "blob", "longblob",
    "mediumblob", "tinyblob", "longtext", "mediumtext", "bytea", "geography",
    "geometry", "hierarchyid", "sql_variant", "json", "jsonb",
}
_NUMERIC_TYPES = {
    "int", "integer", "bigint", "smallint", "tinyint", "mediumint", "decimal",
    "numeric", "float", "real", "double", "double precision", "money",
    "smallmoney", "number",
}
_DATE_TYPES = {
    "date", "datetime", "datetime2", "smalldatetime", "datetimeoffset",
    "timestamp", "timestamp without time zone", "timestamp with time zone",
}
_DATE_STRING = re.compile(r"^\s*(\d{2,4})[/\-.](\d{1,2})[/\-.](\d{1,4})")
_IDENT = re.compile(r"[A-Za-z_][\w]*")


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------
def _catalog_dir() -> str:
    base = os.getenv("DBGPT_CATALOG_DIR")
    if not base:
        try:
            from dbgpt.configs.model_config import PILOT_PATH

            base = os.path.join(PILOT_PATH, "meta_data", "db_catalog")
        except Exception:
            base = os.path.join(os.getcwd(), "pilot", "meta_data", "db_catalog")
    os.makedirs(base, exist_ok=True)
    return base


def _safe(db_name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", str(db_name))


def catalog_path(db_name: str) -> str:
    return os.path.join(_catalog_dir(), f"{_safe(db_name)}.json")


_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def load_catalog(db_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Load a catalog (cached by file mtime). Returns None if missing."""
    if not db_name:
        return None
    path = catalog_path(db_name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.warning("Could not read catalog %s", path, exc_info=True)
        return None
    _CACHE[path] = (mtime, data)
    return data


def save_catalog(db_name: str, data: Dict[str, Any]) -> None:
    path = catalog_path(db_name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=_json_default)
    os.replace(tmp, path)


def delete_catalog(db_name: str) -> None:
    try:
        os.remove(catalog_path(db_name))
    except OSError:
        pass


def catalog_status(db_name: str) -> Dict[str, Any]:
    cat = load_catalog(db_name)
    if not cat:
        return {"db_name": db_name, "status": "missing"}
    tables = cat.get("tables") or {}
    return {
        "db_name": db_name,
        "status": cat.get("status"),
        "progress": cat.get("progress"),
        "error": cat.get("error"),
        "built_at": cat.get("built_at"),
        "dialect": cat.get("dialect"),
        "total_tables": cat.get("total_tables"),
        "non_empty_tables": sum(1 for t in tables.values() if t.get("row_count")),
        "described_tables": sum(1 for t in tables.values() if t.get("description")),
        "relationships": len(cat.get("relationships") or []),
        "llm_model": cat.get("llm_model"),
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, (decimal.Decimal,)):
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


# --------------------------------------------------------------------------
# Scheduling (one build per database at a time, never blocks a request)
# --------------------------------------------------------------------------
_LOCK = threading.Lock()
_BUILDING: set = set()
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
_TASKS: set = set()


def set_main_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _MAIN_LOOP
    if loop is not None:
        _MAIN_LOOP = loop


def is_building(db_name: str) -> bool:
    with _LOCK:
        return db_name in _BUILDING


def schedule_build(
    db_name: str,
    connector_getter: Callable[[str], Any],
    *,
    force: bool = False,
    llm_client: Any = None,
    llm_model: Optional[str] = None,
    delay_seconds: float = 0.0,
) -> bool:
    """Start a background catalog build. Returns True when a build started.

    ``force=False`` only builds when no ready catalog exists.
    """
    if not db_name:
        return False
    if not force:
        cat = load_catalog(db_name)
        if cat and cat.get("status") == "ready" and cat.get("version") == CATALOG_VERSION:
            return False
    with _LOCK:
        if db_name in _BUILDING:
            return False
        _BUILDING.add(db_name)

    async def _runner():
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await build_catalog(
                db_name, connector_getter, llm_client=llm_client, llm_model=llm_model
            )
        finally:
            with _LOCK:
                _BUILDING.discard(db_name)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        set_main_loop(loop)
        task = loop.create_task(_runner())
        _TASKS.add(task)  # keep a strong reference until it finishes
        task.add_done_callback(_TASKS.discard)
        return True
    if _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(_runner(), _MAIN_LOOP)
        return True
    threading.Thread(
        target=lambda: asyncio.run(_runner()), name=f"db-catalog-{db_name}", daemon=True
    ).start()
    return True


# --------------------------------------------------------------------------
# SQL helpers
# --------------------------------------------------------------------------
def _engine(connector: Any):
    engine = getattr(connector, "_engine", None)
    if engine is None:
        raise ValueError("catalog requires a SQLAlchemy based (RDBMS) connector")
    return engine


def _query(connector: Any, sql: str, params: Optional[dict] = None) -> Optional[List[tuple]]:
    """Run a read-only query; return rows or None on failure (never raises)."""
    from sqlalchemy import text

    try:
        with _engine(connector).connect() as conn:
            result = conn.execute(text(sql), params or {})
            return [tuple(r) for r in result.fetchall()]
    except Exception as e:
        logger.debug("catalog query failed: %s | %s", e, sql[:200])
        return None


def _quote(connector: Any, full_name: str) -> str:
    preparer = _engine(connector).dialect.identifier_preparer
    return ".".join(preparer.quote(part) for part in full_name.split("."))


def _limit_sql(dialect: str, select_list: str, table_sql: str, n: int) -> str:
    if dialect == "mssql":
        return f"SELECT TOP {int(n)} {select_list} FROM {table_sql}"
    return f"SELECT {select_list} FROM {table_sql} LIMIT {int(n)}"


def _s(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return "" if value is None else str(value)


def _leaf(name: str) -> str:
    return name.split(".")[-1].casefold()


# --------------------------------------------------------------------------
# 1. Metadata
# --------------------------------------------------------------------------
def _collect_metadata(connector: Any) -> Dict[str, Any]:
    dialect = getattr(connector, "dialect", "") or ""
    names = [n for n in connector.get_table_names() if isinstance(n, str)]
    tables: Dict[str, Dict[str, Any]] = {
        n: {"name": n, "row_count": None, "columns": [], "primary_key": []} for n in names
    }
    by_full = {n.casefold(): n for n in names}
    by_leaf: Dict[str, List[str]] = defaultdict(list)
    for n in names:
        by_leaf[_leaf(n)].append(n)

    def resolve(schema: Any, table: Any) -> Optional[str]:
        schema, table = _s(schema), _s(table)
        full = f"{schema}.{table}".casefold() if schema else table.casefold()
        if full in by_full:
            return by_full[full]
        cands = by_leaf.get(table.casefold()) or []
        return cands[0] if len(cands) == 1 else None

    # Row counts (cheap metadata where available).
    rows = None
    if dialect == "mssql":
        rows = _query(
            connector,
            "SELECT s.name, t.name, SUM(p.rows) FROM sys.tables t "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "LEFT JOIN sys.partitions p ON p.object_id = t.object_id "
            "AND p.index_id IN (0,1) GROUP BY s.name, t.name",
        )
    elif dialect == "mysql":
        rows = _query(
            connector,
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE()",
        )
    elif dialect == "postgresql":
        rows = _query(
            connector,
            "SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables",
        )
    for schema, table, count in rows or []:
        name = resolve(schema, table)
        if name:
            tables[name]["row_count"] = int(count or 0)
    # Exact count where metadata was missing or reported 0 (estimates).
    exact_needed = dialect not in ("mssql",)
    for name, info in tables.items():
        if info["row_count"] is None or (exact_needed and info["row_count"] == 0):
            res = _query(connector, f"SELECT COUNT(*) FROM {_quote(connector, name)}")
            info["row_count"] = int(res[0][0]) if res else 0

    # Columns.
    col_rows = None
    if dialect in ("mssql", "mysql", "postgresql"):
        where = {
            "mysql": "WHERE TABLE_SCHEMA = DATABASE()",
            "postgresql": "WHERE table_schema NOT IN ('pg_catalog','information_schema')",
            "mssql": "",
        }[dialect]
        col_rows = _query(
            connector,
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
            "CHARACTER_MAXIMUM_LENGTH, ORDINAL_POSITION "
            f"FROM INFORMATION_SCHEMA.COLUMNS {where} ORDER BY TABLE_NAME, ORDINAL_POSITION",
        )
    if col_rows:
        for schema, table, col, dtype, max_len, _pos in col_rows:
            name = resolve(schema, table)
            if name:
                tables[name]["columns"].append(
                    {
                        "name": _s(col),
                        "type": _s(dtype).casefold(),
                        "max_length": int(max_len) if max_len is not None else None,
                    }
                )
    else:
        for name in names:
            try:
                for c in connector.get_columns(name) or []:
                    tables[name]["columns"].append(
                        {"name": _s(c.get("name")), "type": _s(c.get("type")).casefold().split("(")[0]}
                    )
            except Exception:
                logger.debug("get_columns failed for %s", name, exc_info=True)

    # Comments.
    if dialect == "mssql":
        for tname, col, value in _query(
            connector,
            "SELECT s.name + '.' + t.name, c.name, CAST(ep.value AS nvarchar(2000)) "
            "FROM sys.extended_properties ep "
            "JOIN sys.tables t ON ep.major_id = t.object_id "
            "JOIN sys.schemas s ON s.schema_id = t.schema_id "
            "LEFT JOIN sys.columns c ON c.object_id = ep.major_id AND c.column_id = ep.minor_id "
            "WHERE ep.class = 1 AND ep.name = 'MS_Description'",
        ) or []:
            _apply_comment(tables, by_full.get(_s(tname).casefold()), col, value)
    elif dialect == "mysql":
        for table, col, value in _query(
            connector,
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_COMMENT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_COMMENT <> ''",
        ) or []:
            _apply_comment(tables, resolve(None, table), col, value)
        for table, value in _query(
            connector,
            "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_COMMENT <> ''",
        ) or []:
            _apply_comment(tables, resolve(None, table), None, value)

    # Primary keys.
    if dialect in ("mssql", "mysql", "postgresql"):
        for schema, table, col in _query(
            connector,
            "SELECT kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.COLUMN_NAME "
            "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
            "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
            "ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME "
            "AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA AND tc.TABLE_NAME = kcu.TABLE_NAME "
            "WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'",
        ) or []:
            name = resolve(schema, table)
            if name:
                tables[name]["primary_key"].append(_s(col))

    # Primary keys via SQLAlchemy inspection for other dialects (e.g. SQLite).
    if dialect not in ("mssql", "mysql", "postgresql"):
        try:
            from sqlalchemy import inspect as _inspect

            insp = _inspect(_engine(connector))
            for name in names:
                pk = insp.get_pk_constraint(name) or {}
                tables[name]["primary_key"] = list(pk.get("constrained_columns") or [])
        except Exception:
            logger.debug("PK inspection failed", exc_info=True)

    # Declared foreign keys.
    fks: List[Dict[str, Any]] = []
    fk_rows = None
    if dialect == "mssql":
        fk_rows = _query(
            connector,
            "SELECT SCHEMA_NAME(pt.schema_id) + '.' + pt.name, pc.name, "
            "SCHEMA_NAME(rt.schema_id) + '.' + rt.name, rc.name "
            "FROM sys.foreign_key_columns fkc "
            "JOIN sys.tables pt ON pt.object_id = fkc.parent_object_id "
            "JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id "
            "JOIN sys.tables rt ON rt.object_id = fkc.referenced_object_id "
            "JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id",
        )
    elif dialect == "mysql":
        fk_rows = _query(
            connector,
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL",
        )
    for a, ac, b, bc in fk_rows or []:
        ta = by_full.get(_s(a).casefold()) or resolve(None, a)
        tb = by_full.get(_s(b).casefold()) or resolve(None, b)
        if ta and tb:
            fks.append({"from": ta, "from_column": _s(ac), "to": tb, "to_column": _s(bc), "source": "declared"})

    return {"dialect": dialect, "tables": tables, "foreign_keys": fks}


def _apply_comment(tables: Dict[str, Any], name: Optional[str], col: Any, value: Any) -> None:
    if not name or name not in tables or value in (None, ""):
        return
    text = _s(value).strip()[:300]
    if col in (None, ""):
        tables[name]["comment"] = text
        return
    for c in tables[name]["columns"]:
        if c["name"].casefold() == _s(col).casefold():
            c["comment"] = text


# --------------------------------------------------------------------------
# 2. Database code (views / procedures / functions)
# --------------------------------------------------------------------------
def _collect_modules(connector: Any, dialect: str) -> List[Tuple[str, str, str]]:
    """Return (kind, name, definition) for code objects stored in the DB."""
    rows: Optional[List[tuple]] = None
    if dialect == "mssql":
        rows = _query(
            connector,
            "SELECT o.type_desc, SCHEMA_NAME(o.schema_id) + '.' + o.name, m.definition "
            "FROM sys.sql_modules m JOIN sys.objects o ON o.object_id = m.object_id",
        )
    elif dialect == "mysql":
        rows = (_query(
            connector,
            "SELECT 'VIEW', TABLE_NAME, VIEW_DEFINITION FROM information_schema.VIEWS "
            "WHERE TABLE_SCHEMA = DATABASE()",
        ) or []) + (_query(
            connector,
            "SELECT ROUTINE_TYPE, ROUTINE_NAME, ROUTINE_DEFINITION FROM information_schema.ROUTINES "
            "WHERE ROUTINE_SCHEMA = DATABASE()",
        ) or [])
    elif dialect == "postgresql":
        rows = _query(
            connector,
            "SELECT 'VIEW', schemaname || '.' || viewname, definition FROM pg_views "
            "WHERE schemaname NOT IN ('pg_catalog','information_schema')",
        )
    elif dialect == "sqlite":
        rows = _query(connector, "SELECT type, name, sql FROM sqlite_master WHERE type IN ('view','trigger')")
    return [(_s(k), _s(n), _s(d)) for k, n, d in rows or [] if d]


_FROM_JOIN = re.compile(
    r"\b(?:from|join)\s+((?:\[?\w+\]?\.){0,2}\[?\w+\]?)(?:\s+(?:as\s+)?(\[?\w+\]?))?",
    re.IGNORECASE,
)
_EQ = re.compile(r"\b(\[?\w+\]?)\.(\[?\w+\]?)\s*=\s*(\[?\w+\]?)\.(\[?\w+\]?)")
_SQL_WORDS = {
    "where", "on", "inner", "left", "right", "full", "outer", "cross", "join",
    "group", "order", "union", "select", "set", "with", "as", "and", "or", "having",
}


def _analyse_modules(
    modules: List[Tuple[str, str, str]], tables: Dict[str, Any]
) -> Tuple[Dict[str, List[str]], List[Dict[str, Any]]]:
    """Which modules reference each table, and joins found in module code."""
    leaf_to_names: Dict[str, List[str]] = defaultdict(list)
    for n in tables:
        leaf_to_names[_leaf(n)].append(n)
    columns_of = {n: {c["name"].casefold() for c in t["columns"]} for n, t in tables.items()}

    def resolve(token: str) -> Optional[str]:
        token = token.replace("[", "").replace("]", "")
        cands = leaf_to_names.get(token.split(".")[-1].casefold()) or []
        if len(cands) == 1:
            return cands[0]
        for c in cands:
            if c.casefold() == token.casefold():
                return c
        return None

    referenced_by: Dict[str, List[str]] = defaultdict(list)
    join_counter: Counter = Counter()
    known_leaves = set(leaf_to_names)
    for kind, mname, definition in modules:
        idents = {i.casefold() for i in _IDENT.findall(definition)}
        for leaf in idents & known_leaves:
            for n in leaf_to_names[leaf]:
                if len(referenced_by[n]) < 50:
                    referenced_by[n].append(mname)
        # alias -> table
        aliases: Dict[str, str] = {}
        for m in _FROM_JOIN.finditer(definition):
            table = resolve(m.group(1))
            if not table:
                continue
            aliases[_leaf(m.group(1).replace("[", "").replace("]", ""))] = table
            alias = (m.group(2) or "").replace("[", "").replace("]", "")
            if alias and alias.casefold() not in _SQL_WORDS:
                aliases[alias.casefold()] = table
        for a1, c1, a2, c2 in _EQ.findall(definition):
            t1 = aliases.get(a1.replace("[", "").replace("]", "").casefold())
            t2 = aliases.get(a2.replace("[", "").replace("]", "").casefold())
            c1 = c1.replace("[", "").replace("]", "")
            c2 = c2.replace("[", "").replace("]", "")
            if not t1 or not t2 or t1 == t2:
                continue
            if c1.casefold() not in columns_of.get(t1, set()) or c2.casefold() not in columns_of.get(t2, set()):
                continue
            key = tuple(sorted([(t1, c1), (t2, c2)]))
            join_counter[key] += 1
    joins = [
        {"from": a[0], "from_column": a[1], "to": b[0], "to_column": b[1],
         "source": "db_code", "evidence_count": n}
        for (a, b), n in join_counter.most_common(5000)
    ]
    return dict(referenced_by), joins


# --------------------------------------------------------------------------
# 3. Profiling
# --------------------------------------------------------------------------
def _type_family(dtype: str, dialect: str) -> str:
    t = (dtype or "").casefold()
    if dialect == "mssql" and t in ("timestamp", "rowversion"):
        return "skip"
    if dialect == "mssql" and t in _MSSQL_LOB_TYPES:
        return "skip"
    if t in _SKIP_TYPES:
        return "skip"
    if t == "bit" or t == "boolean" or t == "bool":
        return "bool"
    if t in _NUMERIC_TYPES:
        return "number"
    if t in _DATE_TYPES:
        return "date"
    return "string"


def _profile_table(connector: Any, dialect: str, name: str, info: Dict[str, Any]) -> None:
    cols = []
    for c in info["columns"]:
        fam = _type_family(c.get("type", ""), dialect)
        max_len = c.get("max_length")
        if fam == "skip" or (fam == "string" and max_len is not None and (max_len < 0 or max_len > 1000)):
            continue
        cols.append((c, fam))
        if len(cols) >= 200:
            break
    if not cols:
        return
    table_sql = _quote(connector, name)
    preparer = _engine(connector).dialect.identifier_preparer
    select_list = ", ".join(preparer.quote(c["name"]) for c, _ in cols)
    rows = _query(connector, _limit_sql(dialect, select_list, table_sql, SAMPLE_ROWS))
    if not rows:
        return
    n = len(rows)
    row_count = info.get("row_count") or n
    low_card_exact: List[Dict[str, Any]] = []
    date_exact: List[Dict[str, Any]] = []
    for idx, (col, fam) in enumerate(cols):
        values = [r[idx] for r in rows]
        non_null = [v for v in values if v is not None and _s(v).strip() != ""]
        prof: Dict[str, Any] = {"null_ratio": round(1 - len(non_null) / n, 3)}
        if not non_null:
            col["profile"] = prof
            continue
        counts = Counter(_s(v).strip()[:60] for v in non_null)
        distinct = len(counts)
        prof["distinct_in_sample"] = distinct
        if distinct == len(non_null) and len(non_null) >= min(n, 50):
            prof["unique_in_sample"] = True
        if fam == "number":
            nums = [float(v) for v in non_null if _is_number(v)]
            if nums:
                prof["min"] = _num(min(nums))
                prof["max"] = _num(max(nums))
                prof["range_source"] = "sample"
                prof["zero_ratio"] = round(sum(1 for x in nums if x == 0) / len(nums), 3)
        elif fam == "date":
            prof["min"] = _s(min(non_null))[:19]
            prof["max"] = _s(max(non_null))[:19]
            prof["range_source"] = "sample"
            date_exact.append(col)
        elif fam == "string":
            ds = [m for m in (_DATE_STRING.match(_s(v)) for v in non_null) if m]
            if len(ds) >= 0.9 * len(non_null):
                sample_val = _s(non_null[0]).strip()
                prof["looks_like"] = "date_string"
                prof["format_example"] = sample_val[:20]
                prof["min"] = _s(min(_s(v) for v in non_null))[:20]
                prof["max"] = _s(max(_s(v) for v in non_null))[:20]
                prof["range_source"] = "sample"
                date_exact.append(col)
        if distinct <= LOW_CARDINALITY and fam in ("string", "number", "bool"):
            prof["values"] = [[v, c] for v, c in counts.most_common(LOW_CARDINALITY)]
            prof["values_source"] = "sample"
            low_card_exact.append(col)
        col["profile"] = prof

    if row_count <= EXACT_PROFILE_MAX_ROWS:
        # Exact distribution for low-cardinality columns (category / code columns).
        for col in low_card_exact[:15]:
            q = preparer.quote(col["name"])
            top = f"TOP {LOW_CARDINALITY + 5} " if dialect == "mssql" else ""
            lim = "" if dialect == "mssql" else f" LIMIT {LOW_CARDINALITY + 5}"
            res = _query(
                connector,
                f"SELECT {top}{q}, COUNT(*) FROM {table_sql} GROUP BY {q} ORDER BY COUNT(*) DESC{lim}",
            )
            if res:
                col["profile"]["values"] = [[_s(v).strip()[:60], int(c)] for v, c in res]
                col["profile"]["values_source"] = "exact"
                col["profile"]["distinct_exact"] = len(res) if len(res) <= LOW_CARDINALITY else None
        # Exact range for date columns.
        if date_exact:
            parts = []
            for col in date_exact[:10]:
                q = preparer.quote(col["name"])
                parts.append(f"MIN({q}), MAX({q})")
            res = _query(connector, f"SELECT {', '.join(parts)} FROM {table_sql}")
            if res:
                vals = res[0]
                for i, col in enumerate(date_exact[:10]):
                    col["profile"]["min"] = _s(vals[2 * i])[:20]
                    col["profile"]["max"] = _s(vals[2 * i + 1])[:20]
                    col["profile"]["range_source"] = "exact"


def _is_number(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _num(x: float) -> Any:
    return int(x) if float(x).is_integer() else round(x, 4)


# --------------------------------------------------------------------------
# 4. Relationships
# --------------------------------------------------------------------------
def _infer_name_relationships(tables: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Shared identifier-like column names between non-empty tables.

    A column name that appears in most tables (audit columns etc.) carries no
    relationship signal, so names present in more than 5% of the non-empty
    tables (min 8) are ignored. A link needs one side where the column looks
    like a key (primary key or unique in the sample).
    """
    non_empty = {n: t for n, t in tables.items() if t.get("row_count")}
    occurrences: Dict[str, List[str]] = defaultdict(list)
    for n, t in non_empty.items():
        for c in t["columns"]:
            occurrences[c["name"].casefold()].append(n)
    limit = max(8, int(0.05 * len(non_empty)))
    rels = []
    for col, owners in occurrences.items():
        if len(owners) < 2 or len(owners) > limit:
            continue
        keys = []
        for n in owners:
            t = non_empty[n]
            pk = {p.casefold() for p in t.get("primary_key", [])}
            colinfo = next((c for c in t["columns"] if c["name"].casefold() == col), {})
            if col in pk or (colinfo.get("profile") or {}).get("unique_in_sample"):
                keys.append(n)
        for key_table in keys:
            for other in owners:
                if other == key_table:
                    continue
                rels.append({"from": other, "from_column": col, "to": key_table,
                             "to_column": col, "source": "shared_key_name"})
    return rels[:5000]


# --------------------------------------------------------------------------
# 5. LLM descriptions
# --------------------------------------------------------------------------
_DESCRIBE_PROMPT = """You are documenting tables of a company database so that an
assistant can answer business questions. Use ONLY the evidence given (names,
types, comments, value distributions, related tables, database objects that use
the table). Do not invent meanings: if the purpose of a table or of a code value
is not clear from the evidence, say "unclear" and lower the confidence.

For each table return a JSON object with:
- "table": exact table name as given
- "description": 1-2 sentences in English: what one row represents and what the table is for
- "description_fa": the same in Persian
- "keywords": up to 10 search terms a business user might use for this data, in Persian and English
- "measures": column names that hold amounts/quantities that can be summed
- "dates": column names that hold dates (including date-like strings)
- "categories": column names that split rows into types/statuses/kinds
- "identifiers": column names that identify rows or link to other tables
- "confidence": number 0-1

Return ONLY a JSON array, no prose.

TABLES:
"""


def _table_evidence(name: str, t: Dict[str, Any], referenced_by: List[str], related: List[str]) -> str:
    lines = [f"## {name}  (rows: {t.get('row_count')})"]
    if t.get("comment"):
        lines.append(f"comment: {t['comment']}")
    if t.get("primary_key"):
        lines.append("primary key: " + ", ".join(t["primary_key"]))
    col_parts = []
    # Columns that are never filled carry no evidence; skip them to keep the
    # (paid) prompt small.
    useful = [c for c in t["columns"] if (c.get("profile") or {}).get("null_ratio", 0) < 0.99]
    for c in useful[:EVIDENCE_MAX_COLUMNS]:
        part = f"{c['name']} {c.get('type','')}"
        p = c.get("profile") or {}
        extra = []
        if c.get("comment"):
            extra.append(f"comment={c['comment'][:80]}")
        if p.get("null_ratio", 0) >= 0.99:
            extra.append("mostly empty")
        if p.get("values") and len(p["values"]) <= 12:
            extra.append("values=" + ",".join(f"{v}:{n}" for v, n in p["values"][:12]))
        if p.get("looks_like") == "date_string":
            extra.append(f"date string e.g. {p.get('format_example')}")
        elif "min" in p and "max" in p:
            extra.append(f"range={p['min']}..{p['max']}")
        if p.get("unique_in_sample"):
            extra.append("unique")
        if extra:
            part += " [" + "; ".join(extra) + "]"
        col_parts.append(part)
    lines.append("columns: " + " | ".join(col_parts))
    if related:
        lines.append("related tables: " + ", ".join(related[:10]))
    if referenced_by:
        lines.append("used by db objects: " + ", ".join(referenced_by[:10]))
    return "\n".join(lines)


def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    if not text:
        return []
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
        return [d for d in data if isinstance(d, dict)]
    except Exception:
        return []


async def _resolve_llm(llm_client: Any, llm_model: Optional[str]) -> Tuple[Any, Optional[str]]:
    if llm_client is None:
        try:
            from dbgpt._private.config import Config
            from dbgpt.component import ComponentType
            from dbgpt.model.cluster import WorkerManagerFactory
            from dbgpt.model.cluster.client import DefaultLLMClient

            cfg = Config()
            llm_client = DefaultLLMClient(
                cfg.SYSTEM_APP.get_component(
                    ComponentType.WORKER_MANAGER_FACTORY, WorkerManagerFactory
                ).create(),
                auto_convert_message=True,
            )
        except Exception:
            logger.warning("Catalog: no LLM client available", exc_info=True)
            return None, None
    model = llm_model or os.getenv("DBGPT_CATALOG_MODEL")
    if not model:
        try:
            from dbgpt._private.config import Config

            app_cfg = Config().SYSTEM_APP.config.configs.get("app_config")
            model = getattr(getattr(app_cfg, "models", None), "default_llm", None)
        except Exception:
            model = None
    if not model:
        try:
            models = await llm_client.models()
            model = models[0].model if models else None
        except Exception:
            model = None
    return llm_client, model


async def _describe_tables(
    llm_client: Any,
    model: str,
    evidences: List[Tuple[str, str]],
    on_batch: Optional[Callable[[Dict[str, Dict[str, Any]]], None]] = None,
    usage_key: str = "_",
) -> Dict[str, Dict[str, Any]]:
    """Describe tables in batches. Each LLM call has a timeout and one retry,
    so a hanging provider call can never block the whole build. ``on_batch``
    is called after every batch (used to checkpoint progress)."""
    from dbgpt.core import ModelMessage, ModelMessageRoleType, ModelRequest

    sem = asyncio.Semaphore(max(1, LLM_CONCURRENCY))
    results: Dict[str, Dict[str, Any]] = {}

    async def run_batch(batch: List[Tuple[str, str]]):
        prompt = _DESCRIBE_PROMPT + "\n\n".join(ev for _, ev in batch)
        names = {n.casefold(): n for n, _ in batch}
        got: Dict[str, Dict[str, Any]] = {}
        async with sem:
            for attempt in range(2):
                request = ModelRequest.build_request(
                    model,
                    messages=[ModelMessage(role=ModelMessageRoleType.HUMAN, content=prompt)],
                    temperature=0.0,
                    max_new_tokens=LLM_MAX_OUTPUT_TOKENS,
                )
                try:
                    output = await asyncio.wait_for(
                        llm_client.generate(request), timeout=LLM_TIMEOUT_SECONDS
                    )
                    text = output.text if getattr(output, "success", True) else ""
                    _record_usage(usage_key, prompt, text, getattr(output, "usage", None))
                except asyncio.TimeoutError:
                    logger.warning("Catalog description batch timed out (attempt %d)", attempt + 1)
                    text = ""
                except Exception:
                    logger.warning("Catalog description batch failed", exc_info=True)
                    text = ""
                for item in _parse_json_array(text):
                    key = _s(item.get("table")).casefold()
                    if key in names:
                        got[names[key]] = item
                if got:
                    break
        results.update(got)
        if on_batch and got:
            try:
                on_batch(got)
            except Exception:
                logger.debug("catalog checkpoint failed", exc_info=True)

    batches = [evidences[i : i + LLM_BATCH_SIZE] for i in range(0, len(evidences), LLM_BATCH_SIZE)]
    await asyncio.gather(*(run_batch(b) for b in batches))
    return results


def _record_usage(db: str, prompt: str, text: str, usage: Any) -> None:
    u = _USAGE[db]
    u["calls"] += 1
    u["prompt_chars"] += len(prompt or "")
    u["output_chars"] += len(text or "")
    if isinstance(usage, dict):
        u["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        u["completion_tokens"] += int(usage.get("completion_tokens") or 0)
    logger.info("Catalog LLM usage %s: %s", db, dict(u))


def _usage_since(db_name: str, before: Dict[str, int]) -> Dict[str, int]:
    now = _USAGE.get(db_name, {})
    return {k: int(now.get(k, 0)) - int(before.get(k, 0)) for k in now}


def _apply_description(t: Dict[str, Any], item: Dict[str, Any]) -> None:
    t["description"] = _s(item.get("description"))[:400]
    t["description_fa"] = _s(item.get("description_fa"))[:400]
    t["keywords"] = [_s(k)[:40] for k in (item.get("keywords") or [])][:12]
    valid = {c["name"].casefold(): c["name"] for c in t["columns"]}
    for key in ("measures", "dates", "categories", "identifiers"):
        t[key] = [valid[_s(x).casefold()] for x in (item.get(key) or []) if _s(x).casefold() in valid]
    try:
        t["confidence"] = float(item.get("confidence"))
    except (TypeError, ValueError):
        pass


def _checkpoint_path(db_name: str) -> str:
    return os.path.join(_catalog_dir(), f"{_safe(db_name)}.descriptions.json")


def _load_checkpoint(db_name: str) -> Dict[str, Any]:
    try:
        with open(_checkpoint_path(db_name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_checkpoint(db_name: str, data: Dict[str, Any]) -> None:
    path = _checkpoint_path(db_name)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def _fingerprint(t: Dict[str, Any]) -> str:
    sig = "|".join(f"{c['name']}:{c.get('type')}" for c in t.get("columns", []))
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]


def _importance(t: Dict[str, Any], refs: int, degree: int) -> float:
    rows = t.get("row_count") or 0
    return round(math.log10(rows + 1) + 1.5 * math.log1p(refs) + math.log1p(degree), 3)


async def build_catalog(
    db_name: str,
    connector_getter: Callable[[str], Any],
    *,
    llm_client: Any = None,
    llm_model: Optional[str] = None,
) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    started = time.time()
    previous = load_catalog(db_name) or {}
    prev_tables = previous.get("tables") or {}
    state: Dict[str, Any] = {
        "version": CATALOG_VERSION,
        "db_name": db_name,
        "status": "building",
        "progress": "metadata",
        "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "tables": prev_tables,  # keep serving the old catalog while building
    }
    if previous.get("status") == "ready":
        # Keep serving the previous catalog (incl. relationships) while rebuilding.
        state = {**previous, "rebuilding": True, "progress": "metadata"}
    save_catalog(db_name, state)
    try:
        connector = await loop.run_in_executor(None, connector_getter, db_name)
        meta = await loop.run_in_executor(None, _collect_metadata, connector)
        dialect = meta["dialect"]
        tables = meta["tables"]
        if not state.get("rebuilding"):
            # First build: publish the metadata right away (row counts,
            # columns) as a *partial* catalog, so questions asked during the
            # build already skip empty tables. Profiles are added as they come.
            state.update({"tables": tables, "partial": True, "dialect": dialect,
                          "total_tables": len(tables), "progress": "profiling"})
            save_catalog(db_name, state)

        modules = await loop.run_in_executor(None, _collect_modules, connector, dialect)
        referenced_by, code_joins = _analyse_modules(modules, tables)

        non_empty = sorted(
            (n for n, t in tables.items() if t.get("row_count")),
            key=lambda n: -(tables[n].get("row_count") or 0),
        )[:MAX_PROFILED_TABLES]
        for i, name in enumerate(non_empty):
            await loop.run_in_executor(None, _profile_table, connector, dialect, name, tables[name])
            if i % 20 == 0:
                state.update({"progress": f"profiling {i}/{len(non_empty)}"})
                save_catalog(db_name, state)

        relationships = meta["foreign_keys"] + code_joins + _infer_name_relationships(tables)
        degree: Counter = Counter()
        related: Dict[str, List[str]] = defaultdict(list)
        for r in relationships:
            degree[r["from"]] += 1
            degree[r["to"]] += 1
            if r["to"] not in related[r["from"]]:
                related[r["from"]].append(r["to"])
            if r["from"] not in related[r["to"]]:
                related[r["to"]].append(r["from"])

        for name, t in tables.items():
            t["fingerprint"] = _fingerprint(t)
            t["referenced_by"] = referenced_by.get(name, [])[:20]
            t["importance"] = _importance(t, len(referenced_by.get(name, [])), degree[name])
            prev = prev_tables.get(name) or {}
            if prev.get("fingerprint") == t["fingerprint"] and prev.get("description"):
                for key in ("description", "description_fa", "keywords", "measures",
                            "dates", "categories", "identifiers", "confidence"):
                    if key in prev:
                        t[key] = prev[key]

        # LLM descriptions for non-empty tables that do not have one yet.
        # Descriptions are checkpointed per batch, so an interrupted build
        # (restart, provider outage) resumes without redoing finished tables.
        checkpoint = _load_checkpoint(db_name)
        for n in non_empty:
            saved = checkpoint.get(n)
            if (not tables[n].get("description") and isinstance(saved, dict)
                    and saved.get("fingerprint") == tables[n]["fingerprint"]):
                _apply_description(tables[n], saved.get("item") or {})
        # Only the most important tables are described (cost control); the
        # rest still have full structure + profiles in the catalog.
        todo = [n for n in non_empty if not tables[n].get("description")]
        todo.sort(key=lambda n: -(tables[n].get("importance") or 0))
        described_already = sum(1 for n in non_empty if tables[n].get("description"))
        budget = max(0, DESCRIBE_MAX_TABLES - described_already)
        skipped_for_cost = max(0, len(todo) - budget)
        todo = todo[:budget] if DESCRIBE_ENABLED else []
        usage_before = dict(_USAGE.get(db_name, {}))
        llm_used = None
        if todo:
            state["progress"] = f"describing {len(todo)} tables"
            save_catalog(db_name, state)
            client, model = await _resolve_llm(llm_client, llm_model)
            if client is not None and model:
                llm_used = model
                evidences = [
                    (n, _table_evidence(n, tables[n], referenced_by.get(n, []), related.get(n, [])))
                    for n in todo
                ]
                done_count = [0]

                def _on_batch(got: Dict[str, Dict[str, Any]]) -> None:
                    for name, item in got.items():
                        checkpoint[name] = {"fingerprint": tables[name]["fingerprint"], "item": item}
                    _save_checkpoint(db_name, checkpoint)
                    done_count[0] += len(got)
                    state["progress"] = f"describing {done_count[0]}/{len(todo)} tables"
                    save_catalog(db_name, state)

                described = await _describe_tables(
                    client, model, evidences, on_batch=_on_batch, usage_key=db_name
                )
                for n, item in described.items():
                    _apply_description(tables[n], item)

        catalog = {
            "version": CATALOG_VERSION,
            "db_name": db_name,
            "dialect": dialect,
            "status": "ready",
            "partial": False,
            "progress": None,
            "error": None,
            "built_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "build_seconds": round(time.time() - started, 1),
            "llm_model": llm_used or previous.get("llm_model"),
            "llm_usage": _usage_since(db_name, usage_before),
            "undescribed_tables_for_cost": skipped_for_cost if DESCRIBE_ENABLED else len(non_empty),
            "total_tables": len(tables),
            "module_count": len(modules),
            "tables": tables,
            "relationships": relationships,
        }
        save_catalog(db_name, catalog)
        try:
            os.remove(_checkpoint_path(db_name))
        except OSError:
            pass
        logger.info(
            "DB catalog for %s ready: %d tables (%d non-empty), %d relationships, %.0fs",
            db_name, len(tables), len(non_empty), len(relationships), time.time() - started,
        )
        if EMBED_ENABLED:
            try:
                await build_embeddings(db_name, catalog)
            except Exception:
                logger.warning("Catalog embeddings failed for %s", db_name, exc_info=True)
        return catalog
    except Exception as e:
        logger.exception("DB catalog build failed for %s", db_name)
        if previous.get("status") == "ready":
            failed = {**previous, "error": str(e)[:500]}
        else:
            failed = {**state, "status": "failed", "error": str(e)[:500], "progress": None}
        save_catalog(db_name, failed)
        return failed


# --------------------------------------------------------------------------
# Using the catalog for a question
# --------------------------------------------------------------------------
_WORD = re.compile(r"[\w؀-ۿ]{3,}")


def rank_tables(catalog: Dict[str, Any], question: str, limit: int = 10) -> List[str]:
    """Rank non-empty catalog tables for a question by lexical overlap with
    table/column names and the LLM-written descriptions and keywords."""
    words = {w.casefold() for w in _WORD.findall(question or "")}
    if not words:
        return []
    scored = []
    for name, t in (catalog.get("tables") or {}).items():
        if not t.get("row_count"):
            continue
        hay_strong = " ".join([_leaf(name)] + [k.casefold() for k in t.get("keywords") or []])
        hay_weak = " ".join([
            (t.get("description") or "").casefold(),
            (t.get("description_fa") or "").casefold(),
            " ".join(c["name"].casefold() for c in t.get("columns", [])),
        ])
        score = 0.0
        for w in words:
            if w in hay_strong:
                score += 3
            elif w in hay_weak:
                score += 1
        if score:
            scored.append((score + 0.1 * (t.get("importance") or 0), name))
    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:limit]]


def is_usable(catalog: Optional[Dict[str, Any]]) -> bool:
    """A catalog can be used for prompts when it is ready, or when a first
    build has already published its metadata (partial)."""
    return bool(
        catalog
        and catalog.get("tables")
        and (catalog.get("status") == "ready" or catalog.get("partial"))
    )


def render_table_index(catalog: Dict[str, Any], max_chars: int = 20000) -> str:
    tables = [(n, t) for n, t in (catalog.get("tables") or {}).items() if t.get("row_count")]
    tables.sort(key=lambda x: (-(x[1].get("importance") or 0), -(x[1].get("row_count") or 0)))
    lines = []
    used = 0
    for n, t in tables:
        desc = t.get("description") or ""
        kw = ", ".join((t.get("keywords") or [])[:6])
        line = f"- {n} ({t.get('row_count'):,} rows)"
        if desc:
            line += f": {desc}"
        if kw:
            line += f" [{kw}]"
        if used + len(line) > max_chars:
            lines.append(f"- ... {len(tables) - len(lines)} more non-empty tables omitted")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


def render_table_details(catalog: Dict[str, Any], names: Iterable[str], max_chars: int = 24000) -> str:
    tables = catalog.get("tables") or {}
    rels = catalog.get("relationships") or []
    out = []
    used = 0
    for n in names:
        t = tables.get(n)
        if not t:
            continue
        lines = [f"### {n} — {t.get('row_count', 0):,} rows"]
        if t.get("description"):
            lines.append(t["description"])
        if t.get("comment"):
            lines.append(f"comment: {t['comment']}")
        if t.get("primary_key"):
            lines.append("primary key: " + ", ".join(t["primary_key"]))
        for key in ("measures", "dates", "categories", "identifiers"):
            if t.get(key):
                lines.append(f"{key}: " + ", ".join(t[key]))
        col_lines = []
        for c in t.get("columns", []):
            p = c.get("profile") or {}
            if p.get("null_ratio", 0) >= 0.999:
                continue  # column never filled — omit to save space
            s = f"{c['name']} {c.get('type','')}"
            extra = []
            if c.get("comment"):
                extra.append(c["comment"][:80])
            if p.get("values"):
                src = "" if p.get("values_source") == "exact" else " (sample)"
                extra.append("values" + src + ": " + ", ".join(f"{v} ({n:,})" for v, n in p["values"][:12]))
            if p.get("looks_like") == "date_string":
                extra.append(f"date string like '{p.get('format_example')}', range {p.get('min')}..{p.get('max')}")
            elif "min" in p and "max" in p:
                src = " (sample)" if p.get("range_source") == "sample" else ""
                extra.append(f"range{src} {p['min']}..{p['max']}")
            if extra:
                s += " — " + "; ".join(extra)
            col_lines.append(s)
        lines.append("columns:\n  " + "\n  ".join(col_lines))
        links: List[str] = []
        for r in rels:
            if r["from"] == n:
                item = f"{r['from_column']} = {r['to']}.{r['to_column']} ({r['source']})"
            elif r["to"] == n:
                item = f"{r['to_column']} = {r['from']}.{r['from_column']} ({r['source']})"
            else:
                continue
            if item not in links:
                links.append(item)
            if len(links) >= 12:
                break
        if links:
            lines.append("links: " + "; ".join(links))
        block = "\n".join(lines)
        if used + len(block) > max_chars:
            break
        out.append(block)
        used += len(block)
    return "\n\n".join(out)


# --------------------------------------------------------------------------
# Semantic table search (local embedding model)
# --------------------------------------------------------------------------
_EMBEDDINGS_GETTER: Optional[Callable[[], Any]] = None
_EMB_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_EMB_BUILDING: set = set()
_CONTEXT_TAG = re.compile(r"^\s*(\[[^\]]{1,200}\]\s*)+")


def set_embeddings_getter(getter: Optional[Callable[[], Any]]) -> None:
    """Register how to obtain the embedding model (set by the datasource service)."""
    global _EMBEDDINGS_GETTER
    if getter is not None:
        _EMBEDDINGS_GETTER = getter


def _get_embeddings() -> Any:
    if not EMBED_ENABLED or _EMBEDDINGS_GETTER is None:
        return None
    try:
        return _EMBEDDINGS_GETTER()
    except Exception:
        logger.warning("Embedding model not available for catalog search", exc_info=True)
        return None


def embeddings_path(db_name: str) -> str:
    return os.path.join(_catalog_dir(), f"{_safe(db_name)}.embeddings.json")


_EMBEDDING_MODEL_LABEL_CACHE: Dict[int, Optional[str]] = {}
# Semantic vectors from different embedding providers live in different
# spaces; cosine across a provider switch is meaningless. Store the model
# identifier next to the vectors and force a rebuild when it changes.


def _embedding_model_label(embeddings: Any) -> Optional[str]:
    """Human-readable identifier of the embedding model currently in use."""
    if embeddings is None:
        return None
    key = id(embeddings)
    if key in _EMBEDDING_MODEL_LABEL_CACHE:
        return _EMBEDDING_MODEL_LABEL_CACHE[key]
    label: Optional[str] = None
    for attr in ("model_name", "model", "model_name_or_path", "provider"):
        value = getattr(embeddings, attr, None)
        if isinstance(value, str) and value.strip():
            label = value.strip()
            break
        if isinstance(value, property):
            continue
    _EMBEDDING_MODEL_LABEL_CACHE[key] = label
    return label


def _embedding_text(name: str, t: Dict[str, Any]) -> str:
    cols = [c["name"] for c in t.get("columns", [])
            if (c.get("profile") or {}).get("null_ratio", 0) < 0.999][:60]
    parts = [name, _leaf(name)]
    if t.get("description"):
        parts.append(t["description"])
    if t.get("comment"):
        parts.append(str(t["comment"]))
    if t.get("keywords"):
        parts.append("keywords: " + ", ".join(t["keywords"]))
    parts.append("columns: " + ", ".join(cols))
    return "\n".join(parts)[:2000]


def load_embeddings(db_name: str) -> Dict[str, Any]:
    path = embeddings_path(db_name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _EMB_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.warning("Could not read catalog embeddings %s", path, exc_info=True)
        return {}
    _EMB_CACHE[path] = (mtime, data)
    return data


def _unit(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [round(x / norm, 5) for x in vec]


def embeddings_stale(db_name: str, catalog: Optional[Dict[str, Any]]) -> bool:
    """True when some non-empty table has no up-to-date vector."""
    if not catalog or not EMBED_ENABLED:
        return False
    data = load_embeddings(db_name) or {}
    # Rebuild when vectors were produced by a different embedding model:
    # cross-provider cosine similarity is meaningless.
    current_label = _embedding_model_label(_get_embeddings())
    if current_label and data.get("model") != current_label:
        return True
    items = data.get("items") or {}
    for name, t in (catalog.get("tables") or {}).items():
        if not t.get("row_count"):
            continue
        h = hashlib.sha1(_embedding_text(name, t).encode("utf-8")).hexdigest()[:16]
        if (items.get(name) or {}).get("h") != h:
            return True
    return False


async def build_embeddings(db_name: str, catalog: Dict[str, Any], embeddings: Any = None) -> int:
    """Embed every non-empty table whose text changed. Returns #tables embedded."""
    embeddings = embeddings or _get_embeddings()
    if embeddings is None or not catalog:
        return 0
    old = load_embeddings(db_name) or {}
    items: Dict[str, Any] = dict(old.get("items") or {})
    todo: List[Tuple[str, str, str]] = []
    keep: Dict[str, Any] = {}
    for name, t in (catalog.get("tables") or {}).items():
        if not t.get("row_count"):
            continue
        text = _embedding_text(name, t)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        current_label = _embedding_model_label(embeddings)
        stored = items.get(name) or {}
        # A provider switch invalidates stored vectors even when the text
        # hash still matches (vectors from different models are not
        # comparable).
        if stored.get("h") == h and (
            current_label is None or old.get("model") == current_label
        ):
            keep[name] = items[name]
        else:
            todo.append((name, text, h))
    started = time.time()
    done = 0
    for i in range(0, len(todo), max(1, EMBED_BATCH)):
        batch = todo[i:i + max(1, EMBED_BATCH)]
        vectors = await embeddings.aembed_documents([b[1] for b in batch])
        for (name, _text, h), vec in zip(batch, vectors):
            keep[name] = {"h": h, "v": _unit(list(vec))}
            done += 1
    data = {
        "version": 1,
        "model": _embedding_model_label(embeddings),
        "built_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "items": keep,
    }
    path = embeddings_path(db_name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    logger.info(
        "Catalog embeddings for %s: %d embedded, %d total, %.0fs",
        db_name, done, len(keep), time.time() - started,
    )
    return done


def schedule_embeddings(db_name: str, delay_seconds: float = 0.0) -> bool:
    """Embed catalog tables in the background (no-op when up to date)."""
    if not EMBED_ENABLED or _EMBEDDINGS_GETTER is None:
        return False
    catalog = load_catalog(db_name)
    if not catalog or catalog.get("status") != "ready" or not embeddings_stale(db_name, catalog):
        return False
    with _LOCK:
        if db_name in _EMB_BUILDING or db_name in _BUILDING:
            return False
        _EMB_BUILDING.add(db_name)

    async def _runner():
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await build_embeddings(db_name, load_catalog(db_name) or catalog)
        except Exception:
            logger.warning("Catalog embedding build failed for %s", db_name, exc_info=True)
        finally:
            with _LOCK:
                _EMB_BUILDING.discard(db_name)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        task = loop.create_task(_runner())
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return True
    if _MAIN_LOOP is not None and _MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(_runner(), _MAIN_LOOP)
        return True
    with _LOCK:
        _EMB_BUILDING.discard(db_name)
    return False


async def semantic_rank_tables(
    db_name: str,
    catalog: Dict[str, Any],
    question: str,
    limit: int = 8,
    embeddings: Any = None,
) -> List[str]:
    """Rank non-empty tables by meaning (cosine similarity of embeddings).

    Returns [] when embeddings are not built yet or the model is unavailable —
    callers then fall back to the lexical ranker.
    """
    items = (load_embeddings(db_name) or {}).get("items") or {}
    if not items or not question:
        return []
    embeddings = embeddings or _get_embeddings()
    if embeddings is None:
        return []
    text = _CONTEXT_TAG.sub("", question).strip() or question
    try:
        qv = await asyncio.wait_for(embeddings.aembed_query(text), EMBED_QUERY_TIMEOUT)
    except Exception:
        logger.warning("Question embedding failed; using lexical table ranking", exc_info=True)
        return []
    q = _unit(list(qv))
    tables = catalog.get("tables") or {}
    scored = []
    for name, it in items.items():
        t = tables.get(name)
        if not t or not t.get("row_count"):
            continue
        v = it.get("v") or []
        if len(v) != len(q):
            continue
        sim = sum(a * b for a, b in zip(q, v))
        scored.append((sim + 0.002 * (t.get("importance") or 0), name))
    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:limit]]

