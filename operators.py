# SPDX-License-Identifier: GPL-3.0-or-later

"""Middle-mouse precision sliding: the modal operator."""

import math
import time

import bpy
from bpy.types import Operator

from . import keymaps, regions
from .drawing import LadderState, draw_ladder, ladder_center, ladder_height, panel_size, row_height, ui_scale
from .ladder import clamp_exponent, exponent_range, slide_value, step_for_exponent
from .numeric_field import NumericField
from .preferences import ladder_rows, pixels_per_step

_SLIDE_ERRORS = (ReferenceError, AttributeError, TypeError, ValueError, RuntimeError, KeyError)

#: Axis-switch margin, as a multiple of one step. At 1.0 the pointer must travel one
#: full step along the other axis before that axis takes over, so the deadzone is a
#: square one step wide in each direction.
AXIS_MARGIN_STEPS = 1.0

#: Cursor speed, in UI pixels per second, below which the ladder may be changed.
#: Faster movement only slides the value, so a quick drag never changes precision.
VERTICAL_SPEED_LIMIT = 400.0

#: Time constant of the smoothed speed estimate, in seconds.
SPEED_SMOOTHING = 0.05

#: Pause after which the speed estimate restarts, in seconds.
SPEED_TIMEOUT = 0.1

#: Vertical distance per ladder step, as a fraction of the drawn row height. 0.5 makes
#: one step take half a row of pointer travel, so vertical selection is twice as fast.
VERTICAL_STEP_SCALE = 0.5

# Manifest id: the add-on's module name, also the leaf of __package__ when installed
# as an extension (bl_ext.<repository>.<id>). Operator idnames are built from it.
_ADDON_ID = __package__.rpartition('.')[-1]


class PVS_OT_slide(Operator):
    """Write live while dragging, restore on cancel, commit on release."""

    bl_idname = f'{_ADDON_ID}.slide'
    bl_label = 'Precision Value Slide'
    bl_description = 'Hold middle mouse over a number field and drag the precision ladder'
    # BLOCKING + GRAB_CURSOR grab the pointer for the modal (locked on Wayland, hidden on
    # X11). One undo step is pushed on commit only.
    bl_options = {'UNDO', 'INTERNAL', 'BLOCKING', 'GRAB_CURSOR'}

    # ---- lifecycle --------------------------------------------------------------

    def invoke(self, context, event):
        window = context.window
        if window is None or event.type != 'MIDDLEMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        start_x, start_y = event.mouse_x, event.mouse_y
        area, region = regions.region_at(window, (start_x, start_y))
        if area is None:
            return {'PASS_THROUGH'}
        try:
            # The hovered button is only visible from inside its own region.
            with context.temp_override(window=window, area=area, region=region):
                # Popovers are temporary regions whose blocking UI handler consumes
                # mouse events before keymaps run, so the operator is not invoked
                # there. Guard anyway: region_at() cannot see the temporary region,
                # so acting here could target the field behind the popover.
                if getattr(bpy.context, 'region_popup', None) is not None:
                    return {'PASS_THROUGH'}
                self._field = NumericField(bpy.context, getattr(bpy.context, 'property', None))
                display = self._field.display()
                original_raw = self._field.raw()
                original_all = self._field.raw_values()
        except _SLIDE_ERRORS:
            return {'PASS_THROUGH'}  # not a numeric field: native middle-mouse continues

        prefs_pixels = pixels_per_step(context)
        scale = ui_scale()
        self._pixels = max(1.0, prefs_pixels) * scale
        self._axis_margin = self._pixels * AXIS_MARGIN_STEPS
        self._speed_limit = VERTICAL_SPEED_LIMIT * scale

        self._original_all = original_all           # every component as it was
        self._expected_all = list(original_all)     # what we last wrote (re-applied on TIMER)
        self._base = display                        # value the drag distance is measured from
        self._base_raw = original_raw               # the same value in stored units (exact)
        self._anchor_x = start_x                    # where horizontal measurement starts
        self._anchor_y = start_y                    # where vertical precision measurement starts
        self._ticks = 0
        self._axis_x = start_x                      # last position along the active axis
        self._axis_y = start_y
        self._precision_locked = False              # start in vertical (ladder) mode
        self._last_x = start_x                      # previous event, for the speed estimate
        self._last_y = start_y
        self._last_time = time.monotonic()
        self._speed = 0.0                           # window pixels per second, smoothed

        # The ladder is always centred on the unit step, never on the value's own
        # precision; the preference adds N coarser and N finer steps around it. Every
        # row is shown at every value. A step finer than the field can store is not
        # turned into another amount: the drag accumulates the exact step and the field
        # takes the nearest value it can hold, so a long enough drag still moves.
        span = ladder_rows(context)
        low, high = exponent_range(self._field.is_int, span)
        # Start on a row whose single tick changes the stored value, so the first tick
        # of a drag visibly moves. The finer rows stay visible, and a coarser row is
        # added if the usable one is above the preference's range (a billion needs a
        # million).
        usable = max(low, self._field.finest_step_exponent())
        high = max(high, usable)
        self._anchor_exponent = clamp_exponent(0, usable, high)  # step at the vertical anchor
        avoid = regions.avoid_rect(region, ladder_height(low, high))
        x, y = ladder_center(start_x, start_y, low, high, self._anchor_exponent)
        self._state = LadderState(
            screen=window.screen.as_pointer(), window=window.as_pointer(),
            x=x, y=y, low=low, high=high,
            exponent=self._anchor_exponent, avoid=avoid)
        self._refresh_text()

        # Normally the panel stays inside the area it was opened in; an editor too
        # short or narrow for it lets it spill over the neighbours instead of being
        # clipped, and never past the top bar or the status bar.
        width, height = panel_size(self._state)
        area_rect = regions.area_bounds(area)
        self._state.bounds = (
            area_rect if width <= area.width and height <= area.height
            else regions.screen_bounds(window))

        # The panel is drawn above every region it overlaps, so each region type of
        # every area needs the callback: each call renders only the slice of the
        # ladder that falls inside its own region, after that region's content.
        self._handlers = []
        seen = set()
        for screen_area in window.screen.areas:
            space = type(screen_area.spaces.active)
            for screen_region in screen_area.regions:
                key = (space, screen_region.type)
                if key in seen:
                    continue
                seen.add(key)
                handle = space.draw_handler_add(
                    draw_ladder, (self._state,), screen_region.type, 'POST_PIXEL')
                self._handlers.append((handle, space, screen_region.type))

        # Animation evaluation can overwrite the value, so re-apply it while sliding.
        self._timer = context.window_manager.event_timer_add(.1, window=window)
        self._window = window
        self._cursor_start = (start_x, start_y)
        self._sliding = True
        self._cursor_hidden = False
        self._hide_cursor()
        # Blender < 5.1 gives the hovered field's "move X" cursor through the modal
        # cursor slot and only exits that hover after this operator starts, which
        # shows the pointer again (5.1 checks `WM_cursor_modal_is_set_ok()` and leaves
        # a running modal operator's cursor alone). A timer runs after the event
        # handlers and before drawing, so it re-hides the pointer in the same frame
        # instead of leaving it visible until the next modal event.
        self._hide_timer = None
        if self._cursor_hidden and bpy.app.version < (5, 1, 0):
            self._hide_timer = self._rehide_cursor
            bpy.app.timers.register(self._hide_timer, first_interval=0.0)
        context.window_manager.modal_handler_add(self)
        self._redraw(context)  # the ladder may cover several areas, not just one
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Called by Blender when the modal is aborted from outside."""
        self._restore()
        self._cleanup(context)

    def modal(self, context, event):
        kind = event.type
        try:
            if kind == 'WINDOW_DEACTIVATE' or (kind in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS'):
                self._restore()
                self._cleanup(context)
                return {'CANCELLED'}

            if kind == 'MIDDLEMOUSE' and event.value == 'RELEASE':
                now = self._field.raw_values()
                changed = now != self._original_all
                if changed:
                    # Keyframe each changed component once, like a native edit.
                    for axis, (before, after) in enumerate(zip(self._original_all, now)):
                        if before != after:
                            self._field.autokey(context, axis)
                self._cleanup(context)
                return {'FINISHED'} if changed else {'CANCELLED'}

            if kind in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                self._update_speed(event)
                self._update_axis(context, event)
                if self._precision_locked:
                    self._apply_drag(context, event)       # horizontal motion moves the value
                else:
                    self._apply_precision(context, event)  # vertical motion picks the step
            elif kind == 'TIMER':
                if self._field.raw_values() != self._expected_all:
                    self._field.write_raw_values(self._expected_all)
        except _SLIDE_ERRORS:
            self.report({'WARNING'}, 'The field changed during the slide; the original value was restored')
            self._restore()
            self._cleanup(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}

    # ---- interaction ------------------------------------------------------------

    def _update_speed(self, event) -> None:
        """Estimate cursor speed in window pixels per second, smoothed over time."""
        now = time.monotonic()
        dt = now - self._last_time
        distance = math.hypot(event.mouse_x - self._last_x, event.mouse_y - self._last_y)
        self._last_x, self._last_y = event.mouse_x, event.mouse_y
        self._last_time = now
        if dt <= 0.0:
            return
        if dt > SPEED_TIMEOUT:
            # A pause starts a new motion. Cap the interval so a fast move after the
            # pause is not averaged down to a slow speed by the idle time.
            self._speed = 0.0
            dt = SPEED_TIMEOUT
        alpha = 1.0 - math.exp(-dt / SPEED_SMOOTHING)
        self._speed += (distance / dt - self._speed) * alpha

    def _update_axis(self, context, event) -> None:
        """Give control to the axis the pointer is moving along.

        Horizontal motion slides the value and freezes the step. Vertical motion hands
        control back to the ladder, so the step can be changed at any point in the drag,
        not only near the start. ``AXIS_MARGIN_STEPS`` keeps small jitter from switching,
        and the speed gate ignores vertical motion during a quick drag.
        """
        dx = event.mouse_x - self._axis_x
        dy = event.mouse_y - self._axis_y
        if self._precision_locked:
            if abs(dx) >= abs(dy):
                self._axis_x, self._axis_y = event.mouse_x, event.mouse_y
            elif self._speed >= self._speed_limit:
                # A fast vertical move is ignored, and becomes the new run start, so
                # slowing down later does not apply all of it at once.
                self._axis_x, self._axis_y = event.mouse_x, event.mouse_y
            elif abs(dy) >= self._axis_margin:
                self._precision_locked = False
                self._anchor_y = self._axis_y
                self._anchor_exponent = self._state.exponent
                self._reanchor(context, event)
        else:
            if abs(dy) >= abs(dx):
                self._axis_x, self._axis_y = event.mouse_x, event.mouse_y
            elif abs(dx) >= self._axis_margin:
                self._precision_locked = True
                self._reanchor(context, event)
                self._anchor_x = self._axis_x  # apply the whole horizontal run

    def _apply_precision(self, context, event) -> None:
        """Vertical drag selects the step: up is coarser, down is finer.

        One step takes ``VERTICAL_STEP_SCALE`` of a drawn row of pointer travel, so the
        ladder can be more or less sensitive than its row spacing. While the cursor is
        fast, the anchor follows it without changing the step.
        """
        if self._speed >= self._speed_limit:
            self._anchor_y = event.mouse_y
            self._anchor_exponent = self._state.exponent
            return
        state = self._state
        distance = max(1.0, row_height() * VERTICAL_STEP_SCALE)
        rows = round((event.mouse_y - self._anchor_y) / distance)
        exponent = clamp_exponent(self._anchor_exponent + rows, state.low, state.high)
        if exponent == state.exponent:
            return
        state.exponent = exponent
        self._reanchor(context, event)

    def _apply_drag(self, context, event) -> None:
        """Write the value for the current horizontal distance (whole steps only)."""
        ticks = math.trunc((event.mouse_x - self._anchor_x) / self._pixels)
        if ticks == self._ticks:
            return
        self._ticks = ticks
        step = step_for_exponent(self._state.exponent)
        # The exact value is always the anchor plus whole steps, even when the step is
        # finer than the field can store. The field keeps the nearest value it can hold,
        # and the accumulated offset is never rounded away: at 5 million a float32 is
        # 0.5 apart, so a .0001 step only changes the stored value after enough ticks,
        # but it changes by the field's own resolution rather than by a wrong amount on
        # every tick. This is how Blender's own number drag behaves.
        value = slide_value(self._base, step, ticks, self._field.double)
        if value is None:
            self._field.write_raw(self._base_raw)  # back at the anchor: exactly as it was
        else:
            self._field.write(value)
        self._expected_all = self._field.raw_values()  # read back: includes float32 rounding
        self._refresh_text()
        self._redraw(context)

    def _reanchor(self, context, event) -> None:
        """Continue from the current value, so a step or axis change never makes it jump."""
        self._base = self._field.display()
        self._base_raw = self._field.raw()
        self._expected_all = self._field.raw_values()
        self._anchor_x = event.mouse_x
        self._ticks = 0
        self._refresh_text()
        self._redraw(context)

    # ---- state ------------------------------------------------------------------

    def _refresh_text(self) -> None:
        """Show the value as Blender does, with the step's digits when they are finer."""
        try:
            self._state.value_text = self._field.text(max(0, -self._state.exponent))
        except _SLIDE_ERRORS:
            pass

    def _restore(self) -> None:
        """Put the original value back (cancel path); failures are ignored on purpose."""
        try:
            self._field.write_raw_values(self._original_all)
        except _SLIDE_ERRORS:
            pass

    def _hide_cursor(self) -> bool:
        """Hide the pointer for the slide, remembering whether that worked."""
        try:
            self._window.cursor_modal_set('NONE')
        except (AttributeError, ReferenceError, TypeError, RuntimeError):
            self._cursor_hidden = False
        else:
            self._cursor_hidden = True
        return self._cursor_hidden

    def _rehide_cursor(self) -> float | None:
        """Stay hidden where the hovered field keeps showing the pointer again.

        Only registered on Blender < 5.1: the hovered number field sets its "move X"
        cursor through the modal cursor slot there, so hiding once does not stick.
        Returning ``0.0`` asks for another call on the next main loop iteration.
        """
        if not self._sliding or not self._hide_cursor():
            return None
        return 0.0

    def _cleanup(self, context) -> None:
        """Remove the draw handlers and timer and refresh the screen. Safe to call twice."""
        if getattr(self, '_sliding', False):
            self._sliding = False
            hide_timer = getattr(self, '_hide_timer', None)
            self._hide_timer = None
            if hide_timer is not None and bpy.app.timers.is_registered(hide_timer):
                bpy.app.timers.unregister(hide_timer)
        if getattr(self, '_cursor_hidden', False):
            try:
                self._window.cursor_modal_restore()
            except (AttributeError, ReferenceError, RuntimeError):
                pass  # the window is already gone
            self._cursor_hidden = False
        # Put the pointer back once, so releasing does not leave it at the drag end.
        start = getattr(self, '_cursor_start', None)
        if start is not None:
            self._cursor_start = None
            try:
                self._window.cursor_warp(*start)
            except (AttributeError, ReferenceError, RuntimeError):
                pass
        for handle, space, region_type in getattr(self, '_handlers', None) or ():
            try:
                space.draw_handler_remove(handle, region_type)
            except (ReferenceError, RuntimeError):
                pass  # the space type is gone with its add-on
        self._handlers = []
        timer = getattr(self, '_timer', None)
        if timer is not None:
            context.window_manager.event_timer_remove(timer)
            self._timer = None
        self._redraw(context)

    @staticmethod
    def _redraw(context) -> None:
        """The ladder may live in another area than the cursor's; refresh all of them."""
        for area in context.screen.areas if context.screen else ():
            area.tag_redraw()


# ---- registration ----------------------------------------------------------------


def register():
    try:
        bpy.utils.register_class(PVS_OT_slide)
        keymaps.bind(PVS_OT_slide.bl_idname)
    except Exception:
        unregister()  # roll back whatever did register, then let the original error through
        raise


def unregister():
    keymaps.unbind(PVS_OT_slide.bl_idname)
    try:
        bpy.utils.unregister_class(PVS_OT_slide)
    except RuntimeError:
        pass  # not registered any more
