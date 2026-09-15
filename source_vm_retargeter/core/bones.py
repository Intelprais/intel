"""Armature / bone inspection helpers.

All functions here are read-only with respect to the armatures they inspect.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence

from mathutils import Matrix, Vector

from . import mathx


def hierarchy_order(armature_obj, names: Optional[Iterable[str]] = None) -> List[str]:
    """Return bone names ordered parents-before-children.

    ``names`` restricts the result while keeping the full-armature ordering.
    """
    wanted = set(names) if names is not None else None
    ordered: List[str] = []

    def walk(bone) -> None:
        if wanted is None or bone.name in wanted:
            ordered.append(bone.name)
        for child in bone.children:
            walk(child)

    for bone in armature_obj.data.bones:
        if bone.parent is None:
            walk(bone)
    return ordered


def rest_matrix_world(armature_obj, bone_name: str) -> Matrix:
    """World-space rest matrix of a bone."""
    bone = armature_obj.data.bones[bone_name]
    return armature_obj.matrix_world @ bone.matrix_local


def pose_matrix_world(armature_obj, bone_name: str) -> Matrix:
    """World-space posed matrix of a bone at the current frame."""
    pose_bone = armature_obj.pose.bones[bone_name]
    return armature_obj.matrix_world @ pose_bone.matrix


def bone_direction_world(armature_obj, bone_name: str, rest: bool = True) -> Vector:
    """Unit vector along the bone (head -> tail) in world space."""
    mat = rest_matrix_world(armature_obj, bone_name) if rest else pose_matrix_world(armature_obj, bone_name)
    direction = mathx.orthonormalize(mat).col[1].copy()
    return Vector(direction).normalized()


def bone_length_world(armature_obj, bone_name: str) -> float:
    """Bone length in world units (respects the object's scale)."""
    bone = armature_obj.data.bones[bone_name]
    scale = armature_obj.matrix_world.to_scale()
    uniform = (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0
    return bone.length * uniform


def chain_length_world(armature_obj, bone_names: Sequence[str]) -> float:
    """Summed world-space length of the given bones (missing ones ignored)."""
    total = 0.0
    for name in bone_names:
        if name and name in armature_obj.data.bones:
            total += bone_length_world(armature_obj, name)
    return total


def descendants(armature_obj, bone_name: str, include_self: bool = False) -> List[str]:
    """All descendant bone names of ``bone_name``."""
    bone = armature_obj.data.bones.get(bone_name)
    if bone is None:
        return []
    out = [bone_name] if include_self else []
    stack = list(bone.children)
    while stack:
        current = stack.pop()
        out.append(current.name)
        stack.extend(current.children)
    return out


def child_chain(armature_obj, root_name: str, max_depth: int = 8) -> List[str]:
    """Follow the single-child chain starting at ``root_name``.

    Stops at branches (more than one child) or at ``max_depth``.
    """
    chain = [root_name]
    bone = armature_obj.data.bones.get(root_name)
    while bone is not None and len(chain) < max_depth:
        children = [c for c in bone.children]
        if len(children) != 1:
            break
        bone = children[0]
        chain.append(bone.name)
    return chain


def animated_bone_names(armature_obj, action=None) -> List[str]:
    """Bone names driven by F-Curves in ``action`` (or the current action)."""
    if action is None:
        anim = armature_obj.animation_data
        action = anim.action if anim else None
    if action is None:
        return []
    names: List[str] = []
    seen = set()
    for fcurve in action.fcurves:
        path = fcurve.data_path
        if not path.startswith('pose.bones["'):
            continue
        end = path.find('"]', 12)
        if end == -1:
            continue
        name = path[12:end]
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def action_frame_range(action) -> tuple:
    """Integer ``(start, end)`` frame range of an Action."""
    if action is None:
        return (1, 1)
    start, end = action.frame_range
    return (int(round(start)), int(round(end)))


def build_parent_map(armature_obj) -> Dict[str, Optional[str]]:
    """``{bone_name: parent_name_or_None}``."""
    return {
        bone.name: (bone.parent.name if bone.parent else None)
        for bone in armature_obj.data.bones
    }
