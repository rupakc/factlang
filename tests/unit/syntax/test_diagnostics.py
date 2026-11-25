from __future__ import annotations

from factlang.diagnostics import Diagnostic, DiagnosticBag, Severity, Span


def test_render_caret() -> None:
    bag = DiagnosticBag()
    bag.error("FL102", "unexpected character ':'", Span(1, 2, 3), hint="remove it")
    assert bag.render("a:b") == (
        "error[FL102]: unexpected character ':'\n  --> 1:2\na:b\n ^\nhelp: remove it"
    )


def test_render_suggestion_and_multiline_source() -> None:
    bag = DiagnosticBag()
    bag.error("FL300", "unknown verb", Span(2, 1, 5), suggestion="UNIQUE")
    out = bag.render("x\r\nUNIK t.c")
    assert out == "error[FL300]: unknown verb\n  --> 2:1\nUNIK t.c\n^^^^\ndid you mean: UNIQUE?"


def test_render_without_span() -> None:
    bag = DiagnosticBag()
    bag.info("FL313", "could not verify names", None)
    assert bag.render("") == "info[FL313]: could not verify names"


def test_to_dict_keys() -> None:
    d = Diagnostic("FL101", Severity.ERROR, "m", Span(1, 1, 2)).to_dict()
    assert set(d) == {"code", "severity", "message", "span", "hint", "suggestion"}
    assert d["severity"] == "error"
    assert d["span"] == {"line": 1, "col": 1, "end_col": 2}


def test_bag_basics() -> None:
    bag = DiagnosticBag()
    assert not bag.has_errors and len(bag) == 0
    bag.warning("FL312", "w", None)
    assert not bag.has_errors
    other = DiagnosticBag()
    other.error("FL101", "e", None)
    bag.extend(other)
    assert bag.has_errors
    assert bag.codes() == ["FL312", "FL101"]
    assert [d.code for d in bag] == ["FL312", "FL101"]
    assert len(bag.to_list()) == 2


def test_render_caret_reproduces_tabs_of_the_source_line() -> None:
    bag = DiagnosticBag()
    bag.error("FL102", "x", Span(1, 5, 7))
    # source: tab, tab, "a", " ", "b:" -> col 5 is the 'b' after two tabs, 'a' and a space
    src = "\t\ta b:"
    out = bag.render(src).split("\n")
    assert out[2] == src
    assert out[3] == "\t\t  ^^"


def test_render_caret_tab_after_the_offending_column_is_not_copied() -> None:
    bag = DiagnosticBag()
    bag.error("FL102", "x", Span(1, 2, 3))
    out = bag.render("a\tb").split("\n")
    assert out[3] == " ^"


def test_render_caret_lines_up_for_a_tab_indented_claim() -> None:
    import factlang as fl

    text = "\tUNIQE orders.id"
    out = fl.lint(text).render().split("\n")
    assert out[2] == text and out[3] == "\t^^^^^"
