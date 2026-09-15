"""Operators for building, editing and storing the bone mapping."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from ..analysis import armature as analysis_mod
from ..mapping import auto as auto_map
from ..mapping import presets as preset_mod
from ..profiles.base import ALL_KEYS
from ..retarget import rig as rig_mod
from .common import settings_of


class SVMR_OT_auto_map(Operator):
    bl_idname = "svmr.auto_map"
    bl_label = "Auto Detect Mapping"
    bl_description = ("Detect the source arm/finger chains and map them onto the "
                      "Unreal skeleton")
    bl_options = {'REGISTER', 'UNDO'}

    keep_manual: BoolProperty(
        name="Keep Manual Edits", default=True,
        description="Preserve rows you edited by hand",
    )

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return settings.source_armature is not None and settings.target_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        profile = rig_mod.resolve_profile(settings)
        analysis = analysis_mod.analyze(
            settings.source_armature, rig_mod.active_source_action(settings)
        )
        entries = auto_map.build_mapping(
            settings.source_armature, settings.target_armature, profile, analysis,
            settings.body_mode, settings.use_fingers,
        )

        manual = {}
        if self.keep_manual:
            manual = {
                item.key: (item.source_bone, item.influence, tuple(item.manual_offset))
                for item in settings.mapping
                if item.method == auto_map.METHOD_MANUAL
            }

        settings.mapping.clear()
        for entry in entries:
            item = settings.mapping.add()
            item.key = entry.key
            item.target_bone = entry.target_bone
            if entry.key in manual:
                item.source_bone, item.influence, item.manual_offset = manual[entry.key]
                item.method = auto_map.METHOD_MANUAL
                item.confidence = 1.0
            else:
                item.source_bone = entry.source_bone
                item.method = entry.method
                item.confidence = entry.confidence
            item.enabled = bool(item.source_bone)

        stats = auto_map.mapping_stats(entries)
        self.report(
            {'INFO'},
            f"Mapped {stats['mapped']}/{stats['total']} bones "
            f"({stats['unmapped']} unmapped, {stats['low_confidence']} low confidence).",
        )
        return {'FINISHED'}


class SVMR_OT_clear_mapping(Operator):
    bl_idname = "svmr.clear_mapping"
    bl_label = "Clear Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings_of(context).mapping.clear()
        return {'FINISHED'}


class SVMR_OT_add_mapping_row(Operator):
    bl_idname = "svmr.add_mapping_row"
    bl_label = "Add Row"
    bl_options = {'REGISTER', 'UNDO'}

    key: EnumProperty(
        name="Bone Key",
        items=[(k, k, "") for k in ALL_KEYS],
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        settings = settings_of(context)
        for item in settings.mapping:
            if item.key == self.key:
                self.report({'WARNING'}, f"'{self.key}' is already in the list.")
                return {'CANCELLED'}
        item = settings.mapping.add()
        item.key = self.key
        item.method = auto_map.METHOD_MANUAL
        item.confidence = 1.0
        profile = rig_mod.resolve_profile(settings)
        item.target_bone = profile.bone(self.key) or ""
        settings.mapping_index = len(settings.mapping) - 1
        return {'FINISHED'}


class SVMR_OT_remove_mapping_row(Operator):
    bl_idname = "svmr.remove_mapping_row"
    bl_label = "Remove Row"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return 0 <= settings.mapping_index < len(settings.mapping)

    def execute(self, context):
        settings = settings_of(context)
        settings.mapping.remove(settings.mapping_index)
        settings.mapping_index = max(0, settings.mapping_index - 1)
        return {'FINISHED'}


class SVMR_OT_mark_manual(Operator):
    bl_idname = "svmr.mark_manual"
    bl_label = "Mark As Manual"
    bl_description = "Protect this row from being overwritten by Auto Detect"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return 0 <= settings.mapping_index < len(settings.mapping)

    def execute(self, context):
        settings = settings_of(context)
        item = settings.mapping[settings.mapping_index]
        item.method = auto_map.METHOD_MANUAL
        item.confidence = 1.0
        return {'FINISHED'}


class SVMR_OT_save_preset(Operator):
    bl_idname = "svmr.save_preset"
    bl_label = "Save Preset"
    bl_description = "Store the current mapping and calibration as a reusable JSON preset"

    def execute(self, context):
        settings = settings_of(context)
        try:
            path = preset_mod.save(settings, settings.preset_name)
        except OSError as exc:
            self.report({'ERROR'}, f"Could not write preset: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Saved preset to {path}")
        return {'FINISHED'}


def _preset_items(self, context):
    names = preset_mod.list_presets()
    if not names:
        return [('', "No presets found", "")]
    return [(n, n, "") for n in names]


class SVMR_OT_load_preset(Operator):
    bl_idname = "svmr.load_preset"
    bl_label = "Load Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(name="Preset", items=_preset_items)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        settings = settings_of(context)
        if not self.preset:
            self.report({'WARNING'}, "No preset selected.")
            return {'CANCELLED'}
        try:
            data = preset_mod.load_data(self.preset)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, f"Could not read preset: {exc}")
            return {'CANCELLED'}
        stats = preset_mod.apply(settings, data, settings.source_armature,
                                 settings.target_armature)
        message = f"Loaded {stats['applied']} mapping(s)."
        if stats["missing_source"] or stats["missing_target"]:
            message += (f" {stats['missing_source']} source and "
                        f"{stats['missing_target']} target bone(s) are missing and were "
                        f"disabled.")
            self.report({'WARNING'}, message)
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}


class SVMR_OT_refresh_actions(Operator):
    bl_idname = "svmr.refresh_actions"
    bl_label = "Refresh Action List"
    bl_description = "List the Actions that animate the source armature"

    @classmethod
    def poll(cls, context) -> bool:
        return settings_of(context).source_armature is not None

    def execute(self, context):
        settings = settings_of(context)
        source = settings.source_armature
        source_bones = {b.name for b in source.data.bones}
        previous = {item.name: item.selected for item in settings.actions}
        settings.actions.clear()

        current = rig_mod.active_source_action(settings)
        for action in bpy.data.actions:
            if action.name.startswith("RTG_"):
                continue
            hits = 0
            for fcurve in action.fcurves:
                path = fcurve.data_path
                if path.startswith('pose.bones["'):
                    end = path.find('"]', 12)
                    if end != -1 and path[12:end] in source_bones:
                        hits += 1
                        break
            if not hits:
                continue
            item = settings.actions.add()
            item.name = action.name
            item.selected = previous.get(action.name, action is current)

        self.report({'INFO'}, f"Found {len(settings.actions)} source Action(s).")
        return {'FINISHED'}


class SVMR_OT_select_all_actions(Operator):
    bl_idname = "svmr.select_all_actions"
    bl_label = "Select All / None"
    bl_options = {'REGISTER', 'UNDO'}

    select: BoolProperty(default=True)

    def execute(self, context):
        for item in settings_of(context).actions:
            item.selected = self.select
        return {'FINISHED'}


CLASSES = (
    SVMR_OT_auto_map,
    SVMR_OT_clear_mapping,
    SVMR_OT_add_mapping_row,
    SVMR_OT_remove_mapping_row,
    SVMR_OT_mark_manual,
    SVMR_OT_save_preset,
    SVMR_OT_load_preset,
    SVMR_OT_refresh_actions,
    SVMR_OT_select_all_actions,
)
