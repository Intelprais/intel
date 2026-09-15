"""Built-in Unreal target profiles."""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import SIDES, TargetProfile, arm_keys, finger_keys


def _common_bones(spine_top: str) -> Dict[str, str]:
    """Canonical -> bone name for the parts UE4 and UE5 share."""
    bones: Dict[str, str] = {
        "root": "root",
        "pelvis": "pelvis",
        "spine_01": "spine_01",
        "spine_02": "spine_02",
        "spine_03": spine_top,
        "neck_01": "neck_01",
        "head": "head",
    }
    for side in SIDES:
        for key in arm_keys(side):
            bones[key] = key
    for key in finger_keys():
        bones[key] = key
    return bones


UE4_MANNEQUIN = TargetProfile(
    identifier='UE4_MANNEQUIN',
    label="UE4 Mannequin",
    description="Epic's UE4 SK_Mannequin skeleton (spine_01..03, single twist bones)",
    bones=_common_bones("spine_03"),
    root_bone="root",
    spine_chain=["pelvis", "spine_01", "spine_02", "spine_03"],
    ik_bones={
        "hand_root": "ik_hand_root",
        "hand_gun": "ik_hand_gun",
        "hand_l": "ik_hand_l",
        "hand_r": "ik_hand_r",
        "foot_root": "ik_foot_root",
        "foot_l": "ik_foot_l",
        "foot_r": "ik_foot_r",
    },
    twist_bones=[
        "upperarm_twist_01_l", "upperarm_twist_01_r",
        "lowerarm_twist_01_l", "lowerarm_twist_01_r",
        "thigh_twist_01_l", "thigh_twist_01_r",
        "calf_twist_01_l", "calf_twist_01_r",
    ],
    signature_bones=[
        "root", "pelvis", "spine_01", "spine_03", "clavicle_l", "upperarm_l",
        "lowerarm_l", "hand_l", "ik_hand_gun", "upperarm_twist_01_l",
    ],
    anti_signature_bones=["spine_04", "spine_05", "index_metacarpal_l", "clavicle_out_l"],
    action_suffix="UE4Mannequin",
    leg_bones=[
        "thigh_l", "calf_l", "foot_l", "ball_l",
        "thigh_r", "calf_r", "foot_r", "ball_r",
    ],
)


def _ue5_bones() -> Dict[str, str]:
    bones = _common_bones("spine_03")
    return bones


UE5_MANNY = TargetProfile(
    identifier='UE5_MANNY',
    label="UE5 Manny / Quinn",
    description="UE5 SKM_Manny skeleton (spine_01..05, metacarpals, dual twist bones)",
    bones=_ue5_bones(),
    root_bone="root",
    spine_chain=["pelvis", "spine_01", "spine_02", "spine_03", "spine_04", "spine_05"],
    ik_bones={
        "hand_root": "ik_hand_root",
        "hand_gun": "ik_hand_gun",
        "hand_l": "ik_hand_l",
        "hand_r": "ik_hand_r",
        "foot_root": "ik_foot_root",
        "foot_l": "ik_foot_l",
        "foot_r": "ik_foot_r",
    },
    twist_bones=[
        "upperarm_twist_01_l", "upperarm_twist_02_l",
        "upperarm_twist_01_r", "upperarm_twist_02_r",
        "lowerarm_twist_01_l", "lowerarm_twist_02_l",
        "lowerarm_twist_01_r", "lowerarm_twist_02_r",
        "thigh_twist_01_l", "thigh_twist_02_l",
        "thigh_twist_01_r", "thigh_twist_02_r",
        "calf_twist_01_l", "calf_twist_02_l",
        "calf_twist_01_r", "calf_twist_02_r",
        "clavicle_out_l", "clavicle_out_r",
        "clavicle_scap_l", "clavicle_scap_r",
        "index_metacarpal_l", "middle_metacarpal_l", "ring_metacarpal_l", "pinky_metacarpal_l",
        "index_metacarpal_r", "middle_metacarpal_r", "ring_metacarpal_r", "pinky_metacarpal_r",
    ],
    signature_bones=[
        "root", "pelvis", "spine_04", "spine_05", "clavicle_l", "upperarm_l",
        "hand_l", "ik_hand_gun", "index_metacarpal_l", "upperarm_twist_02_l",
    ],
    anti_signature_bones=[],
    action_suffix="Manny",
    leg_bones=[
        "thigh_l", "calf_l", "foot_l", "ball_l",
        "thigh_r", "calf_r", "foot_r", "ball_r",
    ],
)

#: Registry of every built-in target profile.
PROFILES: List[TargetProfile] = [UE4_MANNEQUIN, UE5_MANNY]

PROFILES_BY_ID: Dict[str, TargetProfile] = {p.identifier: p for p in PROFILES}


def get_profile(identifier: str) -> TargetProfile:
    """Look up a profile, defaulting to UE4 Mannequin."""
    return PROFILES_BY_ID.get(identifier, UE4_MANNEQUIN)


def enum_items() -> List[tuple]:
    items = [('AUTO', "Auto Detect", "Detect the Unreal skeleton from its bone names")]
    items.extend((p.identifier, p.label, p.description) for p in PROFILES)
    return items


def detect_profile(armature_obj) -> Optional[TargetProfile]:
    """Best-matching profile for an armature, or ``None`` when unclear."""
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return None
    names = {bone.name for bone in armature_obj.data.bones}
    best: Optional[TargetProfile] = None
    best_score = 0.0
    for profile in PROFILES:
        score = profile.score(names)
        if score > best_score:
            best_score, best = score, profile
    return best if best_score >= 0.5 else None


def detect_profile_scores(armature_obj) -> Dict[str, float]:
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return {}
    names = {bone.name for bone in armature_obj.data.bones}
    return {p.identifier: p.score(names) for p in PROFILES}


#: Fallback candidates when the target rig is a *renamed* Unreal skeleton.
TARGET_BONE_CANDIDATES: Dict[str, List[str]] = {
    "root": ["root", "reference", "armature"],
    "pelvis": ["pelvis", "root_pelvis", "hips"],
    "spine_01": ["spine_01", "spine1", "spine"],
    "spine_02": ["spine_02", "spine2"],
    "spine_03": ["spine_03", "spine3", "chest"],
    "neck_01": ["neck_01", "neck1", "neck"],
    "head": ["head", "head_01"],
}

for _side in SIDES:
    TARGET_BONE_CANDIDATES[f"clavicle_{_side}"] = [f"clavicle_{_side}", f"shoulder_{_side}"]
    TARGET_BONE_CANDIDATES[f"upperarm_{_side}"] = [
        f"upperarm_{_side}", f"upperarm_{_side}_01", f"arm_{_side}_upper"
    ]
    TARGET_BONE_CANDIDATES[f"lowerarm_{_side}"] = [
        f"lowerarm_{_side}", f"forearm_{_side}", f"arm_{_side}_lower"
    ]
    TARGET_BONE_CANDIDATES[f"hand_{_side}"] = [f"hand_{_side}", f"wrist_{_side}"]
    for _finger in ("thumb", "index", "middle", "ring", "pinky"):
        for _seg in (1, 2, 3):
            TARGET_BONE_CANDIDATES[f"{_finger}_{_seg:02d}_{_side}"] = [
                f"{_finger}_{_seg:02d}_{_side}",
                f"{_finger}{_seg}_{_side}",
                f"{_finger}_{_seg}_{_side}",
                f"{_side}_{_finger}{_seg}",
            ]
