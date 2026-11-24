"""Connector protocol, ExecutionError and the metadata-SQL guard (spec 9.1, 18 R8, FL703)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from factlang.connectors import (
    METADATA_KEYWORDS,
    Connector,
    ExecutionError,
    QueryResult,
    check_metadata_sql,
    run_metadata,
)
from factlang.ir import Arity
from factlang.semantics.filters import Param
from factlang.sql import RenderedProbe


class Recorder:
    """A connector that records every probe it is asked to execute."""

    dialect = "duckdb"
    paramstyle = "qmark"

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[RenderedProbe, float | None]] = []
        self.rows = rows if rows is not None else [{"table_name": "orders"}]

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        self.calls.append((probe, timeout_s))
        return QueryResult(list(self.rows))


# -- ExecutionError / QueryResult ----------------------------------------------------------


def test_execution_error_code_from_message() -> None:
    e = ExecutionError("FL702 timed out after 1s")
    assert e.code == "FL702"
    assert str(e) == "FL702 timed out after 1s"


def test_execution_error_code_keyword_prefixes_message() -> None:
    e = ExecutionError("boom", code="FL700")
    assert e.code == "FL700"
    assert str(e).startswith("FL700 ")


def test_execution_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError):
        ExecutionError("x", code="FL999")
    with pytest.raises(ValueError):
        ExecutionError("no code here")


def test_query_result_defaults() -> None:
    r = QueryResult([{"a": 1}])
    assert r.rows == [{"a": 1}]
    assert r.bytes_processed is None


def test_recorder_satisfies_protocol() -> None:
    c: Connector = Recorder()
    assert c.dialect == "duckdb"


# -- the guard: accepted forms --------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT table_name FROM information_schema.tables",
        "  select 1",
        "-- leading comment\nSELECT 1",
        "/* block */ SELECT 1",
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "SHOW TABLES",
        "show tables",
        "DESCRIBE orders",
        "DESC orders",
        "EXPLAIN SELECT 1",
        "SELECT 'a;b' AS s",
        "SELECT 'it''s' AS s, 'DELETE' AS k",
        "SELECT is_insertable_into, update_rule FROM information_schema.tables",
        "SHOW CREATE TABLE orders",
    ],
)
def test_guard_accepts(sql: str) -> None:
    check_metadata_sql(sql)  # no raise


def test_metadata_keywords_are_exactly_the_spec_list() -> None:
    assert METADATA_KEYWORDS == ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")


# -- the guard: each refusal class ------------------------------------------------------------


def _refused(sql: str) -> ExecutionError:
    with pytest.raises(ExecutionError) as info:
        check_metadata_sql(sql)
    assert info.value.code == "FL703"
    assert str(info.value).startswith("FL703 ")
    return info.value


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "delete from orders",
        "INSERT INTO orders VALUES (1)",
        "UPDATE orders SET id = 1",
        "MERGE INTO orders USING x ON TRUE WHEN MATCHED THEN DELETE",
        "TRUNCATE orders",
        "CALL proc()",
        "-- harmless\nDROP TABLE orders",
    ],
)
def test_guard_refuses_dml_at_start(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "sql",
    ["CREATE TABLE x (a INT)", "DROP TABLE orders", "ALTER TABLE orders ADD c INT", "GRANT ALL"],
)
def test_guard_refuses_ddl(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE x",
        "SELECT 1;",
        "SHOW TABLES; SHOW TABLES",
        "SELECT 1 -- ; comment",
        # a quote inside a comment must not hide a later statement
        "SELECT 1 -- it's\n; DROP TABLE x; --'",
        # MySQL backslash escape: the ANSI reading hides the ';', the MySQL one does not
        "SELECT '\\'' ; DROP TABLE x; --'",
        # MySQL '#' comment
        "SELECT 1 # it's\n; DROP TABLE x; #'",
        # PostgreSQL nested block comment
        "SELECT 1 /* /* */ 'x */ ; DROP TABLE x; --'",
        # PostgreSQL dollar quoting
        "SELECT $$ ' $$ ; DROP TABLE x; --'",
        # T-SQL bracket identifier
        "SELECT [it's] ; DROP TABLE x --'",
    ],
)
def test_guard_refuses_multiple_statements(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "   ",
        "-- only a comment",
        "(SELECT 1)",
        "VALUES (1)",
        "PRAGMA table_info('orders')",
        "SELECT 'unterminated",
        "SELECT 1 /* unterminated",
    ],
)
def test_guard_refuses_other_first_tokens_and_unterminated(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d",
        "SELECT * INTO new_orders FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "EXPLAIN ANALYZE SELECT 1",
        "EXPLAIN DELETE FROM orders",
    ],
)
def test_guard_refuses_writes_inside_a_query(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "sql",
    [
        # Snowflake statement chaining
        "SHOW TABLES ->> SELECT * FROM $1",
        "SELECT 1 ->> DROP TABLE x",
        # MySQL / MariaDB executable comments are code, not comments
        "SELECT 1 /*! , (SELECT 1) */",
        "SELECT 1 /*!50000 UNION SELECT 2 */",
        "SELECT 1 /*M! UNION SELECT 2 */",
    ],
)
def test_guard_refuses_chains_and_executable_comments(sql: str) -> None:
    _refused(sql)


def test_arrow_and_executable_comment_text_inside_literals_is_fine() -> None:
    check_metadata_sql("SELECT '->>' AS a, '/*! x */' AS b")


@pytest.mark.parametrize(
    "sql",
    [
        "SHOW TABLES IN x INTO OUTFILE '/tmp/t'",
        "DESCRIBE DELETE FROM orders",
        "DESC INSERT INTO orders VALUES (1)",
        "EXPLAIN INSERT INTO orders VALUES (1)",
        "EXPLAIN ANALYSE SELECT 1",
        "SHOW CREATE TABLE orders DROP",
        "SHOW GRANTS; DROP TABLE x",
    ],
)
def test_guard_scans_show_describe_and_explain_bodies(sql: str) -> None:
    _refused(sql)


def test_mysql_backslash_escaped_literal_is_refused() -> None:
    # the ANSI reading leaves it unterminated: refused, the safe direction
    _refused("SELECT 'it\\'s' AS s")


def test_guard_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        check_metadata_sql(b"SELECT 1")  # type: ignore[arg-type]


# -- run_metadata ----------------------------------------------------------------------------


def test_run_metadata_refuses_before_executing() -> None:
    rec = Recorder()
    with pytest.raises(ExecutionError) as info:
        run_metadata(rec, "SELECT 1; DROP TABLE orders")
    assert info.value.code == "FL703"
    assert rec.calls == []


def test_run_metadata_builds_a_row_set_probe() -> None:
    rec = Recorder()
    result = run_metadata(
        rec,
        "SELECT table_name FROM information_schema.tables WHERE table_schema = {{s}}",
        (Param("s", "main", "string"),),
        timeout_s=3.0,
    )
    assert result.rows == [{"table_name": "orders"}]
    probe, timeout = rec.calls[0]
    assert timeout == 3.0
    assert probe.arity is Arity.ROW_SET
    assert probe.plan is None
    assert probe.dialect == "duckdb"
    assert probe.params == (Param("s", "main", "string"),)
    assert "'main'" not in probe.sql_template  # bound, never spliced


def test_run_metadata_accepts_a_mapping_and_infers_families() -> None:
    rec = Recorder()
    when = dt.datetime(2026, 1, 1, 12, 0)
    run_metadata(
        rec,
        "SELECT {{a}}, {{b}}, {{c}}, {{d}}, {{e}}, {{f}}, {{g}}",
        {
            "a": "x",
            "b": 1,
            "c": True,
            "d": Decimal("1.5"),
            "e": 2.5,
            "f": when,
            "g": when.date(),
        },
    )
    families = {p.name: p.family for p in rec.calls[0][0].params}
    assert families == {
        "a": "string",
        "b": "integer",
        "c": "boolean",
        "d": "decimal",
        "e": "float",
        "f": "timestamp",
        "g": "date",
    }


def test_run_metadata_rejects_unbindable_value() -> None:
    with pytest.raises(TypeError):
        run_metadata(Recorder(), "SELECT {{a}}", {"a": object()})


def test_run_metadata_template_and_params_must_agree() -> None:
    with pytest.raises(ExecutionError) as info:
        run_metadata(Recorder(), "SELECT {{a}}", ())
    assert info.value.code == "FL703"


def test_run_metadata_uses_aexecute_free_sync_path() -> None:
    # the connector's own errors are not wrapped or swallowed
    class Failing(Recorder):
        def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            raise ExecutionError("FL702 timed out")

    with pytest.raises(ExecutionError) as info:
        run_metadata(Failing(), "SHOW TABLES")
    assert info.value.code == "FL702"
