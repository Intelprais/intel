"""Baking the retarget result into a standalone Action.

After baking, the target Action reproduces the animation with **no**
dependency on the source rig, the driver armature or any constraint - which
is exactly what the Unreal FBX exporter needs.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import bpy
from mathutils import Quaternion

from ..core import scene as scene_utils
from ..core.log import get_logger

LOG = get_logger()

ACTION_PREFIX = "RTG_"


@contextmanager
def _all_bones_visible(armature_obj) -> Iterator[None]:
    """Temporarily reveal bones/collections so ``only_selected`` can see them."""
    data = armature_obj.data
    hidden_bones = [b for b in data.bones if b.hide]
    collections = list(getattr(data, "collections_all", []) or [])
    hidden_collections = [c for c in collections if not c.is_visible]
    try:
        for bone in hidden_bones:
            bone.hide = False
        for coll in hidden_collections:
            coll.is_visible = True
        yield
    finally:
        for bone in hidden_bones:
            bone.hide = True
        for coll in hidden_collections:
            coll.is_visible = False


@contextmanager
def _pose_selection(context, armature_obj, bone_names: Sequence[str]) -> Iterator[None]:
    """Activate ``armature_obj`` in pose mode with exactly ``bone_names`` selected."""
    previous_active = context.view_layer.objects.active
    previous_mode = armature_obj.mode
    previous_selection = {b.name: b.select for b in armature_obj.data.bones}
    context.view_layer.objects.active = armature_obj
    armature_obj.hide_set(False)
    bpy.ops.object.mode_set(mode='POSE')
    try:
        for bone in armature_obj.data.bones:
            bone.select = bone.name in set(bone_names)
        yield
    finally:
        for bone in armature_obj.data.bones:
            bone.select = previous_selection.get(bone.name, False)
        try:
            bpy.ops.object.mode_set(mode=previous_mode)
        except RuntimeError:
            bpy.ops.object.mode_set(mode='OBJECT')
        if previous_active is not None:
            context.view_layer.objects.active = previous_active


def make_quaternions_continuous(action) -> int:
    """Remove +/-180 degree flips from every quaternion channel of an Action."""
    groups: Dict[str, Dict[int, bpy.types.FCurve]] = {}
    for fcurve in action.fcurves:
        if fcurve.data_path.endswith("rotation_quaternion"):
            groups.setdefault(fcurve.data_path, {})[fcurve.array_index] = fcurve

    flipped = 0
    for channels in groups.values():
        if len(channels) != 4:
            continue
        curves = [channels[i] for i in range(4)]
        count = min(len(c.keyframe_points) for c in curves)
        previous: Optional[Quaternion] = None
        for index in range(count):
            points = [c.keyframe_points[index] for c in curves]
            quat = Quaternion([p.co.y for p in points])
            if previous is not None and quat.dot(previous) < 0.0:
                for point in points:
                    point.co.y = -point.co.y
                    point.handle_left.y = -point.handle_left.y
                    point.handle_right.y = -point.handle_right.y
                quat.negate()
                flipped += 1
            previous = quat
        for curve in curves:
            curve.update()
    return flipped


def _reduce_fcurve(fcurve, tolerance: float) -> int:
    """Ramer-Douglas-Peucker key reduction on one F-Curve."""
    points = fcurve.keyframe_points
    count = len(points)
    if count < 3 or tolerance <= 0.0:
        return 0

    coords: List[Tuple[float, float]] = [(p.co.x, p.co.y) for p in points]
    keep = [False] * count
    keep[0] = keep[-1] = True

    stack: List[Tuple[int, int]] = [(0, count - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        x0, y0 = coords[start]
        x1, y1 = coords[end]
        span = x1 - x0
        worst, worst_index = 0.0, -1
        for index in range(start + 1, end):
            x, y = coords[index]
            ratio = 0.0 if abs(span) < 1.0e-12 else (x - x0) / span
            distance = abs(y - (y0 + (y1 - y0) * ratio))
            if distance > worst:
                worst, worst_index = distance, index
        if worst > tolerance and worst_index > 0:
            keep[worst_index] = True
            stack.append((start, worst_index))
            stack.append((worst_index, end))

    removed = 0
    for index in range(count - 1, -1, -1):
        if not keep[index]:
            points.remove(points[index], fast=True)
            removed += 1
    if removed:
        fcurve.update()
    return removed


def reduce_keys(action, tolerance: float) -> int:
    if tolerance <= 0.0:
        return 0
    return sum(_reduce_fcurve(fc, tolerance) for fc in action.fcurves)


def unique_action_name(base: str) -> str:
    if base not in bpy.data.actions:
        return base
    index = 1
    while f"{base}.{index:03d}" in bpy.data.actions:
        index += 1
    return f"{base}.{index:03d}"


def target_action_name(source_action, profile_suffix: str, custom: str = "") -> str:
    if custom:
        return custom
    base = source_action.name if source_action is not None else "retarget"
    return f"{ACTION_PREFIX}{base}_{profile_suffix}"


def bake(
    context,
    settings,
    bone_names: Sequence[str],
    frame_start: int,
    frame_end: int,
    action_name: str,
    step: int = 1,
) -> Optional[bpy.types.Action]:
    """Bake the constrained target rig into a fresh Action.

    Returns the new Action, or ``None`` when Blender refused to bake.
    """
    target_obj = settings.target_armature
    if target_obj is None:
        return None
    bone_names = [n for n in bone_names if n in target_obj.data.bones]
    if not bone_names:
        LOG.warning("Bake skipped: no driven bones.")
        return None

    kwargs = dict(
        frame_start=int(frame_start),
        frame_end=int(frame_end),
        step=max(1, int(step)),
        only_selected=True,
        visual_keying=bool(settings.visual_keying),
        clear_constraints=False,
        clear_parents=False,
        use_current_action=False,
        bake_types={'POSE'},
    )
    if bool(settings.clean_curves):
        kwargs["clean_curves"] = True

    with _all_bones_visible(target_obj), _pose_selection(context, target_obj, bone_names):
        try:
            bpy.ops.nla.bake(**kwargs)
        except TypeError:
            kwargs.pop("clean_curves", None)
            bpy.ops.nla.bake(**kwargs)

    anim = target_obj.animation_data
    action = anim.action if anim else None
    if action is None:
        LOG.error("Bake produced no Action.")
        return None

    action.name = unique_action_name(action_name)
    action.use_fake_user = True

    if settings.quaternion_cleanup:
        flips = make_quaternions_continuous(action)
        if flips:
            LOG.info("Quaternion cleanup fixed %d flip(s) in '%s'.", flips, action.name)
    removed = reduce_keys(action, float(settings.key_reduction))
    if removed:
        LOG.info("Key reduction removed %d keyframe(s) from '%s'.", removed, action.name)
    return action


def push_to_nla(armature_obj, action, track_name: str = "SVMR Retarget") -> None:
    """Stash a baked Action into an NLA track so the next bake starts clean."""
    anim = armature_obj.animation_data
    if anim is None or action is None:
        return
    track = anim.nla_tracks.new()
    track.name = track_name
    strip = track.strips.new(action.name, int(action.frame_range[0]), action)
    strip.mute = True
    anim.action = None
