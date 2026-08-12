"""Canonicalisation of steel designations, grades, bolts and dimensions.

Pure functions, no I/O. This module is small but load-bearing: a drawing
schedule writes `ISMB300`, the specification writes `ISMB 300`, an OCR'd
title block writes `I.S.M.B.-300`, and a search for any one of them must
find all three. Canonicalisation is what makes that true, and it is why the
canonical form -- not the raw span -- is what gets indexed.

Unit normalisation exists for the same reason on the numeric side: a
capacity written `250 N/mm2` and one written `250 MPa` are the same claim,
and a query about one should retrieve the other.
"""

from __future__ import annotations

import re

# Separators that appear between a section prefix and its size, in the wild:
# spaces, hyphens, and the dots left by OCR reading "I.S.M.B."
_NOISE = re.compile(r"[.\-_\s]+")
# The same, minus the dot. Sizes must keep their decimal point: AISC weights
# are decimal, and `C10x15.3` collapsed to `C 10X153` is a different section.
_SIZE_NOISE = re.compile(r"[\-_\s]+")
_MULT = re.compile(r"[x×*]", re.IGNORECASE)  # noqa: RUF001 - U+00D7 is written on real drawings; matching it is the point

# Unit spellings mapped to their SI canonical form. The key is lowercased and
# stripped of spaces before lookup, so "N / mm2" and "n/mm²" both land here.
_UNIT_CANONICAL = {
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "in": "in",
    '"': "in",
    "ft": "ft",
    "'": "ft",
    "kg": "kg",
    "kg/m": "kg/m",
    "t": "t",
    "kn": "kN",
    "kn/m": "kN/m",
    "knm": "kNm",
    "kn-m": "kNm",
    "n": "N",
    "nm": "Nm",
    "mpa": "MPa",
    "n/mm2": "MPa",
    "n/mm²": "MPa",
    "n/mm^2": "MPa",
    "ksi": "ksi",
    "psi": "psi",
    "deg": "deg",
    "°": "deg",
}

# Multipliers into the canonical unit for each dimension family, so a value
# can be compared across the spellings a drawing set actually mixes.
_TO_SI: dict[str, tuple[float, str]] = {
    "mm": (1.0, "mm"),
    "cm": (10.0, "mm"),
    "m": (1000.0, "mm"),
    "in": (25.4, "mm"),
    "ft": (304.8, "mm"),
    "MPa": (1.0, "MPa"),
    "ksi": (6.894757, "MPa"),
    "psi": (0.00689476, "MPa"),
    "kN": (1.0, "kN"),
    "N": (0.001, "kN"),
    "t": (9.80665, "kN"),
}


def canonical_section(prefix: str, size: str) -> str:
    """`ISMB`, `300x140` -> `ISMB 300X140`.

    One space between family and size, `x` as the only multiplication sign,
    uppercase throughout. Deliberately not prettified further: the canonical
    form is a matching key, and every transformation applied here must be
    applied identically to a query, so simpler is safer.
    """
    clean_prefix = _NOISE.sub("", prefix).upper()
    clean_size = _MULT.sub("X", _SIZE_NOISE.sub("", size)).upper().strip(".")
    return f"{clean_prefix} {clean_size}".strip()


def canonical_grade(raw: str) -> str:
    """`fe-415` -> `Fe 415`; `s355j2` -> `S355 J2`; `IS2062 E250` -> `E 250`."""
    text = _NOISE.sub(" ", raw).strip().upper()
    text = re.sub(r"\s+", " ", text)

    if match := re.fullmatch(r"FE\s*(\d{3})", text):
        return f"Fe {match.group(1)}"
    if match := re.fullmatch(r"(?:IS\s*2062\s*)?E\s*(\d{3})\s*([ABC])?", text):
        suffix = f" {match.group(2)}" if match.group(2) else ""
        return f"E {match.group(1)}{suffix}"
    if match := re.fullmatch(r"S\s*(\d{3})\s*(J[RO0-2]|K2|N|M|NL|ML)?", text):
        suffix = f" {match.group(2)}" if match.group(2) else ""
        return f"S{match.group(1)}{suffix}"
    if match := re.fullmatch(r"A\s*(\d{2,3})", text):
        return f"A{match.group(1)}"
    return text


def canonical_bolt(diameter: str, grade: str | None = None, length: str | None = None) -> str:
    """`20`, `8.8`, `60` -> `M20x60 8.8`.

    Length is kept when present because a bolt schedule distinguishes M20x60
    from M20x80, and dropping it would merge two different line items.
    """
    parts = f"M{int(diameter)}"
    if length:
        parts += f"X{int(length)}"
    return f"{parts} {grade}" if grade else parts


def canonical_unit(raw: str) -> str:
    """Map a unit as written to its canonical spelling; unknown units pass through."""
    key = _NOISE.sub("", raw).lower().replace(" ", "")
    return _UNIT_CANONICAL.get(key, raw.strip())


def to_si(value: float, unit: str) -> tuple[float, str] | None:
    """Convert to the SI unit of the same family.

    Returns None for units with no conversion defined, so a caller records
    the value as written rather than inventing an equivalence.
    """
    factor_unit = _TO_SI.get(unit)
    if factor_unit is None:
        return None
    factor, si_unit = factor_unit
    return round(value * factor, 4), si_unit


def canonical_identifier(raw: str) -> str:
    """Drawing/part/mark numbers: `s-101`, `S 101` -> `S-101`.

    Collapses internal whitespace to a single hyphen, which is how these are
    written on a title block, and uppercases. A number written `S101` stays
    `S101` -- inserting a separator that was never there would invent an
    identifier that does not exist in the client's drawing register.
    """
    text = raw.strip().upper()
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-{2,}", "-", text).strip("-")
