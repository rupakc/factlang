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
# keywords that write or run code; refused anywhere in the statement (``SHOW CREATE …`` aside)
_WRITE_WORDS = frozenset(
    (
        *("INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "INTO", "COPY", "UNLOAD"),
        *("DROP", "CREATE", "ALTER", "TRUNCATE", "GRANT", "REVOKE", "LOCK", "VACUUM"),
        *("CALL", "EXEC", "EXECUTE", "ATTACH", "DETACH", "PRAGMA"),
    )
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


@dataclass(frozen=True)
class _Lexing:
    """One reading of the SQL text. A ``;`` (or a word) counts only if *every* reading finds
    it outside literals, so a quirk of one dialect cannot hide a second statement."""

    backslash: bool  # MySQL/BigQuery: backslash escapes inside quotes
    hash_comment: bool  # MySQL: '#' starts a line comment
    nested: bool  # PostgreSQL: block comments nest
    dollar: bool  # PostgreSQL: $tag$ … $tag$ strings
    brackets: bool  # T-SQL: [identifier]


_LEXINGS = tuple(
    _Lexing(b, h, n, d, k)
    for b in (False, True)
    for h in (False, True)
    for n in (False, True)
    for d in (False, True)
    for k in (False, True)
)
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def _refuse(reason: str) -> ExecutionError:
    return ExecutionError(f"metadata SQL refused by guard: {reason}", code="FL703")


def _code_text(sql: str, mode: _Lexing) -> str:
    """``sql`` with literals, quoted identifiers and comments blanked out (``' '``)."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"`" or (ch == "[" and mode.brackets):
            close = "]" if ch == "[" else ch
            j = i + 1
            while True:
                if j >= n:
                    raise _refuse("unterminated quoted text")
                if mode.backslash and close != "]" and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == close:
                    if j + 1 < n and sql[j + 1] == close:
                        j += 2
                        continue
                    break
                j += 1
            out.append(" ")
            i = j + 1
        elif sql.startswith("--", i) or (ch == "#" and mode.hash_comment):
            j = sql.find("\n", i)
            if ";" in sql[i : (n if j < 0 else j)]:
                raise _refuse("';' inside a comment")
            out.append(" ")
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            if sql.startswith(("/*!", "/*M!"), i):
                raise _refuse("MySQL/MariaDB executable comment (/*! … */)")
            depth, j = 1, i + 2
            while depth:
                if j >= n:
                    raise _refuse("unterminated comment")
                if sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                elif mode.nested and sql.startswith("/*", j):
                    depth += 1
                    j += 2
                else:
                    j += 1
            if ";" in sql[i:j]:
                raise _refuse("';' inside a comment")
            out.append(" ")
            i = j
        elif ch == "$" and mode.dollar and (m := _DOLLAR.match(sql, i)) is not None:
            tag = m.group(0)
            end = sql.find(tag, m.end())
            if end < 0:
                raise _refuse("unterminated dollar-quoted text")
            out.append(" ")
            i = end + len(tag)
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _check_reading(code: str) -> None:
    if ";" in code:
        raise _refuse("more than one statement (';' outside a string literal)")
    if "->>" in code:
        raise _refuse("'->>' outside a string literal (Snowflake statement chaining)")
    words = [w.upper() for w in _WORD.findall(code)]
    if not words or not code.lstrip() or not _WORD.match(code.lstrip()):
        raise _refuse(f"the statement must start with one of {', '.join(METADATA_KEYWORDS)}")
    first = words[0]
    if first not in METADATA_KEYWORDS:
        raise _refuse(f"{first} is not a metadata statement")
    body = words[2:] if words[:2] == ["SHOW", "CREATE"] else words[1:]
    bad = sorted(set(body) & _WRITE_WORDS)
    if bad:
        raise _refuse(f"{first} statement contains {', '.join(bad)}")
    if first == "EXPLAIN" and ({"ANALYZE", "ANALYSE"} & set(words)):
        raise _refuse("EXPLAIN ANALYZE executes the statement")


def check_metadata_sql(sql: str) -> None:
    """Raise ``ExecutionError`` ``FL703`` unless ``sql`` is one read-only metadata statement.

    Accepted: one statement whose first keyword (after comments/whitespace) is one of
    :data:`METADATA_KEYWORDS`. Refused: any ``;`` outside string literals (also inside
    comments); Snowflake's ``->>`` chain operator outside literals; MySQL/MariaDB executable
    comments (``/*! … */``, ``/*M! … */``), which are code; unterminated quotes or comments;
    ``EXPLAIN ANALYZE``; and any write keyword anywhere in the statement body (data-modifying
    CTEs, ``SELECT … INTO``, ``FOR UPDATE``, ``EXPLAIN DELETE …``), except the ``CREATE`` of
    ``SHOW CREATE …``.

    The text is read under every combination of dialect lexing quirks (backslash escapes,
    ``#`` comments, nested comments, dollar quotes, bracket identifiers) and must pass under
    all of them. So a MySQL backslash-escaped quote (``'it\\'s'``), which leaves the ANSI
    reading unterminated, is refused: the safe direction.

    This is a keyword guard, not a sandbox. Side-effecting functions called from a
    ``SELECT`` (``dblink_exec``, ``pg_terminate_backend``, …) are not detected; the
    connection's credentials are the real security boundary.
    """
    if not isinstance(sql, str):
        raise TypeError(f"metadata SQL must be a string, got {type(sql).__name__}")
    for mode in _LEXINGS:
        _check_reading(_code_text(sql, mode))


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
    reach the driver unchanged.
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
