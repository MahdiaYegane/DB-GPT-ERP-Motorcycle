"""MSSQL connector."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type
from urllib.parse import quote, quote_plus

from sqlalchemy import text

from dbgpt.core.awel.flow import (
    TAGS_ORDER_HIGH,
    ResourceCategory,
    auto_register_resource,
)
from dbgpt.datasource.rdbms.base import RDBMSConnector, RDBMSDatasourceParameters
from dbgpt.util.i18n_utils import _


@auto_register_resource(
    label=_("MSSQL datasource"),
    category=ResourceCategory.DATABASE,
    tags={"order": TAGS_ORDER_HIGH},
    description=_(
        "Powerful, scalable, secure relational database system by Microsoft."
    ),
)
@dataclass
class MSSQLParameters(RDBMSDatasourceParameters):
    """MSSQL connection parameters."""

    __type__ = "mssql"
    driver: str = field(
        default="mssql+pymssql",
        metadata={
            "help": _("Driver name for MSSQL, default is mssql+pymssql."),
        },
    )

    def create_connector(self) -> "MSSQLConnector":
        """Create MS SQL connector."""
        return MSSQLConnector.from_parameters(self)


class MSSQLConnector(RDBMSConnector):
    """MSSQL connector."""

    db_type: str = "mssql"
    db_dialect: str = "mssql"
    driver: str = "mssql+pymssql"

    default_db = ["master", "model", "msdb", "tempdb", "modeldb", "resource", "sys"]

    @classmethod
    def param_class(cls) -> Type[MSSQLParameters]:
        """Return the parameter class."""
        return MSSQLParameters

    @classmethod
    def from_uri_db(
        cls,
        host: str,
        port: int,
        user: str,
        pwd: str,
        db_name: str,
        engine_args: Optional[dict] = None,
        driver: Optional[str] = None,
        odbc_driver: Optional[str] = None,
        **kwargs: Any,
    ) -> "MSSQLConnector":
        """Construct a SQLAlchemy engine, honoring a driver override.

        Args:
            host: Database host.
            port: Database port.
            user: Database user.
            pwd: Database password (URL-encoded when special chars present).
            db_name: Database name.
            engine_args: Optional SQLAlchemy engine arguments.
            driver: SQLAlchemy dialect+DBAPI selector. Defaults to
                ``mssql+pymssql``. Set to ``mssql+pyodbc`` on hosts where
                FreeTDS-based ``pymssql`` cannot reach the server (e.g. named
                instances / strict TLS) but a local ODBC driver can.
            odbc_driver: Local ODBC driver name used only with
                ``mssql+pyodbc`` (e.g. ``ODBC Driver 18 for SQL Server`` or
                the legacy ``SQL Server``). Passed as the ``driver`` query
                parameter of the connection URL.
        """
        selected = (driver or cls.driver or "").strip() or cls.driver
        if selected == "mssql+pyodbc":
            odbc = (odbc_driver or "ODBC Driver 18 for SQL Server").strip()
            db_url = (
                f"{selected}://{quote(user)}:{quote_plus(pwd)}"
                f"@{host}:{str(port)}/{db_name}"
                f"?driver={quote_plus(odbc)}"
            )
        else:
            db_url = (
                f"{selected}://{quote(user)}:{quote_plus(pwd)}"
                f"@{host}:{str(port)}/{db_name}"
            )
        return cls.from_uri(db_url, engine_args, **kwargs)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Init the MSSQL connector without eager full reflection.

        ``RDBMSConnector.__init__`` runs ``MetaData.reflect(bind=engine)``
        eagerly, which the legacy ``SQL Server`` ODBC driver cannot serve
        (``HY104`` on the reflection queries). Reflection targets all 1241
        tables here and is never needed up front: the table set is synced
        lazily on first use via :meth:`_sync_tables_from_db`, and per-table
        columns/keys are read on demand (``get_fields``/``get_columns``/
        ``get_indexes``). Skipping the base ``__init__`` entirely and
        initializing only the session/metadata state keeps construction to a
        single cheap connection.
        """
        import weakref

        from sqlalchemy import MetaData, inspect
        from sqlalchemy.orm import scoped_session, sessionmaker

        engine = kwargs.get("engine", args[0] if args else None)
        schema = kwargs.get("schema", args[1] if len(args) > 1 else None)
        metadata = kwargs.get("metadata")
        ignore_tables = kwargs.get("ignore_tables")
        include_tables = kwargs.get("include_tables")
        sample_rows_in_table_info = kwargs.get("sample_rows_in_table_info", 3)
        indexes_in_table_info = kwargs.get("indexes_in_table_info", False)
        custom_table_info = kwargs.get("custom_table_info") or {}
        view_support = kwargs.get("view_support", False)

        self._is_closed = False
        self._engine = engine
        self._schema = schema
        if include_tables and ignore_tables:
            raise ValueError("Cannot specify both include_tables and ignore_tables")

        self._inspector = inspect(engine)
        session_factory = sessionmaker(bind=engine)
        session_manages = scoped_session(session_factory)
        self._db_sessions = session_manages
        self._sessions = weakref.WeakSet()

        self.view_support = view_support
        self._usable_tables = set()
        self._include_tables = set(include_tables) if include_tables else set()
        self._ignore_tables = set(ignore_tables) if ignore_tables else set()
        self._custom_table_info = custom_table_info
        self._sample_rows_in_table_info = sample_rows_in_table_info
        self._indexes_in_table_info = indexes_in_table_info

        self._metadata = metadata or MetaData()
        # Lazy table set: synced on first get_table_names() call.
        self._all_tables = set()
        self._tables_synced = False

    def _sync_tables_from_db(self) -> Iterable[str]:
        """Read table information without SQLAlchemy reflection.

        The default ``RDBMSConnector`` implementation uses SQLAlchemy
        reflection (``inspector.get_table_names``), which the legacy
        ``SQL Server`` ODBC driver (SQLSRV32) cannot serve — its parameter
        binding fails with ``HY104 Invalid precision value`` on the
        reflection queries. A plain ``INFORMATION_SCHEMA.TABLES`` query
        works fine on the same driver, so override the sync to use it.

        ``_schema`` semantics mirror the base: reflect tables for the
        connected catalog (the engine URL database).
        """
        with self.session_scope() as session:
            cursor = session.execute(
                text(
                    "SELECT TABLE_SCHEMA + '.' + TABLE_NAME "
                    "FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_TYPE = 'BASE TABLE'"
                )
            )
            self._all_tables = {row[0] for row in cursor.fetchall()}
            self._tables_synced = True
            return self._all_tables

    def table_simple_info(self) -> Iterable[str]:
        """Get table simple info."""
        _tables_sql = """
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE
                TABLE_TYPE='BASE TABLE'
            """
        with self.session_scope() as session:
            cursor = session.execute(text(_tables_sql))
            tables_results = cursor.fetchall()
            results = []
            for row in tables_results:
                table_name = row[0]
                _sql = f"""
                    SELECT COLUMN_NAME, DATA_TYPE FROM INFORMATION_SCHEMA.COLUMNS WHERE
                     TABLE_NAME='{table_name}'
                """
                cursor_colums = session.execute(text(_sql))
                colum_results = cursor_colums.fetchall()
                table_colums = []
                for row_col in colum_results:
                    field_info = list(row_col)
                    table_colums.append(field_info[0])
                results.append(f"{table_name}({','.join(table_colums)});")
            return results

    def get_fields(self, table_name, db_name=None) -> List[Tuple]:
        """Get column fields about specified table.

        SQL Server's INFORMATION_SCHEMA does NOT have COLUMN_TYPE or
        COLUMN_COMMENT (those are MySQL-only). Override the base
        ``RDBMSConnector.get_fields`` MySQL-shaped query with a SQL Server
        variant that returns the same 5-tuple shape:
        ``(column_name, data_type, default, is_nullable, comment)``.

        Column comments come from ``sys.extended_properties`` with name
        ``MS_Description`` (the standard MS convention).

        ``table_name`` may include a schema prefix like ``dbo.MyTable``;
        if absent, defaults to ``dbo``. The ``db_name`` argument is ignored
        because the active connection is already scoped to a database, and
        upstream callers in editor APIs sometimes pass the catalog name in
        a position where INFORMATION_SCHEMA expects the schema.
        """
        if "." in table_name:
            schema_name, pure_table_name = table_name.split(".", 1)
        else:
            schema_name = "dbo"
            pure_table_name = table_name

        with self.session_scope() as session:
            query = """
            SELECT
                c.COLUMN_NAME,
                c.DATA_TYPE,
                c.COLUMN_DEFAULT,
                c.IS_NULLABLE,
                CAST(ep.value AS NVARCHAR(MAX)) AS COLUMN_COMMENT
            FROM INFORMATION_SCHEMA.COLUMNS c
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = OBJECT_ID(
                       QUOTENAME(c.TABLE_SCHEMA) + '.' + QUOTENAME(c.TABLE_NAME))
                AND ep.minor_id = COLUMNPROPERTY(
                       OBJECT_ID(QUOTENAME(c.TABLE_SCHEMA) + '.'
                                 + QUOTENAME(c.TABLE_NAME)),
                       c.COLUMN_NAME, 'ColumnId')
                AND ep.class = 1
                AND ep.name = 'MS_Description'
            WHERE c.TABLE_SCHEMA = :schema
              AND c.TABLE_NAME   = :table
            ORDER BY c.ORDINAL_POSITION
            """
            cursor = session.execute(
                text(query), {"schema": schema_name, "table": pure_table_name}
            )
            fields = cursor.fetchall()
            return [
                (
                    self._decode_if_bytes(f[0]),
                    self._decode_if_bytes(f[1]),
                    self._decode_if_bytes(f[2]) if f[2] is not None else None,
                    self._decode_if_bytes(f[3]),
                    self._decode_if_bytes(f[4]) if f[4] is not None else "",
                )
                for f in fields
            ]

    def get_users(self):
        # sys.server_principals via SQLAlchemy reflection parameters trips
        # the same legacy-driver HY104 binding bug; raw result access is fine.
        try:
            with self.session_scope() as session:
                cursor = session.execute(
                    text(
                        "SELECT name FROM sys.server_principals "
                        "WHERE type_desc = 'SQL_LOGIN'"
                    )
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception:
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "MSSQL get_users unavailable on this driver; returning []",
                exc_info=True,
            )
            return []

    def get_grants(self):
        # Server-level permission catalog; guard the same way as get_users:
        # a driver that cannot run it must not break db-summary indexing.
        try:
            with self.session_scope() as session:
                query = """
                SELECT
                    CASE WHEN perm.state <> 'W' THEN perm.state_desc ELSE 'GRANT WITH
                     GRANT OPTION' END AS [Permission],
                    perm.permission_name AS [Permission Name],
                    CASE
                        WHEN perm.class = 0 THEN 'SERVER'
                        WHEN perm.class = 1 THEN OBJECT_NAME(perm.major_id)
                        WHEN perm.class = 3 THEN SCHEMA_NAME(perm.major_id)
                        ELSE CAST(perm.class AS VARCHAR)
                    END AS [Securable],
                    princ.name AS [Principal]
                FROM
                    sys.server_permissions perm
                    JOIN sys.server_principals princ ON perm.grantee_principal_id =
                    princ.principal_id
                """
                cursor = session.execute(text(query))
                return cursor.fetchall()
        except Exception:
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "MSSQL get_grants unavailable on this driver; returning []",
                exc_info=True,
            )
            return []

    def _decode_if_bytes(self, value):
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return value

    def get_charset(self):
        with self.session_scope() as session:
            query = (
                "SELECT DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS DatabaseCollation"
            )
            cursor = session.execute(text(query))
            result = cursor.fetchone()

            if result and result[0]:
                collation = self._decode_if_bytes(result[0])
                parts = collation.split("_")
                if len(parts) >= 2:
                    return parts[1]
                return collation

            return "SQL_Server_Default"

    def get_collation(self):
        with self.session_scope() as session:
            cursor = session.execute(
                text("SELECT SERVERPROPERTY('Collation') AS DatabaseCollation")
            )
            collation = cursor.fetchone()[0]
            return collation

    def get_table_names(self):
        # Prefer the already-synced in-memory table set (populated by the
        # HY104-safe _sync_tables_from_db above) over re-running reflection.
        if self._include_tables:
            return self._include_tables
        if not getattr(self, "_tables_synced", False):
            self._sync_tables_from_db()
        if getattr(self, "_all_tables", None):
            return sorted(self._all_tables - self._ignore_tables)
        tables = []

        with self.session_scope() as session:
            query = """
            SELECT 
                TABLE_SCHEMA + '.' + TABLE_NAME AS full_table_name
            FROM 
                INFORMATION_SCHEMA.TABLES
            WHERE 
                TABLE_TYPE = 'BASE TABLE'
                AND TABLE_CATALOG = DB_NAME()
            """

            cursor = session.execute(text(query))
            tables = [row[0] for row in cursor.fetchall()]

            if not tables:
                query = """
                SELECT 
                    SCHEMA_NAME(schema_id) + '.' + name AS full_table_name
                FROM 
                    sys.tables
                """
                cursor = session.execute(text(query))
                tables = [row[0] for row in cursor.fetchall()]

            if not tables:
                query = """
                SELECT 
                    name AS table_name
                FROM 
                    sys.tables
                """
                cursor = session.execute(text(query))
                tables = [row[0] for row in cursor.fetchall()]

            return tables

    def get_columns(self, table_name: str):
        if "." in table_name:
            schema_name, pure_table_name = table_name.split(".", 1)
        else:
            schema_name = "dbo"
            pure_table_name = table_name

        with self.session_scope() as session:
            query = """
            SELECT 
                COLUMN_NAME AS name,
                DATA_TYPE AS type,
                CASE WHEN IS_NULLABLE = 'YES' THEN 1 ELSE 0 END AS nullable,
                COLUMN_DEFAULT AS default_value,
                CHARACTER_MAXIMUM_LENGTH AS max_length
            FROM 
                INFORMATION_SCHEMA.COLUMNS
            WHERE 
                TABLE_SCHEMA = :schema
                AND TABLE_NAME = :table
            ORDER BY 
                ORDINAL_POSITION
            """
            cursor = session.execute(
                text(query), {"schema": schema_name, "table": pure_table_name}
            )
            results = cursor.fetchall()

            columns = []
            for row in results:
                name = row[0].decode("utf-8") if isinstance(row[0], bytes) else row[0]
                col_type = (
                    row[1].decode("utf-8") if isinstance(row[1], bytes) else row[1]
                )
                column = {
                    "name": name,
                    "type": col_type,
                    "nullable": bool(row[2]),
                }

                if row[3] is not None:
                    default = (
                        row[3].decode("utf-8") if isinstance(row[3], bytes) else row[3]
                    )
                    column["default"] = default

                if row[4] is not None:
                    column["max_length"] = row[4]
                columns.append(column)

            return columns

    def get_indexes(self, table_name: str):
        if "." in table_name:
            schema_name, pure_table_name = table_name.split(".", 1)
        else:
            schema_name = "dbo"
            pure_table_name = table_name

        with self.session_scope() as session:
            query = """
            SELECT
                i.name AS index_name,
                c.name AS column_name,
                i.is_unique AS is_unique,
                i.is_primary_key AS is_primary_key
            FROM
                sys.indexes i
            INNER JOIN
                sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id 
                = ic.index_id
            INNER JOIN
                sys.columns c ON ic.object_id = c.object_id AND ic.column_id 
                = c.column_id
            INNER JOIN
                sys.tables t ON i.object_id = t.object_id
            INNER JOIN
                sys.schemas s ON t.schema_id = s.schema_id
            WHERE
                t.name = :table
                AND s.name = :schema
                AND i.name IS NOT NULL
            ORDER BY
                i.name, ic.key_ordinal
            """
            cursor = session.execute(
                text(query), {"schema": schema_name, "table": pure_table_name}
            )
            results = cursor.fetchall()

            index_dict = {}
            for row in results:
                index_name = (
                    row[0].decode("utf-8") if isinstance(row[0], bytes) else row[0]
                )
                column_name = (
                    row[1].decode("utf-8") if isinstance(row[1], bytes) else row[1]
                )
                is_unique = bool(row[2])
                is_primary_key = bool(row[3])
                if index_name not in index_dict:
                    index_dict[index_name] = {
                        "name": index_name,
                        "column_names": [],
                        "unique": is_unique,
                        "primary": is_primary_key,
                    }

                index_dict[index_name]["column_names"].append(column_name)

            return list(index_dict.values())
