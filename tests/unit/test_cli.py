"""The ``factlang`` command (spec 10): subcommands, ``--format``, exit codes 0/1/2/3."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from factlang import cli
from factlang.connectors import DBAPIConnector


def run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def write(tmp_path: Path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- lint -------------------------------------------------------------------------------


def test_lint_clean_file_exits_0(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = write(tmp_path, "ok.fl", "UNIQUE orders.id\n")
    code, out, _ = run(["lint", f], capsys)
    assert code == 0 and "no problems" in out


def test_lint_errors_exit_2_with_carets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good = write(tmp_path, "ok.fl", "UNIQUE orders.id\n")
    bad = write(tmp_path, "bad.fl", "UNIQUE orders.id\nBOGUS orders.id\n")
    code, out, _ = run(["lint", good, bad], capsys)
    assert code == 2
    assert f"{bad}:2:1: error[FL300]" in out and "^^^^^" in out and good not in out


def test_lint_json_and_markdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = write(tmp_path, "bad.fl", "BOGUS orders.id\n")
    code, out, _ = run(["--format", "json", "lint", bad], capsys)
    data = json.loads(out)
    assert code == 2 and data["ok"] is False
    assert data["files"][0]["file"] == bad
    assert [d["code"] for d in data["files"][0]["diagnostics"]] == ["FL300"]
    # --format is accepted after the subcommand too
    code, out, _ = run(["lint", bad, "--format", "markdown"], capsys)
    assert code == 2 and "| file | line | code | severity | message |" in out and "FL300" in out


def test_missing_file_is_invalid_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(["lint", str(tmp_path / "nope.fl")], capsys)
    assert code == 2 and "nope.fl" in err


# --- fmt --------------------------------------------------------------------------------


def test_fmt_check_and_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = write(tmp_path, "a.fl", "JOIN a.x->b.y MATCH >= 99%   # why\n")
    code, out, _ = run(["fmt", "--check", f], capsys)
    assert code == 1 and "would reformat" in out
    assert Path(f).read_text(encoding="utf-8") == "JOIN a.x->b.y MATCH >= 99%   # why\n"
    code, out, _ = run(["fmt", f], capsys)
    assert code == 0 and "reformatted" in out
    assert Path(f).read_text(encoding="utf-8") == "JOIN a.x -> b.y MATCH >= 99%  # why\n"
    code, out, _ = run(["fmt", "--check", f], capsys)
    assert code == 0


def test_fmt_refuses_broken_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = "UNIQUE orders.id\nUNIQUE orders.id MATCH\n"
    f = write(tmp_path, "a.fl", text)
    code, _, err = run(["fmt", f], capsys)
    assert code == 2 and "FL20" in err
    assert Path(f).read_text(encoding="utf-8") == text


def test_fmt_refuses_to_drop_comment_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = "# header comment\nUNIQUE   orders.id\n"
    f = write(tmp_path, "a.fl", text)
    code, _, err = run(["fmt", f], capsys)
    assert code == 2 and "comment" in err
    assert Path(f).read_text(encoding="utf-8") == text


def test_fmt_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE   orders.id\n")
    code, out, _ = run(["fmt", "--check", "--format", "json", f], capsys)
    assert code == 1 and json.loads(out) == {"files": [{"file": f, "changed": True}]}


# --- explain ----------------------------------------------------------------------------


def test_explain_prints_sql(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = write(
        tmp_path, "a.fl", "UNIQUE orders.id\nNULLS orders.status RATE == 0 WHERE x = 'it''s'\n"
    )
    code, out, _ = run(["explain", f, "--dialect", "postgres"], capsys)
    assert code == 0
    assert "-- UNIQUE orders.id" in out and "SELECT" in out and "'it''s'" in out


def test_explain_json_and_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = write(tmp_path, "a.fl", "BOGUS orders.id\nUNIQUE orders.id\nUNIQUE (\n")
    code, out, _ = run(["--format", "json", "explain", f, "--dialect", "duckdb"], capsys)
    items = json.loads(out)
    assert code == 2
    assert [i["claim"] for i in items] == ["BOGUS orders.id", "UNIQUE orders.id", "UNIQUE ("]
    assert items[0]["verdict"] == "invalid" and "sql" in items[1]
    assert items[1]["sql_template"] and isinstance(items[1]["params"], list)
    assert items[2]["verdict"] == "invalid"


def test_explain_unknown_dialect_is_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\n")
    code, out, _ = run(["explain", f, "--dialect", "nonesuch"], capsys)
    assert code == 3 and "error" in out


def test_explain_requires_dialect(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\n")
    with pytest.raises(SystemExit) as exc:
        cli.main(["explain", f])
    assert exc.value.code == 2


# --- check ------------------------------------------------------------------------------


@pytest.fixture
def connector_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A module exposing a DuckDB connector, a DB-API connection and a factory."""
    pytest.importorskip("duckdb")
    db = tmp_path / "shop.duckdb"
    name = f"fl_cli_conn_{uuid.uuid4().hex}"
    (tmp_path / f"{name}.py").write_text(
        "import duckdb\n"
        "from factlang import CallableConnector, DBAPIConnector\n"
        f"conn = duckdb.connect({str(db)!r})\n"
        "conn.execute('CREATE TABLE orders (id INTEGER, customer_id INTEGER)')\n"
        "conn.execute('INSERT INTO orders VALUES (1, 10), (2, 10), (3, NULL)')\n"
        "connector = DBAPIConnector(conn, 'duckdb')\n"
        "def make():\n"
        "    return connector\n"
        "not_a_connector = 42\n"
        "def _run(sql, params):\n"
        "    if 'nope' in sql or 'nope' in str(params):\n"
        "        raise RuntimeError('boom')\n"
        "    cur = conn.execute(sql, params)\n"
        "    cols = [d[0] for d in cur.description]\n"
        "    return [dict(zip(cols, r)) for r in cur.fetchall()]\n"
        "picky = CallableConnector(_run, 'duckdb')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_check_pass_exits_0(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\nNULLS orders.id RATE == 0\n")
    code, out, _ = run(["check", f, "--connector", f"{connector_module}:connector"], capsys)
    assert code == 0 and "PASS" in out


def test_check_fail_exits_1_and_json_is_the_report(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\nUNIQUE orders.customer_id\n")
    args = ["--format", "json", "check", f, "--connector", f"{connector_module}:make"]
    code, out, _ = run(args, capsys)
    data = json.loads(out)
    assert code == 1 and data["ok"] is False
    assert [o["verdict"] for o in data["outcomes"]] == ["pass", "fail"]
    code, out, _ = run([*args[2:], "--format", "markdown"], capsys)
    assert code == 1 and out.startswith("# FactLang report")


def test_check_invalid_claim_exits_2(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.customer_id\nBOGUS orders.id\n")
    code, _, _ = run(["check", f, "--connector", f"{connector_module}:connector"], capsys)
    assert code == 2  # invalid input outranks a FAIL


def test_check_error_exits_3(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE nope.id\n")
    code, out, _ = run(["check", f, "--connector", f"{connector_module}:picky"], capsys)
    assert code == 3 and "ERROR" in out


def test_check_no_data_is_undecidable_exit_3(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id WHERE id > 100\n")
    code, out, _ = run(["check", f, "--connector", f"{connector_module}:connector"], capsys)
    assert code == 3 and "NO_DATA" in out


def test_check_fail_outranks_error(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.customer_id\nUNIQUE nope.id\n")
    code, _, _ = run(["check", f, "--connector", f"{connector_module}:picky"], capsys)
    assert code == 1


def test_check_dbapi_connection_needs_dialect(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str]
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\n")
    target = f"{connector_module}:conn"
    code, _, err = run(["check", f, "--connector", target], capsys)
    assert code == 2 and "--dialect" in err
    code, out, _ = run(["check", f, "--connector", target, "--dialect", "duckdb"], capsys)
    assert code == 0 and "PASS" in out


@pytest.mark.parametrize(
    "target", ["nosuchmodule_xyz:conn", "{mod}:missing", "{mod}:not_a_connector", "no-colon"]
)
def test_check_bad_connector_is_invalid_input(
    tmp_path: Path, connector_module: str, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\n")
    code, _, err = run(["check", f, "--connector", target.format(mod=connector_module)], capsys)
    assert code == 2 and err


def test_check_dsn_uses_sqlalchemy_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    conn.execute("CREATE TABLE orders (id INTEGER)")
    conn.execute("INSERT INTO orders VALUES (1), (2)")
    seen: list[str] = []

    def fake_connect(dsn: str) -> Any:
        seen.append(dsn)
        return DBAPIConnector(conn, "duckdb")

    monkeypatch.setattr(cli, "connect", fake_connect)
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\n")
    code, out, _ = run(["check", f, "--dsn", "duckdb:///:memory:"], capsys)
    assert code == 0 and seen == ["duckdb:///:memory:"] and "PASS" in out


def test_check_dsn_failure_is_invalid_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(dsn: str) -> Any:
        raise ImportError("sqlalchemy is not installed")

    monkeypatch.setattr(cli, "connect", boom)
    f = write(tmp_path, "a.fl", "UNIQUE orders.id\n")
    code, _, err = run(["check", f, "--dsn", "x://"], capsys)
    assert code == 2 and "sqlalchemy" in err


def test_check_needs_exactly_one_source(tmp_path: Path) -> None:
    f = str(tmp_path / "a.fl")
    for args in (["check", f], ["check", f, "--dsn", "a://", "--connector", "m:a"]):
        with pytest.raises(SystemExit) as exc:
            cli.main(args)
        assert exc.value.code == 2


# --- verbs ------------------------------------------------------------------------------


def test_verbs_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["verbs"], capsys)
    assert code == 0 and "UNIQUE" in out and "JOIN" in out
    code, out, _ = run(["verbs", "--json"], capsys)
    data = json.loads(out)
    unique = next(v for v in data if v["name"] == "UNIQUE")
    assert code == 0 and unique["summary"] and unique["operands"][0]["kind"] == "COLUMN"
    code, out2, _ = run(["--format", "json", "verbs"], capsys)
    assert json.loads(out2) == data
    code, out3, _ = run(["verbs", "--format", "markdown"], capsys)
    assert code == 0 and "| verb |" in out3


# --- entry points -----------------------------------------------------------------------


def test_no_command_prints_help_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run([], capsys)
    assert code == 2 and "usage" in err


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0 and "0.1.0" in capsys.readouterr().out


def test_python_dash_m(tmp_path: Path) -> None:
    f = write(tmp_path, "a.fl", "BOGUS orders.id\n")
    src = str(Path(cli.__file__).resolve().parents[1])
    proc = subprocess.run(
        [sys.executable, "-m", "factlang", "lint", f],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": src},  # keep SYSTEMROOT etc. (Windows)
        check=False,
    )
    assert proc.returncode == 2 and "FL300" in proc.stdout
