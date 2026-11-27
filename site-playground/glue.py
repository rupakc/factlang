"""The playground's Python side, run by Pyodide after the factlang wheel is unpacked.

Each ``fl_*`` function takes text (JSON for structured input) and returns a JSON string for
playground.js / engine.js. docs.yml runs this file against the freshly built wheel in a clean
venv (no extras), and the tests under ``tests/playground`` and
``tests/conformance/test_docs_examples.py`` run it against the source tree. It imports neither
``js`` nor ``duckdb``: SQL runs in DuckDB-WASM (engine.js), reached through an injected
``run`` coroutine.

The bridge (``PlaygroundConnector``) and its wire format
----------------------------------------------------------
``run(sql, params_json)`` executes one prepared statement and resolves to
``{"columns": [[name, duckdb_type], ...], "rows": [[tagged, ...], ...]}`` (a JSON string or
an equivalent mapping). ``sql`` uses ``?`` placeholders, each wrapped in an explicit
``CAST(? AS <type>)``; ``params_json`` is a JSON list of tagged values in placeholder order.

A tagged value is ``{"t": tag, "v": value}``. ``t`` is ``"null"`` for NULL, else the value
kind of the DuckDB type (``value_kind``): ``integer``, ``decimal``, ``float``, ``string``,
``boolean``, ``date``, ``timestamp``, ``time`` or ``other``. Exact values cross as strings so
nothing is rounded on the way: integers (INT64 and HUGEINT included) as decimal digits,
DECIMAL as plain digits with exactly its scale (``"209000.00"``), DATE / TIMESTAMP / TIME as
ISO-8601 (``"2024-01-02T03:04:05.000006"``; a TIMESTAMP WITH TIME ZONE carries ``+00:00``).
A finite float is a JSON number (shortest round-trip form); NaN and infinities are the
strings ``"nan"``, ``"inf"``, ``"-inf"``. Booleans are JSON booleans. ``encode_cell`` is the
reference statement of these rules (engine.js applies them to Arrow cells; the tests apply
``encode_cell`` to Python ``duckdb`` values) and ``decode_value`` rebuilds the Python value.
"""

import asyncio
import datetime as dt
import json
import math
import re
import sys
from decimal import Decimal

import factlang as fl
from factlang.connectors.base import QueryResult, normalise_rows
from factlang.engine import Checker, display_sql, string_quoter
from factlang.judge import Outcome, json_safe
from factlang.semantics.catalog import StaticCatalog
from factlang.sql import family_of, get_profile
from factlang.sql.probe import bind_template
from factlang.syntax import needs_quoting, quote_name

FL_VERSION = fl.__version__
FL_DIALECTS = json.dumps(fl.sql.native_dialects())

DUCKDB = get_profile("duckdb")

# The sample shop: the two CREATE TABLE statements of the Getting started page's first code
# block, verbatim (tests/playground/test_glue_check.py asserts they are equal).
SAMPLE_SQL = (
    """CREATE TABLE customers AS
SELECT i AS id,
       CASE WHEN i % 25 = 0 THEN NULL ELSE 'customer' || i || '@example.com' END AS email
FROM range(1, 501) AS t(i)""",
    """CREATE TABLE orders AS
SELECT i AS id,
       CASE WHEN i % 160 = 0 THEN 10000 + i ELSE 1 + (i * 7) % 500 END AS customer_id,
       CASE i % 10 WHEN 0 THEN 'refunded' WHEN 1 THEN 'open' WHEN 2 THEN 'open'
                   ELSE 'paid' END AS status,
       CAST(5 + (i * 37) % 200 AS DECIMAL(10, 2)) AS amount,
       CASE WHEN i % 10 IN (1, 2) THEN NULL
            ELSE current_date - CAST(i % 30 AS INTEGER) END AS shipped_at
FROM range(1, 2001) AS t(i)""",
)
SAMPLE_SQL_JSON = json.dumps(list(SAMPLE_SQL))

# The playground's default examples on the sample shop. Both are date-free, so a share link
# reproduces their verdicts on any day. The first is the landing page's claim ledger.
SAMPLE_EXAMPLES = (
    {
        "name": "Sample shop — an agent's answer",
        "action": "check",
        "text": "\n".join(
            [
                "# An agent's answer about the sample shop, written as claims",
                "JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%",
                "NULLS customers.email RATE <=5%",
                "VALUES orders.status IN ['paid', 'open', 'shipped']",
                "COUNT orders VALUE ==1400 WHERE status = 'paid'",
                "SUM orders.amount VALUE ==209000 TOLERANCE 1%",
                "AVG orders.amount VALUE ==100 WHERE status = 'lost'",
                "",
            ]
        ),
    },
    {
        "name": "Sample shop — data contract",
        "action": "check",
        "text": "\n".join(
            [
                "# What the sample shop promises: every line holds",
                "ROWS customers VALUE ==500",
                "UNIQUE customers.id",
                "NULLS customers.email RATE <=5%",
                "UNIQUE orders.id",
                "TYPE orders.amount IS decimal",
                "VALUES orders.status IN ['paid', 'open', 'refunded']",
                "COUNT orders VALUE ==1400 WHERE status = 'paid'",
                "RANGE orders.amount BETWEEN [5, 204]",
                "JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=97%",
                "",
            ]
        ),
    },
)
SAMPLE_EXAMPLES_JSON = json.dumps(list(SAMPLE_EXAMPLES))


def _logical_lines(text):
    out, buf = [], []
    for line in text.split("\n"):
        buf.append(line)
        if line.rstrip().endswith("\\"):
            continue
        out.append("\n".join(buf))
        buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def _position(diagnostic):
    span = diagnostic.span
    return (span.line, span.col) if span is not None else (0, 0)


def _lint(text, catalog):
    diagnostics = sorted(fl.lint(text, catalog=catalog), key=_position)
    claims = sum(1 for i in fl.parse(text).document.items if type(i).__name__ == "Claim")
    return json.dumps(
        {
            "claims": claims,
            "diagnostics": [d.to_dict() for d in diagnostics],
            "rendered": "\n\n".join(d.render(text) for d in diagnostics),
        }
    )


def fl_lint(text):
    """Every front-end diagnostic, sorted by (line, column), rendered with carets."""
    return _lint(text, None)


def fl_lint_with(text, catalog_json):
    """``fl_lint`` with the loaded tables as the catalog, so names and types are checked."""
    return _lint(text, _static_catalog(catalog_json))


def fl_format(text):
    try:
        return json.dumps({"text": fl.format(text)})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


def fl_explain(text, dialect):
    """Each claim's display SQL, template and typed parameters for ``dialect``."""
    checker, use, out = Checker(dialect=dialect), "", []
    for line in _logical_lines(text):
        body = line.strip()
        if not body or body.startswith("#"):
            continue
        if body.startswith("USE ") or body == "USE":
            use = line
            continue
        for item in checker.compile(use + "\n" + line):
            if isinstance(item, Outcome):
                out.append(
                    {
                        "claim": body,
                        "kind": "outcome",
                        "verdict": item.verdict.value,
                        "explanation": item.explanation,
                    }
                )
            else:
                out.append(
                    {
                        "claim": body,
                        "kind": "probe",
                        "sql": item.inline(),
                        "template": item.sql_template,
                        "tier": item.tier,
                        "params": [[p.name, repr(p.value), p.family] for p in item.params],
                    }
                )
    return json.dumps({"dialect": dialect, "claims": out})


# -- value marshalling -----------------------------------------------------------------------

TAGS = ("null", "integer", "decimal", "float", "string", "boolean", "date", "timestamp", "time")
_KINDS = (
    (r"U?(TINYINT|SMALLINT|INTEGER|BIGINT|HUGEINT)", "integer"),
    (r"(DECIMAL|NUMERIC)(\s*\(.*\))?", "decimal"),
    (r"DOUBLE|FLOAT|REAL", "float"),
    (r"VARCHAR(\s*\(.*\))?", "string"),
    (r"BOOLEAN", "boolean"),
    (r"DATE", "date"),
    (r"TIMESTAMP(_S|_MS|_NS| WITH TIME ZONE|TZ)?", "timestamp"),
    (r"TIME", "time"),
)
_INT64 = (-(2**63), 2**63)
_INT128 = (-(2**127), 2**127)


def value_kind(duckdb_type):
    """The wire tag for a column of DuckDB type ``duckdb_type`` (as ``DESCRIBE`` names it)."""
    name = str(duckdb_type).strip().upper()
    for pattern, kind in _KINDS:
        if re.fullmatch(pattern, name):
            return kind
    return "other"


def _float_text(value):
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return None


def _utc_naive(value):
    if value.tzinfo is None:
        return value
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def encode_cell(value, kind):
    """A result cell as engine.js sends it: ``value`` of a column whose kind is ``kind``."""
    if value is None:
        return {"t": "null", "v": None}
    if kind == "integer":
        return {"t": kind, "v": str(int(value))}
    if kind == "decimal":
        return {"t": kind, "v": format(Decimal(value), "f")}
    if kind == "float":
        text = _float_text(float(value))
        return {"t": kind, "v": text if text is not None else float(value)}
    if kind == "boolean":
        return {"t": kind, "v": bool(value)}
    if kind in ("date", "time"):
        return {"t": kind, "v": value.isoformat()}
    if kind == "timestamp":
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc)
        return {"t": kind, "v": value.isoformat()}
    if kind == "string":
        return {"t": kind, "v": str(value)}
    return {"t": "other", "v": str(value)}


def decode_value(tagged):
    """The Python value of a tagged cell (the types Python ``duckdb`` itself returns)."""
    tag, value = tagged["t"], tagged.get("v")
    if tag == "null" or value is None:
        return None
    if tag == "integer":
        return int(value)
    if tag == "decimal":
        return Decimal(value)
    if tag == "float":
        return float(value)
    if tag == "string":
        return str(value)
    if tag == "boolean":
        return bool(value)
    if tag == "date":
        return dt.date.fromisoformat(value)
    if tag == "timestamp":
        return dt.datetime.fromisoformat(value)
    if tag == "time":
        return dt.time.fromisoformat(value)
    if tag == "other":
        return value
    raise ValueError(f"unknown value tag {tag!r}")


def _infer_family(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dt.datetime):
        return "timestamp"
    if isinstance(value, dt.date):
        return "date"
    return "string"


def _param_wire(value, family):
    if family == "integer":
        return str(int(value))
    if family == "decimal":
        return format(value if isinstance(value, Decimal) else Decimal(str(value)), "f")
    if family == "float":
        return repr(float(value))
    if family == "boolean":
        return bool(value)
    if family == "date":
        return value.isoformat()
    if family == "timestamp":
        if not isinstance(value, dt.datetime):
            value = dt.datetime.combine(value, dt.time())
        return _utc_naive(value).isoformat()
    return str(value)


def encode_param(value, family=None):
    """A bind parameter as tagged JSON: ``{"t": family, "v": wire value}`` (``t`` is
    ``"null"`` for ``None``). ``family`` defaults to the one ``value``'s type implies."""
    if value is None:
        return {"t": "null", "v": None}
    fam = family if family is not None else _infer_family(value)
    return {"t": fam, "v": _param_wire(value, fam)}


def param_sql_type(value, family):
    """The DuckDB type a parameter is cast to: the duckdb profile's ``param_types``, except
    integers (BIGINT, or HUGEINT past 64 bits) and decimals (their own precision and scale),
    so the bound value means exactly the literal. Timestamps bind as naive UTC TIMESTAMP, the
    duckdb profile's convention (``now_sql``) and what the evidence SQL shows."""
    if value is None:
        return DUCKDB.param_types[family]
    if family == "integer":
        number = int(value)
        if _INT64[0] <= number < _INT64[1]:
            return "BIGINT"
        if _INT128[0] <= number < _INT128[1]:
            return "HUGEINT"
        return "DOUBLE"
    if family == "decimal":
        number = value if isinstance(value, Decimal) else Decimal(str(value))
        if not number.is_finite():
            return "DOUBLE"
        _, digits, exponent = number.as_tuple()
        if exponent >= 0:
            precision, scale = len(digits) + exponent, 0
        else:
            scale = -exponent
            precision = max(len(digits), scale)
        if max(precision, 1) > 38:
            return "DOUBLE"
        return f"DECIMAL({max(precision, 1)},{scale})"
    return DUCKDB.param_types[family]


_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")  # as factlang.sql.probe binds


def prepare(probe):
    """``(sql, tagged params)``: ``probe`` bound with ``?`` placeholders, each wrapped in an
    explicit ``CAST(? AS <type>)``, and its parameters tagged in placeholder order."""
    by_name = {p.name: p for p in probe.params}
    order = [by_name[m.group(1)] for m in _TOKEN.finditer(probe.sql_template)]

    def cast(m):
        p = by_name[m.group(1)]
        return f"CAST({m.group(0)} AS {param_sql_type(p.value, p.family)})"

    sql, _ = bind_template(_TOKEN.sub(cast, probe.sql_template), probe.params, "qmark")
    return sql, [encode_param(p.value, p.family) for p in order]


def _result(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    to_py = getattr(raw, "to_py", None)
    return to_py() if to_py is not None else raw


def decode_result(raw):
    """Rows (dicts keyed by column name) from a ``run`` result."""
    result = _result(raw)
    names = [str(c[0]) for c in result["columns"]]
    return [
        {name: decode_value(cell) for name, cell in zip(names, row, strict=True)}
        for row in result["rows"]
    ]


async def _awaited(awaitable):
    return await awaitable


def _reports_timeout(exc):
    """A rejection from the browser bridge that says the query ran out of time (engine.js
    rejects with a JS ``Error`` named ``TimeoutError``; Pyodide puts the name first)."""
    return getattr(exc, "name", None) == "TimeoutError" or str(exc).startswith("TimeoutError")


class PlaygroundConnector:
    """An ``AsyncConnector`` over an injected ``run(sql, params_json)`` coroutine.

    ``timeout_s`` is enforced here: past it, the query raises ``TimeoutError`` (FL702). The
    parameters of each executed probe are kept by display SQL (``bound``) for the result cards.
    """

    dialect = "duckdb"
    paramstyle = "qmark"

    def __init__(self, run):
        self.run = run
        self.bound = {}

    async def aexecute(self, probe, *, timeout_s):
        sql, params = prepare(probe)
        shown = display_sql(probe, string_quoter(probe.dialect))
        self.bound[shown] = [[p.name, json_safe(p.value), p.family] for p in probe.params]
        limit = "" if timeout_s is None else f" after {timeout_s:g}s"
        try:
            pending = _awaited(self.run(sql, json.dumps(params)))
            raw = await (pending if timeout_s is None else asyncio.wait_for(pending, timeout_s))
        except asyncio.TimeoutError:
            raise TimeoutError(f"query timed out{limit}") from None
        except Exception as exc:
            if _reports_timeout(exc):
                raise TimeoutError(f"query timed out{limit}") from exc
            raise
        return QueryResult(normalise_rows(probe, decode_result(raw)))


# -- tables and catalog ----------------------------------------------------------------------

_OUTSIDE = re.compile(r"[^A-Za-z0-9_]")


def _names(value):
    if isinstance(value, str):
        return json.loads(value)
    to_py = getattr(value, "to_py", None)
    return list(to_py() if to_py is not None else value)


def table_name_for(filename, existing):
    """The table a CSV file loads into: the file name without its last extension, lower-cased,
    every character outside ``[A-Za-z0-9_]`` replaced by ``_``, ``t_`` before a leading digit,
    and ``_2``, ``_3``, ... appended while it clashes (case-insensitively) with a loaded table.
    ``existing`` is a list of names or its JSON."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = _OUTSIDE.sub("_", stem.lower()) or "table"
    if base[0].isdigit():
        base = "t_" + base
    taken = {str(n).lower() for n in _names(existing)}
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    return name


def spelling(name):
    """How ``name`` is written in FactLang (bare or double-quoted), or ``None`` when it cannot
    be written at all (a CR, LF or NUL in it): such a column is not addressable."""
    try:
        return quote_name(name) if needs_quoting(name) else name
    except ValueError:
        return None


def fl_table_info(name, rows, columns_json, source):
    """A TableInfo for engine.js: ``{name, spelling, rows, source, columns: [{name, type,
    family, spelling}]}``; ``columns_json`` lists ``[name, duckdb_type]`` pairs."""
    columns = _names(columns_json)
    return json.dumps(
        {
            "name": name,
            "spelling": spelling(name),
            "rows": int(rows),
            "source": source,
            "columns": [
                {
                    "name": str(col),
                    "type": str(typ),
                    "family": family_of(str(typ), DUCKDB),
                    "spelling": spelling(str(col)),
                }
                for col, typ in columns
            ],
        }
    )


def fl_catalog(tables_json):
    """``{table: {column: type family}}`` for ``StaticCatalog``, from TableInfo dicts."""
    out = {}
    for table in _names(tables_json):
        out[table["name"]] = {
            c["name"]: c.get("family") or family_of(c["type"], DUCKDB) for c in table["columns"]
        }
    return json.dumps(out)


def _static_catalog(catalog_json):
    data = json.loads(catalog_json) if isinstance(catalog_json, str) else catalog_json
    return StaticCatalog(data)


# -- checking --------------------------------------------------------------------------------


async def _acheck(text, run, catalog_json, timeout_s):
    conn = PlaygroundConnector(run)
    checker = Checker(
        conn,
        dialect="duckdb",
        catalog=_static_catalog(catalog_json),
        timeout_s=timeout_s,
        concurrency=1,
    )
    return await checker.acheck(text), conn


async def acheck_report(text, run, catalog_json, timeout_s=30):
    """The ``Report`` of checking ``text`` through ``run`` (``Checker.acheck``, never
    ``Checker.check``, which needs threads the browser does not have)."""
    report, _ = await _acheck(text, run, catalog_json, timeout_s)
    return report


def _card(outcome, bound):
    out = outcome.to_dict()
    ev = outcome.evidence
    sql = ev.sql if ev is not None else None
    params = bound.get(sql) if sql is not None else None
    if params is None:
        values = ev.params if ev is not None else ()
        params = [[f"${i}", json_safe(v), _infer_family(v)] for i, v in enumerate(values, 1)]
    elapsed = None
    if ev is not None and ev.duration_ms is not None:
        elapsed = round(ev.duration_ms)
    out.update(sql=sql, params=params, elapsed_ms=elapsed)
    return out


async def fl_check(text, run, catalog_json, timeout_s=30):
    """Check ``text`` on the loaded tables: ``{"ok", "verified", "counts": {verdict: n},
    "outcomes": [Outcome.to_dict() + {"sql", "params": [[name, value, family]],
    "elapsed_ms"}], "diagnostics": [...]}``, or ``{"error": message}`` if checking itself
    failed (a bug, not a claim outcome)."""
    try:
        report, conn = await _acheck(text, run, catalog_json, timeout_s)
        payload = {
            "ok": report.ok,
            "verified": report.verified,
            "counts": {v.value: n for v, n in report.summary().items()},
            "outcomes": [_card(o, conn.bound) for o in report.outcomes],
            "diagnostics": report.diagnostics.to_list(),
        }
    except Exception as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    return json.dumps(payload, default=str)


# -- suggested claims ------------------------------------------------------------------------

NULL_RATE_LIMIT = "5%"  # a fixed convention, never derived from the data
MAX_NULLS_SUGGESTIONS = 3
MAX_VALUES = 10
MAX_PROFILED_COLUMNS = 20
_ID_COLUMN = re.compile(r"(?i)(^|_)id$")


def _sql_ident(name):
    return '"' + name.replace('"', '""') + '"'


def _addressable(table):
    return [c for c in table["columns"] if c.get("spelling")]


def _profiled(table):
    cols = [c for c in _addressable(table) if c.get("family") == "string"]
    return cols[:MAX_PROFILED_COLUMNS]


def _load(value):
    return json.loads(value) if isinstance(value, str) else value


def fl_profile_sql(table_json):
    """The profiling queries ``fl_suggest`` needs for one table (JSON list of SQL): one query
    counting the distinct values of each text column, with the values themselves (as JSON)
    when there are at most ten."""
    table = _load(table_json)
    cols = _profiled(table)
    if not cols:
        return json.dumps([])
    parts = []
    for i, col in enumerate(cols):
        q = _sql_ident(col["name"])
        parts.append(f"COUNT(DISTINCT {q}) AS n{i}")
        parts.append(
            f"CASE WHEN COUNT(DISTINCT {q}) <= {MAX_VALUES} THEN CAST(to_json(list(DISTINCT {q} "
            f"ORDER BY {q}) FILTER (WHERE {q} IS NOT NULL)) AS VARCHAR) END AS v{i}"
        )
    return json.dumps([f"SELECT {', '.join(parts)} FROM {_sql_ident(table['name'])}"])


def _string_literal(text):
    return "'" + text.replace("'", "''") + "'"


def _representable(text):
    return isinstance(text, str) and not any(ch in text for ch in "\r\n\x00")


def _values_claim(table, profiles):
    if not profiles:
        return None
    rows = decode_result(profiles[0])
    if not rows:
        return None
    row = rows[0]
    for i, col in enumerate(_profiled(table)):
        count, values = row.get(f"n{i}"), row.get(f"v{i}")
        if not count or count > MAX_VALUES or values is None:
            continue
        items = json.loads(values)
        if not items or not all(_representable(v) for v in items):
            continue
        listed = ", ".join(_string_literal(v) for v in items)
        return f"VALUES {table['spelling']}.{col['spelling']} IN [{listed}]"
    return None


def fl_suggest(table_json, profile_json):
    """Suggested claims for one table (JSON list of strings): starting points, not facts.

    ``ROWS <t> VALUE >0``; ``UNIQUE`` for the first column named ``id`` or ``…_id``;
    ``NULLS … RATE <=5%`` for the first three columns; ``VALUES … IN [...]`` for the first text
    column with one to ten distinct values (from ``fl_profile_sql``'s results,
    ``profile_json``). Deterministic; unaddressable columns are skipped.
    """
    table = _load(table_json)
    t = table.get("spelling") or spelling(table["name"])
    if t is None:
        return json.dumps([])
    cols = _addressable(table)
    claims = [f"ROWS {t} VALUE >0"]
    ids = [c for c in cols if _ID_COLUMN.search(c["name"])]
    if ids:
        claims.append(f"UNIQUE {t}.{ids[0]['spelling']}")
    for col in cols[:MAX_NULLS_SUGGESTIONS]:
        claims.append(f"NULLS {t}.{col['spelling']} RATE <={NULL_RATE_LIMIT}")
    values = _values_claim(dict(table, spelling=t), _load(profile_json))
    if values is not None:
        claims.append(values)
    return json.dumps([c for c in claims if not fl.lint(c).has_errors])


# -- smoke -----------------------------------------------------------------------------------


def smoke():
    """Exercise every entry point once; raises on any surprise (used by docs.yml)."""
    dialects = json.loads(FL_DIALECTS)
    assert "duckdb" in dialects and len(dialects) >= 11, dialects
    lint = json.loads(fl_lint("NULLS orders.id RATE <=120%\nUNIQE orders.id\n"))
    assert [d["code"] for d in lint["diagnostics"]] == ["FL309", "FL300"], lint
    assert json.loads(fl_format("ROWS orders VALUE=1"))["text"] == "ROWS orders VALUE == 1\n"
    assert "error" in json.loads(fl_format("ROWS orders VALUE"))
    for dialect in dialects:
        claims = json.loads(fl_explain("USE shop\nUNIQUE orders.id\nBOGUS x\n", dialect))["claims"]
        assert [c["kind"] for c in claims] == ["probe", "outcome"], (dialect, claims)
        assert claims[0]["sql"].startswith(("SELECT", "WITH")), (dialect, claims)
    assert table_name_for("Orders 2024.csv", ["orders"]) == "orders_2024"
    info = fl_table_info("orders", 2, json.dumps([["id", "BIGINT"], ["note", "VARCHAR"]]), "csv")
    catalog = fl_catalog(json.dumps([json.loads(info)]))
    assert json.loads(catalog) == {"orders": {"id": "integer", "note": "string"}}
    assert json.loads(fl_lint_with("UNIQUE orders.nope\n", catalog))["diagnostics"]
    assert json.loads(fl_suggest(info, "[]"))[:2] == ["ROWS orders VALUE >0", "UNIQUE orders.id"]
    assert json.loads(fl_profile_sql(info))[0].startswith('SELECT COUNT(DISTINCT "note")')

    async def run(sql, params_json):
        assert sql.startswith("SELECT COUNT(*) AS row_count"), sql
        assert json.loads(params_json) == [{"t": "string", "v": "x"}]
        cell = encode_cell(2, "integer")
        return json.dumps({"columns": [["row_count", "BIGINT"]], "rows": [[cell]]})

    out = json.loads(asyncio.run(fl_check("ROWS orders VALUE ==2 WHERE note = 'x'", run, catalog)))
    assert [o["verdict"] for o in out["outcomes"]] == ["pass"], out
    return "ok " + FL_VERSION


# Pyodide's runPython also runs this file as ``__main__``: the smoke run is for CPython only.
if __name__ == "__main__" and sys.platform != "emscripten":
    print(smoke())
