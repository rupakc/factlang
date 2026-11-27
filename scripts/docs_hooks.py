"""MkDocs hooks for the FactLang site (wired in ``mkdocs.yml``).

The playground lives outside ``docs/`` (``site-playground/``) and needs the wheel that
``python -m build --wheel`` writes to ``dist/``. This hook publishes both under
``assets/playground/`` and writes ``assets/playground/wheel.json`` naming the wheel, so the
browser installs exactly the code this site documents.

A missing wheel is a logged warning, so ``mkdocs build --strict`` fails rather than shipping
a playground that cannot start.

The landing page states facts about the library (how many verbs, dialects, verdicts and
core dependencies, which dialect has which tier, the version). ``site_facts`` reads every
one of them from the code and ``pyproject.toml``; ``on_config`` hands them to
``docs/overrides/home.html`` as ``config.extra.fl_facts``, so the page cannot drift from
the code (``tests/conformance/test_landing_facts.py``). ``mkdocs`` is imported lazily, so
the facts can be computed (and tested) without it.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mkdocs.structure.files import Files

ROOT = Path(__file__).resolve().parents[1]
PLAYGROUND = ROOT / "site-playground"
DIST = ROOT / "dist"
DEST = "assets/playground"

log = logging.getLogger("mkdocs.plugins.factlang")


def _newest_wheel() -> Path | None:
    wheels = sorted(DIST.glob("factlang-*-py3-none-any.whl"), key=lambda p: p.stat().st_mtime)
    return wheels[-1] if wheels else None


def _core_dependencies() -> list[str]:
    """``[project] dependencies`` of ``pyproject.toml`` (tomllib; a narrow parse on 3.10)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10: read the one array this needs
        project = text.split("[project]", 1)[1].split("\n[", 1)[0]
        found = re.search(r"^dependencies\s*=\s*\[(.*?)\]", project, re.M | re.S)
        if found is None:
            return []
        return re.findall(r"[\"']([^\"']+)[\"']", found.group(1))
    return list(tomllib.loads(text)["project"].get("dependencies", []))


def site_facts() -> dict[str, Any]:
    """The facts the landing page states, read from the code and ``pyproject.toml``."""
    import factlang as fl
    from factlang.sql.registry import get_profile

    tiers: dict[str, list[str]] = {"verified": [], "native": []}
    for name in fl.sql.native_dialects():
        profile = get_profile(name)
        if profile is not None:
            tiers.setdefault(profile.tier, []).append(name)
    return {
        "version": fl.__version__,
        "native_dialects": len(fl.sql.native_dialects()),
        "verbs": len(fl.standard_pack().names()),
        "verdicts": len(fl.Verdict),
        "core_dependencies": len(_core_dependencies()),
        "tiers": {tier: sorted(names) for tier, names in tiers.items()},
    }


def on_config(config: Any) -> Any:
    import factlang

    here = Path(factlang.__file__).resolve()
    if not here.is_relative_to(ROOT / "src"):
        log.warning(
            "the landing page facts come from %s, not from this checkout's src/: install it "
            "with `pip install -e .` so the site states this code's facts",
            here,
        )
    config["extra"]["fl_facts"] = site_facts()
    return config


def on_files(files: Files, config: Any) -> Files:
    from mkdocs.structure.files import File

    for path in sorted(PLAYGROUND.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            files.append(File.generated(config, f"{DEST}/{path.name}", abs_src_path=str(path)))
    wheel = _newest_wheel()
    if wheel is None:
        log.warning(
            "no factlang wheel in dist/: run `python -m build --wheel` before building the "
            "docs, or the playground cannot load"
        )
        manifest: dict[str, str | None] = {"wheel": None}
    else:
        files.append(File.generated(config, f"{DEST}/{wheel.name}", abs_src_path=str(wheel)))
        manifest = {"wheel": wheel.name}
    files.append(File.generated(config, f"{DEST}/wheel.json", content=json.dumps(manifest)))
    return files
