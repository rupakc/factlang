"""Fixtures for the playground glue tests: the glue module and the DuckDB bridge helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

HERE = Path(__file__).resolve().parent


def _bridge() -> ModuleType:
    name = "factlang_playground_duckdb_run"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / "fixtures" / "duckdb_run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def bridge() -> ModuleType:
    return _bridge()


@pytest.fixture(scope="session")
def glue() -> ModuleType:
    return _bridge().load_glue()
