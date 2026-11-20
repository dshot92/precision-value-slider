# SPDX-License-Identifier: GPL-3.0-or-later

"""Precision ladder math.

Steps are powers of ten and a slide only adds or subtracts whole steps; nothing here
touches Blender, which keeps the arithmetic testable on its own.
"""

import math
import struct
from decimal import Decimal
from typing import Optional


def step_for_exponent(exponent: int) -> Decimal:
    """Size of one step at ``exponent``: exactly 10**exponent."""
    return Decimal(1).scaleb(exponent)


def step_label(exponent: int) -> str:
    """Step size as the ladder writes it ("0.0001" -> ".0001")."""
    text = format(step_for_exponent(exponent), 'f')
    return text[1:] if text.startswith('0.') else text


def stored_text(value: float, double: bool = False) -> str:
    """The decimal the value was meant to be: the shortest text that reads back.

    RNA floats and some Blender ID properties are stored in 32 bits, so reading one
    back exposes its binary digits (``1.1`` reads back as ``1.100000023841858``);
    Python custom properties are doubles and keep their own digits. Adding a step to
    that noise lands on a value whose digits are just as noisy, and those extra digits
    would then show up in the ladder. Going through the shortest round-tripping decimal
    instead keeps the slide on the numbers the user picked.
    """
    if not math.isfinite(value):
        return repr(value)
    fmt = '<d' if double else '<f'
    try:
        stored = struct.pack(fmt, value)
    except (OverflowError, struct.error):
        return repr(value)
    for digits in range(1, 18 if double else 10):
        text = f'{value:.{digits}g}'
        try:
            if struct.pack(fmt, float(text)) == stored:
                return text
        except (OverflowError, struct.error, ValueError):
            continue
    return f'{value:.{17 if double else 9}g}'


def slide_value(original: float, step: Decimal, ticks: int,
                double: bool = False) -> Optional[float]:
    """``original`` moved by ``ticks`` whole steps.

    ``ticks == 0`` returns ``None``: the caller leaves the value exactly as it was.
    The sum is done in decimal, from the value's shortest round-tripping text, so one
    step is one decimal step instead of one step plus the stored float's noise.
    """
    if ticks == 0:
        return None
    if isinstance(original, int):
        # Integers are exact in Python: never round them through a 32 bit float,
        # which would freeze the slide above 16 777 216.
        return int(Decimal(original) + step * ticks)
    if not math.isfinite(original):
        return original
    return float(Decimal(stored_text(original, double)) + step * ticks)


def exponent_range(is_int: bool, span: int) -> tuple[int, int]:
    """Finest and coarsest exponent the preference asks for, around the unit step.

    An integer field cannot go finer than 1, so its range is not symmetric. Every row
    stays in the ladder at every value; a step finer than the field can store is applied
    to the slide's exact running total, so enough ticks still move the stored value.
    """
    return (0 if is_int else -span), span


def clamp_exponent(exponent: int, low: int, high: int) -> int:
    """``exponent`` limited to the ladder's hard precision range."""
    return max(low, min(high, exponent))
