# SPDX-License-Identifier: GPL-3.0-or-later

"""Precision ladder math.

Steps are powers of ten and a slide only adds or subtracts whole steps; nothing here
touches Blender, which keeps the arithmetic testable on its own.
"""

from decimal import Decimal
from typing import Optional


def step_for_exponent(exponent: int) -> Decimal:
    """Size of one step at ``exponent``: exactly 10**exponent."""
    return Decimal(1).scaleb(exponent)


def step_label(exponent: int) -> str:
    """Step size as the ladder writes it ("0.0001" -> ".0001")."""
    text = format(step_for_exponent(exponent), 'f')
    return text[1:] if text.startswith('0.') else text


def slide_value(original: float, step: Decimal, ticks: int) -> Optional[float]:
    """``original`` moved by ``ticks`` whole steps.

    ``ticks == 0`` returns ``None``: the caller leaves the value exactly as it was.
    """
    if ticks == 0:
        return None
    return original + ticks * float(step)


def exponent_range(is_int: bool, span: int) -> tuple[int, int]:
    """Finest and coarsest exponent shown, always centred on the unit step (10**0).

    An integer field cannot go finer than 1, so its range is not symmetric.
    """
    return (0 if is_int else -span), span


def clamp_exponent(exponent: int, low: int, high: int) -> int:
    """``exponent`` limited to the ladder's hard precision range."""
    return max(low, min(high, exponent))
