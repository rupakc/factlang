"""Suggested claims: starting points generated from the schema and small profiles (spec 5.2)."""

from __future__ import annotations

import json
from types import ModuleType
from typing import Any

import pytest

import factlang as fl
from factlang.semantics.catalog import StaticCatalog

duckdb = pytest.importorskip("duckdb")


def _info(glue: ModuleType, con: Any, name: str, source: str = "sample") -> str:
    cols = con.execute(f'DESCRIBE "{name}"').fetchall()
    (rows,) = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
    return glue.fl_table_info(name, rows, json.dumps([[c[0], c[1]] for c in cols]), source)


def _suggest(glue: ModuleType, bridge: ModuleType, con: Any, table_json: str) -> list[str]:
    profiles = [bridge.query(con, sql, []) for sql in json.loads(glue.fl_profile_sql(table_json))]
    return json.loads(glue.fl_suggest(table_json, json.dumps(profiles)))


def test_sample_orders_suggestions(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    claims = _suggest(glue, bridge, con, _info(glue, con, "orders"))
    assert claims == [
        "ROWS orders VALUE >0",
        "UNIQUE orders.id",
        "NULLS orders.id RATE <=5%",
        "NULLS orders.customer_id RATE <=5%",
        "NULLS orders.status RATE <=5%",
        "VALUES orders.status IN ['open', 'paid', 'refunded']",
    ]


def test_suggestions_lint_clean_against_the_catalog(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    path = bridge.FIXTURES / "quoted_names.csv"
    con.execute(f"CREATE TABLE quoted_names AS SELECT * FROM read_csv_auto('{path}')")
    infos = [_info(glue, con, n) for n in ("customers", "orders", "quoted_names")]
    catalog = StaticCatalog(json.loads(glue.fl_catalog(json.dumps([json.loads(i) for i in infos]))))
    for info in infos:
        claims = _suggest(glue, bridge, con, info)
        assert claims, info
        for claim in claims:
            bag = fl.lint(claim, catalog=catalog)
            assert not bag.has_errors and len(bag) == 0, f"{claim}\n{bag.render()}"
    quoted = _suggest(glue, bridge, con, infos[2])
    assert quoted[0] == "ROWS quoted_names VALUE >0"
    assert 'UNIQUE quoted_names."ID"' in quoted


def test_suggestions_are_deterministic(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    info = _info(glue, con, "customers")
    first = _suggest(glue, bridge, con, info)
    assert first == _suggest(glue, bridge, con, info)
    assert first == [
        "ROWS customers VALUE >0",
        "UNIQUE customers.id",
        "NULLS customers.id RATE <=5%",
        "NULLS customers.email RATE <=5%",
    ]


def test_id_rule_and_fixed_threshold(glue: ModuleType) -> None:
    columns = [
        ["paid", "BOOLEAN"],
        ["valid", "BOOLEAN"],
        ["note", "VARCHAR"],
        ["customer_id", "BIGINT"],
        ["id", "BIGINT"],
    ]
    info = glue.fl_table_info("t", 10, json.dumps(columns), "csv")
    # "note" has eleven distinct values: more than ten, so no VALUES suggestion
    eleven = [[{"t": "string", "v": f"n{i:02d}"}] for i in range(11)]
    profile = [{"columns": [["v", "VARCHAR"]], "rows": eleven}]
    claims = json.loads(glue.fl_suggest(info, json.dumps(profile)))
    assert claims == [
        "ROWS t VALUE >0",
        "UNIQUE t.customer_id",
        "NULLS t.paid RATE <=5%",
        "NULLS t.valid RATE <=5%",
        "NULLS t.note RATE <=5%",
    ]
    assert all("RATE <=5%" in c for c in claims if c.startswith("NULLS"))


def test_values_skip_unrepresentable_text(glue: ModuleType, bridge: ModuleType) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE notes AS SELECT * FROM (VALUES ('a' || chr(10) || 'b', 'x'), ('c', 'it''s'),"
        " ('d', 'x')) AS t(body, tag)"
    )
    claims = _suggest(glue, bridge, con, _info(glue, con, "notes", "csv"))
    assert "VALUES notes.tag IN ['it''s', 'x']" in claims
    assert not any(c.startswith("VALUES notes.body") for c in claims)


def test_profiles_use_core_sql_only(glue: ModuleType, bridge: ModuleType) -> None:
    """No DuckDB extension (json, icu) is needed to profile: autoloading is off in the browser."""
    con = bridge.shop(glue)
    sqls = json.loads(glue.fl_profile_sql(_info(glue, con, "orders")))
    assert sqls == [
        'SELECT DISTINCT "status" AS v FROM "orders" WHERE "status" IS NOT NULL ORDER BY v LIMIT 11'
    ]
    assert not any(word in s.lower() for s in sqls for word in ("json", "timezone", "list("))


def test_unaddressable_columns_are_never_suggested(glue: ModuleType) -> None:
    columns = [["line\rbreak_id", "BIGINT"], ["ok", "VARCHAR"]]
    info = glue.fl_table_info("t", 1, json.dumps(columns), "csv")
    sqls = json.loads(glue.fl_profile_sql(info))
    assert all("line" not in s for s in sqls)
    claims = json.loads(glue.fl_suggest(info, json.dumps([])))
    assert claims == ["ROWS t VALUE >0", "NULLS t.ok RATE <=5%"]
