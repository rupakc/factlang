from __future__ import annotations

import json
from pathlib import Path

from factlang.semantics.catalog import CachedCatalog, NullCatalog, StaticCatalog
from factlang.semantics.refs import Ident, TableRef


def T(table: str, schema: str | None = None, *, q: bool = False, catalog: str | None = None):
    return TableRef(
        Ident(catalog) if catalog else None,
        Ident(schema, q) if schema else None,
        Ident(table, q),
    )


def test_null_catalog_unknown() -> None:
    assert NullCatalog().table_exists(T("a")) is None
    assert NullCatalog().columns(T("a")) is None


def test_static_case_insensitive_unquoted() -> None:
    c = StaticCatalog({"Shop.Orders": {"ID": "INTEGER"}, "customers": {"id": "text"}})
    assert c.table_exists(T("ORDERS", "SHOP")) is True
    assert c.columns(T("orders", "shop")) == {"ID": "integer"}
    assert c.table_exists(T("Customers")) is True
    assert c.table_exists(T("missing")) is False
    assert c.columns(T("missing")) is None


def test_static_quoted_is_case_sensitive() -> None:
    c = StaticCatalog({"shop.orders": {"id": "int"}, "Shop.Orders": {"x": "int"}})
    assert c.columns(T("Orders", "Shop", q=True)) == {"x": "int"}
    assert c.table_exists(T("ORDERS", "shop", q=True)) is False


def test_static_fullest_key_first_and_fallback() -> None:
    c = StaticCatalog({"cat.s.t": {"a": "int"}, "s.t": {"b": "int"}, "t": {"c": "int"}})
    assert c.columns(T("t", "s", catalog="cat")) == {"a": "int"}
    assert c.columns(T("t", "s")) == {"b": "int"}
    assert c.columns(T("t")) == {"c": "int"}
    only = StaticCatalog({"s.t": {"b": "int"}})
    assert only.columns(T("t", "s", catalog="other")) == {"b": "int"}


def test_static_columns_returns_copy_and_json(tmp_path: Path) -> None:
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"s.t": {"a": "INT"}}))
    c = StaticCatalog.from_json(p)
    got = c.columns(T("t", "s"))
    assert got == {"a": "int"}
    assert got is not None
    got["z"] = "x"
    assert c.columns(T("t", "s")) == {"a": "int"}


class Counting:
    def __init__(self) -> None:
        self.calls = 0

    def table_exists(self, ref: TableRef) -> bool | None:
        self.calls += 1
        return None

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        self.calls += 1
        return {"a": "int"} if ref.table.text == "t" else None


def test_cached_ttl_and_none_cached() -> None:
    now = [0.0]
    inner = Counting()
    c = CachedCatalog(inner, ttl_s=10, clock=lambda: now[0])
    assert c.columns(T("t")) == {"a": "int"}
    assert c.columns(T("t")) == {"a": "int"}
    assert c.columns(T("nope")) is None
    assert c.columns(T("nope")) is None
    assert inner.calls == 2
    now[0] = 11.0
    c.columns(T("t"))
    assert inner.calls == 3


def test_cached_lru_eviction() -> None:
    inner = Counting()
    c = CachedCatalog(inner, max_tables=2, clock=lambda: 0.0)
    c.columns(T("t"))
    c.columns(T("a"))
    c.columns(T("t"))  # refresh t; a is now oldest
    c.columns(T("b"))  # evicts a
    assert inner.calls == 3
    c.columns(T("t"))
    assert inner.calls == 3
    c.columns(T("a"))
    assert inner.calls == 4


def test_static_qualified_ref_does_not_fall_back_past_registered_schema() -> None:
    c = StaticCatalog({"s.t": {"a": "int"}})
    assert c.table_exists(T("t", "other")) is False
    assert c.columns(T("t", "other")) is None
    assert c.table_exists(T("t")) is False  # unqualified ref does not match s.t
    plain = StaticCatalog({"t": {"a": "int"}})
    assert plain.table_exists(T("t", "other")) is True  # catalog has no schema info for t
    both = StaticCatalog({"s.t": {"a": "int"}, "t": {"b": "int"}})
    assert both.table_exists(T("t", "other")) is False
    assert both.columns(T("t", "s")) == {"a": "int"}
    assert both.columns(T("t")) == {"b": "int"}


class Exists:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def table_exists(self, ref: TableRef) -> bool | None:
        self.calls.append("e")
        return True

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        self.calls.append("c")
        return None


def test_cached_table_exists_and_key_separation() -> None:
    inner = Exists()
    c = CachedCatalog(inner, clock=lambda: 0.0)
    assert c.table_exists(T("t")) is True
    assert c.table_exists(T("t")) is True
    assert inner.calls == ["e"]
    assert c.columns(T("t")) is None  # separate cache entry despite same ref
    assert c.columns(T("t")) is None
    assert inner.calls == ["e", "c"]


def test_cached_ttl_exact_boundary() -> None:
    now = [0.0]
    inner = Exists()
    c = CachedCatalog(inner, ttl_s=10, clock=lambda: now[0])
    c.table_exists(T("t"))
    now[0] = 9.999
    c.table_exists(T("t"))
    assert len(inner.calls) == 1
    now[0] = 10.0  # age == ttl is expired
    c.table_exists(T("t"))
    assert len(inner.calls) == 2


def test_cached_catalog_is_thread_safe() -> None:
    import threading
    import time as _time

    from factlang.semantics.catalog import CachedCatalog as _Cached
    from factlang.semantics.refs import Ident as _Ident
    from factlang.semantics.refs import TableRef as _TableRef

    class Slow:
        def table_exists(self, ref: _TableRef) -> bool | None:
            _time.sleep(0.0005)
            return True

        def columns(self, ref: _TableRef) -> dict[str, str] | None:
            return {"id": "integer"}

    cache = _Cached(Slow(), max_tables=5)
    refs = [_TableRef(None, None, _Ident(f"t{i}")) for i in range(20)]
    errors: list[BaseException] = []

    def hammer() -> None:
        try:
            for _ in range(30):
                for r in refs:
                    assert cache.table_exists(r) is True
                    assert cache.columns(r) == {"id": "integer"}
        except BaseException as exc:  # pragma: no cover - only on a race
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(cache._cache) <= 5
