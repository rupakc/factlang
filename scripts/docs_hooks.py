"""MkDocs hooks for the FactLang site (wired in ``mkdocs.yml``).

The playground lives outside ``docs/`` (``site-playground/``) and needs the wheel that
``python -m build --wheel`` writes to ``dist/``. This hook publishes both under
``assets/playground/`` and writes ``assets/playground/wheel.json`` naming the wheel, so the
browser installs exactly the code this site documents.

A missing wheel is a logged warning, so ``mkdocs build --strict`` fails rather than shipping
a playground that cannot start.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from mkdocs.structure.files import File, Files

ROOT = Path(__file__).resolve().parents[1]
PLAYGROUND = ROOT / "site-playground"
DIST = ROOT / "dist"
DEST = "assets/playground"

log = logging.getLogger("mkdocs.plugins.factlang")


def _newest_wheel() -> Path | None:
    wheels = sorted(DIST.glob("factlang-*-py3-none-any.whl"), key=lambda p: p.stat().st_mtime)
    return wheels[-1] if wheels else None


def on_files(files: Files, config: Any) -> Files:
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
