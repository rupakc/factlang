"""Built-in native dialect profiles (spec 8.3, 8.5; layer 12)."""

from __future__ import annotations

from factlang.sql.profile import DialectProfile
from factlang.sql.profiles.duckdb import DUCKDB
from factlang.sql.profiles.postgres import POSTGRES

BUILTIN_PROFILES: tuple[DialectProfile, ...] = (DUCKDB, POSTGRES)

__all__ = ["BUILTIN_PROFILES", "DUCKDB", "POSTGRES"]
