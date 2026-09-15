"""FBX export tuned for Unreal Engine animation import.

The safest pipeline - and the one this add-on uses - is to export the
*original* Unreal armature with a freshly baked Action on it.  No bones are
added, renamed or reparented, so the clip imports straight onto the existing
Skeleton asset in UE.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import bpy

from ..core import scene as scene_utils
from ..core.log import get_logger
from ..profiles.base import TargetProfile

LOG = get_logger()

#: Export settings that make Blender's FBX match what Unreal expects.
UNREAL_FBX_PRESET: Dict[str, object] = {
    "use_selection": True,
    "use_visible": False,
    "use_active_collection": False,
    "object_types": {'ARMATURE', 'MESH'},
    "use_mesh_modifiers": True,
    "mesh_smooth_type": 'FACE',
    "use_armature_deform_only": False,
    "add_leaf_bones": False,
    "primary_bone_axis": 'Y',
    "secondary_bone_axis": 'X',
    "armature_nodetype": 'NULL',
    "bake_anim": True,
    "bake_anim_use_all_bones": True,
    "bake_anim_use_nla_strips": False,
    "bake_anim_use_all_actions": False,
    "bake_anim_force_startend_keying": True,
    "bake_anim_step": 1.0,
    "bake_anim_simplify_factor": 0.0,
    "apply_scale_options": 'FBX_SCALE_NONE',
    "global_scale": 1.0,
    "apply_unit_scale": True,
    "axis_forward": '-Z',
    "axis_up": 'Y',
    "path_mode": 'AUTO',
    "use_triangles": False,
}


@dataclass
class PreflightResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    infos: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_lines(self) -> List[str]:
        return (
            [f"ERROR: {e}" for e in self.errors]
            + [f"WARNING: {w}" for w in self.warnings]
            + [f"INFO: {i}" for i in self.infos]
        )


def preflight(context, settings, profile: TargetProfile) -> PreflightResult:
    """Check that the scene is in a state Unreal will accept."""
    result = PreflightResult()
    target_obj = settings.target_armature

    if target_obj is None:
        result.errors.append("No target armature selected.")
        return result

    leftovers = [
        f"{pb.name}.{c.name}"
        for pb in target_obj.pose.bones
        for c in pb.constraints
        if c.name.startswith(scene_utils.CONSTRAINT_PREFIX)
    ]
    if leftovers:
        result.errors.append(
            f"{len(leftovers)} temporary retarget constraint(s) still on the target rig "
            f"(e.g. {leftovers[0]}). Bake first, or run Clear Retarget Rig - exporting now "
            f"would produce an animation that depends on the source rig."
        )

    anim = target_obj.animation_data
    action = anim.action if anim else None
    if action is None:
        result.errors.append(
            "The target armature has no active Action. Bake the retarget first."
        )
    else:
        result.infos.append(f"Exporting Action '{action.name}'.")

    if target_obj.parent is not None:
        result.warnings.append(
            f"Target armature is parented to '{target_obj.parent.name}'; Unreal imports "
            f"cleanest when the armature sits at the scene root."
        )

    helper_names = [o.name for o in scene_utils.owned_objects()]
    if helper_names:
        result.infos.append(
            f"{len(helper_names)} helper object(s) exist and will be excluded from the export."
        )

    missing = [b for b in (profile.root_bone,) if b not in target_obj.data.bones]
    if missing:
        result.warnings.append(
            f"Expected Unreal bone(s) missing from the target: {', '.join(missing)}."
        )

    extra_deform = [
        b.name for b in target_obj.data.bones
        if b.use_deform and b.name.startswith(("src_", "drv_", "proc_", "VM_RTG"))
    ]
    if extra_deform:
        result.errors.append(
            f"Helper bones ended up inside the target skeleton: {', '.join(extra_deform[:5])}."
        )

    scale = target_obj.matrix_world.to_scale()
    if max(abs(s - 1.0) for s in scale) > 1.0e-4:
        result.warnings.append(
            f"Target armature object scale is ({scale.x:.4f}, {scale.y:.4f}, {scale.z:.4f}); "
            f"Unreal will bake this into the clip."
        )

    if settings.use_bake_fps:
        result.infos.append(f"Scene FPS will be set to {settings.bake_fps} for the export.")
    return result


def _selection_for_export(context, settings) -> List[bpy.types.Object]:
    """Target armature plus, optionally, the meshes skinned to it."""
    target_obj = settings.target_armature
    objects = [target_obj]
    if settings.export_include_mesh:
        for obj in context.scene.objects:
            if obj.type != 'MESH' or scene_utils.is_owned(obj):
                continue
            for modifier in obj.modifiers:
                if modifier.type == 'ARMATURE' and modifier.object is target_obj:
                    objects.append(obj)
                    break
    return objects


def export_fbx(context, settings, filepath: str,
               profile: Optional[TargetProfile] = None) -> str:
    """Export the target rig + its Action as an Unreal-ready FBX."""
    target_obj = settings.target_armature
    if target_obj is None:
        raise ValueError("No target armature selected.")

    directory = os.path.dirname(bpy.path.abspath(filepath))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)

    previous_selection = [o for o in context.scene.objects if o.select_get()]
    previous_active = context.view_layer.objects.active
    previous_fps = context.scene.render.fps
    previous_mode = target_obj.mode

    try:
        if target_obj.mode != 'OBJECT':
            context.view_layer.objects.active = target_obj
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in context.scene.objects:
            obj.select_set(False)
        exported = _selection_for_export(context, settings)
        for obj in exported:
            obj.hide_set(False)
            obj.select_set(True)
        context.view_layer.objects.active = target_obj

        if settings.use_bake_fps:
            context.scene.render.fps = int(settings.bake_fps)
            context.scene.render.fps_base = 1.0

        options = dict(UNREAL_FBX_PRESET)
        if not settings.export_include_mesh:
            options["object_types"] = {'ARMATURE'}
        start, end = int(settings.export_frame_start), int(settings.export_frame_end)
        previous_range = (context.scene.frame_start, context.scene.frame_end)
        context.scene.frame_start, context.scene.frame_end = start, end
        try:
            bpy.ops.export_scene.fbx(filepath=bpy.path.abspath(filepath), **options)
        finally:
            context.scene.frame_start, context.scene.frame_end = previous_range
        LOG.info("Exported '%s'.", filepath)
        return filepath
    finally:
        context.scene.render.fps = previous_fps
        for obj in context.scene.objects:
            obj.select_set(False)
        for obj in previous_selection:
            obj.select_set(True)
        if previous_active is not None:
            context.view_layer.objects.active = previous_active
        if previous_mode != 'OBJECT' and target_obj.name in context.view_layer.objects:
            try:
                context.view_layer.objects.active = target_obj
                bpy.ops.object.mode_set(mode=previous_mode)
            except RuntimeError:
                pass
