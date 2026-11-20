# SPDX-License-Identifier: GPL-3.0-or-later

"""Precision Value Slider: middle-mouse sliding for numeric fields.

The manifest id (``precision_value_slider``) is the add-on's module name, so the
Blender identifiers are built from it: preferences use the full ``__package__`` path
and the operator idname uses its leaf. The ``PVS`` class prefix is its short form.

Modules are kept apart by responsibility:

    preferences    add-on settings, their defaults and their UI
    keymaps        runtime discovery of the editor keymaps
    ladder         step math, with no Blender dependency
    numeric_field  reading and writing the hovered property
    regions        pointer hit-testing and draw-target geometry
    drawing        GPU ladder rendering
    operators      the modal operator that ties them together
"""

import importlib

from . import drawing, keymaps, ladder, numeric_field, operators, preferences, regions

# Dependency order: later modules import the earlier ones.
_MODULES = (keymaps, preferences, ladder, numeric_field, regions, drawing, operators)


def _call_hook(module, name: str) -> None:
    """Call a module's register/unregister hook; pure modules have none."""
    hook = getattr(module, name, None)
    if hook is not None:
        hook()


def register():
    # "Reload Scripts" (F8) calls register again without re-importing the modules;
    # re-execute them in dependency order so every import binds to fresh code.
    for module in _MODULES:
        importlib.reload(module)
    for module in _MODULES:
        _call_hook(module, 'register')


def unregister():
    for module in reversed(_MODULES):
        _call_hook(module, 'unregister')
