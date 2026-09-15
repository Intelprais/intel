"""Known Source-engine bone naming tables.

These are *hints* for automatic mapping, never a hard requirement: the auto
mapper falls back to normalised and heuristic matching so rigs from games not
listed here still map.  Keys are the canonical keys from :mod:`.base`.
"""

from __future__ import annotations

from typing import Dict, List

#: Canonical key -> candidate Source bone names (most specific first).
DEFAULT_BONE_MAP: Dict[str, List[str]] = {
    "pelvis": ["ValveBiped.Bip01_Pelvis", "bip_pelvis", "root_pelvis", "pelvis"],
    "spine_01": ["ValveBiped.Bip01_Spine", "bip_spine_0", "spine_01", "spine"],
    "spine_02": ["ValveBiped.Bip01_Spine1", "bip_spine_1", "spine_02", "spine1"],
    "spine_03": ["ValveBiped.Bip01_Spine2", "ValveBiped.Bip01_Spine4", "bip_spine_2", "spine_03", "spine2"],
    "neck_01": ["ValveBiped.Bip01_Neck1", "bip_neck", "neck_01", "neck"],
    "head": ["ValveBiped.Bip01_Head1", "bip_head", "head", "head_01"],
    "clavicle_l": ["ValveBiped.Bip01_L_Clavicle", "bip_collar_l", "clavicle_l", "shoulder_l"],
    "upperarm_l": ["ValveBiped.Bip01_L_UpperArm", "bip_upperarm_l", "upperarm_l", "arm_l_upper"],
    "lowerarm_l": ["ValveBiped.Bip01_L_Forearm", "bip_lowerarm_l", "bip_forearm_l", "lowerarm_l", "forearm_l"],
    "hand_l": ["ValveBiped.Bip01_L_Hand", "bip_hand_l", "hand_l", "wrist_l"],
    "clavicle_r": ["ValveBiped.Bip01_R_Clavicle", "bip_collar_r", "clavicle_r", "shoulder_r"],
    "upperarm_r": ["ValveBiped.Bip01_R_UpperArm", "bip_upperarm_r", "upperarm_r", "arm_r_upper"],
    "lowerarm_r": ["ValveBiped.Bip01_R_Forearm", "bip_lowerarm_r", "bip_forearm_r", "lowerarm_r", "forearm_r"],
    "hand_r": ["ValveBiped.Bip01_R_Hand", "bip_hand_r", "hand_r", "wrist_r"],
}


def _add_fingers() -> None:
    """Populate the finger entries for both hands.

    Valve packs the finger index and the segment into one number
    (``Finger0``/``Finger01``/``Finger02`` is the thumb chain), while most
    other rigs spell the finger out.
    """
    fingers = (("thumb", 0), ("index", 1), ("middle", 2), ("ring", 3), ("pinky", 4))
    for side_upper, side in (("L", "l"), ("R", "r")):
        for finger, valve_index in fingers:
            for segment in (1, 2, 3):
                key = f"{finger}_{segment:02d}_{side}"
                valve_suffix = "" if segment == 1 else str(segment - 1)
                DEFAULT_BONE_MAP[key] = [
                    f"ValveBiped.Bip01_{side_upper}_Finger{valve_index}{valve_suffix}",
                    f"finger_{finger}_{segment - 1}_{side}",
                    f"{finger}_{segment:02d}_{side}",
                    f"{finger}{segment - 1}_{side}",
                    f"{side}_{finger}{segment}",
                    f"{finger}_{segment}_{side}",
                ]


_add_fingers()


#: Names that usually indicate the animation root of a Source viewmodel.
SOURCE_ROOT_HINTS = (
    "valvebiped.bip01",
    "bip01",
    "root",
    "v_root",
    "vm_root",
    "reference",
)

#: Substrings that identify weapon bones inside a viewmodel skeleton.
WEAPON_BONE_HINTS = (
    "weapon",
    "gun",
    "rifle",
    "pistol",
    "smg",
    "shotgun",
    "v_weapon",
    "wpn",
)

#: Sub-part hints, ``logical role -> substrings``.
WEAPON_PART_HINTS: Dict[str, tuple] = {
    "magazine": ("magazine", "mag", "clip", "ammo"),
    "bolt": ("bolt", "breech"),
    "slide": ("slide",),
    "trigger": ("trigger",),
    "charging_handle": ("charging", "charge_handle", "handle", "lever", "pump"),
    "hammer": ("hammer",),
    "cylinder": ("cylinder", "drum"),
    "attachment": ("attach", "muzzle", "sight", "scope", "laser", "flash"),
}

#: Substrings marking a hand-grip point on a weapon.
WEAPON_GRIP_HINTS = ("grip", "handguard", "foregrip", "handle_hold", "hold")

#: Bones that are helpers in Source rigs and should never be retargeted.
SOURCE_IGNORE_HINTS = (
    "attachment",
    "helper",
    "ik_",
    "proc_",
    "static_prop",
    "camera",
    "muzzle",
    "shell",
    "eject",
)


def candidates_for(key: str) -> List[str]:
    return DEFAULT_BONE_MAP.get(key, [])


def all_known_source_names() -> List[str]:
    out: List[str] = []
    for names in DEFAULT_BONE_MAP.values():
        out.extend(names)
    return out
