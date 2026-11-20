# SPDX-License-Identifier: GPL-3.0-or-later

"""The hovered numeric field, and the ways to read and write it."""

import math
import re
import struct
from decimal import Decimal

import bpy


def _parse_property_tuple(prop_tuple) -> tuple:
    """Validate ``bpy.context.property`` as (owner, data path, index)."""
    if not isinstance(prop_tuple, tuple) or len(prop_tuple) != 3:
        raise ValueError('Not a numeric field')
    owner, path, index = prop_tuple
    if not isinstance(owner, bpy.types.bpy_struct) or not isinstance(path, str) or not path:
        raise ValueError('Not a numeric field')
    return owner, path, index


def _split_data_path(path: str) -> tuple:
    """Split a data path into (parent path, property name, is_custom_property).

    Blender has no generic set-by-data-path call, so only the parent struct and the
    last component are needed::

        'location'                          RNA property of the owner itself
        'modifiers["Bevel"].width'          RNA property of a nested struct
        '["My Value"]'                      custom property of the owner itself
        'modifiers["Nodes"]["Socket_2"]'    custom property of a nested struct

    Custom-property paths end in ``["name"]``, with quotes escaped as ``\\"``, so the
    last ``["`` is always the real separator.
    """
    if path.endswith('"]'):
        start = path.rfind('["')
        if start != -1:
            name = bpy.utils.unescape_identifier(path[start + 2:-2])
            return path[:start], name, True
    parent, _, name = path.rpartition('.')
    return parent, name, False


class NumericField:
    """A hovered numeric property: resolved once at invoke, written while sliding.

    Handles RNA properties and ID custom properties (Geometry Nodes inputs), scalars
    and single vector components, ints and floats. Raises ``ValueError`` for anything
    else so the caller can pass the middle mouse button through.
    """

    def __init__(self, context, prop_tuple):
        owner, path, index = _parse_property_tuple(prop_tuple)

        parent_path, self.name, self.is_custom = _split_data_path(path)
        self.pointer = owner if not parent_path else owner.path_resolve(parent_path)
        if not isinstance(self.pointer, bpy.types.bpy_struct):
            raise ValueError('Not a numeric field')

        self.rotation = False   # shown in degrees but stored in radians
        self.radians = False    # shown in radians
        self.unit = 'NONE'
        if self.is_custom:
            self._describe_custom(index)
        else:
            self._describe_rna(context, index)

        self.system = context.scene.unit_settings.system
        if self.array and index < 0:
            raise ValueError('Hover a single component')  # whole-vector widgets (colour swatches ...)
        self.index = max(0, index)

    def _describe_custom(self, index: int) -> None:
        """Read the metadata Blender keeps for an IDProperty (it has no RNA description)."""
        value = self.pointer[self.name]
        self.array = hasattr(value, 'typecode')  # IDPropertyArray exposes a typecode
        sample = value[max(0, index)] if self.array else value
        if isinstance(sample, bool) or not isinstance(sample, (int, float)):
            raise ValueError('Not a numeric field')
        self.is_int = isinstance(sample, int)
        self.precision = self._custom_precision()

    def _describe_rna(self, context, index: int) -> None:
        """Take type, precision and units from the property's own RNA description."""
        prop = self.pointer.bl_rna.properties.get(self.name)
        if prop is None or prop.type not in {'FLOAT', 'INT'}:
            raise ValueError('Not a numeric field')
        if prop.is_readonly:
            raise ValueError('The field is read-only')
        self.array = prop.is_array
        self.is_int = prop.type == 'INT'
        precision = getattr(prop, 'precision', 3)
        self.precision = 0 if self.is_int else (3 if precision is None else int(precision))
        self.unit = getattr(prop, 'unit', 'NONE') or 'NONE'
        rotation = prop.type == 'FLOAT' and prop.unit == 'ROTATION'
        mode = context.scene.unit_settings.system_rotation
        self.rotation = rotation and mode == 'DEGREES'
        self.radians = rotation and mode == 'RADIANS'

    def _custom_precision(self) -> int:
        """Decimals Blender uses for this custom property (its own field precision)."""
        if self.is_int:
            return 0
        try:
            return int(self.pointer.id_properties_ui(self.name).as_dict().get('precision', 3))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return 3  # custom properties on non-ID structs (node sockets) keep the default

    # ---- storage ----------------------------------------------------------------

    def _stored_value(self):
        """The stored value: an IDProperty for custom properties, an RNA attribute otherwise."""
        return self.pointer[self.name] if self.is_custom else getattr(self.pointer, self.name)

    def _store_value(self, value) -> None:
        if self.is_custom:
            self.pointer[self.name] = value
        else:
            setattr(self.pointer, self.name, value)

    def _tag_owner(self) -> None:
        """Flag the owning ID for undo and a depsgraph update."""
        owner = self.pointer.id_data
        if owner is not None:
            owner.update_tag()

    # ---- reading ----------------------------------------------------------------

    def raw(self) -> float:
        """Stored value (radians for rotations)."""
        value = self._stored_value()
        return value[self.index] if self.array else value

    def display(self) -> float:
        """Value as the user sees it (degrees for rotations)."""
        value = self.raw()
        return math.degrees(value) if self.rotation else value

    def text(self, decimals: int = 0) -> str:
        """Value text for the ladder: the value's own digits, padded to the selected step.

        ``decimals`` is how many decimals the selected step has (``.01`` -> 2). Every
        significant decimal of the value is always shown (``1.0001`` stays ``1.0001``);
        trailing zeros are only added up to the step (``1.1`` at step 1, ``1.10`` at
        ``.01``, ``1.1000`` at ``.0001``). Unit fields use Blender's own formatter.
        """
        value = self.raw() + 0.0  # Blender turns -0.0 into 0.0 before displaying
        if self.is_int:
            return str(int(value))
        if not math.isfinite(value):
            return str(value)
        wanted = max(decimals, _value_decimals(value))
        if self.unit != 'NONE':
            start = max(0, min(6, self.precision))
            text = self._unit_text(value, start)
            # Ask Blender's formatter for more digits while it still hides some of them.
            for precision in range(start + 1, 7):
                if _decimals(text) >= wanted:
                    break
                text = self._unit_text(value, precision)
            return text
        return f'{value:.{max(0, min(9, wanted))}f}'

    def _unit_text(self, value: float, precision: int) -> str:
        """Blender's unit text for ``value`` at ``precision``, in the field's display units."""
        if self.radians:  # degrees/radians is a scene setting bpy.utils.units does not take
            digits = max(0, min(6, precision - _integer_digits(value)))
            text = f'{value:.{digits}f}'.rstrip('0').rstrip('.')
            return f'{text} rad'
        return bpy.utils.units.to_string(
            self.system, self.unit, value, precision=precision).strip()

    def raw_values(self) -> list:
        """Stored values of every component (a one-item list for a scalar)."""
        value = self._stored_value()
        return list(value) if self.array else [value]

    # ---- writing ----------------------------------------------------------------

    def _set_storage(self, value) -> None:
        """Store ``value`` into the field: an array component or the scalar itself."""
        if self.array:
            self._stored_value()[self.index] = value
        else:
            self._store_value(value)
        self._tag_owner()

    def write(self, display_value: float) -> None:
        """Store a value given in display units (degrees for rotations)."""
        value = math.radians(display_value) if self.rotation else display_value
        if self.is_int:
            value = int(round(value))
        self._set_storage(value)

    def write_raw(self, raw_value: float) -> None:
        """Store an exact stored value (no unit conversion); used to restore or re-apply."""
        self._set_storage(raw_value)

    def write_raw_values(self, values) -> None:
        """Store exact stored values for every component, one by one.

        Components are assigned individually (not by replacing the whole property) so
        custom properties keep their UI settings such as min / max.
        """
        if not self.array:
            self.write_raw(values[0])
            return
        array = self._stored_value()
        for i, value in enumerate(values):
            array[i] = int(round(value)) if self.is_int else value
        self._tag_owner()

    def autokey(self, context, index: int) -> None:
        """Mirror native behaviour: with Auto Keying on, only update animated channels.

        Driven channels are skipped. Failures are ignored on purpose: the value is
        already written and auto-keying is a convenience.
        """
        try:
            owner = self.pointer.id_data
            animation = getattr(owner, 'animation_data', None)
            if (not context.scene.tool_settings.use_keyframe_insert_auto
                    or animation is None or animation.action is None):
                return
            path = self.pointer.path_from_id(
                '["' + bpy.utils.escape_identifier(self.name) + '"]' if self.is_custom else self.name)
            if not self.array:
                index = -1  # a scalar has a single channel
            animated = any(f.data_path == path and (index < 0 or f.array_index == index)
                           for f in animation.action.fcurves)
            if animated and not any(d.data_path == path for d in animation.drivers):
                owner.keyframe_insert(path, index=index)
        except (AttributeError, RuntimeError, TypeError, ValueError, ReferenceError):
            pass


# ---- number formatting helpers ---------------------------------------------------


def _integer_digits(value: float) -> int:
    """Digits before the decimal point, counted the way Blender's unit formatter counts."""
    value = abs(value)
    return 0 if value < 1e-9 else math.floor(math.log10(value)) + 1


def _decimals(text: str) -> int:
    """Decimals in a formatted number ("84.079 m" -> 3, "45°" -> 0)."""
    match = re.search(r'\.(\d+)', text)
    return len(match[1]) if match else 0


def _value_decimals(value: float) -> int:
    """Decimals the value really needs: 1.0001 -> 4, 1.1000 -> 1, 84.0795 -> 4.

    Blender stores floats as 32 bit, so the shortest text that round-trips through
    ``float32`` is the value the user sees; reading the float64 back would expose
    noise (84.07949829...).
    """
    try:
        bits = struct.pack('<f', value)
    except (OverflowError, struct.error):
        return 0
    text = f'{value:.9g}'
    for digits in range(1, 10):
        candidate = f'{value:.{digits}g}'
        if struct.pack('<f', float(candidate)) == bits:
            text = candidate
            break
    return max(0, -Decimal(text).as_tuple().exponent)
