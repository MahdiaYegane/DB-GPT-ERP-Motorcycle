# SQL Server Datasource — SOR / sorooshan1040

Read-only analytical access to the Sorooshan ERP SQL Server from DB-GPT chat
(`sql_query` tool). All access is backend-only; nothing credential-like ever
reaches the frontend.

## Connection

| Item | Value |
|---|---|
| Server / instance | `172.16.15.5:1433`, instance `SOR` |
| Database | `sorooshan1040` |
| Auth | SQL Server auth, user `AI` (SELECT/read-only) |
| Password source | `SQL_SERVER_PASSWORD` env var — **never hardcoded, never committed** |

At runtime the password is resolved from the environment in two places:

- DB-GPT chat datasource: `connect_config.db_pwd` stores the literal
  `${env:SQL_SERVER_PASSWORD}`; `ConnectorManager._build_connector` resolves
  it via `_resolve_env_vars` at connect time.
- Server launcher `start_dbgpt_server.bat` (outside git) sets
  `SQL_SERVER_PASSWORD` before starting the webserver.

`.env` additions (`MSSQL_HOST/PORT/DB/USER`, no password) exist for future
ERP-style module use. Verify with: `git grep -l "Kavir@AI"` must return
nothing.

## Driver selection (important)

`MSSQLConnector.from_uri_db` defaults to `mssql+pymssql` (FreeTDS). On this
network FreeTDS cannot reach the host (`DB-Lib error 20009`), and ODBC
Driver 18 is not installed — but the legacy **`SQL Server` (SQLSRV32)**
driver is. So this datasource's `ext_config` selects pyodbc:

```json
{"driver": "mssql+pyodbc", "odbc_driver": "SQL Server", "pool_size": 5,
 "max_overflow": 10, "pool_timeout": 30, "pool_recycle": 3600,
 "pool_pre_ping": true}
```

`ConnectorManager._build_connector` forwards `driver`/`odbc_driver` from
`ext_config` to `from_uri_db` **for MSSQL only**; every other connector keeps
its previous call shape. If ODBC Driver 18 is installed later, change
`odbc_driver` to `ODBC Driver 18 for SQL Server` for TLS 1.2+ support
(SQLSRV32 negotiates older TLS).

Related commits: `pyodbc` + `pymssql` added to the `kavir` conda env
(`pip install pyodbc pymssql`).

## Verified startup test

```sql
SELECT @@SERVERNAME AS server_name, DB_NAME() AS database_name,
       SYSTEM_USER AS login_name, GETDATE() AS server_time;
-- SOR / sorooshan1040 / AI
SELECT TOP 1 * FROM dbo.account;  -- 33 columns, table has 94,668 rows
```

## Schema snapshot (2026-09-21, read-only discovery)

- **1241 base tables, 0 views.** Key tables: `dbo.account` (94,668 rows),
  `dbo.accbooks` (6 rows).
- `dbo.account` (33 cols): `sql_account_id int` PK, `prcode char(10)`,
  `accountname varchar(60)`, `s_regdate char(10)`, `m_regdate datetime`,
  `debit/credit numeric(19,2)`, plus code columns (`fccode`, `factorsno`,
  `cncode`, `eccode`, `rmcode`, `decode`, `wvcode`, `rjcode`, `blcode`),
  `kindwork char(1)`, `des/sanadno/index*`, `dblaccleveloneid numeric(15,0)`,
  `regname/regpcode/editname/editpcode`, `documentcode char(15)`,
  `amcode char(10)`, `lasteditdatetime datetime`. All `NOT NULL`.
- 1182 PKs across the DB, only **2 FKs** (`State/JobParameter → Job`) —
  joins are application-level; do not assume FK coverage.
- `dbo.account` has 19 indexes.
- Collation `Arabic_CI_AS` (DB and server). **Persian text is stored in
  `varchar` (non-Unicode) columns**, so proper decoding depends on the
  `Arabic_*` code page — e.g. `accountname` reads as mojibake under a
  UTF-8 client. Prefer server-side handling and never re-encode blindly.
- Date columns are mixed: `char(10)` Jalali-ish strings (`s_regdate`) vs
  real `datetime` (`m_regdate`). Inspect per-column before treating any
  date as Gregorian.

## Chat usage notes

- The agent sees this datasource as `sorooshan1040` (type `mssql`).
  `sql_query` is SELECT-only; keep queries `TOP`-limited.
- First chat after (re)start warms the connector (SQLAlchemy reflection
  over 1241 tables takes a while); results are cached 30 minutes
  (`ConnectorManager._CONNECTOR_CACHE_DEFAULT_TTL`).
- Persian answers need the model, not the driver: ask explicitly for
  Persian output when needed.
