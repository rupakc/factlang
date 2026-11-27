"""Test-side stand-ins for what the browser does with DuckDB-WASM (``site-playground/engine.js``).

``load_glue()`` loads ``site-playground/glue.py`` as a module (the browser runs the same file
in Pyodide). ``make_run(con)`` returns the ``run(sql, params_json)`` coroutine the playground
connector awaits, backed by Python ``duckdb`` and going through the same tagged-JSON wire
format as the browser: the column types come from ``DESCRIBE <sql>`` with the same bound
parameters, every cell is encoded by ``glue.encode_cell`` (the rule engine.js applies to Arrow
cells) and the whole result crosses a ``json.dumps``/``json.loads`` round trip.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
GLUE = ROOT / "site-playground" / "glue.py"
FIXTURES = Path(__file__).resolve().parent
_NAME = "factlang_playground_glue"


def load_glue() -> ModuleType:
    cached = sys.modules.get(_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(_NAME, GLUE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    spec.loader.exec_module(module)
    return module


def query(con: Any, sql: str, values: list[Any]) -> dict[str, Any]:
    """``{"columns": [[name, type]], "rows": [[tagged]]}`` for ``sql`` on ``con``."""
    glue = load_glue()
    described = con.execute("DESCRIBE " + sql, values).fetchall()
    columns = [[str(row[0]), str(row[1])] for row in described]
    records = con.execute(sql, values).fetchall()
    kinds = [glue.value_kind(t) for _, t in columns]
    rows = [[glue.encode_cell(v, k) for v, k in zip(rec, kinds, strict=True)] for rec in records]
    return {"columns": columns, "rows": rows}


def make_run(con: Any, log: list[dict[str, Any]] | None = None) -> Callable[..., Awaitable[str]]:
    async def run(sql: str, params_json: str) -> str:
        params = json.loads(params_json)
        values = [p["v"] for p in params]
        result = query(con, sql, values)
        if log is not None:
            log.append({"sql": sql, "params": params, "result": result})
        return json.dumps(result)

    return run


def shop(glue: ModuleType | None = None) -> Any:
    """An in-memory DuckDB holding the playground's sample shop."""
    import duckdb

    g = glue if glue is not None else load_glue()
    con = duckdb.connect()
    for statement in g.SAMPLE_SQL:
        con.execute(statement)
    return con
