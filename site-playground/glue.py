"""The playground's Python side, run by Pyodide after the factlang wheel is unpacked.

Each function takes text and returns a JSON string for playground.js. Nothing here executes
SQL. docs.yml runs this file against the freshly built wheel in a clean venv (no extras), and
tests/conformance/test_docs_examples.py runs it against the source tree.
"""

import json

import factlang as fl
from factlang.engine import Checker
from factlang.judge import Outcome

FL_VERSION = fl.__version__
FL_DIALECTS = json.dumps(fl.sql.native_dialects())


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


def fl_lint(text):
    """Every front-end diagnostic, sorted by (line, column), rendered with carets."""
    diagnostics = sorted(fl.lint(text), key=_position)
    claims = sum(1 for i in fl.parse(text).document.items if type(i).__name__ == "Claim")
    return json.dumps(
        {
            "claims": claims,
            "diagnostics": [d.to_dict() for d in diagnostics],
            "rendered": "\n\n".join(d.render(text) for d in diagnostics),
        }
    )


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
    return "ok " + FL_VERSION


if __name__ == "__main__":
    print(smoke())
