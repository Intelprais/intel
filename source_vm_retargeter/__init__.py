"""Source VM Retargeter - Source-engine viewmodel animation to Unreal Mannequin.

Transfers first-person weapon/hand animations from Source-engine viewmodels
onto the UE4 Mannequin or UE5 Manny/Quinn skeleton using transform-based
retargeting (never raw Euler copying), a two-hand weapon IK mode and an
Unreal-ready FBX export.

Works as a legacy add-on (Blender 4.0/4.1) and as an Extension (4.2+).
"""

from __future__ import annotations

bl_info = {
    "name": "Source VM Retargeter",
    "author": "Source VM Retargeter contributors",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Source VM Retarget",
    "description": "Retarget Source-engine viewmodel weapon/hand animation to the "
                   "UE4 Mannequin or UE5 Manny skeleton",
    "category": "Animation",
    "doc_url": "https://github.com/intelprais/intel",
    "tracker_url": "https://github.com/intelprais/intel/issues",
}

from . import ops, prefs, props, ui  # noqa: E402
from .core import log as log_mod  # noqa: E402

import bpy  # noqa: E402


def register() -> None:
    log_mod.configure()
    for cls in prefs.CLASSES:
        bpy.utils.register_class(cls)
    props.register()
    ops.register()
    ui.register()
    log_mod.get_logger().info("Source VM Retargeter registered.")


def unregister() -> None:
    ui.unregister()
    ops.unregister()
    props.unregister()
    for cls in reversed(prefs.CLASSES):
        bpy.utils.unregister_class(cls)
    log_mod.teardown()


if __name__ == "__main__":
    register()
