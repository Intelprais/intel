"""Operators that build, preview, calibrate and tear down the retarget rig."""

from __future__ import annotations

from typing import List, Optional, Tuple

import bpy
from bpy.props import BoolProperty
from bpy.types import Operator
from mathutils import Matrix

from ..analysis import armature as analysis_mod
from ..core import mathx
from ..core import naming
from ..core import scene as scene_utils
from ..core.log import capture, get_logger
from ..profiles import source_tables
from ..retarget import rig as rig_mod
from ..retarget import sampling
from .common import fill_lines, settings_of

LOG = get_logger()


class SVMR_OT_build_rig(Operator):
    bl_idname = "svmr.build_rig"
    bl_label = "Build / Preview Retarget"
    bl_description = ("Build the temporary retarget rig. The result is live in the "
                      "viewport - scrub the timeline to preview it")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return (settings.source_armature is not None
                and settings.target_armature is not None
                and len(settings.mapping) > 0)

    def execute(self, context):
        settings = settings_of(context)
        result = rig_mod.build(context, settings)
        lines = list(result.notes) + [f"WARNING: {w}" for w in result.warnings]
        fill_lines(settings.analysis_text, lines)

        if result.driver_obj is None:
            for warning in result.warnings:
                self.report({'ERROR'}, warning)
            return {'CANCELLED'}
        for warning in result.warnings:
            self.report({'WARNING'}, warning)
        self.report({'INFO'}, f"Retarget rig built ({len(result.driven_bones)} bones driven).")
        return {'FINISHED'}


class SVMR_OT_clear_rig(Operator):
    bl_idname = "svmr.clear_rig"
    bl_label = "Clear Retarget Rig"
    bl_description = ("Remove every helper object and temporary constraint created by "
                      "this add-on")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = settings_of(context)
        stats = rig_mod.teardown(context, settings)
        self.report(
            {'INFO'},
            f"Removed {stats['constraints']} constraint(s) and {stats['objects']} helper(s).",
        )
        return {'FINISHED'}


class SVMR_OT_capture_retarget_pose(Operator):
    bl_idname = "svmr.capture_retarget_pose"
    bl_label = "Capture Retarget Pose"
    bl_description = ("Store the target rig's current pose as the retarget pose. Pose the "
                      "Unreal rig so it matches the source rig's rest pose, then press this")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return settings_of(context).target_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        target = settings.target_armature
        if settings.rig_built:
            self.report({'ERROR'},
                        "Clear the retarget rig first - its constraints are driving the pose.")
            return {'CANCELLED'}

        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = target.evaluated_get(depsgraph)
        captured = 0
        for item in settings.mapping:
            pose_bone = evaluated.pose.bones.get(item.target_bone)
            if pose_bone is None:
                continue
            world = evaluated.matrix_world @ pose_bone.matrix
            quat = mathx.orthonormalize(world).to_quaternion()
            item.calibration_quat = (quat.w, quat.x, quat.y, quat.z)
            item.has_calibration = True
            captured += 1
        settings.calibration_mode = 'MANUAL'
        self.report({'INFO'}, f"Captured the retarget pose for {captured} bone(s).")
        return {'FINISHED'}


class SVMR_OT_reset_retarget_pose(Operator):
    bl_idname = "svmr.reset_retarget_pose"
    bl_label = "Reset Retarget Pose"
    bl_description = "Drop the captured retarget pose and go back to automatic alignment"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = settings_of(context)
        for item in settings.mapping:
            item.has_calibration = False
            item.calibration_quat = (1.0, 0.0, 0.0, 0.0)
            item.manual_offset = (0.0, 0.0, 0.0)
        settings.calibration_mode = 'AUTO_ALIGN'
        self.report({'INFO'}, "Retarget pose reset to Auto Align.")
        return {'FINISHED'}


class SVMR_OT_calibrate(Operator):
    bl_idname = "svmr.calibrate"
    bl_label = "Calibrate"
    bl_description = ("Compute the global alignment, the unit scale and the per-bone "
                      "retarget offsets without building the rig")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return settings.source_armature is not None and settings.target_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        pairs = rig_mod.collect_pairs(settings)
        if not pairs:
            self.report({'ERROR'}, "No usable bone mapping.")
            return {'CANCELLED'}
        calibration = rig_mod.compute_calibration(context, settings, pairs)
        rig_mod.store_calibration(settings, calibration)
        fill_lines(settings.analysis_text, calibration.notes)
        self.report({'INFO'},
                    f"Calibrated {len(calibration.offsets)} bone(s); "
                    f"scale {calibration.scale:.4f}.")
        return {'FINISHED'}


class SVMR_OT_detect_weapon(Operator):
    bl_idname = "svmr.detect_weapon"
    bl_label = "Detect Weapon"
    bl_description = "Guess the weapon root and its moving parts on the source rig"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        return settings_of(context).source_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        analysis = analysis_mod.analyze(
            settings.source_armature, rig_mod.active_source_action(settings)
        )
        if not analysis.weapon_bones:
            settings.weapon_enabled = False
            settings.two_hand_ik = False
            settings.weapon_follow_mode = 'NONE'
            fill_lines(settings.analysis_text, [
                "No weapon bones found on the source rig.",
                "Two-Hand IK and the weapon anchor were switched off: without a "
                "weapon they would lock the hands together and break clips where "
                "the arms move independently.",
                "Set the weapon reference bone manually if this rig does carry one.",
            ])
            self.report({'WARNING'},
                        "No weapon bones found - retargeting both hands independently.")
            return {'CANCELLED'}

        settings.weapon_enabled = True
        settings.weapon_reference_bone = analysis.weapon_root or ""

        # Grip markers are bones on the *weapon*, and assigning one overrides the
        # source hand motion - so we only report the candidates and let the user
        # opt in, rather than silently changing the result (section 19).
        settings.primary_grip_bone = ""
        settings.secondary_grip_bone = ""
        grips = [
            bone for bone in analysis.weapon_bones
            if any(hint in naming.normalize(bone)
                   for hint in source_tables.WEAPON_GRIP_HINTS)
        ]

        parts = ", ".join(f"{k}={v}" for k, v in sorted(analysis.weapon_parts.items()))
        lines = [f"Weapon root: {analysis.weapon_root}",
                 f"Weapon bones: {len(analysis.weapon_bones)}"]
        if parts:
            lines.append(f"Parts: {parts}")
        if grips:
            lines.append(f"Grip candidates: {', '.join(grips)}")
            lines.append("Assign them under Weapon > Primary/Secondary Grip to pin the "
                         "hands to the weapon instead of following the source hands.")
        else:
            lines.append("No grip marker bones found - hands will follow the source "
                         "animation directly.")
        fill_lines(settings.analysis_text, lines)
        self.report({'INFO'}, f"Weapon reference set to '{settings.weapon_reference_bone}'.")
        return {'FINISHED'}


class SVMR_OT_toggle_helpers(Operator):
    bl_idname = "svmr.toggle_helpers"
    bl_label = "Toggle Debug Helpers"
    bl_description = "Show or hide the retarget helper objects in the viewport"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        collection = bpy.data.collections.get(scene_utils.HELPER_COLLECTION)
        if collection is None:
            self.report({'WARNING'}, "No helper collection in this scene.")
            return {'CANCELLED'}
        collection.hide_viewport = not collection.hide_viewport
        state = "hidden" if collection.hide_viewport else "visible"
        self.report({'INFO'}, f"Retarget helpers are now {state}.")
        return {'FINISHED'}


CLASSES = (
    SVMR_OT_build_rig,
    SVMR_OT_clear_rig,
    SVMR_OT_capture_retarget_pose,
    SVMR_OT_reset_retarget_pose,
    SVMR_OT_calibrate,
    SVMR_OT_detect_weapon,
    SVMR_OT_toggle_helpers,
)
