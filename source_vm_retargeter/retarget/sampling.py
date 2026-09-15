"""Frame sampling helpers.

Sampling always goes through ``scene.frame_set`` + a depsgraph update so that
constraints, drivers and NLA on the *source* rig are respected - we never
read raw F-Curve values and assume they are the final pose.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, Iterator, List, Optional, Sequence

import bpy
from mathutils import Matrix

from ..core import mathx


@contextmanager
def preserved_frame(context) -> Iterator[None]:
    """Restore the scene frame (and subframe) after the block."""
    scene = context.scene
    frame = scene.frame_current
    subframe = scene.frame_subframe
    try:
        yield
    finally:
        scene.frame_set(frame, subframe=subframe)


@contextmanager
def temporary_action(armature_obj, action) -> Iterator[None]:
    """Temporarily assign ``action`` to an object, restoring the previous one."""
    if armature_obj.animation_data is None:
        armature_obj.animation_data_create()
    anim = armature_obj.animation_data
    previous = anim.action
    previous_slot = getattr(anim, "action_slot", None)
    try:
        anim.action = action
        if action is not None and hasattr(anim, "action_suitable_slots"):
            slots = list(anim.action_suitable_slots)
            if slots and getattr(anim, "action_slot", None) is None:
                anim.action_slot = slots[0]
        yield
    finally:
        anim.action = previous
        if previous is not None and previous_slot is not None:
            try:
                anim.action_slot = previous_slot
            except (TypeError, RuntimeError):
                pass


def frame_list(start: int, end: int, step: int = 1) -> List[int]:
    """Inclusive frame list; the end frame is always sampled."""
    step = max(1, int(step))
    frames = list(range(int(start), int(end) + 1, step))
    if frames and frames[-1] != int(end):
        frames.append(int(end))
    if not frames:
        frames = [int(start)]
    return frames


def sample_world_matrices(
    context,
    armature_obj,
    bone_names: Sequence[str],
    frames: Sequence[int],
    progress=None,
) -> Dict[int, Dict[str, Matrix]]:
    """``{frame: {bone_name: world_matrix}}`` for the given bones."""
    scene = context.scene
    depsgraph = context.evaluated_depsgraph_get()
    wanted = [n for n in bone_names if n and n in armature_obj.pose.bones]
    out: Dict[int, Dict[str, Matrix]] = {}
    for index, frame in enumerate(frames):
        scene.frame_set(int(frame))
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = armature_obj.evaluated_get(depsgraph)
        world = evaluated.matrix_world
        out[int(frame)] = {
            name: (world @ evaluated.pose.bones[name].matrix).copy()
            for name in wanted
        }
        if progress is not None and index % 10 == 0:
            progress(index / max(1, len(frames)))
    return out


def rest_world_matrices(armature_obj, bone_names: Sequence[str]) -> Dict[str, Matrix]:
    """``{bone_name: world rest matrix}``."""
    world = armature_obj.matrix_world
    out: Dict[str, Matrix] = {}
    for name in bone_names:
        bone = armature_obj.data.bones.get(name) if name else None
        if bone is not None:
            out[name] = (world @ bone.matrix_local).copy()
    return out


def pose_world_matrices(context, armature_obj, bone_names: Sequence[str],
                        frame: Optional[int] = None) -> Dict[str, Matrix]:
    """World matrices of bones at ``frame`` (current frame when ``None``)."""
    if frame is not None:
        context.scene.frame_set(int(frame))
    depsgraph = context.evaluated_depsgraph_get()
    evaluated = armature_obj.evaluated_get(depsgraph)
    world = evaluated.matrix_world
    return {
        name: (world @ evaluated.pose.bones[name].matrix).copy()
        for name in bone_names
        if name and name in evaluated.pose.bones
    }
