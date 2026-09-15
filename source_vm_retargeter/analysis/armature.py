"""Automatic analysis of a Source viewmodel armature.

The analysis is purely structural + name based and makes no changes to the
scene.  It is used to drive auto-mapping, to pick sensible defaults in the UI
and to warn the user about things the retargeter cannot invent (missing
legs, missing spine, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from ..core import bones as bone_utils
from ..core import naming
from ..profiles import source_tables
from ..profiles.base import FINGERS


@dataclass
class HandInfo:
    """A structurally detected hand and the arm chain leading to it."""

    hand: str
    lowerarm: Optional[str] = None
    upperarm: Optional[str] = None
    clavicle: Optional[str] = None
    side: Optional[str] = None
    finger_roots: List[str] = field(default_factory=list)
    finger_chains: Dict[str, List[str]] = field(default_factory=dict)

    def arm_chain(self) -> List[str]:
        return [b for b in (self.clavicle, self.upperarm, self.lowerarm, self.hand) if b]


@dataclass
class ArmatureAnalysis:
    """Everything the add-on knows about one armature."""

    name: str
    bone_count: int = 0
    root_bones: List[str] = field(default_factory=list)
    animated_bones: List[str] = field(default_factory=list)
    action_names: List[str] = field(default_factory=list)
    hands: Dict[str, HandInfo] = field(default_factory=dict)
    weapon_bones: List[str] = field(default_factory=list)
    weapon_parts: Dict[str, str] = field(default_factory=dict)
    weapon_root: Optional[str] = None
    likely_root: Optional[str] = None
    has_pelvis: bool = False
    has_spine: bool = False
    has_legs: bool = False
    has_fingers: bool = False
    arm_span: float = 0.0
    notes: List[str] = field(default_factory=list)

    def summary_lines(self) -> List[str]:
        lines = [
            f"{self.name}: {self.bone_count} bones, "
            f"{len(self.animated_bones)} animated, {len(self.action_names)} action(s)",
        ]
        for side in ("l", "r"):
            hand = self.hands.get(side)
            if hand:
                lines.append(
                    f"  {side.upper()} arm: {' -> '.join(hand.arm_chain())}"
                    f" ({len(hand.finger_roots)} finger chains)"
                )
            else:
                lines.append(f"  {side.upper()} arm: not detected")
        if self.weapon_bones:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(self.weapon_parts.items()))
            lines.append(
                f"  Weapon: root={self.weapon_root}, {len(self.weapon_bones)} bone(s)"
                + (f" [{parts}]" if parts else "")
            )
        else:
            lines.append("  Weapon: not detected")
        lines.extend(f"  ! {note}" for note in self.notes)
        return lines


#: A finger is a short, non-branching chain that ends the hierarchy.
MIN_FINGER_BONES = 2
MAX_FINGER_BONES = 4


def _finger_chain(armature_obj, bone_name: str) -> Optional[List[str]]:
    """Return the chain starting at ``bone_name`` if it looks like a finger.

    A finger never branches and always terminates, which is what separates a
    real hand from bones such as ``Spine2`` that merely happen to have three
    children (neck + two clavicles).
    """
    chain = [bone_name]
    bone = armature_obj.data.bones.get(bone_name)
    while bone is not None:
        children = list(bone.children)
        if not children:
            break
        if len(children) > 1:
            return None
        bone = children[0]
        chain.append(bone.name)
        if len(chain) > MAX_FINGER_BONES:
            return None
    if len(chain) < MIN_FINGER_BONES:
        return None
    return chain


def _detect_hands(armature_obj) -> Dict[str, HandInfo]:
    """Find hands structurally, then label their side.

    A hand is a bone with at least three child chains (fingers).  This works
    even for rigs whose bones are named in a language or scheme we have never
    seen.  Name matching is used only as a fallback.
    """
    data = armature_obj.data
    candidates: List[HandInfo] = []

    for bone in data.bones:
        chains = {}
        for child in bone.children:
            chain = _finger_chain(armature_obj, child.name)
            if chain is not None:
                chains[child.name] = chain
        if len(chains) < 3:
            continue
        info = HandInfo(hand=bone.name, finger_roots=list(chains))
        info.finger_chains = chains
        info.lowerarm = bone.parent.name if bone.parent else None
        if bone.parent and bone.parent.parent:
            info.upperarm = bone.parent.parent.name
            if bone.parent.parent.parent:
                info.clavicle = bone.parent.parent.parent.name
        candidates.append(info)

    if not candidates:
        # Name-based fallback: look for bones mapping to hand_l / hand_r.
        for key, side in (("hand_l", "l"), ("hand_r", "r")):
            match = _best_named_match(armature_obj, key)
            if match:
                bone = data.bones[match]
                info = HandInfo(hand=match, side=side)
                info.lowerarm = bone.parent.name if bone.parent else None
                if bone.parent and bone.parent.parent:
                    info.upperarm = bone.parent.parent.name
                    if bone.parent.parent.parent:
                        info.clavicle = bone.parent.parent.parent.name
                candidates.append(info)

    # Names win over geometry; a rig whose bones are named tells us the truth.
    for info in candidates:
        if info.side is None:
            info.side = _side_from_names(info)
    named = sum(1 for info in candidates if info.side)
    if named < len(candidates):
        _assign_sides_geometrically(armature_obj, candidates)

    # Prefer the candidate with the most finger chains on each side, so a
    # partially-finger-less proxy bone never wins over the real hand.
    hands: Dict[str, HandInfo] = {}
    for info in sorted(candidates, key=lambda h: -len(h.finger_roots)):
        if info.side and info.side not in hands:
            hands[info.side] = info
    return hands


def _side_from_names(info: HandInfo) -> Optional[str]:
    """Side taken from any bone name in the arm chain."""
    for name in (info.hand, info.lowerarm, info.upperarm, info.clavicle):
        if not name:
            continue
        side = naming.detect_side(name)
        if side:
            return side.lower()
    return None


def _assign_sides_geometrically(armature_obj, candidates: List[HandInfo]) -> None:
    """Label unnamed hands by comparing them against each other.

    Blender rigs imported from Source or Unreal put the character's left at
    +X, so the right-most candidate of a pair is the left hand.
    """
    unlabelled = [info for info in candidates if not info.side]
    if not unlabelled:
        return
    used = {info.side for info in candidates if info.side}
    bones = armature_obj.data.bones
    unlabelled.sort(key=lambda info: bones[info.hand].head_local.x, reverse=True)
    for info, side in zip(unlabelled, [s for s in ("l", "r") if s not in used]):
        info.side = side


def _best_named_match(armature_obj, key: str) -> Optional[str]:
    names = [b.name for b in armature_obj.data.bones]
    lookup = {naming.normalize(n): n for n in names}
    for candidate in source_tables.candidates_for(key):
        hit = lookup.get(naming.normalize(candidate))
        if hit:
            return hit
    best, best_score = None, 0.0
    for name in names:
        score = naming.similarity(name, key)
        if score > best_score:
            best, best_score = name, score
    return best if best_score >= 0.65 else None


def _detect_weapon(armature_obj, hands: Dict[str, HandInfo]) -> tuple:
    """Weapon bones = hinted names plus anything outside the body subtrees."""
    data = armature_obj.data
    body: Set[str] = set()
    for info in hands.values():
        for name in info.arm_chain():
            if name:
                body.add(name)
                body.update(bone_utils.descendants(armature_obj, name))
        # Walk up from the topmost arm bone: spine / pelvis / root are body too.
        top = info.arm_chain()[0] if info.arm_chain() else None
        bone = data.bones.get(top) if top else None
        while bone is not None:
            body.add(bone.name)
            bone = bone.parent

    hinted = [
        b.name for b in data.bones
        if any(hint in naming.normalize(b.name) for hint in source_tables.WEAPON_BONE_HINTS)
    ]
    weapon: Set[str] = set(hinted)
    for name in list(hinted):
        weapon.update(bone_utils.descendants(armature_obj, name))
    weapon -= body

    if not weapon:
        # Nothing hinted: treat non-body root subtrees as weapon candidates.
        for bone in data.bones:
            if bone.parent is None and bone.name not in body:
                subtree = bone_utils.descendants(armature_obj, bone.name, include_self=True)
                if not set(subtree) & body:
                    weapon.update(subtree)

    weapon_root: Optional[str] = None
    if weapon:
        ordered = [n for n in bone_utils.hierarchy_order(armature_obj, weapon)]
        weapon_root = ordered[0] if ordered else None

    parts: Dict[str, str] = {}
    for role, hints in source_tables.WEAPON_PART_HINTS.items():
        for name in sorted(weapon):
            norm = naming.normalize(name)
            if any(hint in norm for hint in hints):
                parts[role] = name
                break
    return sorted(weapon), weapon_root, parts


def analyze(armature_obj, action=None) -> ArmatureAnalysis:
    """Inspect an armature without modifying it."""
    result = ArmatureAnalysis(name=armature_obj.name)
    data = armature_obj.data
    result.bone_count = len(data.bones)
    result.root_bones = [b.name for b in data.bones if b.parent is None]
    result.animated_bones = bone_utils.animated_bone_names(armature_obj, action)

    anim = armature_obj.animation_data
    if anim:
        if anim.action:
            result.action_names.append(anim.action.name)
        for track in anim.nla_tracks:
            for strip in track.strips:
                if strip.action and strip.action.name not in result.action_names:
                    result.action_names.append(strip.action.name)

    result.hands = _detect_hands(armature_obj)
    result.has_fingers = any(h.finger_roots for h in result.hands.values())

    normalized = {naming.normalize(b.name) for b in data.bones}
    result.has_pelvis = any("pelvis" in n or "hips" in n for n in normalized)
    result.has_spine = any("spine" in n for n in normalized)
    result.has_legs = any(
        any(tok in n for tok in ("thigh", "calf", "foot", "leg", "knee", "ankle"))
        for n in normalized
    )

    result.weapon_bones, result.weapon_root, result.weapon_parts = _detect_weapon(
        armature_obj, result.hands
    )

    for name in result.root_bones:
        if any(hint in naming.normalize(name) for hint in source_tables.SOURCE_ROOT_HINTS):
            result.likely_root = name
            break
    if result.likely_root is None and result.root_bones:
        result.likely_root = result.root_bones[0]

    spans = []
    for info in result.hands.values():
        chain = [b for b in (info.upperarm, info.lowerarm) if b]
        if chain:
            spans.append(bone_utils.chain_length_world(armature_obj, chain))
    result.arm_span = max(spans) if spans else 0.0

    if not result.has_legs:
        result.notes.append(
            "No leg bones found - this is a viewmodel; lower-body motion cannot be recovered."
        )
    if not result.has_spine:
        result.notes.append("No spine chain found - PROCEDURAL_UPPER_BODY will synthesise one.")
    if len(result.hands) < 2:
        result.notes.append(
            f"Only {len(result.hands)} hand(s) detected - check the bone mapping manually."
        )
    if not result.animated_bones:
        result.notes.append("No animated bones found in the selected Action.")
    return result
