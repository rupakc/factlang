"""CSV files become addressable tables and columns (site spec 5.2, plan review focus 2)."""

from __future__ import annotations

import asyncio
import json
from types import ModuleType
from typing import Any

import pytest

import factlang as fl

SAMPLE = ["customers", "orders"]


@pytest.mark.parametrize(
    ("filename", "existing", "expected"),
    [
        ("Orders 2024.csv", SAMPLE, "orders_2024"),
        ("2fa.csv", SAMPLE, "t_2fa"),
        ("customers.csv", SAMPLE, "customers_2"),
        ("Customers.CSV", [*SAMPLE, "customers_2"], "customers_3"),
        ("données.csv", SAMPLE, "donn_es"),
        ("a.b.csv", SAMPLE, "a_b"),
        ("report", SAMPLE, "report"),
        ('it\'s "q".csv', SAMPLE, "it_s__q_"),
        (".csv", SAMPLE, "table"),
        ("123.csv", [], "t_123"),
        ("ORDERS.csv", ["Orders"], "orders_2"),
    ],
)
def test_table_name_for(
    glue: ModuleType, filename: str, existing: list[str], expected: str
) -> None:
    assert glue.table_name_for(filename, existing) == expected
    assert glue.table_name_for(filename, json.dumps(existing)) == expected


def test_table_names_are_bare_factlang_names(glue: ModuleType) -> None:
    for filename in ["Orders 2024.csv", "2fa.csv", "données.csv", "select.csv", "WHERE.csv"]:
        name = glue.table_name_for(filename, [])
        assert not fl.syntax.needs_quoting(name), name
        assert not fl.lint(f"ROWS {name} VALUE >0").has_errors


def test_table_info_spells_every_column(glue: ModuleType) -> None:
    columns = [
        ["id", "BIGINT"],
        ["Order ID", "BIGINT"],
        ['say "hi"', "VARCHAR"],
        ["2fa", "VARCHAR"],
        ["ID", "INTEGER"],
        ["amount", "DECIMAL(10,2)"],
        ["donnée", "VARCHAR"],
        ["line\rbreak", "VARCHAR"],
        ["two\nlines", "VARCHAR"],
        ["nul\x00", "VARCHAR"],
    ]
    info = json.loads(glue.fl_table_info("t_2fa", 4, json.dumps(columns), "csv"))
    assert info["name"] == "t_2fa" and info["rows"] == 4 and info["source"] == "csv"
    assert info["spelling"] == "t_2fa"
    got = {c["name"]: (c["type"], c["family"], c["spelling"]) for c in info["columns"]}
    assert got["id"] == ("BIGINT", "integer", "id")
    assert got["Order ID"] == ("BIGINT", "integer", '"Order ID"')
    assert got['say "hi"'] == ("VARCHAR", "string", '"say ""hi"""')
    assert got["2fa"] == ("VARCHAR", "string", '"2fa"')
    assert got["ID"] == ("INTEGER", "integer", '"ID"')
    assert got["amount"] == ("DECIMAL(10,2)", "decimal", "amount")
    assert got["donnée"][2] == '"donnée"'
    for name in ["line\rbreak", "two\nlines", "nul\x00"]:
        assert got[name][2] is None, "not addressable"
    assert [c["name"] for c in info["columns"]] == [c[0] for c in columns]


def test_catalog_lists_families(glue: ModuleType) -> None:
    tables = [
        json.loads(glue.fl_table_info("orders", 3, json.dumps([["id", "INTEGER"]]), "sample")),
        json.loads(
            glue.fl_table_info(
                "csv_a", 1, json.dumps([["When", "TIMESTAMP"], ["x", "DOUBLE"]]), "csv"
            )
        ),
    ]
    catalog = json.loads(glue.fl_catalog(json.dumps(tables)))
    assert catalog == {"orders": {"id": "integer"}, "csv_a": {"When": "timestamp", "x": "float"}}


duckdb = pytest.importorskip("duckdb")


def _load_csv(glue: ModuleType, bridge: ModuleType, con: Any, filename: str) -> dict[str, Any]:
    path = bridge.FIXTURES / filename
    existing = [r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
    name = glue.table_name_for(filename, existing)
    con.execute(f"CREATE TABLE \"{name}\" AS SELECT * FROM read_csv_auto('{path}')")
    cols = con.execute(f'DESCRIBE "{name}"').fetchall()
    (rows,) = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
    pairs = json.dumps([[c[0], c[1]] for c in cols])
    return json.loads(glue.fl_table_info(name, rows, pairs, "csv"))


def test_a_csv_with_hostile_headers_is_checkable(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    info = _load_csv(glue, bridge, con, "quoted_names.csv")
    assert info["name"] == "quoted_names" and info["rows"] == 4
    spell = {c["name"]: c["spelling"] for c in info["columns"]}
    assert spell['say "hi"'] == '"say ""hi"""'
    tables = [info]
    for name in ("customers", "orders"):
        cols = con.execute(f"DESCRIBE {name}").fetchall()
        pairs = json.dumps([[c[0], c[1]] for c in cols])
        tables.append(json.loads(glue.fl_table_info(name, 0, pairs, "sample")))
    catalog = glue.fl_catalog(json.dumps(tables))
    say_hi = spell['say "hi"']
    text = "\n".join(
        [
            f"NULLS quoted_names.{say_hi} RATE <=30%",
            f"UNIQUE quoted_names.{spell['Order ID']}",
            f"VALUES quoted_names.{spell['status']} IN ['open', 'paid', 'refunded']",
            f"SUM quoted_names.{spell['Amount (EUR)']} VALUE ==7.75",
            f"TYPE quoted_names.{spell['ID']} IS integer",
            f"ROWS quoted_names VALUE ==4 WHERE {spell['2fa']} != 'q'",
        ]
    )
    assert not fl.lint(text).has_errors
    out = json.loads(asyncio.run(glue.fl_check(text, bridge.make_run(con), catalog)))
    assert [o["verdict"] for o in out["outcomes"]] == ["pass"] * 6, out["outcomes"]
    reference = fl.Checker(fl.DBAPIConnector(con, "duckdb")).check(text)
    assert [o.verdict.value for o in reference.outcomes] == ["pass"] * 6


def test_the_malformed_fixture_is_refused_and_leaves_tables_intact(
    glue: ModuleType, bridge: ModuleType
) -> None:
    con = bridge.shop(glue)
    with pytest.raises(duckdb.Error):
        _load_csv(glue, bridge, con, "malformed.csv")
    names = sorted(r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall())
    assert names == ["customers", "orders"]
