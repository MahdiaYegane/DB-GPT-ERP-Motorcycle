# ERP Motorcycle — Direct MariaDB Integration (Production Report)

**DBMS:** MariaDB **10.4.32-MariaDB** (InnoDB, `utf8mb4_unicode_ci`)  
**Host:** `172.16.1.131:3306` **Database:** `erp_motorcycle` **User:** `a.mansourpour@CL-1111` (client `172.16.1.133`)  
**Privilege:** `GRANT ALL PRIVILEGES ON *.* TO a.mansourpour@cl-1111` — verified `USER()` / `CURRENT_USER()`  
**Connection:** Direct TCP `mysql+pymysql://a.mansourpour:***@172.16.1.131:3306/erp_motorcycle?charset=utf8mb4` — **no local file DB created**  
**Proof:** `pilot/meta_data/erp_motorcycle.db` does **not** exist; only `pilot/meta_data/dbgpt.db` exists. `ENGINE` reports safe URL, `SHOW DATABASES` lists `erp_motorcycle` on remote host.

---

## 1. Files Created / Modified

### Created
```
.env.example                          # template (commit)
.env                                  # local (ignored via .gitignore .env*), direct credentials
erp/__init__.py
erp/config.py                         # env-based config, dynamic URL, never logs password
erp/database.py                       # SQLAlchemy+PyMySQL engine, pooling, utf8mb4, timeout
erp/health.py                         # health check (SELECT 1 / VERSION / DATABASE / USER) with category
erp/models/__init__.py
erp/models/erp_models.py              # 21 ORM models (read-only mirror, no DDL)
erp/repositories/base.py              # generic read-only, parameterized queries only
erp/repositories/product_repository.py
erp/repositories/customer_repository.py
erp/repositories/inventory_repository.py
erp/repositories/sales_repository.py
erp/repositories/branch_repository.py
erp/repositories/__init__.py
erp/services/product_service.py
erp/services/inventory_service.py
erp/services/sales_service.py
erp/services/reporting_service.py
erp/services/__init__.py
test_database_connection.py           # diagnostic script (read-only, --json)
docs/ERP_INTEGRATION.md               # this report
docs/ERP_SCHEMA_REPORT.md             # full schema per phase 15 (generated below)
C:\Users\...\Temp\opencode\erp_full_scan_utf8.txt  # raw discovery dump (not committed)
```

### Modified
- `pyproject.toml` — add deps `sqlalchemy`, `pymysql`, `python-dotenv` to `[project.dependencies]` or `requirements` (see install)
- `.gitignore` already contains `.env*` (line 9) — `.env` is ignored, `.env.example` is force-tracked with `git add -f .env.example` if needed (`.env*` shadows it; see caveat)
- `db` health hook (optional) — `erp.health.run_health_check()` can be wired to app startup via `ThreadPoolExecutor` (non-blocking)

### Not touched
- No `CREATE / ALTER / DROP / TRUNCATE` executed — 0 DDL. All inspection was `SHOW` / `DESCRIBE` / `SELECT ... LIMIT 5`.

---

## 2. Required Python Packages

```toml
# add to pyproject.toml dependencies or pip install
sqlalchemy>=2.0          # ORM + pooling
pymysql==2.2.8           # MariaDB wire compatible (pure python)
python-dotenv            # .env loader (already in env via 0.8.2 + python-dotenv 1.2.x)
# existing
chromadb, openai, ollama, tiktoken  # already present
```

Install:
```bash
pip install pymysql sqlalchemy python-dotenv
# or
uv pip install pymysql sqlalchemy python-dotenv
```

Driver choice: **SQLAlchemy + PyMySQL** satisfies requirement. Alternatives `mysql-connector-python` also compatible but PyMySQL is pure-python, no C build, best for pooling+utf8mb4 with MariaDB 10.4.

---

## 3. Environment Configuration

### `.env.example`
```ini
DB_HOST=172.16.1.131
DB_PORT=3306
DB_NAME=erp_motorcycle
DB_USER=a.mansourpour
DB_PASSWORD=
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_RECYCLE=3600
DB_CONNECT_TIMEOUT=5
DB_CHARSET=utf8mb4
```

### `.env` (local, same values)
Already created and loaded by `erp/config.py` (with fallback manual parser if `python-dotenv` missing).

### URL construction (dynamic, never hard-coded)
```python
from erp.config import get_database_url
# mysql+pymysql://a.mansourpour:@172.16.1.131:3306/erp_motorcycle?charset=utf8mb4
# When password is empty we emit "user:@" explicitly (PyMySQL-compatible)
get_database_url()
# safe for logs:
# mysql+pymysql://a.mansourpour:***@172.16.1.131:3306/erp_motorcycle?charset=utf8mb4
```

### `.gitignore`
Already: `.env*` (line 9) — caveat: this also ignores `.env.example`. Solution: `git add -f .env.example` or change to `.env` + `!.env.example`. Left as-is to avoid breaking existing ignores.

---

## 4. Connection Architecture

```
UI / API (DB-GPT construct/models, agentic_data_api, future ERP pages)
  |
  v
Services (erp/services/*)  — thin, Persian-aware, no SQL
  product_service, inventory_service, sales_service, reporting_service
  |
  v
Repositories (erp/repositories/*)  — parameterized queries only, LIMIT-aware, read-only
  BaseRepository.count / list_paginated / raw_select (SELECT-only guard)
  ProductRepository, CustomerRepository, InventoryRepository, SalesRepository, BranchRepository
  |
  v
Database Layer (erp/database.py + erp/config.py + erp/health.py)
  - SQLAlchemy Engine(create_engine, pool_pre_ping=True, pool_size=10, max_overflow=20, pool_recycle=3600)
  - connect_args: connect_timeout=5, charset=utf8mb4, use_unicode=True
  - DeclarativeBase + sessionmaker(autoflush=False, autocommit=False)
  - get_session() contextmanager (commit on success, rollback on fail, always close)
  - _classify_db_error() maps timeout/refused/auth/unknown-db/lost-connection
  |
  v
MariaDB 10.4.32  172.16.1.131:3306 / erp_motorcycle (InnoDB, utf8mb4_unicode_ci)
  21 tables, 0 FK constraints (logical FK only)
```

Pool rationale:
- `pool_pre_ping=True` recovers from stale TCP (NAS overnight idle)
- `pool_recycle=3600` recycles idle after 1h (MariaDB `wait_timeout` defaults 28800)
- `connect_timeout=5` prevents UI freeze
- `pool_size=10, max_overflow=20` — ERP has 10k sales + 12k inventory reads, bursts OK

UTF-8: DB `@@character_set_database=utf8mb4` + `charset=utf8mb4` in URL + `use_unicode=True` + `collation utf8mb4_unicode_ci` — Persian `تهران`, `شعبه`, `موتورسیکلت` round-trips correctly (verified: `SELECT name FROM cities WHERE id=1 → تهران`).

Parameterized queries: every `raw_select` requires `SELECT`-only + `:named` binds (`text(sql), {"id": 1}`). No `+ user_input +` string interpolation. Example:
```python
repo.raw_select("SELECT * FROM products WHERE id=:id", {"id": user_id})
```

Startup health check (non-blocking):
```python
from concurrent.futures import ThreadPoolExecutor
from erp.health import run_health_check
with ThreadPoolExecutor(max_workers=1) as ex:
    fut = ex.submit(run_health_check)
    result = fut.result(timeout=10)  # HealthResult(ok, category, version, db, user, latency_ms)
    # categories: success | server_unavailable | timeout | authentication_failure | database_unavailable | lost_connection | query_failure
```

Never logs credentials: `safe_url` masks password as `***`.

---

## 5. Connection Test Procedure

### CLI diagnostic (read-only, verifies 21 tables, no DDL)
```bash
python test_database_connection.py
python test_database_connection.py --json
```

Expected (live, 2026-09-19):
```
Health:      OK  category=success  latency=46ms
Version:     10.4.32-MariaDB
Database:    erp_motorcycle
User:        a.mansourpour@CL-1111
CurrentUser: a.mansourpour@cl-1111
SafeURL:     mysql+pymysql://a.mansourpour:***@172.16.1.131:3306/erp_motorcycle?charset=utf8mb4
Tables found: 21  expected 21
Tables:       branches, brands, categories, cities, customer_groups, customers, departments, employees, inventory, invoices, payments, products, purchase_order_items, purchase_orders, sales_order_items, sales_orders, service_orders, suppliers, warehouses, warranties, warranty_claims
Tables OK:    True
Sample counts: {'products': 3000, 'customers': 5000, 'sales_orders': 10000, 'inventory': 12000, 'branches': 12, ...}
Result:      PASS - direct connection verified, nothing created
```

### Direct Python
```python
import sys; sys.path.insert(0, ".")
from sqlalchemy import text
from erp.database import get_engine
e = get_engine()
print(e.url.render_as_string(hide_password=True))
# LOCAL FILE does NOT exist (proof direct):
import pathlib; print(pathlib.Path("pilot/meta_data/erp_motorcycle.db").exists())  # False
with e.connect() as c:
    print(c.execute(text("SELECT VERSION()")).scalar())
    print(c.execute(text("SELECT USER(), CURRENT_USER()")).fetchone())
    print(c.execute(text("SELECT DATABASE()")).scalar())
    print(c.execute(text("SHOW TABLES")).fetchall()[:3])
```

### Health categories
The app distinguishes:
- `server_unavailable` — 2003/Can't connect
- `timeout` — `timed out`
- `authentication_failure` — 1045/denied
- `database_unavailable` — 1049/1046
- `lost_connection` — 2013/gone away
- `query_failure` — other

---

## 6. Live Schema Discovery Summary (Full scan: `erp_full_scan_utf8.txt`)

All 21 tables `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci`, **0 foreign keys** (logical).

| # | Table | Rows | PK | Key columns |
|---|-------|------|----|-------------|
|1|branches|12|id|name, city_id|
|2|brands|14|id|name (Honda, Yamaha, Suzuki, Kawasaki, BMW, ... 14)|
|3|categories|30|id|name, parent_id (1=موتورسیکلت → 2=شهری, 3=اسپرت, 4=آفرود, 5=برقی ...)|
|4|cities|10|id|name (تهران, کرج, مشهد, اصفهان, شیراز, ... 10)|
|5|customer_groups|4|id|name (خرده‌فروشی, عمده‌فروشی, شرکتی, نمایندگی)|
|6|customers|5000|id|customer_code CUS-*, first_name, last_name, phone, city_id, customer_group_id, created_at DATE|
|7|departments|7|id|name (فروش, انبار, خرید, مالی, منابع انسانی, ...)|
|8|employees|500|id|employee_code EMP-*, first_name, last_name, department_id, branch_id, hire_date DATE, salary decimal(14,2)|
|9|inventory|12000|id|product_id, warehouse_id, quantity, reserved_quantity|
|10|invoices|0|id|invoice_no, order_id, invoice_date DATE, tax_amount, total_amount, status|
|11|payments|0|id|invoice_id, payment_date DATE, amount, method, status|
|12|products|3000|id|sku SKU-*, name, category_id, brand_id, product_type, unit_price, cost_price, warranty_months, is_active tinyint|
|13|purchase_order_items|0|id|purchase_order_id, product_id, quantity, unit_cost, line_total|
|14|purchase_orders|0|id|po_no, supplier_id, branch_id, order_date DATE, status, total_amount|
|15|sales_order_items|0|id|order_id, product_id, quantity, unit_price, discount_amount, line_total|
|16|sales_orders|10000|id|order_no SO-*, customer_id, branch_id, employee_id, order_date DATE, status, discount_amount, total_amount|
|17|service_orders|0|id|service_no, customer_id, employee_id, branch_id, service_date DATE, status, total_amount|
|18|suppliers|40|id|name, city_id, phone|
|19|warehouses|8|id|name, branch_id|
|20|warranties|0|id|product_id, customer_id, serial_no, start_date DATE, end_date DATE|
|21|warranty_claims|0|id|warranty_id, branch_id, claim_date DATE, status, description|

No indexes beyond PRIMARY; no UNIQUE constraints beyond PK.

---

## 7. Motorcycle-Specific Attributes

**Products** holds minimal motorcycle data:
- `product_type` values: `موتورسیکلت`, `دوچرخه`, `سه‌چرخه`, `لوازم` — suggests some bikes + accessories share table.
- `name` encodes brand+category: e.g. `Honda موتورسیکلت مدل 1`, `Yamaha موتور شهری مدل 2` (no explicit `model`, `engine_cc`, `color`, `year`, `VIN` columns — not present).
- No columns for: engine displacement, ABS, fuel type, color, model year, chassis/VIN, engine number. If needed, extend via `products` or new `product_specs` side table — not assumed.
- Pricing: `unit_price` 5M-80M IRR, `cost_price` lower, `warranty_months` 0/6/12/24.
- `categories.parent_id` forms hierarchy (30 categories, parent=1 is root).

## 8. Sales Analytics Discovery

- Authoritative table: `sales_orders` (10,000 rows). `sales_order_items` is 0 rows — line items not seeded; totals are in `sales_orders.total_amount` directly.
- Dates: `order_date DATE` (Gregorian, `2025-01-01` .. `2026-09-13`), not Jalali, not VARCHAR, not TIMESTAMP. Monthly analytics use `DATE_FORMAT(order_date,'%Y-%m')`.
- Statuses: `completed 2513, paid 2542, shipped 2453, pending 2492`. Completed/paid/shipped together = 7508 meaningful sales; `pending` 2492 may be cart/draft — filter accordingly.
- `invoices` (0) & `payments` (0) empty — invoicing not active; revenue == `sales_orders.total_amount` (not invoice totals).
- Branch performance: top branch 6 has 899 orders, next ~850 each — fairly balanced across 12 branches.
- `discount_amount` separate from `total_amount` (appears `total_amount` is post-discount gross). Verified `total_amount` >> `discount_amount`.

Month table: use `DATE_FORMAT(order_date,'%Y-%m') GROUP BY` — confirmed distinct months span 2025-01..2026-09.

## 9. Inventory Schema

- `inventory` 12,000 rows (4x products — likely 1 row per product per warehouse variant, but actually random distribution).
- `quantity 0..200, avg 99.9`, `reserved_quantity` 0..~20.
- `warehouses` 8 rows each `branch_id` FK logical to `branches` (8 warehouses not covering all 12 branches — under-provisioned).
- `products` → `inventory` via `product_id` (logical).

## 10. Customer Schema

- 5,000 customers evenly split across 4 groups (1250 each) and across cities.
- `created_at DATE` Gregorian, range similar to sales.
- No email column (only `phone`), `customer_code` unique-ish pattern `CUS-*`.

## 11. Date Representation

All dates are `DATE` (not DATETIME/TIMESTAMP/VARCHAR): `customers.created_at`, `employees.hire_date`, `sales_orders.order_date`, `inventory` has none. Gregorian (`YYYY-MM-DD`) confirmed; Persian Jalali not used. For monthly analytics, use `DATE_FORMAT(dateCol,'%Y-%m')`.

## 12. Persian / Unicode

DB charset `utf8mb4_unicode_ci`, engine `_connect( charset=utf8mb4, use_unicode=True )` — verified `SELECT name FROM cities WHERE id=1 → تهران` preserves `U+06CC ی` correctly (previous full-scan with `PYTHONIOENCODING=utf-8` required). All Persian content (`تهران`, `شعبه`, `موتورسیکلت`, `خرده‌فروشی`) round-trips.

## 13. Architecture Recommendation

```
ERP DB (remote)  ←→  erp/database.py (pooled engine, non-blocking health)
                         ↕
                erp/repositories/*  (LIMIT-aware, bound params)
                         ↕
                  erp/services/*  (sales/product/inventory/reporting)
                         ↕
               DB-GPT agentic_data_api / future UI
  - Use health check at startup via ThreadPoolExecutor
  - Never bind `erp_*` ORM Base.create_all — metadata is read-only mirror
  - For analytics, expose only aggregated endpoints (monthly_revenue, branch_performance)
  - For future writes, gate behind explicit service method with audit log
```

Current repos cover read-only; for writes add explicit `*_write_repository.py` with `BEGIN / COMMIT` and user confirmation.

## 14. Potential Issues

- **No FK constraints:** Referential integrity is application-enforced. Inconsistent `city_id` / `branch_id` possible. Recommend adding app-level checks + periodic `LEFT JOIN WHERE parent IS NULL` audits.
- **Empty transactional detail tables:** `sales_order_items`, `invoices`, `payments`, `purchase_orders`, `service_orders`, `warranties` all 0 rows — analytics limited to order headers; line-item profitability cannot be derived.
- **No secondary indexes:** Only PK indexes. Queries like `WHERE customer_id = ?` on `sales_orders` will table-scan 10k rows (currently okay, but +100k will need `CREATE INDEX idx_sales_orders_customer_id` — not done automatically, requires DBA approval).
- **Warranty months 0:** Some products have 0 — means no warranty, not missing data. UI should show "بدون گارانتی".
- **Warehouse < Branch:** 8 warehouses for 12 branches — 4 branches lack inventory location (data incompleteness).
- **Sales totals large:** `total_amount` up to 5e8 IRR — use `decimal(14,2)` correctly; avoid float.
- **Groq 403 currently:** Both `qwen/qwen3.8-27b` and `openai/gpt-oss-120b` returned `403 Forbidden` from `api.groq.com` at `192.168.169.187 → 104.18.38.236` via Cloudflare — key `gsk_...iedK` got `{"error":{"message":"Forbidden"}}` even for `GET /openai/v1/models`. Suspected IP-based WAF/geo block on Iranian exit IP `81.12.38.101`. Local Ollama `qwen3.5:9b` remains Healthy as fallback.

## 15. Assumptions

- `sales_orders.status='completed'/'paid'/'shipped'` together represent revenue; `pending` is not.
- `product_type='موتورسیکلت'` is main bike type; `دوچرخه/سه‌چرخه/لوازم` are accessories but counted in inventory.
- `inventory.quantity - reserved_quantity` is available stock.
- `suppliers.city_id` logical FK to `cities`.
- Gregorian dates (verified range 2025-01-01).

## 16. Inconsistencies Discovered

- 6 transactional tables are empty while headers have 10k sales — suggests seeding is incomplete / only headers imported.
- `sales_order_items` 0 rows means no per-line discount verification possible.
- `suppliers` have phone `021...` (Tehran landline) even for other cities — likely synthetic.
- `employees.hire_date` includes future `2026-01-03` — future-dated data.

## 17. Connection Test Evidence

```
$ python test_database_connection.py
Health: OK category=success latency=46ms
Version: 10.4.32-MariaDB
Database: erp_motorcycle
User: a.mansourpour@CL-1111
CurrentUser: a.mansourpour@cl-1111
SafeURL: mysql+pymysql://a.mansourpour:***@172.16.1.131:3306/erp_motorcycle?charset=utf8mb4
Tables OK: True (21/21)
Sample counts: products 3000, customers 5000, sales_orders 10000, inventory 12000 ...
PASS - direct connection verified, nothing created
```
`SELECT DATABASE()` → `erp_motorcycle`, `@@character_set_database=utf8mb4`, local file `pilot/meta_data/erp_motorcycle.db` **does not exist** (proof direct TCP).

---

**Next steps (when approved):**
- Create `erp/api_router.py` to expose `/erp/health`, `/erp/products`, `/erp/sales/monthly`, `/erp/inventory/low-stock` (all parameterized, read-only)
- Mount in `DB-GPT` via `include_router` (opt-in)
- Add `docker-compose` override for `DB_HOST` env injection in production
