"""``assert`` in ``src/`` is for internal invariants only (0.1.3 spec 3.3 P4).

A guard on a value that comes from a caller, a catalog, a driver or a plan must hold under
``python -O`` too, so it is an explicit ``raise`` of the class callers already saw. Each
converted guard is exercised here in a ``-O`` subprocess; every ``assert`` left in ``src/``
is pinned to its function, so a new one is a reviewed decision, not an accident.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from collections import Counter
from pathlib import Path

import pytest

import factlang

SRC = Path(factlang.__file__).resolve().parent

# (module, enclosing function) -> number of asserts: unreachable-state checks and type
# narrowing of values FactLang itself produced (parser/lexer output, validated primitives,
# the analyser's typed claim values, the engine's own slot state, the renderer's own type
# dispatch). None guards a host-supplied value. The counts are informational (what the
# review saw): the test pins by function, so only the keys are enforced, never the numbers.
INTERNAL_ASSERTS: dict[tuple[str, str], int] = {
    ("engine.py", "Checker.compile"): 1,
    ("engine.py", "Checker._error"): 1,
    ("engine.py", "Checker._results"): 2,
    ("engine.py", "Checker._finish"): 1,
    ("optimizer.py", "optimize"): 1,
    ("packs/standard/__init__.py", "standard_pack"): 1,
    ("packs/standard/_common.py", "key_plan"): 1,
    ("packs/standard/exists.py", "catalog_facts"): 1,
    ("packs/standard/join.py", "_side"): 1,
    ("packs/standard/join.py", "plan"): 1,
    ("packs/standard/pattern.py", "plan"): 1,
    ("packs/standard/range.py", "_in_range"): 3,
    ("packs/standard/range.py", "derive"): 1,
    ("packs/standard/values.py", "plan"): 2,
    ("packs/standard/values.py", "derive"): 1,
    ("semantics/filters.py", "_flatten"): 1,
    ("semantics/types.py", "_decimal"): 1,
    ("semantics/types.py", "_ratio_validate"): 1,
    ("semantics/types.py", "_separator_validate"): 1,
    ("semantics/types.py", "_like_pattern_validate"): 1,
    ("semantics/types.py", "_items"): 1,
    ("semantics/types.py", "_value_text"): 1,
    ("sql/fragment.py", "lower_join"): 1,
    ("sql/lower.py", "Lowerer._aggregate"): 1,
    ("sql/lower.py", "Lowerer.column_aggregate"): 1,
    ("sql/lower.py", "Lowerer.composite_aggregate"): 1,
    ("sql/lower.py", "Lowerer.key_overlap"): 1,
    ("sql/probe.py", "_inline_value"): 7,
    ("sql/sqlglot_bridge.py", "_Converter.catalog_columns"): 1,
    ("syntax/formatter.py", "_shift"): 1,
    ("syntax/formatter.py", "_literal"): 4,
    ("syntax/lexer.py", "_percent"): 1,
    ("syntax/parser.py", "_span_of"): 1,
}


def _asserts() -> Counter[tuple[str, str]]:
    """``(module, qualified function)`` -> number of asserts; a nested function counts
    under the function (or method) that contains it."""
    found: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, rel: str, scope: tuple[str, ...], in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Assert):
                found[(rel, ".".join(scope) or "<module>")] += 1
            if isinstance(child, ast.ClassDef) and not in_function:
                visit(child, rel, (*scope, child.name), False)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not in_function:
                visit(child, rel, (*scope, child.name), True)
            else:
                visit(child, rel, scope, in_function)

    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        visit(ast.parse(path.read_text(encoding="utf-8")), rel, (), False)
    return found


def test_every_assert_left_in_src_is_a_reviewed_internal_invariant() -> None:
    # Pinned by function, not by count: another assert of the same kind inside a reviewed
    # function (say, one more branch of the renderer's own type dispatch) needs no new pin.
    found = _asserts()
    unreviewed = {k: n for k, n in found.items() if k not in INTERNAL_ASSERTS}
    assert not unreviewed, (
        f"unreviewed assert(s) in src/: {unreviewed}. If one guards a value from a caller, "
        "catalog, driver or plan, make it an explicit raise (an assert vanishes under "
        "python -O); if it is a pure internal invariant, pin its function in INTERNAL_ASSERTS."
    )
    stale = sorted(k for k in INTERNAL_ASSERTS if k not in found)
    assert not stale, f"INTERNAL_ASSERTS names functions with no assert left: {stale}"


# Each snippet must raise ``AssertionError`` (the class callers saw before the conversion)
# under ``python -O``, where a bare ``assert`` would have let the value through.
GUARDS = {
    "CachedCatalog.table_exists": """
        from factlang.semantics.catalog import CachedCatalog
        from factlang.semantics.refs import Ident, TableRef
        class Lying:
            def table_exists(self, ref): return "yes"
            def columns(self, ref): return None
        CachedCatalog(Lying()).table_exists(TableRef(None, None, Ident("orders")))
    """,
    "CachedCatalog.columns": """
        from factlang.semantics.catalog import CachedCatalog
        from factlang.semantics.refs import Ident, TableRef
        class Lying:
            def table_exists(self, ref): return True
            def columns(self, ref): return [("id", "integer")]
        CachedCatalog(Lying()).columns(TableRef(None, None, Ident("orders")))
    """,
    "UnionAll first branch": """
        from factlang.semantics.refs import Ident, TableRef
        from factlang.sql import ast as A
        s = A.Select((A.Aliased(A.Agg("COUNT", None), "n"),), A.TableName(
            TableRef(None, None, Ident("orders"))))
        A.UnionAll((A.UnionAll((s, s)), s))
    """,
    "UnionAll later branch": """
        from factlang.semantics.refs import Ident, TableRef
        from factlang.sql import ast as A
        s = A.Select((A.Aliased(A.Agg("COUNT", None), "n"),), A.TableName(
            TableRef(None, None, Ident("orders"))))
        A.UnionAll((s, A.UnionAll((s, s))))
    """,
    "format_number of a non-finite caller value": """
        from decimal import Decimal
        import factlang as fl
        from factlang.semantics.filters import FCompare, Param
        t = fl.TableRef(None, None, fl.Ident("orders"))
        flt = FCompare(fl.Ident("amount"), "=", Param("p1", Decimal("NaN"), "decimal"))
        fl.ir.canonical_key(fl.ir.ColumnAggregate(t, fl.Ident("amount"),
                            (fl.Metric.ROW_COUNT,), filter=flt))
    """,
}


@pytest.mark.parametrize("flags", [(), ("-O",)], ids=["normal", "optimized"])
@pytest.mark.parametrize("name", sorted(GUARDS))
def test_converted_guard_holds_under_python_O(name: str, flags: tuple[str, ...]) -> None:
    code = textwrap.dedent(GUARDS[name]).strip()
    probe = (
        "import sys\ntry:\n"
        + textwrap.indent(code, "    ")
        + (
            "\nexcept AssertionError as exc:\n    print('guarded', str(exc) != '')\n    sys.exit(0)"
            "\nprint('not guarded')\nsys.exit(1)\n"
        )
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(SRC.parent), *filter(None, [env.get("PYTHONPATH")])])
    env.pop("PYTHONOPTIMIZE", None)
    done = subprocess.run(
        [sys.executable, *flags, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert done.stdout.strip() == "guarded True", done.stdout  # with a message
