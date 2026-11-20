# SPDX-License-Identifier: GPL-3.0-or-later

"""Add-on settings: the only place that defines their defaults and their UI."""

from typing import Optional

import bpy
from bpy.props import FloatProperty, IntProperty
from bpy.types import AddonPreferences

DEFAULT_LADDER_ROWS = 4
DEFAULT_PIXELS_PER_STEP = 10.0


class PVS_Preferences(AddonPreferences):
    """How many ladder rows to show and how far to drag for one step."""

    # The manifest id is the add-on's module name, so this always matches it.
    bl_idname = __package__

    ladder_rows: IntProperty(
        name='Ladder Steps Above / Below',
        description='Hard limit for how many coarser and finer steps can be reached by sliding. '
                    'The same number of steps is shown above and below the unit step (10**0), '
                    'all at once, and nothing outside that range is shown',
        default=DEFAULT_LADDER_ROWS, min=1, max=8)
    pixels_per_step: FloatProperty(
        name='Pixels per Step',
        description='Horizontal drag distance, in UI pixels, for one step',
        default=DEFAULT_PIXELS_PER_STEP, min=1.0, soft_max=100.0)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'ladder_rows')
        layout.prop(self, 'pixels_per_step')
        layout.label(text='Middle mouse is checked in every editor keymap;')
        layout.label(text='the slider only appears over a numeric field.')


def get_preferences(context) -> Optional[PVS_Preferences]:
    """The add-on preferences, or None when it is not registered as an add-on."""
    try:
        return context.preferences.addons[__package__].preferences
    except KeyError:
        return None


def ladder_rows(context) -> int:
    """The configured number of steps above and below the unit step."""
    prefs = get_preferences(context)
    return prefs.ladder_rows if prefs else DEFAULT_LADDER_ROWS


def pixels_per_step(context) -> float:
    """The configured drag distance, in UI pixels, for one step."""
    prefs = get_preferences(context)
    return prefs.pixels_per_step if prefs else DEFAULT_PIXELS_PER_STEP


def register():
    bpy.utils.register_class(PVS_Preferences)


def unregister():
    try:
        bpy.utils.unregister_class(PVS_Preferences)
    except RuntimeError:
        pass  # not registered any more
