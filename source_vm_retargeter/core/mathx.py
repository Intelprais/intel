"""Matrix / quaternion helpers used by the retarget engine.

Everything here is deliberately transform-based: we never compare or copy
Euler angles between rigs, because Source and Unreal skeletons do not share
bone axes, rolls or rest poses.
"""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence

from mathutils import Matrix, Quaternion, Vector

EPS = 1.0e-9


def is_finite_matrix(mat: Matrix) -> bool:
    """True when every component of ``mat`` is a finite number."""
    for row in mat:
        for value in row:
            if math.isnan(value) or math.isinf(value):
                return False
    return True


def orthonormalize(mat: Matrix) -> Matrix:
    """Return the pure-rotation 3x3 part of ``mat``.

    Column-normalising is not enough: with non-uniform scale the columns stay
    non-orthogonal and the "rotation" would shear the retargeted pose.  Gram-
    Schmidt is used instead, anchored on +Y because that is the bone axis in
    Blender - the bone's direction is preserved exactly and the twist is
    squared up around it.  The result is always right-handed, so a mirrored
    (negatively scaled) input is de-mirrored rather than producing an invalid
    rotation; :mod:`..validation.checks` warns about such rigs.
    """
    rot = mat.to_3x3()
    y = Vector(rot.col[1])
    x = Vector(rot.col[0])
    if y.length < EPS:
        if x.length < EPS:
            return Matrix.Identity(3)
        y = x.orthogonal()
    y.normalize()
    if x.length < EPS:
        x = y.orthogonal()
    x = x - y * x.dot(y)
    if x.length < EPS:
        x = y.orthogonal()
    x.normalize()
    z = x.cross(y)
    out = Matrix.Identity(3)
    out.col[0] = x
    out.col[1] = y
    out.col[2] = z
    return out


def rotation_only(mat: Matrix) -> Matrix:
    """4x4 matrix carrying only the rotation of ``mat``."""
    return orthonormalize(mat).to_4x4()


def swing_to(from_vec: Vector, to_vec: Vector) -> Quaternion:
    """Shortest-arc rotation taking ``from_vec`` onto ``to_vec``.

    Returns identity for degenerate inputs; for exactly opposite vectors an
    arbitrary but stable perpendicular axis is used.
    """
    a = Vector(from_vec)
    b = Vector(to_vec)
    if a.length < EPS or b.length < EPS:
        return Quaternion()
    a.normalize()
    b.normalize()
    dot = max(-1.0, min(1.0, a.dot(b)))
    if dot > 1.0 - 1.0e-10:
        return Quaternion()
    if dot < -1.0 + 1.0e-10:
        axis = a.orthogonal()
        axis.normalize()
        return Quaternion(axis, math.pi)
    return a.rotation_difference(b)


def make_quat_continuous(quats: Sequence[Quaternion]) -> List[Quaternion]:
    """Flip quaternion signs so consecutive samples take the short path.

    ``q`` and ``-q`` describe the same orientation but interpolate through
    opposite sides of the hypersphere; without this pass a baked curve shows
    the classic +/-180 degree flip.
    """
    out: List[Quaternion] = []
    prev: Quaternion | None = None
    for q in quats:
        cur = Quaternion(q)
        if prev is not None and cur.dot(prev) < 0.0:
            cur.negate()
        out.append(cur)
        prev = cur
    return out


def blend_quat(base: Quaternion, target: Quaternion, factor: float) -> Quaternion:
    """Slerp from ``base`` to ``target`` honouring quaternion double cover."""
    factor = max(0.0, min(1.0, factor))
    if factor <= 0.0:
        return Quaternion(base)
    if factor >= 1.0:
        return Quaternion(target)
    tgt = Quaternion(target)
    if base.dot(tgt) < 0.0:
        tgt.negate()
    return Quaternion(base).slerp(tgt, factor)


def clamp_quat_angle(quat: Quaternion, max_angle: float) -> Quaternion:
    """Limit the rotation magnitude of ``quat`` to ``max_angle`` radians."""
    if max_angle <= 0.0:
        return Quaternion()
    q = Quaternion(quat).normalized()
    if q.w < 0.0:
        q.negate()
    angle = q.angle
    if angle <= max_angle:
        return q
    axis = q.axis
    if axis.length < EPS:
        return Quaternion()
    return Quaternion(axis, max_angle)


def angle_between_matrices(a: Matrix, b: Matrix) -> float:
    """Shortest rotation angle (radians, 0..pi) between two transforms.

    ``Quaternion.angle`` is ``2 * acos(w)``, which reaches 2*pi when ``w`` is
    negative - so a one-degree difference can read as 359 degrees.  The sign is
    canonicalised first, otherwise flip detection reports phantom flips.
    """
    qa = orthonormalize(a).to_quaternion()
    qb = orthonormalize(b).to_quaternion()
    diff = qa.rotation_difference(qb)
    if diff.w < 0.0:
        diff.negate()
    return diff.angle


def frame_from_axes(primary: Vector, secondary: Vector) -> Matrix:
    """Build an orthonormal 3x3 frame.

    ``primary`` becomes +Y (Blender's bone axis convention), ``secondary`` is
    used to resolve the remaining twist and is orthogonalised against it.
    """
    y = Vector(primary)
    if y.length < EPS:
        return Matrix.Identity(3)
    y.normalize()
    x = Vector(secondary)
    x = x - y * x.dot(y)
    if x.length < EPS:
        x = y.orthogonal()
    x.normalize()
    z = x.cross(y)
    mat = Matrix.Identity(3)
    mat.col[0] = x
    mat.col[1] = y
    mat.col[2] = z
    return mat


def average_vector(vectors: Iterable[Vector]) -> Vector:
    total = Vector((0.0, 0.0, 0.0))
    count = 0
    for vec in vectors:
        total += vec
        count += 1
    if count == 0:
        return total
    return total / count
