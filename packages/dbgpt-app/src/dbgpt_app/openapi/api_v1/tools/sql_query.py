"""sql_query tool — read-only SQL query against the selected database."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Dict, Optional

from dbgpt.agent.resource.tool.base import tool

# Hard cap per query so one heavy scan can't stall the agent turn (and the
# user's "time out" perception). Dialect-agnostic: enforced with a worker
# thread because not every driver honors statement timeouts.
SQL_QUERY_TIMEOUT_SECONDS = 90


def _is_missing_name_error(message: str) -> bool:
    lowered = message.lower()
    return "invalid column name" in lowered or "invalid object name" in lowered


def make_sql_query(react_state: Dict[str, Any], database_connector: Optional[Any]):
    @tool(
        description=(
            "Execute a SQL query against the user-selected database (SELECT only). "
            'Parameters: {"sql": "SELECT statement"}'
        )
    )
    def sql_query(sql: str) -> str:
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
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(database_connector.run, sql_stripped)
                try:
                    result = future.result(timeout=SQL_QUERY_TIMEOUT_SECONDS)
                except FutureTimeoutError:
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
                md_rows.append("| " + " | ".join(str(v) for v in row) + " |")
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

    return sql_query
