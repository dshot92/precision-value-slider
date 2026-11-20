# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the middle-mouse slide starts: the editor keymaps.

The set of keymaps is discovered from the active key configuration at registration
time instead of being hard-coded: every non-modal keymap that binds the middle mouse
button, plus the global ``Window`` keymap as a fallback for editors that do not.
The operator then decides at runtime whether the pointer is over a numeric field;
anywhere else it returns ``PASS_THROUGH`` and native panning continues.

Popovers and menus are not covered: Blender gives their temporary regions a blocking
UI handler that consumes mouse events before keymaps run.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

import bpy

#: The event that starts a slide, as (type, value).
TRIGGER = ('MIDDLEMOUSE', 'PRESS')

#: Global fallback for editors whose own keymap does not bind the middle mouse.
FALLBACK_KEYMAP = 'Window'

#: Only used when the key configuration cannot be inspected (e.g. background mode).
STATIC_KEYMAPS = (
    'View2D',
    'View2D Buttons List',
    FALLBACK_KEYMAP,
)


@dataclass(frozen=True)
class SlideKeymap:
    """One keymap that receives the slide operator.

    ``space_type`` and ``region_type`` are the keymap's own identity, not the region
    the pointer is in: Blender's default keymaps use ``EMPTY``/``WINDOW`` for every
    editor, and an add-on keymap only merges when those match.
    """

    name: str
    space_type: str = 'EMPTY'
    region_type: str = 'WINDOW'


def _addon_keyconfig() -> Optional[bpy.types.KeyConfig]:
    """The add-on key configuration, or None in background mode."""
    return bpy.context.window_manager.keyconfigs.addon


def _source_keyconfig() -> Optional[bpy.types.KeyConfig]:
    """The key configuration whose keymaps describe the current editors."""
    keyconfigs = bpy.context.window_manager.keyconfigs
    for name in ('user', 'default'):
        keyconfig = getattr(keyconfigs, name, None)
        if keyconfig is not None and len(keyconfig.keymaps):
            return keyconfig
    return None


def _uses_middle_mouse(keymap) -> bool:
    return any(item.type == TRIGGER[0] for item in keymap.keymap_items)


def _keymaps_to_bind() -> Iterable[SlideKeymap]:
    """Every editor keymap that should receive the operator.

    Modal keymaps are skipped: they belong to a running operator and must keep their
    own middle-mouse handling. The fallback keymap is always included so editors
    without a middle-mouse binding still reach the operator.
    """
    keyconfig = _source_keyconfig()
    if keyconfig is None:
        return (SlideKeymap(name) for name in STATIC_KEYMAPS)

    found = {}
    for keymap in keyconfig.keymaps:
        if getattr(keymap, 'is_modal', False):
            continue
        if keymap.name == FALLBACK_KEYMAP or _uses_middle_mouse(keymap):
            spec = SlideKeymap(keymap.name, keymap.space_type, keymap.region_type)
            found[(spec.name, spec.space_type, spec.region_type)] = spec
    # Editors without a middle-mouse binding fall through to this global keymap.
    found.setdefault((FALLBACK_KEYMAP, 'EMPTY', 'WINDOW'), SlideKeymap(FALLBACK_KEYMAP))
    return found.values()


def _ensure_keymap(keyconfig, spec: SlideKeymap):
    """The add-on keymap for ``spec``, created once and reused afterwards."""
    for keymap in keyconfig.keymaps:
        if (keymap.name, keymap.space_type, keymap.region_type) == (
                spec.name, spec.space_type, spec.region_type):
            return keymap
    return keyconfig.keymaps.new(
        name=spec.name, space_type=spec.space_type, region_type=spec.region_type)


def _remove_items(keymap, operator_idname: str) -> None:
    for item in list(keymap.keymap_items):
        if item.idname == operator_idname:
            keymap.keymap_items.remove(item)


def bind(operator_idname: str) -> None:
    """Bind ``operator_idname`` at the head of every editor keymap; no-op without one."""
    keyconfig = _addon_keyconfig()
    if keyconfig is None:
        return
    event, value = TRIGGER
    for spec in _keymaps_to_bind():
        keymap = _ensure_keymap(keyconfig, spec)
        _remove_items(keymap, operator_idname)
        keymap.keymap_items.new(operator_idname, event, value, head=True)


def unbind(operator_idname: str) -> None:
    """Delete every item bound to ``operator_idname``, including older leftovers."""
    keyconfig = _addon_keyconfig()
    if keyconfig is None:
        return
    for keymap in keyconfig.keymaps:
        _remove_items(keymap, operator_idname)
