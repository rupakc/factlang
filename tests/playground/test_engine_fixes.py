"""Review fixes to the playground engine (site spec 5.2): the timeout keeps its cause, the
DuckDB download has a deadline, only real ICU failures are reworded, and a crash during
Reset never brings the dropped CSV files back.

The engine.js tests run the module itself in Node (skipped when ``node`` is absent) with a
fake DuckDB module and a fake Pyodide: they pin the engine's control flow, not DuckDB.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

import factlang as fl
from factlang.connectors.base import ExecutionError
from factlang.sql import RenderedProbe

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ENGINE = ROOT / "site-playground" / "engine.js"
NODE = shutil.which("node")


def _probe() -> RenderedProbe:
    (probe,) = [p for p in fl.Checker(dialect="duckdb").compile("UNIQUE orders.id")]
    assert isinstance(probe, RenderedProbe)
    return probe


class _BrowserError(Exception):
    """Stands in for Pyodide's JsException: the JS error name leads the message."""


@pytest.mark.parametrize("where", ["python", "browser"])
def test_a_timeout_keeps_its_cause(glue: ModuleType, where: str) -> None:
    raised: list[BaseException] = []

    async def run(sql: str, params_json: str) -> str:
        if where == "python":
            await asyncio.sleep(5)
        err = _BrowserError("TimeoutError: query timed out after 30s")
        raised.append(err)
        raise err

    conn = glue.PlaygroundConnector(run, 30 if where == "browser" else None)
    with pytest.raises(ExecutionError) as info:
        asyncio.run(conn.aexecute(_probe(), timeout_s=0.1 if where == "python" else 30))
    err = info.value
    assert "FL702" in str(err) or getattr(err, "code", None) == "FL702"
    assert err.__cause__ is not None, "the cause the glue sets must survive the raise"
    if where == "browser":
        assert err.__cause__ is raised[0]
    else:
        assert isinstance(err.__cause__, (asyncio.TimeoutError, TimeoutError))


# -- engine.js under Node ----------------------------------------------------------------------

needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _node(tmp_path: Path, body: str) -> dict[str, object]:
    """Run ``body`` (an async JS function body with ``E`` = the engine module) and return
    the JSON it prints last."""
    module = tmp_path / "engine.mjs"
    module.write_text(ENGINE.read_text(encoding="utf-8"), encoding="utf-8")
    script = tmp_path / "run.mjs"
    harness = (HERE / "fixtures" / "engine_harness.mjs").read_text(encoding="utf-8")
    script.write_text(
        harness + "\nconst E = await import('./engine.mjs');\n"
        "const out = await (async () => {\n" + textwrap.dedent(body) + "\n})();\n"
        "console.log(JSON.stringify(out));\nprocess.exit(0);\n",
        encoding="utf-8",
    )
    assert NODE is not None
    proc = subprocess.run(
        [NODE, str(script)], cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result: dict[str, object] = json.loads(proc.stdout.strip().splitlines()[-1])
    return result


@needs_node
def test_a_crash_during_reset_does_not_reload_the_dropped_csvs(tmp_path: Path) -> None:
    out = _node(
        tmp_path,
        """
        const engine = await E.startEngine({ pyodide: __pyodide, _importDuckdb: __fake });
        const loaded = await engine.loadCsv([{ name: "people.csv", size: 10 }]);
        __crashOn = "DROP TABLE";
        __log.length = 0;
        let failed = null;
        try { await engine.reset(); } catch (e) { failed = String(e.message || e); }
        return {
          loaded: loaded.tables.map((t) => t.name),
          failed,
          restarted: engine.restarts,
          reloaded: __log.filter((s) => /read_csv_auto|^register /.test(s)),
          tables: engine.tables().map((t) => t.name),
        };
        """,
    )
    assert out["loaded"] == ["people"]
    assert out["failed"], "the crash is reported"
    assert out["restarted"] == 1, "the worker was restarted"
    assert out["reloaded"] == [], "a CSV being reset must not come back after the restart"
    assert out["tables"] == ["customers"]


@needs_node
def test_a_crash_while_loading_keeps_earlier_csvs(tmp_path: Path) -> None:
    """Negative control for the test above: outside Reset, a restart DOES re-load CSVs."""
    out = _node(
        tmp_path,
        """
        const engine = await E.startEngine({ pyodide: __pyodide, _importDuckdb: __fake });
        await engine.loadCsv([{ name: "people.csv", size: 10 }]);
        __crashOn = "CREATE TABLE \\"orders\\"";
        __log.length = 0;
        const second = await engine.loadCsv([{ name: "orders.csv", size: 10 }]);
        return {
          errors: second.errors.length,
          reloaded: __log.filter((s) => /read_csv_auto/.test(s)).length,
          tables: engine.tables().map((t) => t.name),
        };
        """,
    )
    assert out["errors"] == 1
    assert out["reloaded"] >= 2, "people.csv was loaded again after the restart"
    assert out["tables"] == ["customers", "people"]


@needs_node
def test_the_duckdb_download_has_a_deadline(tmp_path: Path) -> None:
    out = _node(
        tmp_path,
        """
        const hang = await __msg(E.loadDuckdb(() => new Promise(() => {}), 50));
        const offline = () => Promise.reject(new TypeError("Failed to fetch"));
        const fail = await __msg(E.loadDuckdb(offline, 50));
        const ok = await E.loadDuckdb(() => Promise.resolve({ fine: true }), 50);
        return { hang, fail, ok: ok.fine === true, deadline: E.IMPORT_DEADLINE_MS };
        """,
    )
    assert "CDN" in str(out["hang"]) and "within" in str(out["hang"])
    assert "CDN" in str(out["fail"]) and "Failed to fetch" in str(out["fail"])
    assert out["ok"] is True
    assert out["deadline"] == 90000


@needs_node
def test_a_failed_download_is_retried(tmp_path: Path) -> None:
    out = _node(
        tmp_path,
        """
        let calls = 0;
        const flaky = () => {
          calls += 1;
          return calls === 1 ? Promise.reject(new Error("offline")) : __fake();
        };
        const first = await __msg(E.startEngine({ pyodide: __pyodide, _importDuckdb: flaky }));
        const second = await __msg(E.startEngine({ pyodide: __pyodide, _importDuckdb: flaky }));
        return { first, second, calls };
        """,
    )
    assert "could not download DuckDB from the CDN" in str(out["first"])
    assert out["second"] == "ok"
    assert out["calls"] == 2


ICU_MISSING = [
    'Catalog Error: Scalar Function with name "current_date" is not in the catalog, but it '
    "exists in the icu extension.\n\nPlease try installing and loading the icu extension",
    "Catalog Error: Scalar Function with name timezone does not exist!",
    "Missing Extension Error: Extension icu is not loaded",
    'IO Error: Failed to download extension "icu" at URL '
    '"https://extensions.duckdb.org/v1.4.3/wasm_eh/icu.duckdb_extension.wasm"',
]
NOT_ICU = [
    'Binder Error: Referenced column "timezone" not found in FROM clause!\nCandidate '
    'bindings: "tz_name"',
    'Binder Error: Referenced column "time zone" not found in FROM clause!',
    'Binder Error: Referenced column "icu" not found in FROM clause!',
    'Conversion Error: Could not convert string "Europe/Paris" to INT32 (column timezone)',
    "Catalog Error: Table with name icu does not exist!",
]


@needs_node
def test_only_real_icu_failures_are_reworded(tmp_path: Path) -> None:
    out = _node(
        tmp_path,
        f"return {{ yes: {json.dumps(ICU_MISSING)}.map(E.isIcuError),"
        f" no: {json.dumps(NOT_ICU)}.map(E.isIcuError) }};",
    )
    assert out["yes"] == [True] * len(ICU_MISSING)
    assert out["no"] == [False] * len(NOT_ICU)
