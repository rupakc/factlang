"""Connector protocol, ExecutionError and the metadata-SQL guard (spec 9.1, 18 R8, FL703)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from factlang.connectors import (
    METADATA_KEYWORDS,
    CallableConnector,
    Connector,
    ExecutionError,
    QueryResult,
    check_metadata_sql,
    run_metadata,
)
from factlang.connectors.base import FORBIDDEN_WORDS, canonical_dialect, uses_tsql_rules
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
        "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
        "SHOW TABLES",
        "show tables",
        "DESCRIBE orders",
        "DESC orders",
        "EXPLAIN SELECT 1",
        "SELECT 'it''s' AS s",
        "SELECT is_insertable_into, update_rule, character_set_name, table_comment"
        " FROM information_schema.tables",
        "SELECT column_name FROM information_schema.columns WHERE table_schema = {{s}}"
        " AND table_name = {{t}} ORDER BY ordinal_position DESC",
        "SELECT table_name\nFROM information_schema.tables\nWHERE table_type = 'BASE TABLE'",
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


@pytest.mark.parametrize(
    "sql",
    [
        # quotes are ignored: forbidden characters and words inside literals are refused too
        "SELECT '->>' AS a",
        "SELECT '/*! x */' AS b",
        "SELECT 'a;b' AS s",
        "SELECT 'DELETE' AS k",
        "SELECT * FROM information_schema.table_privileges WHERE privilege_type = 'INSERT'",
        "SELECT 1 -- comment",
        "-- leading comment\nSELECT 1",
        "/* block */ SELECT 1",
        "SELECT '#' AS h",
        "SELECT x FROM t WHERE y = 'a$b'",
        "SHOW CREATE TABLE orders",
    ],
)
def test_guard_is_over_strict_by_design(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "sql",
    [
        # PostgreSQL / DuckDB: '$' inside identifiers next to dollar quotes
        "SELECT 1 AS a$b$, 2 AS c ; DROP TABLE orders ; SELECT 3 AS d$b$",
        # MySQL: '--' starts a comment only when followed by whitespace
        "SELECT 2--'\n; DROP TABLE x --'",
        # Snowflake: '//' line comments
        "SELECT 1 // it's\n; DROP TABLE x; //'",
        # Oracle alternative quoting
        "SELECT q'{'}' AS a, 'x' AS b FROM dual",
        "SELECT Q'[it's]' FROM dual",
        "SELECT nq'<a>' FROM dual",
        "SELECT Nq'(a)' FROM dual",
    ],
)
def test_guard_refuses_review_bypasses(sql: str) -> None:
    _refused(sql)


@pytest.mark.parametrize(
    "word",
    [
        "INTO",
        "OUTFILE",
        "DUMPFILE",
        "COPY",
        "CALL",
        "EXEC",
        "EXECUTE",
        "MERGE",
        "GRANT",
        "REVOKE",
        "SET",
        "USE",
        "BEGIN",
        "COMMIT",
        "LOAD",
        "UNLOAD",
        "PUT",
        "GET",
        "REMOVE",
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "VACUUM",
        "ANALYZE",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "TRUNCATE",
    ],
)
def test_guard_refuses_every_listed_keyword_anywhere(word: str) -> None:
    assert word in FORBIDDEN_WORDS
    _refused(f"SELECT a FROM information_schema.tables WHERE x {word.lower()} y")


def test_oracle_q_inside_a_word_is_not_a_quote_prefix() -> None:
    check_metadata_sql("SELECT seq FROM information_schema.sequences")
    # 'q' ends a word here, not a q-quote prefix; the literal itself is harmless
    check_metadata_sql("SELECT faq'' FROM t")


def test_values_with_forbidden_text_pass_as_parameters() -> None:
    rec = Recorder()
    run_metadata(
        rec,
        "SELECT table_name FROM information_schema.tables WHERE table_name = {{t}}",
        {"t": "x'; delete from orders; -- $$ #"},
    )
    ((probe, _),) = rec.calls
    assert probe.params[0].value == "x'; delete from orders; -- $$ #"


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


# -- round 3: non-ASCII, leading tokens, every forbidden word, T-SQL wrapping ---------------

NON_ASCII = {
    "dotless i (Spark folds it to I)": "SELECT a FROM t WHERE x = 1 " + chr(0x0131) + "NSERT",
    "long s": "SELECT a FROM t " + chr(0x017F) + "et",
    "dotted capital I": "SELECT a FROM t " + chr(0x0130) + "NTO",
    "Kelvin sign": "SELECT a FROM t " + chr(0x212A) + "ILL",
    "zero-width space": "SELECT a FROM t WHERE x" + chr(0x200B) + "= 1",
    "no-break space": "SELECT" + chr(0x00A0) + "a FROM t",
    "fullwidth": "SELECT a FROM t WHERE x " + chr(0xFF1D) + " 1",
    "BOM": chr(0xFEFF) + "SELECT 1",
    "BOM inside": "SELECT 1" + chr(0xFEFF),
}


@pytest.mark.parametrize("sql", list(NON_ASCII.values()), ids=list(NON_ASCII))
def test_non_ascii_is_refused(sql: str) -> None:
    _refused(sql)


def test_non_ascii_value_passes_as_a_parameter() -> None:
    rec = Recorder()
    run_metadata(
        rec,
        "SELECT a FROM t WHERE x = {{v}}",
        {"v": chr(0x0131) + "NSERT " + chr(0x00E9) + "t" + chr(0x00E9)},
    )
    assert rec.calls[0][0].params[0].value == chr(0x0131) + "NSERT " + chr(0x00E9) + "t" + chr(
        0x00E9
    )


@pytest.mark.parametrize("sql", ["{{a}} SELECT 1", "{{a}}SELECT 1", " {{a}}"])
def test_leading_token_is_refused(sql: str) -> None:
    with pytest.raises(ExecutionError) as info:
        run_metadata(Recorder(), sql, {"a": "x"})
    assert info.value.code == "FL703"


@pytest.mark.parametrize("word", sorted(FORBIDDEN_WORDS))
def test_every_forbidden_word_is_refused_in_any_dialect(word: str) -> None:
    for dialect in (None, "duckdb", "tsql"):
        with pytest.raises(ExecutionError):
            check_metadata_sql(f"SELECT a FROM t WHERE x = 1 {word.lower()} y", dialect)


def test_round_three_words_are_listed() -> None:
    required = {
        *("PLAN", "SHARE", "SHUTDOWN", "BACKUP", "RESTORE", "DBCC", "DENY", "RECONFIGURE"),
        *("CHECKPOINT", "DISABLE", "ENABLE", "RAISERROR", "WAITFOR", "SETUSER", "REVERT"),
        *("OPEN", "CLOSE", "FETCH", "DEALLOCATE", "SEND", "RECEIVE", "MOVE", "BULK"),
        *("WRITETEXT", "UPDATETEXT", "GOTO", "THROW"),
    }
    assert required <= FORBIDDEN_WORDS


def test_oracle_explain_plan_and_for_share_are_refused() -> None:
    _refused("EXPLAIN PLAN FOR SELECT 1 FROM dual")
    _refused("SELECT * FROM information_schema.tables FOR SHARE")


class TsqlRecorder(Recorder):
    dialect = "tsql"


TSQL_BYPASSES = [
    "SELECT 1 SHUTDOWN WITH NOWAIT",
    "SELECT 1 BACKUP DATABASE x TO DISK = {{p}}",
    "SELECT 1 RESTORE DATABASE x FROM DISK = {{p}}",
    "SELECT 1 DBCC CHECKDB",
    "SELECT 1 DENY SELECT ON t TO u",
    "SELECT 1 RECONFIGURE",
    "SELECT 1 CHECKPOINT",
    "SELECT 1 DISABLE TRIGGER t ON DATABASE",
    "SELECT 1 RAISERROR({{p}}, 16, 1) WITH LOG",
    "SELECT 1 WAITFOR DELAY {{p}}",
    "IF 1=1 SHUTDOWN",
    "BACKUP DATABASE x TO DISK = {{p}}",
    "SELECT 1 SELECT 2",
    "SELECT 1 FROM t) q SHUTDOWN SELECT * FROM (SELECT 1",
    "SELECT 1 FROM t) q SELECT * FROM (SELECT 1",
    "SELECT (1",
    "SELECT 1 UNION SELECT 2",
    "SELECT name FROM [sys].[tables]",
    'SELECT "name" FROM sys.tables',
    "SELECT name FROM sys.tables WHERE name = 'x'",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "SHOW TABLES",
    "DESCRIBE t",
    "EXPLAIN SELECT 1",
]


@pytest.mark.parametrize("sql", TSQL_BYPASSES)
def test_tsql_bypasses_are_refused(sql: str) -> None:
    rec = TsqlRecorder()
    with pytest.raises(ExecutionError) as info:
        run_metadata(rec, sql, {"p": "x"} if "{{p}}" in sql else {})
    assert info.value.code == "FL703"
    assert rec.calls == []


def test_tsql_select_runs_wrapped() -> None:
    rec = TsqlRecorder()
    inner = (
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = {{s}}"
        " AND TABLE_NAME IN (SELECT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS)"
    )
    run_metadata(rec, inner, {"s": "dbo"})
    probe = rec.calls[0][0]
    assert probe.sql_template == f"SELECT * FROM ({inner}) AS fl_meta_q"
    sql, params = probe.bind("qmark")
    assert sql.startswith("SELECT * FROM (SELECT TABLE_NAME") and sql.endswith(") AS fl_meta_q")
    assert params == ("dbo",)


def test_other_dialects_are_not_wrapped() -> None:
    rec = Recorder()
    run_metadata(rec, "SELECT 1 AS a")
    assert rec.calls[0][0].sql_template == "SELECT 1 AS a"


# -- round 4: fail closed on dialect labels; rowset functions and lock hints ----------------

ROUND_FOUR_WORDS = [
    *("OPENQUERY", "OPENROWSET", "OPENDATASOURCE", "OPENXML", "ADD", "TABLOCK", "TABLOCKX"),
    *("XLOCK", "UPDLOCK", "HOLDLOCK", "PAGLOCK", "ROWLOCK", "SERIALIZABLE", "REPEATABLEREAD"),
    "READCOMMITTEDLOCK",
]


@pytest.mark.parametrize("word", ROUND_FOUR_WORDS)
def test_rowset_functions_and_lock_hints_are_refused(word: str) -> None:
    assert word in FORBIDDEN_WORDS
    for dialect in (None, "duckdb", "tsql"):
        with pytest.raises(ExecutionError) as info:
            check_metadata_sql(
                f"SELECT a FROM t WITH ({word.lower()}) WHERE b = {{{{p}}}}", dialect
            )
        assert info.value.code == "FL703"


def test_openrowset_call_is_refused() -> None:
    _refused("SELECT a FROM OPENROWSET({{p}}, {{q}}, {{r}}) AS x")


@pytest.mark.parametrize(
    "label", ["tsql", "mssql", "MSSQL", "sqlserver", "sql_server", "synapse", "fabric", "azuresql"]
)
def test_sql_server_aliases_are_canonical(label: str) -> None:
    assert canonical_dialect(label) == "tsql"
    assert CallableConnector(lambda s, p: [], label).dialect == "tsql"


def test_other_labels_are_unchanged() -> None:
    assert canonical_dialect("postgres") == "postgres"
    assert canonical_dialect("teradata") == "teradata"


def test_unknown_and_tsql_labels_use_the_strict_rules() -> None:
    assert uses_tsql_rules("tsql") and uses_tsql_rules("mssql")
    assert uses_tsql_rules("teradata")  # no native profile: fail closed
    assert not uses_tsql_rules("duckdb") and not uses_tsql_rules("postgres")
    assert not uses_tsql_rules(None)


MISLABELLED_BYPASSES = [
    "SELECT 1 SELECT 2",
    "SELECT 1 FROM t) q SELECT * FROM (SELECT 1",
    "SELECT name FROM [sys].[tables]",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
]


@pytest.mark.parametrize("label", ["mssql", "sqlserver", "synapse", "teradata", "SQL_Server"])
@pytest.mark.parametrize("sql", MISLABELLED_BYPASSES)
def test_mislabelled_tsql_bypasses_are_refused(label: str, sql: str) -> None:
    seen: list[str] = []
    conn = CallableConnector(lambda s, p: seen.append(s) or [], label)  # type: ignore[func-returns-value]
    with pytest.raises(ExecutionError) as info:
        run_metadata(conn, sql)
    assert info.value.code == "FL703"
    assert seen == []


def test_unknown_dialect_runs_wrapped() -> None:
    seen: list[str] = []

    def fn(sql: str, params: object) -> list[dict[str, Any]]:
        seen.append(sql)
        return [{"a": 1}]

    run_metadata(
        CallableConnector(fn, "teradata"), "SELECT a FROM dbc.tables WHERE b = {{p}}", {"p": 1}
    )  # type: ignore[arg-type]
    assert seen == ["SELECT * FROM (SELECT a FROM dbc.tables WHERE b = ?) AS fl_meta_q"]
