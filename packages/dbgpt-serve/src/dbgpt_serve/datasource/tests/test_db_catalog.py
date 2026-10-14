import asyncio
import json
import random
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, inspect

from dbgpt_serve.datasource.service import db_catalog


class _Connector:
    dialect = "sqlite"

    def __init__(self, path):
        self._engine = create_engine(f"sqlite:///{path}")

    def get_table_names(self):
        return ["factors", "account", "salesorders"]

    def get_columns(self, table):
        return [
            {"name": c["name"], "type": str(c["type"])}
            for c in inspect(self._engine).get_columns(table)
        ]


class _FakeLLM:
    calls = 0

    async def generate(self, request):
        _FakeLLM.calls += 1
        text = request.messages[0].content
        names = [l[3:].split("  (")[0] for l in text.splitlines() if l.startswith("## ")]
        out = [
            {"table": n, "description": f"rows of {n}", "keywords": ["k"],
             "measures": ["totalprice", "missing_col"], "confidence": 0.5}
            for n in names
        ]
        return SimpleNamespace(success=True, text=json.dumps(out))


@pytest.fixture()
def erp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DBGPT_CATALOG_DIR", str(tmp_path / "catalog"))
    path = tmp_path / "erp.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE factors (fcindex INTEGER PRIMARY KEY, accountcode TEXT,
            kindwork TEXT, s_regdate TEXT, totalprice NUMERIC, notes TEXT);
        CREATE TABLE account (accountcode TEXT PRIMARY KEY, accountname TEXT);
        CREATE TABLE salesorders (id INTEGER);
        CREATE VIEW vsales AS SELECT f.totalprice FROM factors f
            JOIN account a ON f.accountcode = a.accountcode WHERE f.kindwork = 'S';
        """
    )
    random.seed(0)
    for i in range(100):
        con.execute("INSERT INTO account VALUES (?, ?)", (f"A{i}", f"c{i}"))
    for i in range(1000):
        con.execute(
            "INSERT INTO factors VALUES (?,?,?,?,?,NULL)",
            (i, f"A{i % 100}", "S" if i % 4 else "R",
             f"14{i % 5:02d}/0{1 + i % 9}/1{i % 9}", i * 10),
        )
    con.commit()
    con.close()
    return path


def test_build_profile_relationships_and_incremental(erp_db):
    _FakeLLM.calls = 0
    connector = _Connector(erp_db)
    cat = asyncio.run(
        db_catalog.build_catalog("erp", lambda _: connector, llm_client=_FakeLLM(), llm_model="m")
    )
    assert cat["status"] == "ready"
    tables = cat["tables"]
    assert tables["salesorders"]["row_count"] == 0
    factors = tables["factors"]
    cols = {c["name"]: c for c in factors["columns"]}
    assert cols["kindwork"]["profile"]["values"] == [["S", 750], ["R", 250]]
    assert cols["s_regdate"]["profile"]["looks_like"] == "date_string"
    assert factors["referenced_by"] == ["vsales"]
    assert factors["measures"] == ["totalprice"]  # unknown column dropped
    assert any(r["source"] == "db_code" for r in cat["relationships"])
    assert "salesorders" not in db_catalog.render_table_index(cat)
    assert "S (750)" in db_catalog.render_table_details(cat, ["factors"])

    calls = _FakeLLM.calls
    asyncio.run(
        db_catalog.build_catalog("erp", lambda _: connector, llm_client=_FakeLLM(), llm_model="m")
    )
    assert _FakeLLM.calls == calls  # unchanged tables keep their descriptions
    assert db_catalog.catalog_status("erp")["described_tables"] == 2


def test_hanging_llm_call_times_out_and_build_still_finishes(erp_db, monkeypatch):
    monkeypatch.setattr(db_catalog, "LLM_TIMEOUT_SECONDS", 0.2)

    class _HangingLLM:
        async def generate(self, request):
            await asyncio.sleep(10)

    connector = _Connector(erp_db)
    cat = asyncio.run(
        db_catalog.build_catalog("erp", lambda _: connector, llm_client=_HangingLLM(), llm_model="m")
    )
    assert cat["status"] == "ready"
    assert not cat["tables"]["factors"].get("description")


class _WordEmbeddings:
    vocab = ["sales", "invoice", "price", "model", "serial", "customer", "مدل", "فروش"]

    def _vec(self, text):
        text = text.lower()
        return [float(text.count(w)) + 0.01 for w in self.vocab]

    async def aembed_documents(self, texts):
        return [self._vec(t) for t in texts]

    async def aembed_query(self, text):
        return self._vec(text)


def test_semantic_rank_uses_embeddings(tmp_path, monkeypatch):
    monkeypatch.setenv("DBGPT_CATALOG_DIR", str(tmp_path))
    catalog = {
        "status": "ready",
        "tables": {
            "dbo.factors": {"row_count": 10, "description": "sales invoice price فروش",
                            "keywords": ["sales"], "columns": [{"name": "totalprice"}]},
            "dbo.customer": {"row_count": 10, "description": "customer data",
                             "keywords": ["customer"], "columns": []},
            "dbo.empty": {"row_count": 0, "columns": []},
        },
    }
    db_catalog.save_catalog("emb", catalog)
    emb = _WordEmbeddings()
    assert db_catalog.embeddings_stale("emb", catalog)
    assert asyncio.run(db_catalog.build_embeddings("emb", catalog, emb)) == 2
    assert not db_catalog.embeddings_stale("emb", catalog)
    # unchanged tables are not embedded again
    assert asyncio.run(db_catalog.build_embeddings("emb", catalog, emb)) == 0
    ranked = asyncio.run(
        db_catalog.semantic_rank_tables("emb", catalog, "[Database: emb] کل فروش", 2, emb)
    )
    assert ranked[0] == "dbo.factors"
