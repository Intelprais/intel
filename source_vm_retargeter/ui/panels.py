"""N-panel UI, laid out in the order of the intended workflow."""

from __future__ import annotations

import bpy
from bpy.types import Panel

from ..mapping import auto as auto_map
from ..retarget import rig as rig_mod

CATEGORY = "Source VM Retarget"
SPACE = 'VIEW_3D'
REGION = 'UI'


class _Base:
    bl_space_type = SPACE
    bl_region_type = REGION
    bl_category = CATEGORY

    @staticmethod
    def settings(context):
        return context.scene.svmr


class SVMR_PT_main(_Base, Panel):
    bl_idname = "SVMR_PT_main"
    bl_label = "Source VM Retargeter"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        column = layout.column(align=True)
        column.prop(settings, "source_armature", icon='OUTLINER_OB_ARMATURE')
        column.prop(settings, "target_armature", icon='OUTLINER_OB_ARMATURE')
        layout.prop(settings, "target_profile")

        row = layout.row(align=True)
        row.operator("svmr.analyze", icon='VIEWZOOM')
        if settings.detected_profile and settings.target_profile == 'AUTO':
            row.operator("svmr.use_detected_profile", text="", icon='CHECKMARK')

        if settings.rig_built:
            box = layout.box()
            box.label(text="Retarget rig is live - scrub to preview", icon='CONSTRAINT_BONE')
            row = box.row(align=True)
            row.operator("svmr.toggle_helpers", icon='HIDE_OFF')
            row.operator("svmr.clear_rig", icon='TRASH')


class SVMR_PT_mapping(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Bone Mapping"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        row = layout.row(align=True)
        row.prop(settings, "body_mode", text="")
        row.prop(settings, "use_fingers", text="", icon='HAND')

        row = layout.row(align=True)
        row.operator("svmr.auto_map", icon='AUTO')
        row.operator("svmr.clear_mapping", text="", icon='X')

        row = layout.row(align=True)
        row.prop(settings, "mapping_filter", text="")
        row.prop(settings, "hide_unmapped", text="", icon='FILTER')

        row = layout.row()
        row.template_list("SVMR_UL_mapping", "", settings, "mapping",
                          settings, "mapping_index", rows=8)
        column = row.column(align=True)
        column.operator("svmr.add_mapping_row", text="", icon='ADD')
        column.operator("svmr.remove_mapping_row", text="", icon='REMOVE')
        column.separator()
        column.operator("svmr.mark_manual", text="", icon='GREASEPENCIL')

        if 0 <= settings.mapping_index < len(settings.mapping):
            item = settings.mapping[settings.mapping_index]
            box = layout.box()
            box.label(text=f"{item.key}  ({item.method or 'unset'}, "
                           f"{item.confidence * 100:.0f}%)")
            if settings.target_armature is not None:
                box.prop_search(item, "target_bone", settings.target_armature.data,
                                "bones", icon='BONE_DATA')
            box.prop(item, "influence")
            box.prop(item, "manual_offset")

        row = layout.row(align=True)
        row.prop(settings, "preset_name", text="")
        row.operator("svmr.save_preset", text="", icon='FILE_TICK')
        row.operator("svmr.load_preset", text="", icon='FILE_FOLDER')


class SVMR_PT_calibration(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Retarget Pose"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        layout.prop(settings, "calibration_mode", text="")
        row = layout.row(align=True)
        row.prop(settings, "calibration_pose_source", text="")
        if settings.calibration_pose_source == 'FRAME':
            row.prop(settings, "calibration_frame", text="")

        column = layout.column(align=True)
        column.prop(settings, "global_align_mode")
        if settings.global_align_mode == 'MANUAL':
            column.prop(settings, "global_align_euler", text="")
        column.prop(settings, "scale_mode")
        sub = column.row()
        sub.enabled = settings.scale_mode == 'MANUAL'
        sub.prop(settings, "scale_factor")

        layout.operator("svmr.calibrate", icon='DRIVER')
        row = layout.row(align=True)
        row.operator("svmr.capture_retarget_pose", icon='ARMATURE_DATA')
        row.operator("svmr.reset_retarget_pose", text="", icon='LOOP_BACK')


class SVMR_PT_weapon(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Weapon"

    def draw_header(self, context):
        self.layout.prop(self.settings(context), "weapon_enabled", text="")

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)
        layout.active = settings.weapon_enabled

        layout.prop(settings, "primary_hand", expand=True)
        layout.operator("svmr.detect_weapon", icon='VIEWZOOM')
        if settings.source_armature is not None:
            layout.prop_search(settings, "weapon_reference_bone",
                               settings.source_armature.data, "bones",
                               text="Reference", icon='BONE_DATA')
            grips = layout.column(align=True)
            grips.label(text="Grip markers on the weapon (optional):")
            grips.prop_search(settings, "primary_grip_bone",
                              settings.source_armature.data, "bones",
                              text="Primary")
            grips.prop_search(settings, "secondary_grip_bone",
                              settings.source_armature.data, "bones",
                              text="Secondary")
        layout.prop(settings, "weapon_follow_mode", text="Follows")

        box = layout.box()
        box.prop(settings, "drive_ue_ik_bones")
        sub = box.row()
        sub.enabled = settings.drive_ue_ik_bones
        sub.prop(settings, "ik_gun_hand", expand=True)


class SVMR_PT_ik(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Two-Hand IK"

    def draw_header(self, context):
        self.layout.prop(self.settings(context), "two_hand_ik", text="")

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)
        layout.active = settings.two_hand_ik
        layout.prop(settings, "ik_blend")
        layout.prop(settings, "use_elbow_pole")
        layout.label(
            text=f"Secondary hand: {'LEFT' if settings.primary_hand == 'R' else 'RIGHT'}",
            icon='CON_KINEMATIC',
        )


class SVMR_PT_procedural(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Procedural Upper Body"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)
        layout.active = settings.body_mode != 'ARMS_ONLY'
        if settings.body_mode == 'ARMS_ONLY':
            layout.label(text="Set Body Mode above to enable", icon='INFO')
        column = layout.column(align=True)
        column.prop(settings, "shoulder_influence")
        column.prop(settings, "clavicle_influence")
        column.prop(settings, "chest_influence")
        column.prop(settings, "spine_influence")
        layout.prop(settings, "max_spine_rotation")

        box = layout.box()
        box.label(text="Root Motion", icon='ORIENTATION_GLOBAL')
        box.prop(settings, "root_motion", text="")
        if settings.root_motion == 'CUSTOM' and settings.source_armature is not None:
            box.prop_search(settings, "root_motion_bone",
                            settings.source_armature.data, "bones", text="")


class SVMR_PT_actions(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Actions"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        row = layout.row(align=True)
        row.operator("svmr.refresh_actions", icon='FILE_REFRESH')
        row.operator("svmr.select_all_actions", text="All",
                     icon='CHECKBOX_HLT').select = True
        row.operator("svmr.select_all_actions", text="None",
                     icon='CHECKBOX_DEHLT').select = False

        layout.template_list("SVMR_UL_actions", "", settings, "actions",
                             settings, "actions_index", rows=5)
        layout.operator("svmr.batch_retarget", icon='RENDER_ANIMATION')


class SVMR_PT_bake(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Bake"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        column = layout.column(align=True)
        column.prop(settings, "use_action_range")
        sub = column.row(align=True)
        sub.enabled = not settings.use_action_range
        sub.prop(settings, "frame_start")
        sub.prop(settings, "frame_end")
        if settings.use_action_range:
            start, end = rig_mod.effective_frame_range(settings)
            column.label(text=f"Action range: {start} - {end}", icon='TIME')

        column = layout.column(align=True)
        column.prop(settings, "sample_step")
        column.prop(settings, "bake_step")
        column.prop(settings, "visual_keying")
        column.prop(settings, "quaternion_cleanup")
        column.prop(settings, "clean_curves")
        column.prop(settings, "key_reduction")

        row = layout.row(align=True)
        row.prop(settings, "use_bake_fps", text="")
        sub = row.row()
        sub.enabled = settings.use_bake_fps
        sub.prop(settings, "bake_fps")

        layout.prop(settings, "action_name_override", text="Name")
        layout.prop(settings, "keep_rig_after_bake")

        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("svmr.build_rig", icon='CONSTRAINT_BONE')
        row.operator("svmr.bake", icon='REC')


class SVMR_PT_validation(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Validation"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        row = layout.row(align=True)
        row.operator("svmr.validate", icon='CHECKMARK')
        row.prop(settings, "validation_step", text="Step")
        if len(settings.report):
            layout.template_list("SVMR_UL_report", "", settings, "report",
                                 settings, "report_index", rows=6)
            layout.operator("svmr.save_report", icon='TEXT')


class SVMR_PT_export(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Unreal Export"

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)

        layout.prop(settings, "export_path", text="")
        row = layout.row(align=True)
        row.prop(settings, "export_frame_start")
        row.prop(settings, "export_frame_end")
        layout.prop(settings, "export_include_mesh")

        row = layout.row(align=True)
        row.operator("svmr.export_preflight", icon='CHECKMARK')
        row.operator("svmr.export_fbx", icon='EXPORT')


class SVMR_PT_log(_Base, Panel):
    bl_parent_id = "SVMR_PT_main"
    bl_label = "Log"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = self.settings(context)
        if not len(settings.analysis_text):
            layout.label(text="Run Analyse Rigs to inspect the scene.", icon='INFO')
            return
        column = layout.column(align=True)
        for item in settings.analysis_text:
            row = column.row()
            row.alert = item.message.startswith(("ERROR", "WARNING", "!"))
            row.label(text=item.message)


CLASSES = (
    SVMR_PT_main,
    SVMR_PT_mapping,
    SVMR_PT_calibration,
    SVMR_PT_weapon,
    SVMR_PT_ik,
    SVMR_PT_procedural,
    SVMR_PT_actions,
    SVMR_PT_bake,
    SVMR_PT_validation,
    SVMR_PT_export,
    SVMR_PT_log,
)
