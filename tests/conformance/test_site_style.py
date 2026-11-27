"""The site's visual system (docs/assets/factlang.css) is complete and accessible.

- Every design token the landing page and playground consume is defined for the light
  scheme (``:root``) and the dark scheme (``[data-md-color-scheme="slate"]``).
- Motion is off under ``prefers-reduced-motion: reduce``.
- Text, muted text, accent text and every verdict badge meet WCAG AA (4.5:1) in both
  schemes, computed with the WCAG 2.x relative-luminance formula from the hex values.
- The shared component classes exist, and factlang.js drives fade-up and copy buttons.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "docs" / "assets" / "factlang.css").read_text(encoding="utf-8")
JS = (ROOT / "docs" / "assets" / "factlang.js").read_text(encoding="utf-8")

TOKENS = (
    "--fl-bg",
    "--fl-bg-subtle",
    "--fl-surface",
    "--fl-border",
    "--fl-text",
    "--fl-text-muted",
    "--fl-indigo",
    "--fl-teal",
    "--fl-amber",
    "--fl-rose",
    "--fl-violet",
    "--fl-pass",
    "--fl-fail",
    "--fl-nodata",
    "--fl-error",
    "--fl-radius",
    "--fl-shadow",
)
COLOUR_TOKENS = tuple(t for t in TOKENS if t not in ("--fl-radius", "--fl-shadow"))
ACCENTS = ("--fl-indigo", "--fl-teal", "--fl-amber", "--fl-rose", "--fl-violet")
VERDICTS = ("pass", "fail", "nodata", "error", "unprobeable")
CLASSES = (
    ".fl-card",
    ".fl-badge",
    ".fl-fade-up",
    ".fl-fade-up.is-visible",
    ".fl-button",
    ".fl-button--solid",
    ".fl-section",
    ".fl-section--subtle",
    ".fl-copy",
)
SCHEMES = {"light": ":root", "slate": '[data-md-color-scheme="slate"]'}

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
_HEX = re.compile(r"#([0-9a-fA-F]{6})")


def _blocks(css: str) -> list[tuple[str, str]]:
    return [(sel.strip(), body) for sel, body in _BLOCK.findall(_COMMENT.sub("", css))]


def _tokens(selector: str) -> dict[str, str]:
    """Custom properties declared by rules whose selector is exactly ``selector``."""
    out: dict[str, str] = {}
    for sel, body in _blocks(CSS):
        if sel == selector:
            out.update((k, v.strip()) for k, v in _DECL.findall(body))
    return out


def _hex(value: str) -> str:
    match = _HEX.fullmatch(value.strip())
    assert match, f"expected a #rrggbb colour, got {value!r}"
    return match.group(1)


def _luminance(hex6: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex6[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(_hex(a)), _luminance(_hex(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_contrast_formula_negative_control() -> None:
    assert _contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert _contrast("#777777", "#ffffff") < 4.5  # a familiar AA failure


@pytest.mark.parametrize("scheme", SCHEMES)
def test_every_token_is_defined(scheme: str) -> None:
    tokens = _tokens(SCHEMES[scheme])
    missing = [t for t in TOKENS if t not in tokens]
    assert not missing, f"{scheme}: missing {missing}"
    for token in COLOUR_TOKENS:
        _hex(tokens[token])
    for verdict in VERDICTS:
        _hex(tokens[f"--fl-{verdict}-bg"])


@pytest.mark.parametrize("scheme", SCHEMES)
def test_text_contrast(scheme: str) -> None:
    t = _tokens(SCHEMES[scheme])
    for fg in ("--fl-text", "--fl-text-muted", *ACCENTS):
        for bg in ("--fl-bg", "--fl-bg-subtle", "--fl-surface"):
            ratio = _contrast(t[fg], t[bg])
            assert ratio >= 4.5, f"{scheme}: {fg} on {bg} is {ratio:.2f}:1"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_verdict_badge_contrast(scheme: str) -> None:
    t = _tokens(SCHEMES[scheme])
    for verdict in VERDICTS:
        fg, bg = t[f"--fl-{verdict}"], t[f"--fl-{verdict}-bg"]
        ratio = _contrast(fg, bg)
        assert ratio >= 4.5, f"{scheme}: {verdict} badge is {ratio:.2f}:1"
        # The same colour is used as plain text in docs tables and the ledger margin.
        assert _contrast(fg, t["--fl-surface"]) >= 4.5, f"{scheme}: {verdict} on surface"


def test_reduced_motion_disables_fade_up() -> None:
    css = _COMMENT.sub("", CSS)
    bodies = re.findall(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}", css, re.S)
    assert bodies, "no prefers-reduced-motion: reduce block"
    joined = "\n".join(bodies)
    rules = [(sel, body) for sel, body in _BLOCK.findall(joined) if ".fl-fade-up" in sel]
    assert rules, "the reduced-motion block does not mention .fl-fade-up"
    body = " ".join(b for _, b in rules)
    assert re.search(r"transition\s*:\s*none", body)
    assert re.search(r"animation\s*:\s*none", body)
    assert re.search(r"opacity\s*:\s*1", body)
    assert re.search(r"transform\s*:\s*none", body)


def test_component_classes_exist() -> None:
    selectors = " , ".join(sel for sel, _ in _blocks(CSS))
    for cls in CLASSES:
        assert re.search(re.escape(cls) + r"(?![\w-])", selectors), cls


def test_existing_classes_still_styled() -> None:
    """Classes used by the docs pages, the ledger and the playground keep their rules."""
    selectors = " , ".join(sel for sel, _ in _blocks(CSS))
    for cls in (
        ".fl-verdict",
        ".fl-verdict--pass",
        ".fl-verdict--fail",
        ".fl-verdict--no_data",
        ".fl-verdict--error",
        ".fl-verdict--unprobeable",
        ".fl-verdict--invalid",
        ".fl-ledger",
        ".fl-ledger__row",
        ".fl-hero",
        ".fl-trio",
        ".fl-use",
        ".fl-promises",
        ".fl-lead",
        ".fl-tok-verb",
    ):
        assert re.search(re.escape(cls) + r"(?![\w-])", selectors), cls
    # Legacy tokens the playground stylesheet reads keep resolving in both schemes.
    pg = (ROOT / "site-playground" / "playground.css").read_text(encoding="utf-8")
    used = set(re.findall(r"var\((--fl-[\w-]+)", pg))
    defined = set(_tokens(":root, [data-md-color-scheme]")) | set(_tokens(":root"))
    assert used <= defined, sorted(used - defined)


def test_script_drives_fade_up_and_copy() -> None:
    assert "IntersectionObserver" in JS
    assert "is-visible" in JS
    assert ".fl-fade-up" in JS
    assert ".fl-copy" in JS
    assert "clipboard" in JS


def test_table_code_never_breaks_mid_identifier() -> None:
    """At phone width a cell's identifier stays whole; the table scrolls instead."""
    code_rules = [
        body
        for sel, body in _blocks(CSS)
        if "table:not([class]) td code" in sel and "table:not([class]) th code" in sel
    ]
    assert code_rules, "no rule for code inside table cells"
    body = " ".join(code_rules)
    assert re.search(r"white-space\s*:\s*nowrap", body)
    assert re.search(r"overflow-wrap\s*:\s*normal", body)
    assert re.search(r"word-break\s*:\s*normal", body)
    table = " ".join(b for sel, b in _blocks(CSS) if sel == ".md-typeset table:not([class])")
    assert re.search(r"overflow-x\s*:\s*auto", table)
    assert re.search(r"max-width\s*:\s*100%", table)
