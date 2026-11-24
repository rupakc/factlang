"""Built-in native dialect profiles (spec 8.3, 8.5; layer 12)."""

from __future__ import annotations

from factlang.sql.profile import DialectProfile
from factlang.sql.profiles.bigquery import BIGQUERY
from factlang.sql.profiles.clickhouse import CLICKHOUSE
from factlang.sql.profiles.databricks import DATABRICKS
from factlang.sql.profiles.duckdb import DUCKDB
from factlang.sql.profiles.mysql import MYSQL
from factlang.sql.profiles.oracle import ORACLE
from factlang.sql.profiles.postgres import POSTGRES
from factlang.sql.profiles.redshift import REDSHIFT
from factlang.sql.profiles.snowflake import SNOWFLAKE
from factlang.sql.profiles.trino import TRINO
from factlang.sql.profiles.tsql import TSQL

BUILTIN_PROFILES: tuple[DialectProfile, ...] = (
    DUCKDB,
    POSTGRES,
    BIGQUERY,
    SNOWFLAKE,
    REDSHIFT,
    DATABRICKS,
    CLICKHOUSE,
    TSQL,
    MYSQL,
    ORACLE,
    TRINO,
)

__all__ = [
    "BIGQUERY",
    "BUILTIN_PROFILES",
    "CLICKHOUSE",
    "DATABRICKS",
    "DUCKDB",
    "MYSQL",
    "ORACLE",
    "POSTGRES",
    "REDSHIFT",
    "SNOWFLAKE",
    "TRINO",
    "TSQL",
]
