"""Validation operator and report export."""

from __future__ import annotations

import os

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..analysis import armature as analysis_mod
from ..retarget import rig as rig_mod
from ..validation import checks
from .common import fill_report, settings_of


def _anchor(settings):
    from ..core import scene as scene_utils
    from ..retarget.rig import ANCHOR_NAME
    obj = bpy.data.objects.get(ANCHOR_NAME)
    return obj if obj is not None and scene_utils.is_owned(obj) else None


class SVMR_OT_validate(Operator):
    bl_idname = "svmr.validate"
    bl_label = "Validate"
    bl_description = ("Sample both rigs and measure flips, drift, fidelity and the "
                      "things Unreal cares about")
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return settings.source_armature is not None and settings.target_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        analysis = analysis_mod.analyze(
            settings.source_armature, rig_mod.active_source_action(settings)
        )
        anim = settings.target_armature.animation_data
        report = checks.validate(
            context, settings, analysis,
            action=anim.action if anim else None,
            anchor=_anchor(settings),
        )
        fill_report(settings.report, report.sorted_entries())
        counts = report.counts()
        level = {'INFO'} if report.ok else {'WARNING'}
        self.report(
            level,
            f"{counts['ERROR']} error(s), {counts['WARNING']} warning(s), "
            f"{counts['INFO']} info.",
        )
        return {'FINISHED'}


class SVMR_OT_save_report(Operator):
    bl_idname = "svmr.save_report"
    bl_label = "Save Report"
    bl_description = "Write the validation report next to the blend file"

    filepath: StringProperty(subtype='FILE_PATH', default="//retarget_report.txt")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        settings = settings_of(context)
        lines = [f"[{i.level}] {i.category}: {i.message}" for i in settings.report]
        if not lines:
            self.report({'WARNING'}, "Run Validate first.")
            return {'CANCELLED'}
        path = bpy.path.abspath(self.filepath)
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("Source VM Retargeter - validation report\n\n")
                handle.write("\n".join(lines))
                handle.write("\n")
        except OSError as exc:
            self.report({'ERROR'}, f"Could not write report: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Report written to {path}")
        return {'FINISHED'}


CLASSES = (SVMR_OT_validate, SVMR_OT_save_report)
