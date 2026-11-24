"""The connector protocol, execution errors and guarded metadata SQL (spec 9.1, 18 R8; layer 14).

A connector executes one :class:`~factlang.sql.RenderedProbe`: it binds the probe's neutral
template with its driver's paramstyle (``probe.bind(paramstyle)``), runs it and returns the
rows as plain dicts. Values are passed through as the driver returns them (a ``NUMERIC`` may
arrive as ``Decimal``, ``str`` or ``float``; contracts accept all three).

Failures are never swallowed: a driver timeout becomes :class:`ExecutionError` ``FL702``,
anything else ``FL700`` (``FL701`` when the warehouse itself refuses the cost budget), with
the driver's exception chained as ``__cause__``.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import re
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, cast, runtime_checkable

from factlang.ir import Arity
from factlang.semantics.filters import Param
from factlang.sql import PARAMSTYLES, RenderedProbe, RenderError, get_profile

EXECUTION_CODES: tuple[str, ...] = ("FL700", "FL701", "FL702", "FL703")
_CODE = re.compile(r"(FL7\d\d)\b")


class ExecutionError(Exception):
    """A probe (or metadata query) could not be executed; ``code`` is an ``FL7xx`` code.

    ``ExecutionError("FL702 timed out")`` takes the code from the message;
    ``ExecutionError("timed out", code="FL702")`` prefixes it. ``str(error)`` always starts
    with the code.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        found = _CODE.match(message)
        if code is None:
            if found is None:
                raise ValueError(f"ExecutionError needs an FL7xx code, got {message!r}")
            code = found.group(1)
        elif found is None or found.group(1) != code:
            message = f"{code} {message}"
        if code not in EXECUTION_CODES:
            raise ValueError(f"unknown execution code {code!r}; expected one of {EXECUTION_CODES}")
        super().__init__(message)
        self.code = code


@dataclass
class QueryResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    bytes_processed: int | None = None


@runtime_checkable
class Connector(Protocol):
    """Executes rendered probes. ``dialect`` names a profile; ``paramstyle`` is the driver's.

    A connector may also define ``dry_run_bytes(probe) -> int | None`` (spec 18 R5): price a
    probe without executing it. It is optional; use :func:`dry_run_bytes` to call it.
    """

    dialect: str
    paramstyle: str

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult: ...


@runtime_checkable
class AsyncConnector(Protocol):
    dialect: str
    paramstyle: str

    async def aexecute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult: ...


async def aexecute(
    connector: Connector | AsyncConnector, probe: RenderedProbe, *, timeout_s: float | None
) -> QueryResult:
    """Execute asynchronously: the connector's own ``aexecute`` if any, else ``execute`` in a
    worker thread."""
    native = getattr(connector, "aexecute", None)
    if native is not None:
        result: QueryResult = await native(probe, timeout_s=timeout_s)
        return result
    sync = cast(Connector, connector)
    return await asyncio.to_thread(sync.execute, probe, timeout_s=timeout_s)


def dry_run_bytes(connector: object, probe: RenderedProbe) -> int | None:
    """Bytes the probe would scan, from the connector's optional dry run; ``None`` if it has
    none (or the warehouse does not say)."""
    fn = getattr(connector, "dry_run_bytes", None)
    if fn is None:
        return None
    value = fn(probe)
    return None if value is None else int(value)


def check_paramstyle(paramstyle: str) -> str:
    if paramstyle not in PARAMSTYLES:
        raise ValueError(f"unknown paramstyle {paramstyle!r}; expected one of {PARAMSTYLES}")
    return paramstyle


# -- error mapping -------------------------------------------------------------------------

# A query that ran out of time. Deliberately narrow: a bare "time out"/"interrupted", a
# ``statement_timeout`` parameter error or a *connect* timeout is FL700, not FL702.
_TIMEOUT_NAMES = re.compile(
    r"^(Query|Statement|Execution)?Time(d)?out(Error|Exception)?$|"
    r"^QueryCancel(l)?ed(Error)?$",
    re.IGNORECASE,
)
_TIMEOUT_TEXT = re.compile(
    r"\bstatement timeout\b|\bquery timeout\b|\bquery (has |was )?timed out\b"
    r"|\bcanceling statement due to\b|\bquery (was )?cancel+ed\b|\bdeadline exceeded\b"
    r"|\bexecution (time|timeout) (limit )?exceeded\b|\btimed out after\b",
    re.IGNORECASE,
)
_CONNECT = re.compile(r"\bconnect(ion|ing)?\b|\bhandshake\b|\blogin\b", re.IGNORECASE)


def looks_like_timeout(exc: BaseException) -> bool:
    """Whether a driver exception reports that the *query* ran out of time (or was
    cancelled). Connection/login timeouts and ``statement_timeout`` setting errors are not."""
    text = str(exc)
    names = [k.__name__ for k in type(exc).__mro__ if k not in (Exception, BaseException, object)]
    if _CONNECT.search(text) or any("connect" in n.lower() for n in names):
        return False
    if isinstance(exc, TimeoutError) or any(_TIMEOUT_NAMES.match(n) for n in names):
        return True
    return bool(_TIMEOUT_TEXT.search(text))


def map_error(
    exc: Exception, *, timed_out: bool = False, timeout_s: float | None = None
) -> ExecutionError:
    """The :class:`ExecutionError` for a driver exception (the caller chains it)."""
    if isinstance(exc, ExecutionError):
        return exc
    detail = f"{type(exc).__name__}: {exc}"
    if timed_out or looks_like_timeout(exc):
        limit = f" after {timeout_s:g}s" if timeout_s is not None else ""
        return ExecutionError(f"query timed out{limit} ({detail})", code="FL702")
    return ExecutionError(f"query failed ({detail})", code="FL700")


def normalise_rows(probe: RenderedProbe, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows as the judge reads them.

    For a FactLang probe (``probe.plan`` set, ``MetadataQuery`` included) every result label
    is lower-cased: FactLang aliases are a lower-case wire format, and Snowflake/Oracle return
    unquoted labels upper-cased (``ROW_COUNT``). Host metadata SQL (``plan is None``) keeps
    its labels as the warehouse returned them.
    """
    if probe.plan is None:
        return [dict(r) for r in rows]
    return [{(k.lower() if isinstance(k, str) else k): v for k, v in r.items()} for r in rows]


def _canceller(handles: Sequence[object]) -> Callable[[], None] | None:
    """The first ``interrupt``/``cancel`` hook found on ``handles``, tried in order."""
    for handle in handles:
        for name in ("interrupt", "cancel"):
            fn = getattr(handle, name, None)
            if callable(fn):
                return cast(Callable[[], None], fn)
    return None


@contextmanager
def deadline(timeout_s: float | None, handles: Sequence[object]) -> Iterator[threading.Event]:
    """Cancel the running statement through ``handles`` once ``timeout_s`` elapses.

    Yields an event that is set when the deadline fired, so the caller can report ``FL702``
    whatever exception the driver raises on interruption. A driver offering no cancel hook
    cannot be interrupted; its own timeout errors are still mapped to ``FL702``.
    """
    fired = threading.Event()
    cancel = _canceller(handles) if timeout_s is not None else None
    if timeout_s is None or cancel is None:
        yield fired
        return
    hook = cancel

    def fire() -> None:
        fired.set()
        with contextlib.suppress(Exception):  # a failing cancel leaves the query running
            hook()

    timer = threading.Timer(max(timeout_s, 0.0), fire)
    timer.daemon = True
    timer.start()
    try:
        yield fired
    finally:
        timer.cancel()


# -- the metadata-SQL guard (R8, FL703) ------------------------------------------------------

METADATA_KEYWORDS: tuple[str, ...] = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")
# Character sequences that, in some dialect, start a comment, a quote form, a second
# statement or a chain: refused anywhere in the raw text, inside quotes too.
FORBIDDEN_SEQUENCES: tuple[str, ...] = (";", "->>", "--", "/*", "*/", "//", "#", "$", "\\")
# Write, DDL and control keywords: refused as whole words anywhere, inside quotes too.
FORBIDDEN_WORDS: frozenset[str] = frozenset(
    (
        *("INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "TRUNCATE", "INTO", "OUTFILE"),
        *("DUMPFILE", "COPY", "LOAD", "UNLOAD", "PUT", "GET", "REMOVE"),
        *("CREATE", "DROP", "ALTER", "RENAME", "GRANT", "REVOKE", "LOCK"),
        *("CALL", "EXEC", "EXECUTE", "PREPARE", "DECLARE", "SET", "USE"),
        *("BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "KILL", "NOTIFY"),
        *("ATTACH", "DETACH", "PRAGMA", "VACUUM", "ANALYZE", "ANALYSE"),
    )
)
_TOKEN = re.compile(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ORACLE_QUOTE = re.compile(r"(?<![A-Za-z0-9_])n?q'", re.IGNORECASE)


def _refuse(reason: str) -> ExecutionError:
    return ExecutionError(f"metadata SQL refused by guard: {reason}", code="FL703")


def check_metadata_sql(sql: str) -> None:
    """Raise ``ExecutionError`` ``FL703`` unless ``sql`` passes the metadata guard (R8).

    The guard is **lexer-free and deliberately over-strict**. It reads the raw text, quotes
    ignored (a string literal is scanned like any other text), after removing the
    ``{{name}}`` parameter tokens, and refuses:

    - any of :data:`FORBIDDEN_SEQUENCES` (``;`` ``->>`` ``--`` ``/*`` ``*/`` ``//`` ``#``
      ``$`` ``\\``) anywhere;
    - an Oracle alternative-quote prefix (``q'``, ``nq'``, any case) starting a token;
    - any of :data:`FORBIDDEN_WORDS` (write, DDL and control keywords) as a whole word
      anywhere, case-insensitive;
    - a first word that is not one of :data:`METADATA_KEYWORDS`.

    Modelling each dialect's lexer was rejected: review found bypasses through PostgreSQL /
    DuckDB dollar quotes, MySQL's ``--``, Snowflake's ``//`` and Oracle's ``q'``. Refusing a
    legitimate query is the accepted price: pass any value containing these characters or
    words as a bound parameter (``{{name}}``), which the scan never sees.

    This is a keyword guard, not a sandbox: side-effecting functions called from a
    ``SELECT`` (``dblink_exec``, ``pg_terminate_backend``, …) are not detected, and the
    connection's credentials remain the real security boundary.
    """
    if not isinstance(sql, str):
        raise TypeError(f"metadata SQL must be a string, got {type(sql).__name__}")
    text = _TOKEN.sub(" ", sql)
    for seq in FORBIDDEN_SEQUENCES:
        if seq in text:
            raise _refuse(f"{seq!r} is not allowed in metadata SQL (bind values as {{{{name}}}})")
    if _ORACLE_QUOTE.search(text):
        raise _refuse("Oracle alternative quoting (q'…') is not allowed")
    words = [w.upper() for w in _WORD.findall(text)]
    stripped = text.lstrip()
    if not words or not stripped or _WORD.match(stripped) is None:
        raise _refuse(f"the statement must start with one of {', '.join(METADATA_KEYWORDS)}")
    if words[0] not in METADATA_KEYWORDS:
        raise _refuse(f"{words[0]} is not a metadata statement")
    bad = sorted(set(words) & FORBIDDEN_WORDS)
    if bad:
        raise _refuse(f"keyword(s) {', '.join(bad)} not allowed (bind values as {{{{name}}}})")


def _infer_param(name: str, value: object) -> Param:
    if value is None or isinstance(value, str):
        family = "string"
    elif isinstance(value, bool):
        family = "boolean"
    elif isinstance(value, int):
        family = "integer"
    elif isinstance(value, Decimal):
        family = "decimal"
    elif isinstance(value, float):
        family = "float"
    elif isinstance(value, dt.datetime):
        family = "timestamp"
    elif isinstance(value, dt.date):
        family = "date"
    else:
        raise TypeError(
            f"metadata parameter {name!r}: cannot bind a {type(value).__name__}; pass a Param"
        )
    return Param(name, value, family)


def run_metadata(
    connector: Connector,
    sql: str,
    params: Sequence[Param] | Mapping[str, object] = (),
    *,
    timeout_s: float | None = None,
) -> QueryResult:
    """Execute host-authored catalog metadata SQL through ``connector`` (spec 9.1, R8).

    ``sql`` marks bind parameters with neutral ``{{name}}`` tokens, exactly as a rendered
    probe does, and ``params`` supplies them (``Param`` objects, or a ``{name: value}``
    mapping whose families are inferred). They are bound with the connector's paramstyle,
    never spliced. The guard (:func:`check_metadata_sql`) runs first and raises
    ``ExecutionError`` ``FL703`` before anything executes. Execution, timeouts and error
    mapping are the connector's own (``FL700``/``FL702``). This is not a claim: it yields
    rows, never an ``Outcome``. The probe passed to the connector has ``plan=None``, and the
    rows keep the labels the warehouse returned (no lower-casing).

    Through :class:`~factlang.connectors.SQLAlchemyConnector`, whose ``text()`` would read
    ``:word`` as a bind parameter even inside a string literal, every ``:`` in ``sql`` is
    escaped before binding, so literals such as ``'a:b'`` and casts such as ``x::text``
    reach the driver unchanged. A ``{{name}}`` token *immediately* after a ``:`` (``x::{{t}}``)
    cannot be bound by ``text()`` and is refused there with ``FL700``; put a space or other
    text between them.
    """
    check_metadata_sql(sql)
    if isinstance(params, Mapping):
        bound = tuple(_infer_param(k, v) for k, v in params.items())
    else:
        bound = tuple(params)
    profile = get_profile(connector.dialect)
    tier = profile.tier if profile is not None else "best_effort"
    try:
        probe = RenderedProbe(sql, bound, Arity.ROW_SET, None, connector.dialect, tier)
    except RenderError as exc:
        raise _refuse(str(exc)) from exc
    return connector.execute(probe, timeout_s=timeout_s)


def rows_from_cursor(description: Any, records: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    """PEP 249 rows as dicts keyed by ``cursor.description`` names."""
    if description is None:
        return []
    names = [str(d[0]) for d in description]
    return [dict(zip(names, rec, strict=False)) for rec in records]


__all__ = [
    "EXECUTION_CODES",
    "FORBIDDEN_SEQUENCES",
    "FORBIDDEN_WORDS",
    "METADATA_KEYWORDS",
    "AsyncConnector",
    "Connector",
    "ExecutionError",
    "QueryResult",
    "aexecute",
    "check_metadata_sql",
    "check_paramstyle",
    "deadline",
    "dry_run_bytes",
    "looks_like_timeout",
    "map_error",
    "normalise_rows",
    "rows_from_cursor",
    "run_metadata",
]
