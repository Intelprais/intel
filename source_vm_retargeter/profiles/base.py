"""Canonical bone keys and the target-profile description type.

The add-on never talks about "the skeleton" directly.  Everything is keyed by
a *canonical key* (``hand_r``, ``index_02_l`` ...) and a :class:`TargetProfile`
translates those keys into the bone names of one concrete Unreal skeleton.
UE4 Mannequin and UE5 Manny are deliberately kept as separate profiles - they
differ in spine count, twist bones and metacarpals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

FINGERS: Tuple[str, ...] = ("thumb", "index", "middle", "ring", "pinky")
SIDES: Tuple[str, ...] = ("l", "r")


def finger_keys(side: Optional[str] = None) -> List[str]:
    """Canonical finger keys, optionally restricted to one side."""
    sides = (side,) if side else SIDES
    return [
        f"{finger}_{segment:02d}_{s}"
        for s in sides
        for finger in FINGERS
        for segment in (1, 2, 3)
    ]


def arm_keys(side: str) -> List[str]:
    return [f"clavicle_{side}", f"upperarm_{side}", f"lowerarm_{side}", f"hand_{side}"]


SPINE_KEYS: List[str] = ["spine_01", "spine_02", "spine_03"]
HEAD_KEYS: List[str] = ["neck_01", "head"]
ROOT_KEYS: List[str] = ["root", "pelvis"]

#: Keys that make up a complete upper body (arms + spine + head + fingers).
UPPER_BODY_KEYS: Set[str] = set(
    SPINE_KEYS + HEAD_KEYS + arm_keys("l") + arm_keys("r") + finger_keys()
)

#: Keys retargeted in ARMS_ONLY mode (no spine / clavicle / head).
ARM_ONLY_KEYS: Set[str] = set(
    [k for side in SIDES for k in arm_keys(side) if not k.startswith("clavicle")]
    + finger_keys()
)

#: Ordered list used for UI display and deterministic processing.
ALL_KEYS: List[str] = (
    ROOT_KEYS
    + SPINE_KEYS
    + HEAD_KEYS
    + arm_keys("l")
    + finger_keys("l")
    + arm_keys("r")
    + finger_keys("r")
)


def _chain_successor() -> Dict[str, str]:
    """The canonical key that continues each chain.

    Used to measure a bone's *limb direction* as the vector to the next joint,
    rather than trusting the bone's own +Y axis: Source (SMD) and Unreal (FBX)
    rigs both keep their engine-native bone orientation, so +Y is typically
    perpendicular to the limb and differs between the two rigs.
    """
    successor: Dict[str, str] = {
        "pelvis": "spine_01",
        "spine_01": "spine_02",
        "spine_02": "spine_03",
        "spine_03": "neck_01",
        "neck_01": "head",
    }
    for side in SIDES:
        successor[f"clavicle_{side}"] = f"upperarm_{side}"
        successor[f"upperarm_{side}"] = f"lowerarm_{side}"
        successor[f"lowerarm_{side}"] = f"hand_{side}"
        successor[f"hand_{side}"] = f"middle_01_{side}"
        for finger in FINGERS:
            successor[f"{finger}_01_{side}"] = f"{finger}_02_{side}"
            successor[f"{finger}_02_{side}"] = f"{finger}_03_{side}"
    return successor


#: canonical key -> next key along the same chain
CHAIN_SUCCESSOR: Dict[str, str] = _chain_successor()

#: canonical key -> previous key along the same chain
CHAIN_PREDECESSOR: Dict[str, str] = {v: k for k, v in CHAIN_SUCCESSOR.items()}


def key_group(key: str) -> str:
    """Coarse UI/processing group for a canonical key."""
    if key in ROOT_KEYS:
        return 'ROOT'
    if key in SPINE_KEYS:
        return 'SPINE'
    if key in HEAD_KEYS:
        return 'HEAD'
    side = key.rsplit("_", 1)[-1]
    is_finger = any(key.startswith(f"{f}_") for f in FINGERS)
    if is_finger:
        return 'FINGERS_L' if side == "l" else 'FINGERS_R'
    return 'ARM_L' if side == "l" else 'ARM_R'


def key_side(key: str) -> Optional[str]:
    """``"l"``/``"r"`` for sided keys, else ``None``."""
    if key.endswith("_l"):
        return "l"
    if key.endswith("_r"):
        return "r"
    return None


@dataclass
class TargetProfile:
    """One concrete Unreal target skeleton."""

    identifier: str
    label: str
    description: str
    #: canonical key -> bone name on this skeleton
    bones: Dict[str, str]
    #: root/reference bone of the skeleton
    root_bone: str
    #: full spine chain bottom-to-top (may be longer than SPINE_KEYS)
    spine_chain: List[str]
    #: non-deforming IK bones, ``{logical: bone_name}``
    ik_bones: Dict[str, str] = field(default_factory=dict)
    #: twist bones that must never receive retargeted animation directly
    twist_bones: List[str] = field(default_factory=list)
    #: bones whose presence identifies this skeleton
    signature_bones: List[str] = field(default_factory=list)
    #: bones that must NOT be present (used to disambiguate UE4 from UE5)
    anti_signature_bones: List[str] = field(default_factory=list)
    #: short tag appended to retargeted Action names
    action_suffix: str = "UE"
    #: lower-body bones - never retargeted, only keyed at rest in
    #: NEUTRAL_FULL_BODY mode so the exported clip is full-body
    leg_bones: List[str] = field(default_factory=list)

    def bone(self, key: str) -> Optional[str]:
        return self.bones.get(key)

    def keys_for_mode(self, body_mode: str, fingers: bool) -> List[str]:
        """Canonical keys to retarget for a given body mode."""
        if body_mode == 'ARMS_ONLY':
            allowed = set(ARM_ONLY_KEYS)
        else:
            allowed = set(UPPER_BODY_KEYS)
        if not fingers:
            allowed -= set(finger_keys())
        return [k for k in ALL_KEYS if k in allowed and k in self.bones]

    def score(self, bone_names: Set[str]) -> float:
        """0..1 confidence that ``bone_names`` belongs to this profile."""
        if not self.signature_bones:
            return 0.0
        lowered = {n.lower() for n in bone_names}
        hits = sum(1 for b in self.signature_bones if b.lower() in lowered)
        score = hits / float(len(self.signature_bones))
        if self.anti_signature_bones:
            penalty = sum(1 for b in self.anti_signature_bones if b.lower() in lowered)
            score -= penalty / float(len(self.anti_signature_bones))
        return max(0.0, min(1.0, score))

    def deform_bones(self) -> Set[str]:
        """Every bone that should carry animation on export."""
        out: Set[str] = set(self.bones.values())
        out.update(self.spine_chain)
        out.update(self.twist_bones)
        out.add(self.root_bone)
        return out


def _ue_finger_bones(prefix_metacarpal: bool = False) -> Dict[str, str]:
    """UE finger bone names follow the canonical keys exactly."""
    return {key: key for key in finger_keys()}
