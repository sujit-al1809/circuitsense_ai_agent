"""Parsing and formatting of engineering quantities ("4.7k", "4k7", "330Ω", "100nF", "9.09 mA")."""

from __future__ import annotations

import math
import re

PREFIXES: dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "μ": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}

# Canonical unit symbol -> accepted spellings (case-sensitive where it matters).
UNIT_ALIASES: dict[str, tuple[str, ...]] = {
    "Ω": ("Ω", "ohm", "ohms", "Ohm", "Ohms", "R", "r"),
    "V": ("V", "v", "volt", "volts"),
    "A": ("A", "amp", "amps"),
    "F": ("F", "f"),
    "H": ("H", "h"),
    "W": ("W", "w"),
    "Hz": ("Hz", "hz", "HZ"),
}
QUANTITY_UNITS = {"voltage": "V", "current": "A", "resistance": "Ω", "frequency": "Hz"}

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_PLAIN = re.compile(rf"^\s*(?P<num>{_NUMBER})\s*(?P<rest>.*?)\s*$")
# RKM code: 4k7, 2R2, 1M5, 4u7
_RKM = re.compile(r"^\s*(?P<a>\d+)(?P<p>[pnuµμmkKMGR])(?P<b>\d+)\s*(?P<rest>.*?)\s*$")


class UnitError(ValueError):
    pass


def _canonical_unit(text: str) -> str | None:
    for canonical, aliases in UNIT_ALIASES.items():
        if text in aliases:
            return canonical
    return None


def parse_quantity(text: str | float | int, expected_unit: str | None = None) -> float:
    """Parse an engineering quantity into SI base units.

    ``expected_unit`` (e.g. "Ω", "V") is used to validate an explicit unit and to
    interpret ambiguous single letters ("m" is milli unless the expected unit is metres,
    which we never use).
    """
    if isinstance(text, (int, float)):
        value = float(text)
        if not math.isfinite(value):
            raise UnitError("value must be finite")
        return value
    raw = str(text).strip()
    if not raw:
        raise UnitError("empty quantity")

    m = _RKM.match(raw)
    if m:
        p = m.group("p")
        multiplier = 1.0 if p in ("R",) else PREFIXES[p]
        value = float(f"{m.group('a')}.{m.group('b')}") * multiplier
        _check_unit(m.group("rest"), expected_unit, raw)
        return value

    m = _PLAIN.match(raw)
    if not m:
        raise UnitError(f"could not parse quantity {raw!r}")
    value = float(m.group("num"))
    rest = m.group("rest").replace(" ", "")
    if not rest:
        return value

    unit = _canonical_unit(rest)
    if unit is not None:
        _check_unit(rest, expected_unit, raw)
        return value
    prefix, unit_text = rest[0], rest[1:]
    if prefix in PREFIXES and (unit_text == "" or _canonical_unit(unit_text) is not None):
        _check_unit(unit_text, expected_unit, raw)
        return value * PREFIXES[prefix]
    raise UnitError(f"unrecognised unit in {raw!r}")


def _check_unit(unit_text: str, expected_unit: str | None, raw: str) -> None:
    unit_text = unit_text.strip()
    if not unit_text or expected_unit is None:
        return
    canonical = _canonical_unit(unit_text)
    if canonical is None:
        raise UnitError(f"unrecognised unit in {raw!r}")
    if canonical != expected_unit:
        raise UnitError(f"expected a value in {expected_unit}, got {raw!r}")


def unit_to_si(value: float, unit: str, quantity: str) -> tuple[float, str]:
    """Convert a measured value + unit string (e.g. 3.2, "mV") into SI for the given quantity."""
    base = QUANTITY_UNITS.get(quantity)
    if base is None:
        raise UnitError(f"unsupported quantity {quantity!r}")
    u = unit.strip()
    canonical = _canonical_unit(u)
    if canonical == base:
        return value, base
    if len(u) >= 2 and u[0] in PREFIXES and _canonical_unit(u[1:]) == base:
        return value * PREFIXES[u[0]], base
    if quantity == "resistance" and u in ("k", "K", "M"):
        return value * PREFIXES[u], base
    raise UnitError(f"unit {unit!r} is not valid for {quantity} (expected {base} with optional prefix)")


_FORMAT_PREFIXES = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")]


def format_quantity(value: float, unit: str, sig: int = 3) -> str:
    if value == 0:
        return f"0 {unit}"
    mag = abs(value)
    for factor, prefix in _FORMAT_PREFIXES:
        if mag >= factor * 0.9995:
            scaled = value / factor
            break
    else:
        factor, prefix = 1e-12, "p"
        scaled = value / factor
    digits = max(sig - 1 - int(math.floor(math.log10(abs(scaled)))), 0) if scaled else 0
    text = f"{scaled:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {prefix}{unit}"
