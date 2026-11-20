# SPDX-License-Identifier: GPL-3.0-or-later

"""GPU drawing of the precision ladder.

The operator owns interaction state; this module turns it into pixels and decides
where the panel sits.
"""

import math
from dataclasses import dataclass
from typing import Optional

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from .ladder import step_label

_shader = None  # cached builtin shader; released in unregister()

_ROW_TEXT_LINES = 2
_ROW_LINE_FACTOR = 1.9  # one text line, as a multiple of the widget font size
_EDGE = 2.0             # smallest distance from the region border
_OBSTACLE_GAP = 8.0     # distance kept from a floating panel

# A rectangle in window pixels: left, bottom, width, height.
Rect = tuple[float, float, float, float]


@dataclass
class LadderState:
    """What the draw callback needs; the operator mutates it while sliding.

    Coordinates are window pixels: every region the ladder overlaps draws its own
    slice of the same rectangle, so no region-local origin can be stored here.
    """

    screen: int          # window.screen.as_pointer(); regions are filtered by it
    window: int          # window.as_pointer()
    x: float             # column centre, window pixels
    y: float             # centre of the selected row, window pixels
    low: int             # finest exponent shown
    high: int            # coarsest exponent shown
    exponent: int        # highlighted exponent
    bounds: Rect = (0.0, 0.0, 0.0, 0.0)  # rectangle the panel is kept inside
    value_text: str = ''
    avoid: Optional[Rect] = None  # floating panel the ladder should stay clear of


def ui_scale() -> float:
    """The interface scale, clamped to the range Blender's own widgets use."""
    return max(.25, float(bpy.context.preferences.system.ui_scale))


def _font_size() -> float:
    return float(bpy.context.preferences.ui_styles[0].widget.points) * ui_scale()


def row_height() -> float:
    """Height of one ladder row (step line plus value line) in region pixels."""
    return _font_size() * _ROW_LINE_FACTOR * _ROW_TEXT_LINES


def ladder_height(low: int, high: int) -> float:
    """Height the ladder needs for the exponents ``low``..``high`` in region pixels."""
    return (high - low + 1) * row_height()


def ladder_center(click_x: float, click_y: float, low: int, high: int,
                  exponent: int) -> tuple[float, float]:
    """Ladder centre that puts ``exponent``'s row under the click (region pixels)."""
    rows = high - low + 1
    index = high - exponent
    return click_x, click_y + (index - (rows - 1) * .5) * row_height()


# ---- primitives ------------------------------------------------------------------


def _rgba(value) -> tuple:
    """Theme colours may be RGB or RGBA; always return RGBA."""
    color = tuple(float(c) for c in value)
    return color if len(color) == 4 else (*color[:3], 1.0)


def _rounded_rect(left, bottom, width, height, radius) -> list:
    """Vertices of a (rounded) rectangle, ordered for a triangle fan."""
    radius = min(max(0.0, radius), width * .5, height * .5)
    if radius < .5:
        return [(left, bottom), (left + width, bottom),
                (left + width, bottom + height), (left, bottom + height)]
    steps = max(5, min(12, math.ceil(radius)))  # corner segments; enough for UI sizes
    points = []
    # Four corners: (centre x, centre y, start angle), counter-clockwise from bottom-left.
    for cx, cy, start in ((left + radius, bottom + radius, math.pi),
                          (left + width - radius, bottom + radius, math.pi * 1.5),
                          (left + width - radius, bottom + height - radius, 0.0),
                          (left + radius, bottom + height - radius, math.pi * .5)):
        for i in range(steps + 1):
            angle = start + math.pi * .5 * i / steps
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return points


def _fill(left, bottom, width, height, color, radius=0.0) -> None:
    """Draw a filled, optionally rounded rectangle in region pixel coordinates."""
    global _shader
    if width <= 0 or height <= 0 or color[3] <= 0:
        return
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    _shader.bind()
    _shader.uniform_float('color', color)
    batch_for_shader(_shader, 'TRI_FAN',
                     {'pos': _rounded_rect(left, bottom, width, height, radius)}).draw(_shader)


def _text(text, size, rect, color) -> None:
    """Draw ``text`` centred in ``rect``."""
    left, bottom, width, height = rect
    blf.size(0, size)
    text_w = blf.dimensions(0, text)[0]
    x = left + (width - text_w) * .5
    # Centre on a fixed reference height so digits and letters share a baseline.
    blf.position(0, x, bottom + (height - blf.dimensions(0, 'Ag')[1]) * .5, 0)
    blf.color(0, *color)
    blf.draw(0, text)


# ---- ladder ----------------------------------------------------------------------


def _panel_position(bounds: Rect, state: LadderState, width: float,
                    height: float) -> tuple[float, float]:
    """Bottom-left corner of the panel, kept inside ``bounds``.

    The panel is centred on the click. A floating panel (the last-operation popup)
    would cover that spot, so the panel goes beside it instead.
    """
    left = state.x - width * .5
    bottom = state.y - height * .5
    if state.avoid is not None:
        left, bottom = _beside_obstacle(state.avoid, bounds, width, height)
    edge_left, edge_bottom, edge_width, edge_height = bounds
    max_left = max(edge_left + _EDGE, edge_left + edge_width - width - _EDGE)
    max_bottom = max(edge_bottom + _EDGE, edge_bottom + edge_height - height - _EDGE)
    return (min(max(left, edge_left + _EDGE), max_left),
            min(max(bottom, edge_bottom + _EDGE), max_bottom))


def _beside_obstacle(avoid: Rect, bounds: Rect, width: float,
                     height: float) -> tuple[float, float]:
    """Place the panel next to ``avoid``: right side first, then left, then below."""
    left, bottom, avoid_width, avoid_height = avoid
    edge_left, edge_bottom, edge_width, edge_height = bounds
    edge_right = edge_left + edge_width - _EDGE
    right = left + avoid_width + _OBSTACLE_GAP
    left_side = left - _OBSTACLE_GAP - width
    x = right if right + width <= edge_right else left_side
    y = bottom + (avoid_height - height) * .5
    if x < edge_left + _EDGE or x + width > edge_right:
        x = left + (avoid_width - width) * .5
        y = bottom - _OBSTACLE_GAP - height
        if y < edge_bottom + _EDGE:
            y = bottom + avoid_height + _OBSTACLE_GAP
    return x, y


def panel_size(state: LadderState) -> tuple[float, float]:
    """Width and height the panel needs for ``state``'s rows and value text."""
    labels = {exponent: step_label(exponent)
              for exponent in range(state.high, state.low - 1, -1)}
    pad = 6 * ui_scale()
    base = _font_size()
    blf.size(0, base)
    label_w = max(blf.dimensions(0, f'‹  {text}  ›')[0] for text in labels.values())
    value_w = blf.dimensions(0, state.value_text)[0]
    if not state.value_text.startswith('-'):
        # Keep a sign column even while positive: crossing zero must not resize the panel.
        value_w += blf.dimensions(0, '-')[0]
    # Quantised to whole font sizes so the panel does not resize on every digit.
    width = math.ceil(max(label_w, value_w, base * 5) / base) * base + pad * 2
    height = len(labels) * row_height() + pad * 2
    return width, height


def draw_ladder(state: LadderState) -> None:
    """POST_PIXEL callback: show the whole precision range, highlight the active step.

    Registered for every region type in the window: each region draws the slice of
    the panel that falls inside it, after its own content, so the ladder ends up
    above all of them instead of being clipped to one editor.
    """
    context = bpy.context
    region = context.region
    if region is None:
        return
    try:
        # The handler lives on the space type, so it also runs in other windows.
        if region.id_data.as_pointer() != state.screen:
            return
        window = context.window
    except (ReferenceError, AttributeError, TypeError, RuntimeError):
        return
    if window is not None and window.as_pointer() != state.window:
        return

    prefs = context.preferences
    theme = prefs.themes[0].user_interface
    menu, item = theme.wcol_menu_back, theme.wcol_menu_item
    scale = ui_scale()
    base = _font_size()
    text_color, text_selected = _rgba(item.text), _rgba(item.text_sel)
    selected_color, inner_color = _rgba(item.inner_sel), _rgba(menu.inner)

    row_h = row_height()
    rows = list(range(state.high, state.low - 1, -1))
    labels = {exponent: step_label(exponent) for exponent in rows}

    pad = 6 * scale
    width, height = panel_size(state)
    left, bottom = _panel_position(state.bounds, state, width, height)
    # Window pixels to this region's pixels; Blender scissors the draw to the region,
    # so only the overlapping slice appears here and the slices add up to the panel.
    left -= region.x
    bottom -= region.y
    if (left + width < 0 or bottom + height < 0
            or left > region.width or bottom > region.height):
        return
    radius = base * 1.6 * min(1.0, max(0.0, float(menu.roundness))) * .5

    gpu.state.blend_set('ALPHA')
    _fill(left, bottom, width, height, _rgba(menu.outline), radius)
    inset = max(1.0, scale)
    _fill(left + inset, bottom + inset, width - inset * 2, height - inset * 2,
          inner_color, max(0.0, radius - inset))

    # Every row is two lines tall, so the panel keeps its position and height whether or
    # not the selected row shows a value. The value is drawn on the second line only for
    # the active exponent; the other rows leave it empty.
    top = bottom + height - pad
    for exponent in rows:
        row_bottom = top - row_h
        rect = (left + inset, row_bottom, width - inset * 2, row_h)
        step_rect = (rect[0], row_bottom + row_h * .5, rect[2], row_h * .5)
        value_rect = (rect[0], row_bottom, rect[2], row_h * .5)
        if exponent == state.exponent:
            _fill(*rect, selected_color, radius * .6)
            _text(f'‹  {labels[exponent]}  ›', base, step_rect, text_selected)
            _text(state.value_text, base, value_rect, text_selected)
        else:
            _text(labels[exponent], base, step_rect, text_color)
        top = row_bottom
    gpu.state.blend_set('NONE')


# ---- registration ----------------------------------------------------------------


def register():
    """Nothing to register: drawing runs through the operator's draw handler."""


def unregister():
    """Drop the cached GPU shader wrapper; it is rebuilt on the next draw."""
    global _shader
    _shader = None
