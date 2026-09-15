"""Operator registration."""

from __future__ import annotations

import bpy

from . import (
    analysis_ops,
    bake_ops,
    export_ops,
    mapping_ops,
    rig_ops,
    validate_ops,
)

MODULES = (
    analysis_ops,
    mapping_ops,
    rig_ops,
    bake_ops,
    validate_ops,
    export_ops,
)


def register() -> None:
    for module in MODULES:
        for cls in module.CLASSES:
            bpy.utils.register_class(cls)


def unregister() -> None:
    for module in reversed(MODULES):
        for cls in reversed(module.CLASSES):
            bpy.utils.unregister_class(cls)
