"""Synthetic rigs used by the test-suite.

No .blend file ships with the add-on, so the tests build both sides of the
problem from scratch:

* a **UE4 Mannequin-like** target in metres, A-pose, with twist and ik_* bones;
* a **Source viewmodel** in Hammer units, in a forward-facing viewmodel bind
  pose, rotated and translated in world space, with ValveBiped bone names,
  five-finger hands and a weapon sub-rig (clip / bolt / trigger).

The two rigs deliberately disagree about units, world orientation, bind pose,
bone rolls and proportions - which is exactly what the retargeter must absorb.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

FINGERS = ("thumb", "index", "middle", "ring", "pinky")


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------

def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 41


def _new_armature(name: str, matrix_world: Matrix) -> bpy.types.Object:
    data = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.matrix_world = matrix_world
    return obj


class _Builder:
    """Tiny edit-bone builder so the rig definitions stay readable."""

    def __init__(self, obj: bpy.types.Object) -> None:
        self.obj = obj
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        self.edit_bones = obj.data.edit_bones

    def add(self, name: str, parent: Optional[str], head: Vector, tail: Vector,
            roll: float = 0.0, connect: bool = False) -> str:
        bone = self.edit_bones.new(name)
        bone.head = head
        bone.tail = tail
        bone.roll = roll
        if parent:
            bone.parent = self.edit_bones[parent]
            bone.use_connect = connect
        return name

    def chain(self, names: Sequence[str], parent: Optional[str], start: Vector,
              direction: Vector, lengths: Sequence[float], roll: float = 0.0,
              connect: bool = True) -> List[str]:
        head = Vector(start)
        step = Vector(direction).normalized()
        previous = parent
        out: List[str] = []
        for name, length in zip(names, lengths):
            tail = head + step * length
            self.add(name, previous, head, tail, roll, connect and previous is not None)
            previous = name
            out.append(name)
            head = tail
        return out

    def finish(self) -> None:
        bpy.ops.object.mode_set(mode='OBJECT')


def _hand_fingers(builder: _Builder, hand: str, hand_tail: Vector, forward: Vector,
                  spread_axis: Vector, up_axis: Vector, side: str, scale: float,
                  naming: str) -> None:
    """Add five 3-segment fingers fanning out from the hand tail.

    ``naming`` is ``"ue"`` (``index_01_l``) or ``"valve"``
    (``ValveBiped.Bip01_L_Finger1``).
    """
    forward = Vector(forward).normalized()
    spread_axis = Vector(spread_axis).normalized()
    up_axis = Vector(up_axis).normalized()
    segment = 0.035 * scale

    for index, finger in enumerate(FINGERS):
        offset = (index - 2) * 0.022 * scale
        direction = (forward + spread_axis * (offset / (0.05 * scale)) * 0.18).normalized()
        start = hand_tail + spread_axis * offset
        if finger == "thumb":
            direction = (forward * 0.6 + spread_axis * 0.7 - up_axis * 0.2).normalized()
            start = hand_tail + spread_axis * offset - forward * 0.02 * scale

        if naming == "ue":
            names = [f"{finger}_{i:02d}_{side}" for i in (1, 2, 3)]
        else:
            upper = side.upper()
            valve = index
            names = [
                f"ValveBiped.Bip01_{upper}_Finger{valve}",
                f"ValveBiped.Bip01_{upper}_Finger{valve}1",
                f"ValveBiped.Bip01_{upper}_Finger{valve}2",
            ]
        lengths = [segment * 1.2, segment, segment * 0.8]
        builder.chain(names, hand, start, direction, lengths, connect=True)


# --------------------------------------------------------------------------
# UE4 Mannequin-like target
# --------------------------------------------------------------------------

def build_ue4_mannequin(name: str = "UE4_Mannequin") -> bpy.types.Object:
    """A metre-scale A-pose rig with UE4 bone names, twists and ik_* bones."""
    obj = _new_armature(name, Matrix.Identity(4))
    builder = _Builder(obj)

    builder.add("root", None, Vector((0, 0, 0)), Vector((0, 0.15, 0)))
    builder.add("pelvis", "root", Vector((0, 0, 0.97)), Vector((0, 0, 1.06)))
    spine = builder.chain(
        ["spine_01", "spine_02", "spine_03"], "pelvis",
        Vector((0, 0, 1.06)), Vector((0, -0.05, 1)), [0.11, 0.11, 0.12],
    )
    builder.chain(["neck_01", "head"], spine[-1], Vector((0, -0.017, 1.40)),
                  Vector((0, -0.04, 1)), [0.10, 0.18])

    for side, sign in (("l", 1.0), ("r", -1.0)):
        shoulder = Vector((sign * 0.02, 0, 1.37))
        clavicle_tail = Vector((sign * 0.17, 0, 1.39))
        builder.add(f"clavicle_{side}", spine[-1], shoulder, clavicle_tail,
                    roll=sign * 0.3)
        # A-pose: arms angled 45 degrees down and slightly forward.
        arm_dir = Vector((sign * 0.70, -0.08, -0.71)).normalized()
        arm = builder.chain(
            [f"upperarm_{side}", f"lowerarm_{side}", f"hand_{side}"],
            f"clavicle_{side}", clavicle_tail, arm_dir, [0.30, 0.27, 0.10],
            roll=sign * 0.45,
        )
        upper_head = clavicle_tail
        lower_head = upper_head + arm_dir * 0.30
        hand_head = lower_head + arm_dir * 0.27
        hand_tail = hand_head + arm_dir * 0.10

        builder.add(f"upperarm_twist_01_{side}", f"upperarm_{side}",
                    upper_head + arm_dir * 0.15, upper_head + arm_dir * 0.22)
        builder.add(f"lowerarm_twist_01_{side}", f"lowerarm_{side}",
                    lower_head + arm_dir * 0.13, lower_head + arm_dir * 0.20)

        spread = Vector((0, 1, 0)) if sign > 0 else Vector((0, -1, 0))
        _hand_fingers(builder, f"hand_{side}", hand_tail, arm_dir, spread,
                      Vector((0, 0, 1)), side, 1.0, "ue")

        leg_dir = Vector((0, 0, -1))
        builder.chain([f"thigh_{side}", f"calf_{side}", f"foot_{side}"], "pelvis",
                      Vector((sign * 0.09, 0, 0.95)), leg_dir, [0.45, 0.42, 0.10])
        builder.add(f"thigh_twist_01_{side}", f"thigh_{side}",
                    Vector((sign * 0.09, 0, 0.75)), Vector((sign * 0.09, 0, 0.70)))
        builder.add(f"calf_twist_01_{side}", f"calf_{side}",
                    Vector((sign * 0.09, 0, 0.35)), Vector((sign * 0.09, 0, 0.30)))
        builder.add(f"ball_{side}", f"foot_{side}", Vector((sign * 0.09, 0, 0.08)),
                    Vector((sign * 0.09, -0.12, 0.08)))

    # Unreal's non-deforming IK bones.
    builder.add("ik_hand_root", "root", Vector((0, 0, 0)), Vector((0, 0.1, 0)))
    builder.add("ik_hand_gun", "ik_hand_root", Vector((0, 0, 0)), Vector((0, 0.1, 0)))
    builder.add("ik_hand_l", "ik_hand_gun", Vector((0, 0, 0)), Vector((0, 0.1, 0)))
    builder.add("ik_hand_r", "ik_hand_gun", Vector((0, 0, 0)), Vector((0, 0.1, 0)))
    builder.add("ik_foot_root", "root", Vector((0, 0, 0)), Vector((0, 0.1, 0)))
    builder.add("ik_foot_l", "ik_foot_root", Vector((0.09, 0, 0.08)),
                Vector((0.09, 0.1, 0.08)))
    builder.add("ik_foot_r", "ik_foot_root", Vector((-0.09, 0, 0.08)),
                Vector((-0.09, 0.1, 0.08)))
    builder.finish()

    for bone in obj.data.bones:
        bone.use_deform = not bone.name.startswith("ik_")
    return obj


# --------------------------------------------------------------------------
# Source viewmodel
# --------------------------------------------------------------------------

#: Hammer units per metre-ish; the source rig is ~40x the target's scale.
SOURCE_SCALE = 40.0


def build_source_viewmodel(name: str = "SourceViewmodel",
                           with_weapon: bool = True) -> bpy.types.Object:
    """ValveBiped-named viewmodel rig: arms forward, elbows bent, weapon bones."""
    world = (
        Matrix.Translation((3.0, -2.0, 0.5))
        @ Euler((0.12, -0.08, 2.0), 'XYZ').to_matrix().to_4x4()
    )
    obj = _new_armature(name, world)
    builder = _Builder(obj)
    scale = SOURCE_SCALE

    builder.add("ValveBiped.Bip01", None, Vector((0, 0, 0)), Vector((0, 0.1 * scale, 0)))
    builder.add("ValveBiped.Bip01_Pelvis", "ValveBiped.Bip01",
                Vector((0, 0, 0.97 * scale)), Vector((0, 0, 1.06 * scale)))
    spine = builder.chain(
        ["ValveBiped.Bip01_Spine", "ValveBiped.Bip01_Spine1", "ValveBiped.Bip01_Spine2"],
        "ValveBiped.Bip01_Pelvis", Vector((0, 0, 1.06 * scale)),
        Vector((0, -0.05, 1)), [0.11 * scale, 0.11 * scale, 0.12 * scale],
    )
    builder.chain(["ValveBiped.Bip01_Neck1", "ValveBiped.Bip01_Head1"], spine[-1],
                  Vector((0, -0.017 * scale, 1.40 * scale)), Vector((0, -0.04, 1)),
                  [0.10 * scale, 0.18 * scale])

    hand_heads: Dict[str, Vector] = {}
    for side, sign in (("L", 1.0), ("R", -1.0)):
        shoulder = Vector((sign * 0.02 * scale, 0, 1.37 * scale))
        clavicle_tail = Vector((sign * 0.15 * scale, 0, 1.36 * scale))
        builder.add(f"ValveBiped.Bip01_{side}_Clavicle", spine[-1], shoulder,
                    clavicle_tail, roll=sign * 0.9)

        # Viewmodel bind pose: upper arm down-forward, forearm bent inward.
        upper_dir = Vector((sign * 0.45, -0.45, -0.77)).normalized()
        lower_dir = Vector((-sign * 0.25, -0.93, 0.27)).normalized()
        hand_dir = Vector((-sign * 0.20, -0.95, 0.24)).normalized()

        upper_head = clavicle_tail
        lower_head = upper_head + upper_dir * (0.28 * scale)
        hand_head = lower_head + lower_dir * (0.25 * scale)
        hand_tail = hand_head + hand_dir * (0.09 * scale)

        builder.add(f"ValveBiped.Bip01_{side}_UpperArm",
                    f"ValveBiped.Bip01_{side}_Clavicle", upper_head, lower_head,
                    roll=sign * 1.1)
        builder.add(f"ValveBiped.Bip01_{side}_Forearm",
                    f"ValveBiped.Bip01_{side}_UpperArm", lower_head, hand_head,
                    roll=sign * 0.6, connect=True)
        builder.add(f"ValveBiped.Bip01_{side}_Hand",
                    f"ValveBiped.Bip01_{side}_Forearm", hand_head, hand_tail,
                    roll=sign * -0.4, connect=True)

        hand_heads[side] = hand_head.copy()
        spread = Vector((0, 0, 1)) if sign > 0 else Vector((0, 0, -1))
        _hand_fingers(builder, f"ValveBiped.Bip01_{side}_Hand", hand_tail, hand_dir,
                      spread, Vector((sign, 0, 0)), side.lower(), scale, "valve")

    if with_weapon:
        gun_head = Vector((0, -0.45 * scale, 1.15 * scale))
        builder.add("v_weapon.Rifle_Parent", "ValveBiped.Bip01", gun_head,
                    gun_head + Vector((0, -0.30 * scale, 0)))
        builder.add("v_weapon.Clip", "v_weapon.Rifle_Parent",
                    gun_head + Vector((0, -0.05 * scale, -0.02 * scale)),
                    gun_head + Vector((0, -0.05 * scale, -0.12 * scale)))
        builder.add("v_weapon.Bolt", "v_weapon.Rifle_Parent",
                    gun_head + Vector((0.01 * scale, -0.10 * scale, 0.03 * scale)),
                    gun_head + Vector((0.01 * scale, -0.18 * scale, 0.03 * scale)))
        builder.add("v_weapon.Trigger", "v_weapon.Rifle_Parent",
                    gun_head + Vector((0, -0.02 * scale, -0.01 * scale)),
                    gun_head + Vector((0, -0.02 * scale, -0.05 * scale)))
        # Explicit grip markers, as many production viewmodels ship. They sit
        # where the hands actually hold the weapon, so they stay in reach.
        for grip_side in ("R", "L"):
            grip_head = hand_heads[grip_side]
            builder.add(f"v_weapon.Grip_{grip_side}", "v_weapon.Rifle_Parent",
                        grip_head, grip_head + Vector((0, -0.04 * scale, 0)))
        # A deliberately unreachable marker, used to prove validation catches it.
        far = gun_head + Vector((0, -0.9 * scale, 0.5 * scale))
        builder.add("v_weapon.Grip_Unreachable", "v_weapon.Rifle_Parent",
                    far, far + Vector((0, -0.04 * scale, 0)))
    builder.finish()
    return obj


# --------------------------------------------------------------------------
# animation
# --------------------------------------------------------------------------

def _key_quat(pose_bone, frame: int, quat: Quaternion) -> None:
    pose_bone.rotation_mode = 'QUATERNION'
    pose_bone.rotation_quaternion = quat
    pose_bone.keyframe_insert("rotation_quaternion", frame=frame)


def _key_loc(pose_bone, frame: int, location: Vector) -> None:
    pose_bone.location = location
    pose_bone.keyframe_insert("location", frame=frame)


def build_reload_action(source_obj: bpy.types.Object,
                        action_name: str = "vm_rifle_reload",
                        frames: Tuple[int, int] = (1, 41)) -> bpy.types.Action:
    """A reload-like clip: both arms move, fingers curl, the magazine drops."""
    if source_obj.animation_data is None:
        source_obj.animation_data_create()
    action = bpy.data.actions.new(action_name)
    source_obj.animation_data.action = action
    slots = list(getattr(source_obj.animation_data, "action_suitable_slots", []) or [])
    if slots and getattr(source_obj.animation_data, "action_slot", None) is None:
        source_obj.animation_data.action_slot = slots[0]

    start, end = frames
    middle = (start + end) // 2
    pose = source_obj.pose.bones

    poses = {
        "ValveBiped.Bip01_R_UpperArm": [
            (start, Quaternion()),
            (middle, Quaternion(Vector((1, 0.2, 0)).normalized(), math.radians(-22))),
            (end, Quaternion(Vector((1, 0, 0.3)).normalized(), math.radians(-6))),
        ],
        "ValveBiped.Bip01_R_Forearm": [
            (start, Quaternion()),
            (middle, Quaternion(Vector((0, 0, 1)), math.radians(30))),
            (end, Quaternion(Vector((0, 0, 1)), math.radians(8))),
        ],
        "ValveBiped.Bip01_R_Hand": [
            (start, Quaternion()),
            (middle, Quaternion(Vector((0, 1, 0)), math.radians(18))),
            (end, Quaternion()),
        ],
        "ValveBiped.Bip01_L_UpperArm": [
            (start, Quaternion()),
            (middle, Quaternion(Vector((1, -0.4, 0)).normalized(), math.radians(-40))),
            (end, Quaternion(Vector((1, 0, 0)), math.radians(-10))),
        ],
        "ValveBiped.Bip01_L_Forearm": [
            (start, Quaternion()),
            (middle, Quaternion(Vector((0, 0, 1)), math.radians(-45))),
            (end, Quaternion(Vector((0, 0, 1)), math.radians(-12))),
        ],
        "ValveBiped.Bip01_L_Hand": [
            (start, Quaternion()),
            (middle, Quaternion(Vector((1, 0, 0)), math.radians(35))),
            (end, Quaternion(Vector((1, 0, 0)), math.radians(5))),
        ],
    }
    for name, keys in poses.items():
        bone = pose.get(name)
        if bone is None:
            continue
        for frame, quat in keys:
            _key_quat(bone, frame, quat)

    # Finger curl on the left (support) hand.
    for index in range(5):
        for segment in ("", "1", "2"):
            name = f"ValveBiped.Bip01_L_Finger{index}{segment}"
            bone = pose.get(name)
            if bone is None:
                continue
            amount = math.radians(12 + 10 * len(segment))
            _key_quat(bone, start, Quaternion())
            _key_quat(bone, middle, Quaternion(Vector((0, 0, 1)), amount))
            _key_quat(bone, end, Quaternion(Vector((0, 0, 1)), amount * 0.3))

    # Weapon: magazine drops out and comes back, bolt cycles.
    clip = pose.get("v_weapon.Clip")
    if clip is not None:
        _key_loc(clip, start, Vector((0, 0, 0)))
        _key_loc(clip, middle, Vector((0, -0.18 * SOURCE_SCALE, 0)))
        _key_loc(clip, end, Vector((0, 0, 0)))
    bolt = pose.get("v_weapon.Bolt")
    if bolt is not None:
        _key_loc(bolt, start, Vector((0, 0, 0)))
        _key_loc(bolt, middle, Vector((0, 0.05 * SOURCE_SCALE, 0)))
        _key_loc(bolt, end, Vector((0, 0, 0)))

    for fcurve in action.fcurves:
        for point in fcurve.keyframe_points:
            point.interpolation = 'BEZIER'
    return action


def build_simple_action(source_obj: bpy.types.Object, action_name: str,
                        angle_deg: float = 25.0,
                        frames: Tuple[int, int] = (1, 21)) -> bpy.types.Action:
    """A short clip rotating only the right forearm - used for batch tests."""
    if source_obj.animation_data is None:
        source_obj.animation_data_create()
    action = bpy.data.actions.new(action_name)
    previous = source_obj.animation_data.action
    source_obj.animation_data.action = action
    slots = list(getattr(source_obj.animation_data, "action_suitable_slots", []) or [])
    if slots and getattr(source_obj.animation_data, "action_slot", None) is None:
        source_obj.animation_data.action_slot = slots[0]

    bone = source_obj.pose.bones.get("ValveBiped.Bip01_R_Forearm")
    if bone is not None:
        _key_quat(bone, frames[0], Quaternion())
        _key_quat(bone, frames[1], Quaternion(Vector((0, 0, 1)), math.radians(angle_deg)))
    source_obj.animation_data.action = previous
    return action


def build_scene(with_weapon: bool = True) -> Dict[str, object]:
    """Full test scene: target + source + reload Action, collections included."""
    reset_scene()
    ue_collection = bpy.data.collections.new("UE")
    source_collection = bpy.data.collections.new("Source")
    bpy.context.scene.collection.children.link(ue_collection)
    bpy.context.scene.collection.children.link(source_collection)

    target = build_ue4_mannequin()
    source = build_source_viewmodel(with_weapon=with_weapon)
    for obj, collection in ((target, ue_collection), (source, source_collection)):
        for existing in list(obj.users_collection):
            existing.objects.unlink(obj)
        collection.objects.link(obj)

    action = build_reload_action(source)
    bpy.context.scene.frame_set(1)
    return {"target": target, "source": source, "action": action,
            "ue_collection": ue_collection, "source_collection": source_collection}


def build_ue5_manny(name: str = "UE5_Manny") -> bpy.types.Object:
    """A Manny-like rig: UE4 layout plus spine_04/05, metacarpals, dual twists.

    UE4 and UE5 are deliberately *not* the same skeleton, so the test-suite
    needs a second target to prove the profile system keeps them apart.
    """
    obj = build_ue4_mannequin(name)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = obj.data.edit_bones

    spine_03 = edit_bones["spine_03"]
    head = spine_03.tail.copy()
    direction = (spine_03.tail - spine_03.head).normalized()
    previous = spine_03
    for index in (4, 5):
        bone = edit_bones.new(f"spine_0{index}")
        bone.head = head
        bone.tail = head + direction * 0.06
        bone.parent = previous
        bone.use_connect = True
        previous = bone
        head = bone.tail.copy()
    for child in ("neck_01", "clavicle_l", "clavicle_r"):
        edit_bones[child].parent = previous

    for side in ("l", "r"):
        for limb in ("upperarm", "lowerarm"):
            base = edit_bones[f"{limb}_{side}"]
            step = (base.tail - base.head)
            twist = edit_bones.new(f"{limb}_twist_02_{side}")
            twist.head = base.head + step * 0.6
            twist.tail = base.head + step * 0.8
            twist.parent = base
        clavicle = edit_bones[f"clavicle_{side}"]
        for extra in ("clavicle_out", "clavicle_scap"):
            bone = edit_bones.new(f"{extra}_{side}")
            bone.head = clavicle.tail.copy()
            bone.tail = clavicle.tail + (clavicle.tail - clavicle.head) * 0.3
            bone.parent = clavicle
        hand = edit_bones[f"hand_{side}"]
        for finger in ("index", "middle", "ring", "pinky"):
            first = edit_bones[f"{finger}_01_{side}"]
            meta = edit_bones.new(f"{finger}_metacarpal_{side}")
            meta.head = hand.head.copy()
            meta.tail = first.head.copy()
            meta.parent = hand
            first.parent = meta
    bpy.ops.object.mode_set(mode='OBJECT')

    for bone in obj.data.bones:
        bone.use_deform = not bone.name.startswith("ik_")
    return obj


def build_ue5_scene(with_weapon: bool = True) -> Dict[str, object]:
    """Same as :func:`build_scene` but with a UE5 Manny-like target."""
    reset_scene()
    target = build_ue5_manny()
    source = build_source_viewmodel(with_weapon=with_weapon)
    action = build_reload_action(source)
    bpy.context.scene.frame_set(1)
    return {"target": target, "source": source, "action": action}
