"""Operators for inspecting the selected armatures."""

from __future__ import annotations

import bpy
from bpy.types import Operator

from ..analysis import armature as analysis_mod
from ..profiles import targets as target_profiles
from ..retarget import rig as rig_mod
from .common import fill_lines, settings_of


class SVMR_OT_analyze(Operator):
    bl_idname = "svmr.analyze"
    bl_label = "Analyse Rigs"
    bl_description = "Inspect the source and target armatures without changing them"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context) -> bool:
        return settings_of(context).source_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        source = settings.source_armature
        lines = []

        analysis = analysis_mod.analyze(source, rig_mod.active_source_action(settings))
        lines.extend(analysis.summary_lines())

        target = settings.target_armature
        if target is not None:
            scores = target_profiles.detect_profile_scores(target)
            detected = target_profiles.detect_profile(target)
            settings.detected_profile = detected.identifier if detected else ""
            if detected:
                lines.append(f"{target.name}: detected {detected.label} "
                             f"({scores.get(detected.identifier, 0.0) * 100:.0f}% match)")
            else:
                best = max(scores.items(), key=lambda kv: kv[1]) if scores else ("", 0.0)
                lines.append(f"{target.name}: not recognised as a stock Unreal skeleton "
                             f"(best guess {best[0] or 'none'} at {best[1] * 100:.0f}%). "
                             f"Pick a profile manually.")
            missing = [
                name for name in rig_mod.resolve_profile(settings).ik_bones.values()
                if name not in target.data.bones
            ]
            if missing:
                lines.append(f"  Missing Unreal IK bones: {', '.join(sorted(missing))}")
        else:
            lines.append("No target armature selected yet.")

        fill_lines(settings.analysis_text, lines)
        self.report({'INFO'}, f"Analysis complete: {len(lines)} line(s).")
        return {'FINISHED'}


class SVMR_OT_use_detected_profile(Operator):
    bl_idname = "svmr.use_detected_profile"
    bl_label = "Use Detected Profile"
    bl_description = "Set the target profile to the automatically detected skeleton"

    @classmethod
    def poll(cls, context) -> bool:
        return bool(settings_of(context).detected_profile)

    def execute(self, context):
        settings = settings_of(context)
        settings.target_profile = settings.detected_profile
        self.report({'INFO'}, f"Target profile set to {settings.target_profile}.")
        return {'FINISHED'}


CLASSES = (SVMR_OT_analyze, SVMR_OT_use_detected_profile)
