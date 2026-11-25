"""No ``assert`` in ``src/`` carries behaviour (0.1.3 spec 3.3 P4).

The DuckDB verb matrix runs once normally and once under ``python -O`` (which strips every
``assert``), each in its own subprocess, and the outcomes must be identical.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import factlang

pytest.importorskip("duckdb")

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "matrix_outcomes.py"


def _run(*flags: str) -> dict[str, Any]:
    # the subprocess imports the very factlang this process tests, plus the fixture modules
    src = Path(factlang.__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(src), str(HERE), *filter(None, [env.get("PYTHONPATH")])]
    )
    env.pop("PYTHONOPTIMIZE", None)
    done = subprocess.run(
        [sys.executable, *flags, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    result: dict[str, Any] = json.loads(done.stdout)
    return result


def test_verb_matrix_outcomes_are_identical_under_python_O() -> None:
    normal, optimized = _run(), _run("-O")
    assert (normal["optimize"], optimized["optimize"]) == (0, 1)
    assert len(normal["cases"]) > 60  # the whole matrix plus the hostile-catalog claims
    for plain, opt in zip(normal["cases"], optimized["cases"], strict=True):
        assert plain == opt, plain["claim"]
