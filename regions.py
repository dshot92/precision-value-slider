# SPDX-License-Identifier: GPL-3.0-or-later

"""Which region the pointer is over, and the geometry the ladder is drawn into."""

from typing import Optional

_FLOATING = 'HUD'   # floating "Adjust Last Operation" panel (Blender 4.2+)
_MAIN = 'WINDOW'

# A rectangle in window pixels: left, bottom, width, height.
Rect = tuple[float, float, float, float]


def region_at(window, point) -> tuple:
    """The (area, region) visually under window coordinates ``point``.

    Blender routes events to the topmost region: floating panels first, then narrow
    overlapping regions such as sidebars and headers, and the main region last.
    """
    for area in window.screen.areas:
        for region in sorted(area.regions, key=_stack_order):
            if region.width < 2 or region.height < 2:
                continue
            if (region.x <= point[0] < region.x + region.width
                    and region.y <= point[1] < region.y + region.height):
                return area, region
    return None, None


def _stack_order(region) -> tuple:
    """Sort key: floating on top, then the other regions, main region last."""
    return (region.type != _FLOATING, region.type == _MAIN)


def avoid_rect(region, min_height: float) -> Optional[Rect]:
    """The hovered rect the ladder should keep clear of, in window pixels, or None.

    The ladder is drawn above every region, so a region it would bury needs moving
    aside: a header or sidebar shorter than the ladder gets one, while the main
    region (where fields are hovered) never does.
    """
    if region.type == _MAIN:
        return None
    if region.height >= min_height:
        return None
    return (region.x, region.y, region.width, region.height)


def area_bounds(area) -> Rect:
    """The area's rectangle in window pixels."""
    return (area.x, area.y, area.width, area.height)


def screen_bounds(window) -> Rect:
    """Bounding box of every area: the top bar and status bar stay outside it."""
    areas = window.screen.areas
    left = min(area.x for area in areas)
    bottom = min(area.y for area in areas)
    right = max(area.x + area.width for area in areas)
    top = max(area.y + area.height for area in areas)
    return (left, bottom, right - left, top - bottom)
