"""Add-on preferences."""

from __future__ import annotations

import logging

import bpy
from bpy.props import EnumProperty, FloatProperty
from bpy.types import AddonPreferences

from .core import log as log_mod

_LEVELS = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
}


def _update_level(self, context) -> None:
    log_mod.configure(_LEVELS.get(self.log_level, logging.INFO))


class SVMR_Preferences(AddonPreferences):
    bl_idname = __package__

    log_level: EnumProperty(
        name="Log Level",
        items=[(k, k.title(), "") for k in ("DEBUG", "INFO", "WARNING", "ERROR")],
        default='INFO',
        update=_update_level,
        description="Verbosity of the messages printed to Blender's console",
    )
    heuristic_threshold: FloatProperty(
        name="Heuristic Threshold", default=0.62, min=0.0, max=1.0,
        description="Minimum name similarity for automatic bone matching",
    )

    def draw(self, context):
        layout = self.layout
        column = layout.column()
        column.prop(self, "log_level")
        column.prop(self, "heuristic_threshold")
        column.label(
            text="Helper objects live in the VM_RETARGET_HELPERS collection.",
            icon='INFO',
        )


def get(context):
    """Add-on preferences, or ``None`` when the add-on is used as a plain module."""
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


CLASSES = (SVMR_Preferences,)
