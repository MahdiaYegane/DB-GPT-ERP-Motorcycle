"""sql_query tool — read-only SQL query against the selected database."""

import json
from typing import Any, Dict, Optional

from dbgpt.agent.resource.tool.base import tool


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
            result = database_connector.run(sql_stripped)
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
            return json.dumps(
                {
                    "chunks": [
                        {
                            "output_type": "text",
                            "content": f"SQL execution failed: {str(e)}",
                        }
                    ]
                },
                ensure_ascii=False,
            )

    return sql_query
