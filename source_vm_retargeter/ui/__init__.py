"""User interface registration."""

from __future__ import annotations

import bpy

from . import lists, panels


def register() -> None:
    for cls in lists.CLASSES:
        bpy.utils.register_class(cls)
    for cls in panels.CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(panels.CLASSES):
        bpy.utils.unregister_class(cls)
    for cls in reversed(lists.CLASSES):
        bpy.utils.unregister_class(cls)
