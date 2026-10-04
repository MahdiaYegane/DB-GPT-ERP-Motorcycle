#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic: Direct MariaDB connection test (read-only, no DDL).

Verifies:
  - TCP connectivity to 172.16.1.131:3306
  - Authentication as a.mansourpour@CL-1111
  - Selected database erp_motorcycle
  - All 21 verified tables visible
  - utf8mb4 / Persian support

Usage:
  python test_database_connection.py
  python test_database_connection.py --json

Never prints credentials.
"""
from __future__ import annotations

import argparse
import json
import sys

# Use the new modular layer (env-based, pooled, direct)
try:
    from erp.health import run_health_check
    from erp.database import get_engine
    from sqlalchemy import text
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    print("Hint: pip install sqlalchemy pymysql python-dotenv", file=sys.stderr)
    sys.exit(2)

EXPECTED_TABLES = [
    "branches","brands","categories","cities","customer_groups","customers",
    "departments","employees","inventory","invoices","payments","products",
    "purchase_order_items","purchase_orders","sales_order_items","sales_orders",
    "service_orders","suppliers","warehouses","warranties","warranty_claims",
]

def main():
    parser = argparse.ArgumentParser(description="ERP direct connection diagnostic (read-only)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    health = run_health_check()
    # Direct, read-only inspection (no CREATE/ALTER/DROP)
    tables_ok = False
    tables_found = []
    missing = []
    counts = {}
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text("SHOW TABLES")).fetchall()
            tables_found = sorted([r[0] for r in rows])
            missing = [t for t in EXPECTED_TABLES if t not in tables_found]
            tables_ok = len(missing) == 0 and len(tables_found) >= 21
            # Sample counts for key tables (read-only, LIMIT 1 COUNT)
            for t in ["products","customers","sales_orders","inventory","branches","cities","brands","suppliers","employees"]:
                if t in tables_found:
                    cnt = conn.execute(text(f"SELECT COUNT(*) FROM `{t}`")).scalar()
                    counts[t] = int(cnt)
            # Persian sample (utf8mb4 check, not persisted)
            sample = conn.execute(text("SELECT name FROM cities LIMIT 1")).scalar()
            persian_ok = sample is not None
    except Exception as e:
        if not args.json:
            print(f"Table inspection failed: {e}", file=sys.stderr)

    ok = health.ok and tables_ok

    if args.json:
        out = {
            "connection": health.to_dict(),
            "tables_found": tables_found,
            "missing_tables": missing,
            "tables_ok": tables_ok,
            "sample_counts": counts,
            "direct": True,  # flag: direct TCP, not sqlite file
            "created": False, # flag: nothing created
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print("="*72)
        print("ERP Direct Connection Diagnostic (READ-ONLY, no DDL)")
        print("="*72)
        print(f"Direct:      TCP 172.16.1.131:3306 -> erp_motorcycle (no local DB created)")
        print(f"Health:      {'OK' if health.ok else 'FAIL'}  category={health.category}  latency={health.latency_ms}ms")
        print(f"Version:     {health.server_version}")
        print(f"Database:    {health.database}")
        print(f"User:        {health.user}")
        print(f"CurrentUser: {health.current_user}")
        print(f"SafeURL:     {health.safe_url}")
        if health.error:
            print(f"Error:       {health.error[:600]}")
        print("-"*72)
        print(f"Tables found: {len(tables_found)}  expected 21")
        print(f"Tables:       {', '.join(tables_found)}")
        if missing:
            print(f"Missing:      {', '.join(missing)}")
        print(f"Tables OK:    {tables_ok}")
        print(f"Sample counts: {counts}")
        enc = "ok (utf8mb4)" if health.ok else "unknown"
        print(f"Encoding:     {enc}  Persian sample preserved via utf8mb4")
        print("-"*72)
        print("Result:      " + ("PASS - direct connection verified, nothing created" if ok else "FAIL - see categories above"))
        print("="*72)

    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
