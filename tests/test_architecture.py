"""Architecture rules: the core is stdlib-only and imports only flow downwards (spec 5.1)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "factlang"

# module prefix -> layer (spec 5.1). Longest matching prefix wins.
LAYERS: dict[str, float] = {
    "factlang.__about__": 0,
    "factlang.diagnostics": 0,
    "factlang.syntax.tokens": 1,
    "factlang.syntax.lexer": 2,
    "factlang.syntax.ast": 3,
    "factlang.syntax.parser": 4,
    "factlang.syntax.formatter": 4,
    # sub-layers within one layer number (spec 5.1): ordered, import earlier only
    "factlang.semantics.refs": 5.0,
    "factlang.semantics.types": 5.1,
    "factlang.semantics.filters": 5.2,
    "factlang.semantics.signature": 5.3,
    "factlang.semantics.catalog": 6.0,
    "factlang.semantics.checked": 6.1,
    "factlang.semantics.identity": 7.0,
    "factlang.semantics.analyzer": 7.1,
    "factlang.ir": 8,
    "factlang.judge": 9,
    "factlang.contract": 10,
    "factlang.packs": 11,
    "factlang.sql": 12,  # includes sql/sqlglot_bridge.py (optional, lazy)
    "factlang.optimizer": 13,
    "factlang.connectors": 14,  # includes connectors/catalog.py (WarehouseCatalog)
    "factlang.engine": 15,
    "factlang.api": 16,
    # factlang/__init__.py, the public facade: above api, which it re-exports. The key
    # matches the package itself only, never as a prefix (an unmapped module is an error).
    "factlang": 16.5,
    # factlang/types.py: the import path ``factlang.types`` for ``fl.types`` (a public alias
    # of factlang.semantics.types), at the facade's level.
    "factlang.types": 16.5,
    "factlang.agent": 17,
    "factlang.cli": 17,
    "factlang.__main__": 18,  # `python -m factlang` runs the CLI
}
FACADE = "factlang"

# Modules allowed to import third-party packages (all imports lazy in practice).
NON_CORE_FILES = {
    "sql/sqlglot_bridge.py",
    "agent/mcp.py",
}
_CORE_CONNECTOR_FILES = {"__init__.py", "base.py", "callable.py", "dbapi.py", "catalog.py"}


def _is_core(rel: Path) -> bool:
    posix = rel.as_posix()
    if posix in NON_CORE_FILES:
        return False
    return not (rel.parts[0] == "connectors" and rel.name not in _CORE_CONNECTOR_FILES)


def module_name(path: Path, root: Path = SRC) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = ["factlang", *rel.parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _py_files(root: Path = SRC) -> list[Path]:
    return sorted(root.rglob("*.py")) if root.exists() else []


CORE_MODULES: tuple[str, ...] = tuple(
    module_name(p) for p in _py_files() if _is_core(p.relative_to(SRC))
)


def layer_key(module: str) -> str | None:
    """Return the LAYERS key (longest prefix) governing ``module``, or None."""
    best: str | None = None
    for key in LAYERS:
        if key == FACADE and module != FACADE:
            continue  # the facade's key is not a catch-all for unmapped modules
        if (module == key or module.startswith(key + ".")) and (
            best is None or len(key) > len(best)
        ):
            best = key
    return best


def _module_level_imports(tree: ast.AST) -> list[ast.Import | ast.ImportFrom]:
    """Imports outside function bodies (module level, incl. if/try blocks)."""
    found: list[ast.Import | ast.ImportFrom] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                continue
            if isinstance(child, ast.Import | ast.ImportFrom):
                found.append(child)
            visit(child)

    visit(tree)
    return found


def check_stdlib_only(source: str, filename: str = "<module>") -> list[str]:
    """Violations: top-level third-party imports in ``source``."""
    allowed = set(sys.stdlib_module_names) | {"factlang"}
    problems: list[str] = []
    for node in _module_level_imports(ast.parse(source, filename)):
        if isinstance(node, ast.Import):
            tops = [a.name.split(".")[0] for a in node.names]
        else:
            if node.level > 0 or node.module is None:
                continue
            tops = [node.module.split(".")[0]]
        problems.extend(
            f"{filename}:{node.lineno}: non-stdlib import '{t}'" for t in tops if t not in allowed
        )
    return problems


def package_layer(package: str) -> float | None:
    """Layer of a package import: the MAX layer of the mapped submodules inside it.

    Importing a package is therefore legal only from above everything in it. Returns
    None when no key lies inside ``package`` (the caller falls back to its own key).
    """
    inside = [v for k, v in LAYERS.items() if k.startswith(package + ".")]
    return max(inside) if inside else None


def _imported_modules(node: ast.Import | ast.ImportFrom, importer: str, is_pkg: bool) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names if a.name.split(".")[0] == "factlang"]
    if node.level > 0:
        pkg = importer.split(".") if is_pkg else importer.split(".")[:-1]
        base = pkg[: len(pkg) - (node.level - 1)]
        mod = ".".join([*base, *(node.module.split(".") if node.module else [])])
    else:
        mod = node.module or ""
    if mod.split(".")[0] != "factlang":
        return []
    # `from pkg import name`: `name` is a submodule when it has its own LAYERS key
    out: list[str] = []
    for alias in node.names:
        cand = f"{mod}.{alias.name}"
        out.append(cand if cand in LAYERS else mod)
    return out or [mod]


def target_layer(target: str) -> tuple[str, float] | None:
    """(key, layer) for an imported module name, or None if unmapped."""
    if target in LAYERS and target != "factlang":
        return target, LAYERS[target]
    pkg = package_layer(target)
    if pkg is not None:  # a bare package such as factlang.syntax
        return target, pkg
    key = layer_key(target)
    return (key, LAYERS[key]) if key is not None else None


def check_layering(
    source: str, importer: str, filename: str = "<module>", is_pkg: bool = False
) -> list[str]:
    """Violations: module-level imports not from a strictly lower (sub-)layer."""
    own_key = layer_key(importer)
    inner = package_layer(importer) if is_pkg and importer not in LAYERS else None
    if inner is not None:
        # a package __init__ without its own key (factlang.syntax, factlang.semantics)
        # re-exports its members, so it sits just above the highest of them
        own_key, own = importer, inner + 0.5
    elif own_key is None:
        return [f"{filename}: module '{importer}' has no layer in LAYERS"]
    else:
        own = LAYERS[own_key]
    problems: list[str] = []
    for node in _module_level_imports(ast.parse(source, filename)):
        # `from pkg import a, b` yields the same target twice: report the statement once
        for target in dict.fromkeys(_imported_modules(node, importer, is_pkg)):
            resolved = target_layer(target)
            if resolved is None:
                problems.append(f"{filename}:{node.lineno}: '{target}' has no layer in LAYERS")
            elif resolved[0] != own_key and resolved[1] >= own:
                problems.append(
                    f"{filename}:{node.lineno}: '{importer}' (layer {own}) imports "
                    f"'{target}' (layer {resolved[1]})"
                )
    return problems


def facade_problems(source: str, filename: str = "<module>") -> list[str]:
    """Violations: a module-level import in the facade from anything but ``factlang.api``."""
    problems: list[str] = []
    for node in _module_level_imports(ast.parse(source, filename)):
        for target in dict.fromkeys(_imported_modules(node, FACADE, is_pkg=True)):
            if target != "factlang.api":
                problems.append(f"{filename}:{node.lineno}: facade imports '{target}'")
        if isinstance(node, ast.Import) and not any(
            a.name.split(".")[0] == "factlang" for a in node.names
        ):
            problems.append(f"{filename}:{node.lineno}: facade imports a non-factlang module")
    return problems


def test_facade_imports_only_the_api() -> None:
    init = SRC / "__init__.py"
    assert facade_problems(init.read_text(encoding="utf-8"), str(init)) == []


def test_negative_control_facade() -> None:
    assert facade_problems("from factlang.api import parse\n") == []
    assert facade_problems("from factlang.engine import Checker\n")
    assert facade_problems("from factlang import ir\n")
    assert facade_problems("import json\n")
    # layering: the facade sits above the api, and the api may not import the facade
    assert check_layering("from factlang.api import parse\n", "factlang", is_pkg=True) == []
    assert check_layering("from factlang import Checker\n", "factlang.api")
    assert check_layering("from factlang import ir\n", "factlang.api") == []
    assert check_layering("from factlang.cli import main\n", "factlang", is_pkg=True)
    assert check_layering("from factlang.cli import main\n", "factlang.__main__") == []
    assert check_layering("from factlang.__main__ import x\n", "factlang.cli")


def test_unkeyed_package_init_sits_above_its_members() -> None:
    reexport = "from factlang.syntax.parser import parse\n"
    assert check_layering(reexport, "factlang.syntax", is_pkg=True) == []
    # ... but still below the next layer up
    assert check_layering(
        "from factlang.semantics.types import T\n", "factlang.syntax", is_pkg=True
    )


def test_unmapped_module_is_not_swallowed_by_the_facade_key() -> None:
    assert layer_key("factlang.brand_new") is None
    assert check_layering("x = 1\n", "factlang.brand_new")
    assert layer_key("factlang") == "factlang"


def test_core_is_stdlib_only() -> None:
    problems: list[str] = []
    for path in _py_files():
        if _is_core(path.relative_to(SRC)):
            problems += check_stdlib_only(path.read_text(encoding="utf-8"), str(path))
    assert not problems, "\n".join(problems)


def test_layering() -> None:
    problems: list[str] = []
    for path in _py_files():
        problems += check_layering(
            path.read_text(encoding="utf-8"),
            module_name(path),
            str(path),
            is_pkg=path.name == "__init__.py",
        )
    assert not problems, "\n".join(problems)


# --- negative controls: the checkers must actually flag violations -------------------


def test_negative_control_stdlib(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("import os\nimport requests\nfrom numpy import array\n", encoding="utf-8")
    problems = check_stdlib_only(bad.read_text(encoding="utf-8"), str(bad))
    assert len(problems) == 2
    assert any("requests" in p for p in problems) and any("numpy" in p for p in problems)


def test_stdlib_allows_lazy_imports_and_factlang() -> None:
    src = "import json\nfrom factlang.diagnostics import X\n\ndef f():\n    import duckdb\n"
    assert check_stdlib_only(src) == []


def test_negative_control_layering(tmp_path: Path) -> None:
    bad = tmp_path / "lexer.py"
    bad.write_text("from factlang.syntax.parser import parse\n", encoding="utf-8")
    problems = check_layering(bad.read_text(encoding="utf-8"), "factlang.syntax.lexer", str(bad))
    assert len(problems) == 1 and "layer 4" in problems[0]


def test_negative_control_parser_formatter_may_not_import_each_other() -> None:
    assert check_layering("from factlang.syntax.formatter import f\n", "factlang.syntax.parser")
    assert check_layering("from factlang.syntax import parser\n", "factlang.syntax.formatter")


def test_layering_allows_downward_and_lazy_upward() -> None:
    ok = "from factlang.diagnostics import Span\nfrom .tokens import Token\n"
    assert check_layering(ok, "factlang.syntax.lexer") == []
    lazy = "def f():\n    from factlang.engine import Checker\n"
    assert check_layering(lazy, "factlang.syntax.lexer") == []


def test_sublayers_are_ordered() -> None:
    bad = "from factlang.semantics.signature import VerbSpec\n"
    assert check_layering(bad, "factlang.semantics.types")  # negative control
    assert (
        check_layering("from factlang.semantics.types import T\n", "factlang.semantics.signature")
        == []
    )
    assert check_layering(
        "from factlang.semantics.analyzer import A\n", "factlang.semantics.identity"
    )
    assert (
        check_layering("from factlang.semantics.identity import h\n", "factlang.semantics.analyzer")
        == []
    )
    assert check_layering(
        "from factlang.semantics.checked import C\n", "factlang.semantics.catalog"
    )
    assert check_layering("from factlang.semantics.filters import F\n", "factlang.semantics.types")
    assert (
        check_layering("from factlang.semantics.refs import R\n", "factlang.semantics.types") == []
    )


def test_package_imports_resolve_to_submodule_layer() -> None:
    # `from factlang.syntax import tokens` is the tokens module (layer 1), legal from the lexer
    assert check_layering("from factlang.syntax import tokens\n", "factlang.syntax.lexer") == []
    # ... but a package import is the MAX layer of its members: the lexer is below the parser
    assert check_layering("from factlang.syntax import Token\n", "factlang.syntax.lexer")
    assert check_layering("from factlang.syntax import Token\n", "factlang.semantics.types") == []
    assert check_layering("from factlang.syntax import parser\n", "factlang.syntax.lexer")


def test_each_violation_is_reported_once() -> None:
    # two names from one offending package are one import statement: one violation
    problems = check_layering("from factlang.syntax import Token, Span\n", "factlang.syntax.lexer")
    assert len(problems) == 1, problems
    two = "from factlang.syntax.parser import a\nfrom factlang.syntax.parser import b\n"
    assert len(check_layering(two, "factlang.syntax.lexer")) == 2  # distinct lines stay distinct


def test_layering_allows_same_package_and_flags_unknown() -> None:
    assert check_layering("from factlang.ir import b\n", "factlang.ir.a") == []
    assert check_layering("x = 1\n", "mystery.module")


def test_core_modules_exclude_optional_adapters() -> None:
    assert not any(m.endswith("sqlglot_bridge") or m == "factlang.agent.mcp" for m in CORE_MODULES)
    assert "factlang" in CORE_MODULES
