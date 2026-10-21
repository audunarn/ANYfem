"""Interpret numeric lists at the desktop input boundary."""

from __future__ import annotations

import json
import math
import re


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_PLAIN = re.compile(_NUMBER)
_QUANTITY = re.compile(_NUMBER + r"(?:\s*[A-Za-z][A-Za-z0-9*/^._-]*)?")
_FLAT_NUMERIC_ANNOTATION = re.compile(
    r"^(?:Sequence|Tuple|tuple|List|list)"
    r"\[\s*(?:float|int)(?:\s*,\s*(?:float|int|\.\.\.))*\s*\]"
)


def numeric_sequence_annotation(annotation: str) -> str | None:
    """Return the component type for a flat numeric sequence, not a matrix."""
    annotation = annotation.replace("typing.", "").replace("collections.abc.", "").strip()
    if annotation.startswith("Optional[") and annotation.endswith("]"):
        annotation = annotation[len("Optional["):-1].strip()
    if annotation.endswith(" | None"):
        annotation = annotation[:-len(" | None")].strip()
    match = _FLAT_NUMERIC_ANNOTATION.fullmatch(annotation)
    if match is None:
        return None
    return "int" if re.search(r"\[\s*int\b", match.group()) else "float"


def parse_numeric_sequence(text: str, convert=float, *, units: bool = False) -> tuple:
    """Parse comma- or whitespace-separated numbers, optionally in () or [].

    A converter can interpret each component in project units. JSON arrays of
    numbers or quoted quantities remain accepted. Delimiters and tokens must
    be complete: malformed input never silently loses a component.
    """
    text = text.strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass  # Bracketed whitespace-separated input is not JSON.
        else:
            if not isinstance(data, list) or any(
                isinstance(item, bool) or not isinstance(item, (int, float, str))
                for item in data
            ):
                raise ValueError("Enter a flat list of numbers")
            pattern = _QUANTITY if units else _PLAIN
            tokens = [str(item).strip() for item in data]
            if any(pattern.fullmatch(token) is None for token in tokens):
                raise ValueError("Enter a flat list of numbers")
            values = tuple(convert(token) for token in tokens)
            if any(isinstance(value, float) and not math.isfinite(value) for value in values):
                raise ValueError("Numbers must be finite")
            return values

    if text[:1] in ("[", "("):
        closing = "]" if text[0] == "[" else ")"
        if not text.endswith(closing):
            raise ValueError("Unmatched list brackets")
        text = text[1:-1].strip()
    if any(char in text for char in "[]()"):
        raise ValueError("Invalid list brackets")
    if not text:
        return ()

    pattern = _QUANTITY if units else _PLAIN
    tokens = []
    pos = 0
    while pos < len(text):
        match = pattern.match(text, pos)
        if match is None:
            raise ValueError(f"Invalid number near {text[pos:]!r}")
        tokens.append(match.group())
        pos = match.end()
        start = pos
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            break
        if text[pos] == ",":
            pos += 1
            while pos < len(text) and text[pos].isspace():
                pos += 1
            if pos == len(text) or text[pos] == ",":
                raise ValueError("A number is missing after a comma")
        elif pos == start:
            raise ValueError(f"Numbers need a comma or space before {text[pos:]!r}")
    values = tuple(convert(token) for token in tokens)
    if any(isinstance(value, float) and not math.isfinite(value) for value in values):
        raise ValueError("Numbers must be finite")
    return values
