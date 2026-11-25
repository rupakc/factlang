"""Diagnostics: spans, severities and a bag that renders caret text and JSON (spec 12)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class Span:
    """A 1-based source range on one line; ``end_col`` is exclusive."""

    line: int
    col: int
    end_col: int

    def to_dict(self) -> dict[str, int]:
        return {"line": self.line, "col": self.col, "end_col": self.end_col}


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    message: str
    span: Span | None
    hint: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "span": self.span.to_dict() if self.span is not None else None,
            "hint": self.hint,
            "suggestion": self.suggestion,
        }

    def render(self, source: str) -> str:
        lines = [f"{self.severity.value}[{self.code}]: {self.message}"]
        if self.span is not None:
            span = self.span
            lines.append(f"  --> {span.line}:{span.col}")
            src_lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if 1 <= span.line <= len(src_lines):
                text = src_lines[span.line - 1]
                lines.append(text)
                width = max(1, span.end_col - span.col)
                # a tab in the source is a tab in the padding, so the carets line up under
                # the offending text whatever the terminal's tab width
                pad = "".join("\t" if ch == "\t" else " " for ch in text[: span.col - 1])
                pad += " " * (span.col - 1 - len(pad))
                lines.append(pad + "^" * width)
        if self.hint:
            lines.append(f"help: {self.hint}")
        if self.suggestion:
            lines.append(f"did you mean: {self.suggestion}?")
        return "\n".join(lines)


class DiagnosticBag:
    """Collects diagnostics in emission order.

    ``source`` (optional) is the text the diagnostics point into; :meth:`render` uses it when
    called without one (``fl.lint(text).render()``).
    """

    def __init__(self, source: str | None = None) -> None:
        self._items: list[Diagnostic] = []
        self.source = source

    def _add(
        self,
        severity: Severity,
        code: str,
        message: str,
        span: Span | None,
        hint: str | None,
        suggestion: str | None,
    ) -> None:
        self._items.append(Diagnostic(code, severity, message, span, hint, suggestion))

    def error(
        self,
        code: str,
        message: str,
        span: Span | None,
        *,
        hint: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self._add(Severity.ERROR, code, message, span, hint, suggestion)

    def warning(
        self,
        code: str,
        message: str,
        span: Span | None,
        *,
        hint: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self._add(Severity.WARNING, code, message, span, hint, suggestion)

    def info(
        self,
        code: str,
        message: str,
        span: Span | None,
        *,
        hint: str | None = None,
        suggestion: str | None = None,
    ) -> None:
        self._add(Severity.INFO, code, message, span, hint, suggestion)

    def extend(self, other: DiagnosticBag) -> None:
        self._items.extend(other._items)

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def has_errors(self) -> bool:
        return any(d.severity is Severity.ERROR for d in self._items)

    def codes(self) -> list[str]:
        return [d.code for d in self._items]

    def render(self, source: str | None = None) -> str:
        text = source if source is not None else (self.source or "")
        return "\n\n".join(d.render(text) for d in self._items)

    def to_list(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._items]
