"""Builds the (temporary) retarget rig.

Design
------
The retarget is expressed as one constant rotation per bone::

    P_target(f) = G @ P_source(f) @ K(bone)

which is exactly what Blender's own bone parenting computes.  So instead of
running Python on every frame we build a small helper armature where

* ``src_<key>``  copies the source bone's **world rotation** into pose space
  (Copy Rotation, WORLD -> POSE).  The helper object's world matrix is ``G``,
  so the bone's world rotation becomes ``G @ P_source``.
* ``drv_<key>``  is its child whose rest matrix is ``rest(src) @ K``; Blender's
  parenting then yields ``G @ P_source @ K`` for free.

The user's Unreal rig only receives temporary ``RTG_*`` constraints - its
hierarchy, rest pose and bone names are never touched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from ..core import bones as bone_utils
from ..core import mathx
from ..core import scene as scene_utils
from ..core.log import get_logger
from ..profiles import base as profile_base
from ..profiles import targets as target_profiles
from ..profiles.base import TargetProfile
from . import calibration as calib_mod
from . import sampling
from .calibration import BonePair, Calibration

LOG = get_logger()

DRIVER_OBJECT_NAME = "VM_RTG_DRIVERS"
ANCHOR_NAME = "VM_RTG_WEAPON_ANCHOR"
ROOT_LOC_NAME = "VM_RTG_ROOT_MOTION"
ROLE_PROP = "svmr_role"
SIDE_PROP = "svmr_side"

PREFIX = scene_utils.CONSTRAINT_PREFIX
C_FK = PREFIX + "FK_"
C_PROC = PREFIX + "PROC_"
C_LIMIT = PREFIX + "LIMIT_"
C_IK = PREFIX + "IK_"
C_IKROT = PREFIX + "IKROT_"
C_UEIK = PREFIX + "UEIK_"
C_ROOTLOC = PREFIX + "ROOTLOC_"


@dataclass
class BuildResult:
    driver_obj: Optional[bpy.types.Object] = None
    calibration: Optional[Calibration] = None
    pairs: List[BonePair] = field(default_factory=list)
    weapon_anchor: Optional[bpy.types.Object] = None
    ik_targets: Dict[str, bpy.types.Object] = field(default_factory=dict)
    pole_targets: Dict[str, bpy.types.Object] = field(default_factory=dict)
    root_empty: Optional[bpy.types.Object] = None
    driven_bones: List[str] = field(default_factory=list)
    frames: List[int] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def collect_pairs(settings) -> List[BonePair]:
    """Enabled mapping rows whose bones exist on both rigs."""
    source_obj = settings.source_armature
    target_obj = settings.target_armature
    if source_obj is None or target_obj is None:
        return []
    src_bones = source_obj.data.bones
    tgt_bones = target_obj.data.bones
    pairs: List[BonePair] = []
    for item in settings.mapping:
        if not item.enabled or not item.source_bone or not item.target_bone:
            continue
        if item.source_bone not in src_bones or item.target_bone not in tgt_bones:
            continue
        pairs.append(BonePair(
            key=item.key,
            source_bone=item.source_bone,
            target_bone=item.target_bone,
            influence=float(item.influence),
            manual_offset=tuple(item.manual_offset),
            calibration_quat=tuple(item.calibration_quat) if item.has_calibration else None,
        ))
    return pairs


def _object_up(obj) -> Vector:
    return (obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()


def _rigid(rot3: Matrix, translation: Vector) -> Matrix:
    mat = rot3.to_4x4()
    mat.translation = translation
    return mat


def _scaled_rigid(mat: Matrix, scale: float) -> Matrix:
    """Rigid transform with its translation multiplied by ``scale``."""
    out = mathx.orthonormalize(mat).to_4x4()
    out.translation = mat.translation * scale
    return out


def effective_frame_range(settings) -> Tuple[int, int]:
    action = active_source_action(settings)
    if settings.use_action_range and action is not None:
        return bone_utils.action_frame_range(action)
    return int(settings.frame_start), int(settings.frame_end)


def active_source_action(settings):
    obj = settings.source_armature
    if obj is None or obj.animation_data is None:
        return None
    return obj.animation_data.action


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def limb_directions(armature_obj, key_to_bone: Dict[str, str],
                    world: Dict[str, Matrix]) -> Dict[str, Vector]:
    """Measure each mapped bone's limb direction in world space.

    Bone +Y is not the limb axis on rigs that keep their engine-native
    orientation (both SMD and Unreal FBX imports do), so the direction is
    taken, in order of preference, from:

    1. the next joint along the canonical chain (``upperarm -> lowerarm``),
    2. the bone's own children in the armature,
    3. the previous joint, for chain tips such as the last finger segment,
    4. the bone's +Y axis, when the bone stands alone.

    Both rigs are measured the same way, so the two directions are directly
    comparable whatever each rig's bone convention happens to be.
    """
    out: Dict[str, Vector] = {}
    bones = armature_obj.data.bones
    matrix_world = armature_obj.matrix_world

    for key, bone_name in key_to_bone.items():
        own = world.get(bone_name)
        if own is None:
            continue
        direction: Optional[Vector] = None

        successor_key = profile_base.CHAIN_SUCCESSOR.get(key)
        successor_bone = key_to_bone.get(successor_key) if successor_key else None
        if successor_bone and successor_bone in world:
            direction = world[successor_bone].translation - own.translation

        if direction is None or direction.length < mathx.EPS:
            bone = bones.get(bone_name)
            children = list(bone.children) if bone is not None else []
            if children:
                centre = mathx.average_vector(
                    (matrix_world @ child.head_local) for child in children
                )
                direction = centre - own.translation

        if direction is None or direction.length < mathx.EPS:
            predecessor_key = profile_base.CHAIN_PREDECESSOR.get(key)
            predecessor_bone = key_to_bone.get(predecessor_key) if predecessor_key else None
            if predecessor_bone and predecessor_bone in world:
                direction = own.translation - world[predecessor_bone].translation

        if direction is None or direction.length < mathx.EPS:
            direction = Vector(mathx.orthonormalize(own).col[1])

        if direction.length > mathx.EPS:
            out[key] = direction.normalized()
    return out


def uses_animated_translation(pairs: Sequence[BonePair], rest: Dict[str, Matrix],
                              posed: Dict[str, Matrix], tolerance: float = 0.01) -> bool:
    """Does the source Action move bones off their rest offsets?

    SMD stores an absolute transform per bone per frame, so Source clips often
    place a joint somewhere the rest pose does not - the retarget is rotation
    based, so calibrating against the rest pose would then measure the wrong
    limb geometry.  Detected by predicting each child's head from its parent's
    animated rotation and comparing with where the child actually is.
    """
    successor = profile_base.CHAIN_SUCCESSOR
    by_key = {p.key: p.source_bone for p in pairs}
    for pair in pairs:
        child_bone = by_key.get(successor.get(pair.key, ""))
        if not child_bone:
            continue
        own_rest = rest.get(pair.source_bone)
        own_pose = posed.get(pair.source_bone)
        child_rest = rest.get(child_bone)
        child_pose = posed.get(child_bone)
        if None in (own_rest, own_pose, child_rest, child_pose):
            continue
        segment = (child_rest.translation - own_rest.translation).length
        if segment < mathx.EPS:
            continue
        predicted = own_pose @ own_rest.inverted_safe() @ child_rest.translation
        if (predicted - child_pose.translation).length > tolerance * segment:
            return True
    return False


def compute_calibration(context, settings, pairs: Sequence[BonePair]) -> Calibration:
    """Run the calibration pass for the current mapping and settings."""
    source_obj = settings.source_armature
    target_obj = settings.target_armature

    source_names = [p.source_bone for p in pairs]
    target_names = [p.target_bone for p in pairs]
    # Body-frame detection also wants spine/pelvis bones even when unmapped.
    source_rest = sampling.rest_world_matrices(source_obj, source_names)
    target_rest = sampling.rest_world_matrices(target_obj, target_names)

    mode = settings.calibration_pose_source
    frame = int(settings.calibration_frame)
    action = active_source_action(settings)
    auto_note = ""

    if mode in ('AUTO', 'FRAME') and action is not None:
        start, end = bone_utils.action_frame_range(action)
        if not start <= frame <= end:
            frame = start
        with sampling.preserved_frame(context):
            posed = sampling.pose_world_matrices(context, source_obj, source_names, frame)
    else:
        posed = None
        if mode == 'AUTO':
            mode = 'REST'
            auto_note = "no source Action, so the rest pose is the neutral"

    if mode == 'AUTO':
        if uses_animated_translation(pairs, source_rest, posed):
            mode = 'FRAME'
            auto_note = (f"the source Action offsets bones from their rest positions "
                         f"(typical of SMD), so frame {frame} is the neutral")
        else:
            mode = 'REST'
            auto_note = "the source Action keeps bones at their rest offsets"

    source_calib = posed if mode == 'FRAME' and posed is not None else dict(source_rest)

    global_rotation: Optional[Matrix] = None
    if settings.global_align_mode == 'NONE':
        global_rotation = Matrix.Identity(3)
    elif settings.global_align_mode == 'MANUAL':
        global_rotation = Euler(tuple(settings.global_align_euler), 'XYZ').to_matrix()

    scale: Optional[float] = None
    if settings.scale_mode == 'MANUAL':
        scale = float(settings.scale_factor)

    src_of = {p.key: p.source_bone for p in pairs}
    tgt_of = {p.key: p.target_bone for p in pairs}
    result = calib_mod.compute(
        pairs=pairs,
        source_calib_world=source_calib,
        target_rest_world=target_rest,
        source_rest_world=source_rest,
        mode=settings.calibration_mode,
        global_rotation=global_rotation,
        scale=scale,
        source_up=_object_up(source_obj),
        target_up=_object_up(target_obj),
        source_dirs=limb_directions(source_obj, src_of, source_calib),
        target_dirs=limb_directions(target_obj, tgt_of, target_rest),
    )
    if auto_note:
        result.notes.append(f"Calibration pose: {auto_note}.")
    return result


def store_calibration(settings, calibration: Calibration) -> None:
    """Write the computed retarget pose back into the mapping rows."""
    for item in settings.mapping:
        quat = calibration.target_calibration.get(item.key)
        if quat is None:
            continue
        item.calibration_quat = (quat.w, quat.x, quat.y, quat.z)
    if settings.scale_mode == 'AUTO':
        settings.scale_factor = calibration.scale
    if settings.global_align_mode == 'AUTO':
        settings.global_align_euler = tuple(
            calibration.global_rotation.to_euler('XYZ')
        )


# --------------------------------------------------------------------------
# driver armature
# --------------------------------------------------------------------------

def _enter_edit(context, obj) -> None:
    context.view_layer.objects.active = obj
    obj.hide_set(False)
    bpy.ops.object.mode_set(mode='EDIT')


def _leave_edit() -> None:
    bpy.ops.object.mode_set(mode='OBJECT')


def build_driver_armature(
    context,
    settings,
    pairs: Sequence[BonePair],
    calibration: Calibration,
    extra_drivers: Sequence[Tuple[str, str, Matrix]] = (),
) -> bpy.types.Object:
    """Create the helper armature carrying ``src_*`` and ``drv_*`` bones.

    ``extra_drivers`` adds ``(bone_name, parent_key, offset)`` children used by
    the procedural upper-body pass; their offset is applied on top of the same
    ``src_<parent_key>`` bone.
    """
    source_obj = settings.source_armature
    obj = scene_utils.new_armature(context, DRIVER_OBJECT_NAME)
    obj[ROLE_PROP] = 'DRIVERS'
    obj.matrix_world = calibration.global_rotation_4x4

    g_inv = calibration.global_rotation.transposed()
    rest_world = sampling.rest_world_matrices(source_obj, [p.source_bone for p in pairs])

    lengths = [
        (rest_world[p.source_bone].translation - rest_world[q.source_bone].translation).length
        for p, q in zip(pairs, pairs[1:])
        if p.source_bone in rest_world and q.source_bone in rest_world
    ]
    display = max(1.0e-4, (sum(lengths) / len(lengths)) if lengths else 0.1)

    local_rest: Dict[str, Matrix] = {}
    _enter_edit(context, obj)
    try:
        edit_bones = obj.data.edit_bones
        for pair in pairs:
            world = rest_world.get(pair.source_bone)
            if world is None:
                continue
            rest_local = _rigid(g_inv @ mathx.orthonormalize(world), g_inv @ world.translation)
            local_rest[pair.key] = rest_local

            src_bone = edit_bones.new(f"src_{pair.key}")
            src_bone.head = (0.0, 0.0, 0.0)
            src_bone.tail = (0.0, display, 0.0)
            src_bone.matrix = rest_local

            drv_bone = edit_bones.new(f"drv_{pair.key}")
            drv_bone.head = (0.0, 0.0, 0.0)
            drv_bone.tail = (0.0, display * 0.8, 0.0)
            drv_bone.matrix = rest_local @ calibration.offset(pair.key).to_4x4()
            drv_bone.parent = src_bone
            drv_bone.use_connect = False

        for bone_name, parent_key, offset in extra_drivers:
            parent_rest = local_rest.get(parent_key)
            if parent_rest is None or f"src_{parent_key}" not in edit_bones:
                continue
            extra = edit_bones.new(bone_name)
            extra.head = (0.0, 0.0, 0.0)
            extra.tail = (0.0, display * 0.6, 0.0)
            extra.matrix = parent_rest @ offset.to_4x4()
            extra.parent = edit_bones[f"src_{parent_key}"]
            extra.use_connect = False
    finally:
        _leave_edit()

    for pair in pairs:
        name = f"src_{pair.key}"
        if name not in obj.pose.bones:
            continue
        constraint = obj.pose.bones[name].constraints.new('COPY_ROTATION')
        constraint.name = "SVMR_Source"
        constraint.target = source_obj
        constraint.subtarget = pair.source_bone
        constraint.target_space = 'WORLD'
        constraint.owner_space = 'POSE'

    for bone in obj.data.bones:
        bone.use_deform = False
    return obj


# --------------------------------------------------------------------------
# target constraints
# --------------------------------------------------------------------------

def apply_fk_constraints(settings, driver_obj, pairs: Sequence[BonePair]) -> List[str]:
    """Copy Rotation from every ``drv_*`` bone onto the mapped target bone."""
    target_obj = settings.target_armature
    driven: List[str] = []
    for pair in pairs:
        pose_bone = target_obj.pose.bones.get(pair.target_bone)
        if pose_bone is None or f"drv_{pair.key}" not in driver_obj.pose.bones:
            continue
        constraint = pose_bone.constraints.new('COPY_ROTATION')
        constraint.name = f"{C_FK}{pair.key}"
        constraint.target = driver_obj
        constraint.subtarget = f"drv_{pair.key}"
        constraint.target_space = 'WORLD'
        constraint.owner_space = 'WORLD'
        constraint.influence = max(0.0, min(1.0, pair.influence))
        driven.append(pair.target_bone)
    return driven


def procedural_drivers(
    settings,
    profile: TargetProfile,
    pairs: Sequence[BonePair],
    calibration: Calibration,
    target_rest: Dict[str, Matrix],
) -> Tuple[List[Tuple[str, str, Matrix]], List[Tuple[str, str, float]]]:
    """Plan the procedural upper-body pass.

    Returns ``(extra_driver_specs, constraint_specs)`` where a constraint spec
    is ``(target_bone, driver_bone_name, influence)``.  A driver's world
    rotation is ``hand_delta @ rest(target_bone)``, so a Copy Rotation with
    influence ``i`` distributes exactly ``i`` of the hand's motion onto it.
    """
    extra: List[Tuple[str, str, Matrix]] = []
    constraints: List[Tuple[str, str, float]] = []
    if settings.body_mode not in ('PROCEDURAL_UPPER_BODY', 'NEUTRAL_FULL_BODY'):
        return extra, constraints

    mapped_targets = {p.target_bone for p in pairs}
    by_key = {p.key: p for p in pairs}

    def plan(target_bone: str, driver_key: str, influence: float, tag: str) -> None:
        if influence <= 0.0 or not target_bone:
            return
        if target_bone in mapped_targets:
            return  # real animation exists for this bone - never override it
        if target_bone not in target_rest or driver_key not in by_key:
            return
        hand_calib = calibration.target_calibration.get(driver_key)
        if hand_calib is None:
            return
        offset = (
            calibration.offset(driver_key)
            @ calib_mod.inv_rot(hand_calib.to_matrix())
            @ mathx.orthonormalize(target_rest[target_bone])
        )
        bone_name = f"proc_{tag}_{target_bone}"
        extra.append((bone_name, driver_key, offset))
        constraints.append((target_bone, bone_name, influence))

    primary = settings.primary_hand.lower()
    for side in ("l", "r"):
        clavicle = profile.bone(f"clavicle_{side}")
        plan(clavicle, f"upperarm_{side}", settings.shoulder_influence, "sh")
        plan(clavicle, f"hand_{side}", settings.clavicle_influence, "cl")

    chest = profile.bone("spine_03")
    plan(chest, f"hand_{primary}", settings.chest_influence, "ch")
    for key in ("spine_01", "spine_02"):
        plan(profile.bone(key), f"hand_{primary}", settings.spine_influence, "sp")
    return extra, constraints


def apply_procedural_constraints(
    settings, driver_obj, constraint_specs: Sequence[Tuple[str, str, float]]
) -> List[str]:
    target_obj = settings.target_armature
    driven: List[str] = []
    limit = float(settings.max_spine_rotation)
    for target_bone, driver_bone, influence in constraint_specs:
        pose_bone = target_obj.pose.bones.get(target_bone)
        if pose_bone is None or driver_bone not in driver_obj.pose.bones:
            continue
        constraint = pose_bone.constraints.new('COPY_ROTATION')
        constraint.name = f"{C_PROC}{driver_bone}"
        constraint.target = driver_obj
        constraint.subtarget = driver_bone
        constraint.target_space = 'WORLD'
        constraint.owner_space = 'WORLD'
        constraint.influence = max(0.0, min(1.0, influence))
        driven.append(target_bone)

    for target_bone in set(driven):
        pose_bone = target_obj.pose.bones[target_bone]
        limiter = pose_bone.constraints.new('LIMIT_ROTATION')
        limiter.name = f"{C_LIMIT}{target_bone}"
        limiter.owner_space = 'LOCAL'
        for axis in ("x", "y", "z"):
            setattr(limiter, f"use_limit_{axis}", True)
            setattr(limiter, f"min_{axis}", -limit)
            setattr(limiter, f"max_{axis}", limit)
    return driven


def apply_ue_ik_constraints(settings, profile: TargetProfile) -> List[str]:
    """Make UE's ``ik_hand_*`` bones follow the animated hands.

    Unreal expects ``ik_hand_gun`` to sit on the weapon hand and
    ``ik_hand_l``/``ik_hand_r`` on their respective hands; weapon attachment
    in-engine reads those bones, so they must be keyed in the exported clip.
    """
    if not settings.drive_ue_ik_bones:
        return []
    target_obj = settings.target_armature
    driven: List[str] = []
    gun_side = settings.ik_gun_hand.lower()

    plan = [
        (profile.ik_bones.get("hand_gun"), profile.bone(f"hand_{gun_side}")),
        (profile.ik_bones.get("hand_l"), profile.bone("hand_l")),
        (profile.ik_bones.get("hand_r"), profile.bone("hand_r")),
    ]
    for ik_bone, hand_bone in plan:
        if not ik_bone or not hand_bone:
            continue
        pose_bone = target_obj.pose.bones.get(ik_bone)
        if pose_bone is None or hand_bone not in target_obj.pose.bones:
            continue
        constraint = pose_bone.constraints.new('COPY_TRANSFORMS')
        constraint.name = f"{C_UEIK}{ik_bone}"
        constraint.target = target_obj
        constraint.subtarget = hand_bone
        constraint.target_space = 'WORLD'
        constraint.owner_space = 'WORLD'
        driven.append(ik_bone)
    return driven


# --------------------------------------------------------------------------
# weapon anchor, two-hand IK and root motion
# --------------------------------------------------------------------------

def _key_object_transforms(obj, samples: Dict[int, Matrix]) -> None:
    """Keyframe an object's local transform from ``{frame: matrix}``.

    Quaternions are made continuous first so the baked result never shows the
    classic +/-180 degree flip.
    """
    obj.rotation_mode = 'QUATERNION'
    frames = sorted(samples)
    quats = mathx.make_quat_continuous(
        [mathx.orthonormalize(samples[f]).to_quaternion() for f in frames]
    )
    for frame, quat in zip(frames, quats):
        obj.location = samples[frame].translation
        obj.rotation_quaternion = quat
        obj.keyframe_insert("location", frame=frame)
        obj.keyframe_insert("rotation_quaternion", frame=frame)
    if obj.animation_data and obj.animation_data.action:
        obj.animation_data.action.use_fake_user = False


def _pole_position(shoulder: Vector, elbow: Vector, wrist: Vector,
                   fallback_axis: Vector) -> Vector:
    """Elbow pole placed along the current bend direction."""
    mid = (shoulder + wrist) * 0.5
    bend = elbow - mid
    if bend.length < 1.0e-6:
        bend = Vector(fallback_axis)
        if bend.length < 1.0e-6:
            bend = Vector((0.0, -1.0, 0.0))
    bend = bend.normalized()
    distance = 0.5 * ((elbow - shoulder).length + (wrist - elbow).length)
    distance = max(distance, 1.0e-4)
    return elbow + bend * distance


def solve_pole_angle(context, target_obj, constraint, elbow_bone: str,
                     coarse_step: float = 10.0) -> float:
    """Find the pole angle whose IK solution matches the FK elbow position.

    Blender's pole angle depends on the bone roll, so it cannot be derived
    analytically in a rig-agnostic way; a coarse-to-fine search over one scalar
    is cheap and always lands on the right answer.
    """
    pose_bone = target_obj.pose.bones.get(elbow_bone)
    if pose_bone is None:
        return 0.0

    def elbow_world() -> Vector:
        context.view_layer.update()
        return target_obj.matrix_world @ pose_bone.head.copy()

    previous_influence = constraint.influence
    constraint.influence = 0.0
    reference = elbow_world()
    constraint.influence = previous_influence

    best_angle, best_error = 0.0, None
    steps = int(round(360.0 / coarse_step))
    for index in range(steps):
        angle = math.radians(-180.0 + index * coarse_step)
        constraint.pole_angle = angle
        error = (elbow_world() - reference).length
        if best_error is None or error < best_error:
            best_angle, best_error = angle, error

    for index in range(-9, 10):
        angle = best_angle + math.radians(index * coarse_step / 10.0)
        constraint.pole_angle = angle
        error = (elbow_world() - reference).length
        if best_error is None or error < best_error:
            best_angle, best_error = angle, error

    constraint.pole_angle = best_angle
    context.view_layer.update()
    return best_angle


def build_weapon_and_ik(context, settings, profile: TargetProfile,
                        calibration: Calibration, pairs: Sequence[BonePair],
                        frames: Sequence[int], result: BuildResult) -> None:
    """Create the weapon anchor, the secondary-hand IK target and its pole."""
    source_obj = settings.source_armature
    target_obj = settings.target_armature
    by_key = {p.key: p for p in pairs}

    primary = settings.primary_hand.lower()
    secondary = 'r' if primary == 'l' else 'l'
    primary_pair = by_key.get(f"hand_{primary}")
    secondary_pair = by_key.get(f"hand_{secondary}")

    if primary_pair is None:
        result.warnings.append(
            f"Primary hand ({primary.upper()}) is not mapped - weapon anchor and "
            "two-hand IK are disabled."
        )
        return
    if not settings.weapon_enabled:
        # Both the anchor and two-hand IK exist to keep the hands on a weapon.
        # With no weapon they would lock the hands together and destroy clips
        # where the arms move independently (punches, gestures, swimming).
        if settings.two_hand_ik or settings.weapon_follow_mode != 'NONE':
            result.notes.append(
                "Weapon is disabled, so the weapon anchor and two-hand IK are "
                "skipped; both hands are retargeted independently."
            )
        return
    if settings.weapon_follow_mode == 'NONE' and not settings.two_hand_ik:
        return

    reference_bone = primary_pair.source_bone
    if settings.weapon_enabled and settings.weapon_reference_bone:
        if settings.weapon_reference_bone in source_obj.data.bones:
            reference_bone = settings.weapon_reference_bone
        else:
            result.warnings.append(
                f"Weapon reference bone '{settings.weapon_reference_bone}' not found "
                "on the source armature; using the primary hand instead."
            )

    def _grip(name: str, label: str) -> Optional[str]:
        """Validate an optional grip marker bone on the weapon."""
        if not settings.weapon_enabled or not name:
            return None
        if name in source_obj.data.bones:
            return name
        result.warnings.append(
            f"{label} grip bone '{name}' not found on the source armature; "
            f"falling back to the hand's own position."
        )
        return None

    primary_grip = _grip(settings.primary_grip_bone, "Primary")
    secondary_grip = _grip(settings.secondary_grip_bone, "Secondary")

    wanted = {primary_pair.source_bone, reference_bone}
    wanted.update(g for g in (primary_grip, secondary_grip) if g)
    secondary_chain = []
    if secondary_pair is not None:
        secondary_chain = [
            by_key[k].source_bone
            for k in (f"upperarm_{secondary}", f"lowerarm_{secondary}", f"hand_{secondary}")
            if k in by_key
        ]
        wanted.update(secondary_chain)

    with sampling.preserved_frame(context):
        samples = sampling.sample_world_matrices(context, source_obj, sorted(wanted), frames)

    scale = calibration.scale
    k_primary_inv = calibration.offset(primary_pair.key).transposed().to_4x4()

    # ---- weapon anchor -------------------------------------------------
    anchor = None
    if settings.weapon_follow_mode != 'NONE':
        anchor = scene_utils.new_empty(context, ANCHOR_NAME, display='ARROWS',
                                       size=max(1.0e-3, scale * 0.1),
                                       color=(1.0, 0.75, 0.1, 1.0))
        anchor[ROLE_PROP] = 'WEAPON_ANCHOR'
        constraint = anchor.constraints.new('CHILD_OF')
        constraint.name = "SVMR_FollowHand"
        constraint.target = target_obj
        constraint.subtarget = primary_pair.target_bone
        constraint.inverse_matrix = Matrix.Identity(4)

        def anchor_basis(frame: int) -> Matrix:
            """Offset placing the weapon relative to the target's primary hand.

            The rotation always reproduces the source weapon's orientation.  When
            a primary grip marker is given, the translation is chosen so that the
            marker lands exactly in the hand instead of wherever the source hand
            happened to sit - which removes any residual grip offset.
            """
            hand = samples[frame][primary_pair.source_bone]
            weapon = samples[frame][reference_bone]
            basis = _scaled_rigid(k_primary_inv @ (hand.inverted_safe() @ weapon), scale)
            if primary_grip is not None:
                grip_local = (weapon.inverted_safe()
                              @ samples[frame][primary_grip].translation) * scale
                basis.translation = -(basis.to_3x3() @ grip_local)
            return basis

        if settings.weapon_follow_mode == 'HAND_RIGID':
            reference_frame = _reference_frame(settings, frames)
            anchor.matrix_basis = anchor_basis(reference_frame)
        else:
            _key_object_transforms(anchor, {f: anchor_basis(f) for f in frames})
        result.weapon_anchor = anchor

    if not settings.two_hand_ik or secondary_pair is None or anchor is None:
        if settings.two_hand_ik and secondary_pair is None:
            result.warnings.append(
                f"Two-hand IK needs hand_{secondary} mapped; skipping."
            )
        return
    if len(secondary_chain) < 3:
        result.warnings.append(
            f"Two-hand IK needs the full {secondary.upper()} arm chain mapped "
            "(upperarm, lowerarm, hand); skipping."
        )
        return

    upper_src, lower_src, hand_src = secondary_chain
    k_secondary = calibration.offset(secondary_pair.key).to_4x4()

    ik_empty = scene_utils.new_empty(context, f"VM_RTG_IK_HAND_{secondary.upper()}",
                                     display='SPHERE', size=max(1.0e-3, scale * 0.05),
                                     color=(0.1, 0.8, 1.0, 1.0))
    ik_empty[ROLE_PROP] = 'IK_TARGET'
    ik_empty[SIDE_PROP] = secondary
    ik_empty.parent = anchor
    ik_empty.matrix_parent_inverse = Matrix.Identity(4)

    pole_empty = scene_utils.new_empty(context, f"VM_RTG_POLE_{secondary.upper()}",
                                       display='CONE', size=max(1.0e-3, scale * 0.04),
                                       color=(1.0, 0.25, 0.55, 1.0))
    pole_empty[ROLE_PROP] = 'POLE_TARGET'
    pole_empty[SIDE_PROP] = secondary
    pole_empty.parent = anchor
    pole_empty.matrix_parent_inverse = Matrix.Identity(4)

    ik_samples: Dict[int, Matrix] = {}
    pole_samples: Dict[int, Matrix] = {}
    for frame in frames:
        weapon = samples[frame][reference_bone]
        weapon_inv = weapon.inverted_safe()
        hand = samples[frame][hand_src]
        placement = _scaled_rigid(weapon_inv @ hand, scale) @ k_secondary
        if secondary_grip is not None:
            # Snap the support hand onto the marker, keep its own orientation.
            placement.translation = (weapon_inv
                                     @ samples[frame][secondary_grip].translation) * scale
        ik_samples[frame] = placement

        shoulder = samples[frame][upper_src].translation
        elbow = samples[frame][lower_src].translation
        wrist = hand.translation
        fallback = Vector(mathx.orthonormalize(samples[frame][lower_src]).col[2])
        pole_world = _pole_position(shoulder, elbow, wrist, fallback)
        pole_samples[frame] = Matrix.Translation((weapon_inv @ pole_world) * scale)

    _key_object_transforms(ik_empty, ik_samples)
    _key_object_transforms(pole_empty, pole_samples)
    result.ik_targets[secondary] = ik_empty
    result.pole_targets[secondary] = pole_empty

    lower_target = profile.bone(f"lowerarm_{secondary}")
    pose_bone = target_obj.pose.bones.get(lower_target) if lower_target else None
    if pose_bone is None:
        result.warnings.append(
            f"Target bone lowerarm_{secondary} not found; two-hand IK skipped."
        )
        return

    ik_constraint = pose_bone.constraints.new('IK')
    ik_constraint.name = f"{C_IK}{secondary}"
    ik_constraint.target = ik_empty
    ik_constraint.chain_count = 2
    ik_constraint.use_tail = True
    ik_constraint.use_rotation = False
    ik_constraint.influence = float(settings.ik_blend)
    if settings.use_elbow_pole:
        ik_constraint.pole_target = pole_empty
        ik_constraint.pole_angle = solve_pole_angle(context, target_obj, ik_constraint,
                                                    lower_target)
        result.notes.append(
            f"Solved {secondary.upper()} elbow pole angle: "
            f"{math.degrees(ik_constraint.pole_angle):.1f} deg."
        )

    hand_target = secondary_pair.target_bone
    hand_pose = target_obj.pose.bones.get(hand_target)
    if hand_pose is not None:
        rot = hand_pose.constraints.new('COPY_ROTATION')
        rot.name = f"{C_IKROT}{secondary}"
        rot.target = ik_empty
        rot.target_space = 'WORLD'
        rot.owner_space = 'WORLD'
        rot.influence = float(settings.ik_blend)

    upper_target = profile.bone(f"upperarm_{secondary}")
    for name in (upper_target, lower_target):
        if name and name not in result.driven_bones:
            result.driven_bones.append(name)


def _reference_frame(settings, frames: Sequence[int]) -> int:
    candidate = int(settings.calibration_frame)
    if candidate in frames:
        return candidate
    return int(frames[0]) if frames else candidate


def build_root_motion(context, settings, profile: TargetProfile,
                      calibration: Calibration, frames: Sequence[int],
                      result: BuildResult) -> None:
    """Optionally transfer root translation from the source.

    Viewmodel clips have no character root motion, so the default policy is to
    leave the Unreal root bone perfectly still rather than inventing movement.
    """
    if settings.root_motion == 'IGNORE':
        return
    source_obj = settings.source_armature
    target_obj = settings.target_armature

    bone_name = settings.root_motion_bone
    if settings.root_motion == 'COPY_IF_AVAILABLE' and not bone_name:
        for item in settings.mapping:
            if item.key in ("root", "pelvis") and item.source_bone:
                bone_name = item.source_bone
                break
    if not bone_name or bone_name not in source_obj.data.bones:
        result.warnings.append(
            "Root motion requested but no usable source root bone was found; "
            "the Unreal root stays static."
        )
        return

    target_bone = profile.root_bone
    if target_bone not in target_obj.pose.bones:
        result.warnings.append(f"Target root bone '{target_bone}' not found.")
        return

    rest = sampling.rest_world_matrices(source_obj, [bone_name])[bone_name]
    with sampling.preserved_frame(context):
        samples = sampling.sample_world_matrices(context, source_obj, [bone_name], frames)

    target_rest = sampling.rest_world_matrices(target_obj, [target_bone])[target_bone]
    empty = scene_utils.new_empty(context, ROOT_LOC_NAME, display='PLAIN_AXES',
                                  size=max(1.0e-3, calibration.scale * 0.2),
                                  color=(0.5, 1.0, 0.4, 1.0))
    empty[ROLE_PROP] = 'ROOT_MOTION'

    motion: Dict[int, Matrix] = {}
    for frame in frames:
        delta = samples[frame][bone_name].translation - rest.translation
        world = target_rest.translation + calibration.global_rotation @ delta * calibration.scale
        motion[frame] = Matrix.Translation(world)
    _key_object_transforms(empty, motion)

    pose_bone = target_obj.pose.bones[target_bone]
    constraint = pose_bone.constraints.new('COPY_LOCATION')
    constraint.name = f"{C_ROOTLOC}{target_bone}"
    constraint.target = empty
    constraint.target_space = 'WORLD'
    constraint.owner_space = 'WORLD'
    result.root_empty = empty
    if target_bone not in result.driven_bones:
        result.driven_bones.append(target_bone)


# --------------------------------------------------------------------------
# build / teardown
# --------------------------------------------------------------------------

def resolve_profile(settings) -> TargetProfile:
    if settings.target_profile != 'AUTO':
        return target_profiles.get_profile(settings.target_profile)
    detected = target_profiles.detect_profile(settings.target_armature)
    return detected or target_profiles.UE4_MANNEQUIN


def _clear_target_pose(settings, bone_names: Sequence[str]) -> None:
    target_obj = settings.target_armature
    for name in bone_names:
        pose_bone = target_obj.pose.bones.get(name)
        if pose_bone is not None:
            pose_bone.matrix_basis = Matrix.Identity(4)


def teardown(context, settings, restore_action: bool = True) -> Dict[str, int]:
    """Remove everything the add-on created, leaving the user's rigs intact."""
    stats = {"constraints": 0, "objects": 0}
    if settings.target_armature is not None:
        stats["constraints"] = scene_utils.remove_owned_constraints(settings.target_armature)
        driven = [n for n in settings.driven_bones.split(",") if n]
        _clear_target_pose(settings, driven)
    stats["objects"] = scene_utils.clear_helpers(context)
    if restore_action and settings.target_armature is not None:
        anim = settings.target_armature.animation_data
        if anim is not None and settings.stashed_target_action is not None:
            anim.action = settings.stashed_target_action
    settings.stashed_target_action = None
    settings.driver_object = None
    settings.rig_built = False
    settings.driven_bones = ""
    return stats


def build(context, settings) -> BuildResult:
    """Build the full retarget rig for the current settings."""
    result = BuildResult()
    source_obj = settings.source_armature
    target_obj = settings.target_armature
    if source_obj is None or target_obj is None:
        result.warnings.append("Select both a Source and a Target armature first.")
        return result
    if source_obj is target_obj:
        result.warnings.append("Source and Target must be different armatures.")
        return result

    teardown(context, settings)

    pairs = collect_pairs(settings)
    if not pairs:
        result.warnings.append(
            "No usable bone mapping - run Auto Detect Mapping or load a preset."
        )
        return result
    result.pairs = pairs

    profile = resolve_profile(settings)
    calibration = compute_calibration(context, settings, pairs)
    store_calibration(settings, calibration)
    result.calibration = calibration
    result.notes.extend(calibration.notes)

    target_rest = sampling.rest_world_matrices(
        target_obj, sorted(set(profile.bones.values()) | set(profile.spine_chain))
    )
    extra_drivers, proc_specs = procedural_drivers(
        settings, profile, pairs, calibration, target_rest
    )

    driver_obj = build_driver_armature(context, settings, pairs, calibration, extra_drivers)
    result.driver_obj = driver_obj

    anim = target_obj.animation_data
    if anim is not None and anim.action is not None:
        settings.stashed_target_action = anim.action
        anim.action = None

    driven = list(dict.fromkeys(p.target_bone for p in pairs))
    driven += [b for b in (spec[0] for spec in proc_specs) if b not in driven]
    for ik_bone in profile.ik_bones.values():
        if ik_bone in target_obj.pose.bones and ik_bone not in driven:
            driven.append(ik_bone)
    _clear_target_pose(settings, driven)

    result.driven_bones = apply_fk_constraints(settings, driver_obj, pairs)
    for name in apply_procedural_constraints(settings, driver_obj, proc_specs):
        if name not in result.driven_bones:
            result.driven_bones.append(name)

    start, end = effective_frame_range(settings)
    frames = sampling.frame_list(start, end, settings.sample_step)
    result.frames = frames

    build_weapon_and_ik(context, settings, profile, calibration, pairs, frames, result)
    build_root_motion(context, settings, profile, calibration, frames, result)

    for name in apply_ue_ik_constraints(settings, profile):
        if name not in result.driven_bones:
            result.driven_bones.append(name)

    if settings.body_mode == 'NEUTRAL_FULL_BODY':
        # The source has no legs, so nothing drives them - but keying them at
        # rest makes the exported clip a complete full-body take instead of an
        # upper-body-only one.  No leg motion is invented.
        neutral = [profile.root_bone, profile.bone("pelvis")]
        neutral += list(profile.leg_bones) + list(profile.spine_chain)
        added = 0
        for name in neutral:
            if name and name in target_obj.pose.bones and name not in result.driven_bones:
                result.driven_bones.append(name)
                added += 1
        result.notes.append(
            f"Neutral full body: {added} lower-body bone(s) keyed at their rest pose."
        )

    settings.driver_object = driver_obj
    settings.rig_built = True
    settings.driven_bones = ",".join(result.driven_bones)
    context.view_layer.update()

    result.notes.append(
        f"Built retarget rig: {len(pairs)} mapped bone(s), "
        f"{len(result.driven_bones)} driven target bone(s), frames {start}-{end}."
    )
    return result
