"""sql_query tool — read-only SQL query against the selected database."""

import json
import logging
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
)
from typing import Any, Dict, Optional

from dbgpt.agent.resource.tool.base import tool

logger = logging.getLogger(__name__)

# Hard cap per query so one heavy scan can't stall the agent turn (and the
# user's "time out" perception). Dialect-agnostic: enforced with a worker
# thread because not every driver honors statement timeouts.
SQL_QUERY_TIMEOUT_SECONDS = 90

# Shared executor for all SQL tool calls process-wide. Creating a new
# ThreadPoolExecutor per query leaked threads and paid pool startup cost on
# every turn; timeouts still leave a thread busy only while the driver runs.
_SQL_EXECUTOR = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="dbgpt-sql-tool"
)


def _is_missing_name_error(message: str) -> bool:
    lowered = message.lower()
    return "invalid column name" in lowered or "invalid object name" in lowered


def make_sql_query(react_state: Dict[str, Any], database_connector: Optional[Any]):
    def _run_sql(sql: str) -> str:
        """Execute a read-only SQL query against the selected database."""
        if database_connector is None:
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": "No database selected. Please select a data source in the left panel.",
                        }
                    ]
                },
                ensure_ascii=False,
            )

        sql_stripped = sql.strip().rstrip(";")
        sql_upper = sql_stripped.upper().lstrip()
        forbidden = [
            "INSERT",
            "UPDATE",
            "DELETE",
            "DROP",
            "ALTER",
            "TRUNCATE",
            "CREATE",
            "GRANT",
            "REVOKE",
        ]
        for kw in forbidden:
            if sql_upper.startswith(kw):
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": f"Security restriction: {kw} statements are not allowed. "
                                "Only SELECT queries are supported.",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )

        try:
            future = _SQL_EXECUTOR.submit(database_connector.run, sql_stripped)
            try:
                result = future.result(timeout=SQL_QUERY_TIMEOUT_SECONDS)
            except FutureTimeoutError:
                # NOTE: cancelling the Python future does not stop the query
                # server-side. Driver-level statement timeouts (pymssql
                # ``timeout=`` connect arg, see conn_mssql.py) plus this tool's
                # own cap keep runaway scans bounded.
                return json.dumps(
                    {
                        "chunks": [
                            {
                                "output_type": "text",
                                "content": (
                                    f"Query timed out after "
                                    f"{SQL_QUERY_TIMEOUT_SECONDS}s. "
                                    "Retry with a cheaper query: add TOP, "
                                    "filter on indexed columns, and avoid "
                                    "SELECT * on wide tables."
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if not result:
                return json.dumps(
                    {
                        "chunks": [
                            {"output_type": "text", "content": "Query returned no results."}
                        ]
                    },
                    ensure_ascii=False,
                )

            columns = result[0]
            col_names = [str(c[0]) if isinstance(c, tuple) else str(c) for c in columns]
            rows = result[1:]

            header = "| " + " | ".join(col_names) + " |"
            separator = "| " + " | ".join(["---"] * len(col_names)) + " |"
            md_rows = []
            for row in rows[:50]:
                # CHAR columns come back space-padded; trim them so the
                # model does not copy (and pay for) the padding.
                md_rows.append(
                    "| " + " | ".join(str(v).strip() for v in row) + " |"
                )
            table = "\n".join([header, separator] + md_rows)
            if len(rows) > 50:
                table += f"\n\n(Showing first 50 rows of {len(rows)} rows)"

            # Cap total output size so a single wide query can't blow out the
            # LLM context window. The full result remains available via the
            # ToolResultStorage persistence layer if it exceeds the threshold.
            MAX_SQL_OUTPUT_CHARS = 20_000
            if len(table) > MAX_SQL_OUTPUT_CHARS:
                table = (
                    table[:MAX_SQL_OUTPUT_CHARS]
                    + f"\n\n... [Output truncated at {MAX_SQL_OUTPUT_CHARS} chars. "
                    f"Total rows: {len(rows)}]"
                )

            return json.dumps(
                {"chunks": [{"output_type": "markdown", "content": table}]},
                ensure_ascii=False,
            )
        except Exception as e:
            message = str(e)
            # Self-correction hint: the model often guesses table/column
            # names (e.g. a "fullname" column that doesn't exist). Tell it
            # exactly how to discover real names so the NEXT attempt
            # succeeds instead of failing the same way.
            if _is_missing_name_error(message):
                message += (
                    " Suggestion: that table or column does not exist — do "
                    "not guess names. First run: SELECT COLUMN_NAME, "
                    "DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE "
                    "TABLE_NAME='<table>' ORDER BY ORDINAL_POSITION "
                    "(SQL Server), then retry with a real column."
                )
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"SQL execution failed: {message}",
                        }
                    ]
                },
                ensure_ascii=False,
            )


    @tool(
        description=(
            "Execute a SQL query against the user-selected database (SELECT only). "
            'Parameters: {"sql": "SELECT statement"}'
        )
    )
    def sql_query(sql: str) -> str:
        """Execute a read-only SQL query against the selected database."""
        # Same query already run for this question: return the earlier result
        # instead of hitting the database again, and say so, so the model
        # stops re-checking things it already knows.
        key = " ".join(str(sql or "").strip().rstrip(";").lower().split())
        cache = react_state.setdefault("_sql_result_cache", {})
        if key and key in cache:
            return (
                "NOTE: this exact query was already run earlier for this "
                "question; the result is unchanged (shown below). Do not run "
                "it again — use it, or move on / finish.\n" + cache[key]
            )
        result = _run_sql(sql)
        if key:
            cache[key] = result
        return result

    return sql_query
