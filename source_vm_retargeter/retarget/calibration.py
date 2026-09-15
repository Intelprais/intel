"""Retarget-pose calibration.

The whole retarget reduces to one constant rotation per mapped bone::

    P_target(f) = G @ P_source(f) @ K(bone)

where ``G`` aligns the two rigs' body frames and ``K`` encodes the difference
between the two rest/retarget poses.  Deriving ``K`` is what "calibration"
means; once it is known the runtime cost is a plain parent offset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from mathutils import Euler, Matrix, Quaternion, Vector

from ..core import mathx
from ..core.log import get_logger

LOG = get_logger()

MODE_REST_TO_REST = 'REST_TO_REST'
MODE_AUTO_ALIGN = 'AUTO_ALIGN'
MODE_MANUAL = 'MANUAL'


def inv_rot(mat: Matrix) -> Matrix:
    """Inverse of an orthonormal 3x3 matrix (its transpose)."""
    return mat.transposed()


@dataclass
class BonePair:
    """One resolved mapping entry, ready for calibration."""

    key: str
    source_bone: str
    target_bone: str
    influence: float = 1.0
    manual_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    calibration_quat: Optional[Tuple[float, float, float, float]] = None


@dataclass
class Calibration:
    """Result of the calibration pass."""

    global_rotation: Matrix = field(default_factory=lambda: Matrix.Identity(3))
    scale: float = 1.0
    #: canonical key -> constant offset K (3x3 rotation)
    offsets: Dict[str, Matrix] = field(default_factory=dict)
    #: canonical key -> target calibration world rotation (for UI / MANUAL mode)
    target_calibration: Dict[str, Quaternion] = field(default_factory=dict)
    #: canonical key -> aligned source calibration world rotation (G @ A_s)
    source_calibration: Dict[str, Matrix] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def offset(self, key: str) -> Matrix:
        return self.offsets.get(key, Matrix.Identity(3))

    @property
    def global_rotation_4x4(self) -> Matrix:
        return self.global_rotation.to_4x4()


def _frame_from_x_up(x_axis: Vector, up_axis: Vector) -> Optional[Matrix]:
    """Right-handed orthonormal frame from a left->right axis and an up axis."""
    x = Vector(x_axis)
    z = Vector(up_axis)
    if x.length < mathx.EPS or z.length < mathx.EPS:
        return None
    x.normalize()
    z.normalize()
    y = z.cross(x)
    if y.length < 1.0e-6:
        return None
    y.normalize()
    z = x.cross(y)
    frame = Matrix.Identity(3)
    frame.col[0] = x
    frame.col[1] = y
    frame.col[2] = z
    return frame


def body_frame(
    rest_world: Dict[str, Matrix],
    bone_of_key,
    object_up: Vector,
) -> Optional[Matrix]:
    """Build a rig-local body frame from shoulders + spine.

    ``bone_of_key`` maps a canonical key to the bone name on that rig.
    Falls back to the armature object's own +Z when no spine is available,
    which is correct for both SMD and FBX imports (both are Z-up).
    """
    left = bone_of_key("upperarm_l") or bone_of_key("clavicle_l")
    right = bone_of_key("upperarm_r") or bone_of_key("clavicle_r")
    if not left or not right:
        return None
    mat_l = rest_world.get(left)
    mat_r = rest_world.get(right)
    if mat_l is None or mat_r is None:
        return None

    x_axis = mat_r.translation - mat_l.translation

    up = Vector(object_up)
    bottom = bone_of_key("pelvis") or bone_of_key("spine_01")
    top = bone_of_key("spine_03") or bone_of_key("neck_01") or bone_of_key("spine_02")
    if bottom and top and bottom in rest_world and top in rest_world:
        spine_up = rest_world[top].translation - rest_world[bottom].translation
        if spine_up.length > mathx.EPS:
            up = spine_up
    return _frame_from_x_up(x_axis, up)


def compute_global_alignment(
    source_rest: Dict[str, Matrix],
    target_rest: Dict[str, Matrix],
    pairs: Sequence[BonePair],
    source_up: Vector,
    target_up: Vector,
) -> Tuple[Matrix, List[str]]:
    """Rotation taking the source rig's body frame onto the target's."""
    notes: List[str] = []
    src_of = {p.key: p.source_bone for p in pairs}
    tgt_of = {p.key: p.target_bone for p in pairs}

    frame_s = body_frame(source_rest, src_of.get, source_up)
    frame_t = body_frame(target_rest, tgt_of.get, target_up)

    if frame_s is None or frame_t is None:
        notes.append(
            "Global alignment: both upper arms must be mapped to auto-detect the "
            "body orientation; falling back to identity."
        )
        return Matrix.Identity(3), notes

    global_rot = frame_t @ inv_rot(frame_s)
    angle = Quaternion(global_rot.to_quaternion()).angle
    notes.append(f"Global alignment: {angle * 57.29577951308232:.1f} deg source->target.")
    return global_rot, notes


def compute_scale(
    source_rest: Dict[str, Matrix],
    target_rest: Dict[str, Matrix],
    pairs: Sequence[BonePair],
) -> Tuple[float, List[str]]:
    """Source->target unit scale derived from arm-chain lengths.

    Source rigs are usually in Hammer units while Unreal rigs are in
    centimetres or metres; comparing measured limb lengths avoids guessing.
    """
    notes: List[str] = []
    src_of = {p.key: p.source_bone for p in pairs}
    tgt_of = {p.key: p.target_bone for p in pairs}

    ratios: List[float] = []
    for side in ("l", "r"):
        shoulder_key, elbow_key, wrist_key = (
            f"upperarm_{side}", f"lowerarm_{side}", f"hand_{side}"
        )
        s_bones = [src_of.get(shoulder_key), src_of.get(elbow_key), src_of.get(wrist_key)]
        t_bones = [tgt_of.get(shoulder_key), tgt_of.get(elbow_key), tgt_of.get(wrist_key)]
        if not all(s_bones) or not all(t_bones):
            continue
        if not all(b in source_rest for b in s_bones) or not all(b in target_rest for b in t_bones):
            continue
        s_len = (
            (source_rest[s_bones[1]].translation - source_rest[s_bones[0]].translation).length
            + (source_rest[s_bones[2]].translation - source_rest[s_bones[1]].translation).length
        )
        t_len = (
            (target_rest[t_bones[1]].translation - target_rest[t_bones[0]].translation).length
            + (target_rest[t_bones[2]].translation - target_rest[t_bones[1]].translation).length
        )
        if s_len > mathx.EPS and t_len > mathx.EPS:
            ratios.append(t_len / s_len)

    if not ratios:
        notes.append("Scale: arm chains unavailable, using 1.0 (check the mapping).")
        return 1.0, notes
    scale = sum(ratios) / len(ratios)
    notes.append(f"Scale: source->target factor {scale:.4f} (from arm chain lengths).")
    return scale, notes


def compute(
    pairs: Sequence[BonePair],
    source_calib_world: Dict[str, Matrix],
    target_rest_world: Dict[str, Matrix],
    source_rest_world: Dict[str, Matrix],
    mode: str = MODE_AUTO_ALIGN,
    global_rotation: Optional[Matrix] = None,
    scale: Optional[float] = None,
    source_up: Vector = Vector((0.0, 0.0, 1.0)),
    target_up: Vector = Vector((0.0, 0.0, 1.0)),
) -> Calibration:
    """Compute ``G``, the unit scale and the per-bone offsets ``K``.

    ``source_calib_world`` is the source pose used as the neutral reference -
    either the rest pose or a user-chosen frame of the source Action.
    """
    result = Calibration()

    if global_rotation is None:
        global_rotation, notes = compute_global_alignment(
            source_rest_world, target_rest_world, pairs, source_up, target_up
        )
        result.notes.extend(notes)
    result.global_rotation = mathx.orthonormalize(global_rotation.to_4x4())

    if scale is None:
        scale, notes = compute_scale(source_rest_world, target_rest_world, pairs)
        result.notes.extend(notes)
    result.scale = scale

    for pair in pairs:
        src_mat = source_calib_world.get(pair.source_bone)
        tgt_mat = target_rest_world.get(pair.target_bone)
        if src_mat is None or tgt_mat is None:
            continue

        source_aligned = result.global_rotation @ mathx.orthonormalize(src_mat)
        target_rest_rot = mathx.orthonormalize(tgt_mat)

        if mode == MODE_MANUAL and pair.calibration_quat is not None:
            quat = Quaternion(pair.calibration_quat)
            if quat.magnitude < mathx.EPS:
                quat = target_rest_rot.to_quaternion()
            target_calib = quat.normalized().to_matrix()
        elif mode == MODE_AUTO_ALIGN:
            dir_target = Vector(target_rest_rot.col[1]).normalized()
            dir_source = Vector(source_aligned.col[1]).normalized()
            swing = mathx.swing_to(dir_target, dir_source).to_matrix()
            target_calib = swing @ target_rest_rot
        else:  # MODE_REST_TO_REST
            target_calib = target_rest_rot

        if any(abs(v) > 1.0e-9 for v in pair.manual_offset):
            target_calib = target_calib @ Euler(pair.manual_offset, 'XYZ').to_matrix()

        result.target_calibration[pair.key] = target_calib.to_quaternion()
        result.source_calibration[pair.key] = source_aligned
        result.offsets[pair.key] = inv_rot(source_aligned) @ target_calib

    missing = [p.key for p in pairs if p.key not in result.offsets]
    if missing:
        result.notes.append(
            f"{len(missing)} mapped bone(s) could not be calibrated: {', '.join(missing[:6])}"
            + (" ..." if len(missing) > 6 else "")
        )
    return result


def retarget_world_rotation(
    calibration: Calibration, key: str, source_world: Matrix
) -> Matrix:
    """Reference implementation of ``G @ P_source @ K`` (used by the tests)."""
    return (
        calibration.global_rotation
        @ mathx.orthonormalize(source_world)
        @ calibration.offset(key)
    )
