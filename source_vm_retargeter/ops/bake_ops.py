"""Bake operators: single Action and batch."""

from __future__ import annotations

from typing import List, Optional, Tuple

import bpy
from bpy.types import Operator

from ..core.log import get_logger
from ..retarget import bake as bake_mod
from ..retarget import rig as rig_mod
from .common import ProgressGuard, fill_lines, settings_of

LOG = get_logger()


def retarget_action(
    context,
    settings,
    action: Optional[bpy.types.Action] = None,
    name_override: str = "",
) -> Tuple[Optional[bpy.types.Action], rig_mod.BuildResult]:
    """Build, bake and clean up for one source Action.

    The source rig, its Actions and the Unreal skeleton are left exactly as
    they were; only a new target Action is added.
    """
    source = settings.source_armature
    previous_action = None
    if action is not None:
        if source.animation_data is None:
            source.animation_data_create()
        previous_action = source.animation_data.action
        source.animation_data.action = action
        slots = list(getattr(source.animation_data, "action_suitable_slots", []) or [])
        if slots and getattr(source.animation_data, "action_slot", None) is None:
            source.animation_data.action_slot = slots[0]

    try:
        result = rig_mod.build(context, settings)
        if result.driver_obj is None:
            return None, result

        start, end = rig_mod.effective_frame_range(settings)
        profile = rig_mod.resolve_profile(settings)
        target_name = bake_mod.target_action_name(
            rig_mod.active_source_action(settings), profile.action_suffix, name_override
        )
        baked = bake_mod.bake(
            context, settings, result.driven_bones, start, end, target_name,
            settings.bake_step,
        )
        if baked is not None and not settings.keep_rig_after_bake:
            rig_mod.teardown(context, settings, restore_action=False)
            anim = settings.target_armature.animation_data
            if anim is not None:
                anim.action = baked
        return baked, result
    finally:
        if action is not None and previous_action is not None:
            source.animation_data.action = previous_action


class SVMR_OT_bake(Operator):
    bl_idname = "svmr.bake"
    bl_label = "Bake Retarget"
    bl_description = ("Sample the retargeted pose into a new Action that no longer depends "
                      "on the source rig or any constraint")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return (settings.source_armature is not None
                and settings.target_armature is not None
                and len(settings.mapping) > 0)

    def execute(self, context):
        settings = settings_of(context)
        with ProgressGuard(context, 1) as progress:
            baked, result = retarget_action(
                context, settings, None, settings.action_name_override
            )
            progress.update(1)

        lines = list(result.notes) + [f"WARNING: {w}" for w in result.warnings]
        if baked is None:
            lines.append("ERROR: bake produced no Action.")
            fill_lines(settings.analysis_text, lines)
            for warning in result.warnings:
                self.report({'ERROR'}, warning)
            self.report({'ERROR'}, "Bake failed - see the report panel.")
            return {'CANCELLED'}

        settings.export_frame_start, settings.export_frame_end = \
            rig_mod.effective_frame_range(settings)
        lines.append(f"Baked Action '{baked.name}' "
                     f"({len(baked.fcurves)} F-Curves).")
        fill_lines(settings.analysis_text, lines)
        for warning in result.warnings:
            self.report({'WARNING'}, warning)
        self.report({'INFO'}, f"Baked '{baked.name}'.")
        return {'FINISHED'}


def _selected_actions(settings) -> List[bpy.types.Action]:
    out: List[bpy.types.Action] = []
    for item in settings.actions:
        if not item.selected:
            continue
        action = bpy.data.actions.get(item.name)
        if action is not None:
            out.append(action)
    return out


class SVMR_OT_batch_retarget(Operator):
    bl_idname = "svmr.batch_retarget"
    bl_label = "Batch Retarget"
    bl_description = "Retarget and bake every selected source Action into its own target Action"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _queue: List[str] = []
    _done: int = 0
    _results: List[str] = []

    @classmethod
    def poll(cls, context) -> bool:
        settings = settings_of(context)
        return (settings.source_armature is not None
                and settings.target_armature is not None
                and any(item.selected for item in settings.actions))

    # -- blocking path (scripts, background) -----------------------------
    def execute(self, context):
        settings = settings_of(context)
        actions = _selected_actions(settings)
        if not actions:
            self.report({'WARNING'}, "No Actions selected.")
            return {'CANCELLED'}

        lines: List[str] = []
        with ProgressGuard(context, len(actions)) as progress:
            for index, action in enumerate(actions):
                lines.append(self._process(context, settings, action))
                progress.update(index + 1)
        fill_lines(settings.analysis_text, lines)
        self.report({'INFO'}, f"Batch retargeted {len(actions)} Action(s).")
        return {'FINISHED'}

    def _process(self, context, settings, action) -> str:
        baked, result = retarget_action(context, settings, action, "")
        for item in settings.actions:
            if item.name == action.name:
                item.result = baked.name if baked else "FAILED"
        if baked is None:
            reason = result.warnings[0] if result.warnings else "unknown error"
            return f"FAILED  {action.name}: {reason}"
        return f"OK      {action.name} -> {baked.name}"

    # -- modal path (UI) --------------------------------------------------
    def invoke(self, context, event):
        settings = settings_of(context)
        actions = _selected_actions(settings)
        if not actions:
            self.report({'WARNING'}, "No Actions selected.")
            return {'CANCELLED'}
        self._queue = [a.name for a in actions]
        self._done = 0
        self._results = []
        window_manager = context.window_manager
        self._timer = window_manager.event_timer_add(0.01, window=context.window)
        window_manager.modal_handler_add(self)
        context.workspace.status_text_set(f"Retargeting 0/{len(self._queue)} ...")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            return self._finish(context, cancelled=True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        settings = settings_of(context)
        if self._done >= len(self._queue):
            return self._finish(context)

        name = self._queue[self._done]
        action = bpy.data.actions.get(name)
        if action is None:
            self._results.append(f"FAILED  {name}: Action no longer exists")
        else:
            self._results.append(self._process(context, settings, action))
        self._done += 1
        context.workspace.status_text_set(
            f"Retargeting {self._done}/{len(self._queue)} ({name}) ..."
        )
        return {'RUNNING_MODAL'}

    def _finish(self, context, cancelled: bool = False):
        settings = settings_of(context)
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.workspace.status_text_set(None)
        if cancelled:
            self._results.append("Cancelled by user.")
        fill_lines(settings.analysis_text, self._results)
        self.report({'INFO'}, f"Batch finished: {self._done}/{len(self._queue)} Action(s).")
        return {'CANCELLED'} if cancelled else {'FINISHED'}


CLASSES = (SVMR_OT_bake, SVMR_OT_batch_retarget)
