"""Automated WCAG 2.1 contrast ratio check for Theme.qml Light & Dark palettes."""

import re
from pathlib import Path


def _lum(hex_c: str) -> float:
    h = hex_c.lstrip("#")
    r, g, b = [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def _f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _f(r) + 0.7152 * _f(g) + 0.0722 * _f(b)


def _contrast(a: str, b: str) -> float:
    l1, l2 = sorted([_lum(a), _lum(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def _parse_theme_qml() -> tuple[dict[str, str], dict[str, str]]:
    src = Path("orion/ui/qml/components/Theme.qml").read_text(encoding="utf-8")
    dark: dict[str, str] = {}
    light: dict[str, str] = {}
    pat = re.compile(
        r'readonly property color (\w+):\s*isDark\s*\?\s*"(#[0-9A-Fa-f]{6})"\s*:\s*"(#[0-9A-Fa-f]{6})"'
    )
    for m in pat.finditer(src):
        name, d_hex, l_hex = m.group(1), m.group(2), m.group(3)
        dark[name] = d_hex
        light[name] = l_hex
    return light, dark


def test_theme_qml_contrast_ratios():
    light, dark = _parse_theme_qml()
    assert len(light) >= 15
    assert len(dark) >= 15

    text_pairs = [
        ("text", "window"),
        ("text", "surface"),
        ("text", "sidebar"),
        ("text", "userBubble"),
        ("textSecondary", "window"),
        ("textSecondary", "surface"),
        ("textSecondary", "sidebar"),
        ("onAccent", "accent"),
        ("danger", "surface"),
        ("warning", "surface"),
        ("accent", "surface"),
    ]
    stroke_pairs = [
        ("border", "surface"),
        ("border", "window"),
        ("habit1", "surface"),
        ("habit2", "surface"),
        ("habit3", "surface"),
        ("habit4", "surface"),
    ]

    for mode_name, pal in (("light", light), ("dark", dark)):
        for fg, bg in text_pairs:
            ratio = _contrast(pal[fg], pal[bg])
            assert ratio >= 4.5, f"{mode_name}: {fg} on {bg} contrast {ratio:.2f} < 4.5"
        for fg, bg in stroke_pairs:
            ratio = _contrast(pal[fg], pal[bg])
            assert ratio >= 3.0, f"{mode_name}: {fg} on {bg} contrast {ratio:.2f} < 3.0"
