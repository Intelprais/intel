"""Unreal FBX export operators."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..exporting import unreal
from ..retarget import rig as rig_mod
from .common import fill_lines, settings_of


class SVMR_OT_export_preflight(Operator):
    bl_idname = "svmr.export_preflight"
    bl_label = "Check Export"
    bl_description = "Verify the scene is ready for an Unreal animation import"

    @classmethod
    def poll(cls, context) -> bool:
        return settings_of(context).target_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        profile = rig_mod.resolve_profile(settings)
        result = unreal.preflight(context, settings, profile)
        fill_lines(settings.analysis_text, result.as_lines())
        if result.errors:
            self.report({'ERROR'}, result.errors[0])
            return {'CANCELLED'}
        for warning in result.warnings:
            self.report({'WARNING'}, warning)
        self.report({'INFO'}, "Export pre-flight passed.")
        return {'FINISHED'}


class SVMR_OT_export_fbx(Operator):
    bl_idname = "svmr.export_fbx"
    bl_label = "Export FBX For Unreal"
    bl_description = ("Export the Unreal armature and its baked Action using settings "
                      "that import cleanly into UE")

    @classmethod
    def poll(cls, context) -> bool:
        return settings_of(context).target_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        profile = rig_mod.resolve_profile(settings)
        result = unreal.preflight(context, settings, profile)
        fill_lines(settings.analysis_text, result.as_lines())
        if not result.ok:
            for error in result.errors:
                self.report({'ERROR'}, error)
            return {'CANCELLED'}
        try:
            path = unreal.export_fbx(context, settings, settings.export_path, profile)
        except (RuntimeError, ValueError, OSError) as exc:
            self.report({'ERROR'}, f"Export failed: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Exported {path}")
        return {'FINISHED'}


CLASSES = (SVMR_OT_export_preflight, SVMR_OT_export_fbx)
