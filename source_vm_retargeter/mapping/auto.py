"""Automatic bone mapping.

Four strategies are tried per canonical key, strongest first:

1. **Exact** - a literal name from the known Source naming tables.
2. **Structural** - the arm chains and finger chains found by
   :mod:`..analysis.armature`.  Works on rigs whose names we have never seen.
3. **Normalised** - a table name after prefix/punctuation normalisation.
4. **Heuristic** - token-overlap similarity above a threshold.

Finger chains of a length the target does not share are converted by
proportional index mapping rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from mathutils import Vector

from ..analysis.armature import ArmatureAnalysis, HandInfo
from ..core import naming
from ..profiles import source_tables, targets
from ..profiles.base import FINGERS, TargetProfile, key_group

#: Minimum similarity for the heuristic fallback to accept a match.
HEURISTIC_THRESHOLD = 0.62

METHOD_STRUCTURAL = 'STRUCTURAL'
METHOD_EXACT = 'EXACT'
METHOD_NORMALIZED = 'NORMALIZED'
METHOD_HEURISTIC = 'HEURISTIC'
METHOD_CHAIN = 'CHAIN'
METHOD_MANUAL = 'MANUAL'
METHOD_PRESET = 'PRESET'


@dataclass
class MappingEntry:
    key: str
    source_bone: str
    target_bone: str
    method: str
    confidence: float

    @property
    def group(self) -> str:
        return key_group(self.key)


def _name_lookup(armature_obj) -> Dict[str, str]:
    return {naming.normalize(b.name): b.name for b in armature_obj.data.bones}


def _match_target(target_obj, profile: TargetProfile, key: str) -> Optional[str]:
    """Resolve a canonical key to a bone on the target armature."""
    bones = target_obj.data.bones
    direct = profile.bone(key)
    if direct and direct in bones:
        return direct
    lookup = _name_lookup(target_obj)
    for candidate in targets.TARGET_BONE_CANDIDATES.get(key, []):
        hit = lookup.get(naming.normalize(candidate))
        if hit:
            return hit
    return None


def _match_source_exact(source_obj, key: str) -> Optional[tuple]:
    """Literal hit in the known Source naming tables - the strongest signal."""
    for candidate in source_tables.candidates_for(key):
        if candidate in source_obj.data.bones:
            return candidate, METHOD_EXACT, 1.0
    return None


def _match_source_by_name(source_obj, key: str) -> Optional[tuple]:
    """``(bone_name, method, confidence)`` or ``None``."""
    bones = [b.name for b in source_obj.data.bones]
    lookup = _name_lookup(source_obj)

    for candidate in source_tables.candidates_for(key):
        hit = lookup.get(naming.normalize(candidate))
        if hit:
            return hit, METHOD_NORMALIZED, 0.92

    best, best_score = None, 0.0
    for name in bones:
        if any(hint in naming.normalize(name) for hint in source_tables.SOURCE_IGNORE_HINTS):
            continue
        score = naming.similarity(name, key)
        if score > best_score:
            best, best_score = name, score
    if best and best_score >= HEURISTIC_THRESHOLD:
        return best, METHOD_HEURISTIC, best_score
    return None


def _order_finger_roots(source_obj, hand: HandInfo) -> Dict[str, List[str]]:
    """Label each detected finger chain with a canonical finger name.

    Names win when they are informative.  Otherwise the thumb is identified as
    the chain pointing furthest away from the mean finger direction, and the
    remaining chains are ordered by their distance from it (index .. pinky).
    """
    labelled: Dict[str, List[str]] = {}
    unlabelled: List[str] = []

    for root in hand.finger_roots:
        info = naming.finger_info(root)
        if info and info[0] in FINGERS and info[0] not in labelled:
            labelled[info[0]] = hand.finger_chains.get(root, [root])
        else:
            unlabelled.append(root)

    if not unlabelled:
        return labelled

    data = source_obj.data
    heads = {r: data.bones[r].head_local.copy() for r in unlabelled}
    dirs = {}
    for root in unlabelled:
        bone = data.bones[root]
        vec = (bone.tail_local - bone.head_local)
        dirs[root] = vec.normalized() if vec.length > 1e-9 else Vector((0, 1, 0))

    mean_dir = Vector((0.0, 0.0, 0.0))
    for vec in dirs.values():
        mean_dir += vec
    if mean_dir.length > 1e-9:
        mean_dir.normalize()

    remaining = list(unlabelled)
    if "thumb" not in labelled and len(remaining) > 1:
        thumb = min(remaining, key=lambda r: dirs[r].dot(mean_dir))
        labelled["thumb"] = hand.finger_chains.get(thumb, [thumb])
        remaining.remove(thumb)
        anchor = heads[thumb]
    elif remaining:
        anchor = heads[remaining[0]]
    else:
        anchor = Vector((0.0, 0.0, 0.0))

    remaining.sort(key=lambda r: (heads[r] - anchor).length)
    order = [f for f in ("index", "middle", "ring", "pinky") if f not in labelled]
    for finger, root in zip(order, remaining):
        labelled[finger] = hand.finger_chains.get(root, [root])
    return labelled


def _chain_index(target_segment: int, source_len: int, target_len: int = 3) -> int:
    """Map a target finger segment onto a source chain of a different length."""
    if source_len <= 1:
        return 0
    if target_len <= 1:
        return source_len - 1
    raw = target_segment * (source_len - 1) / float(target_len - 1)
    return max(0, min(source_len - 1, int(raw + 0.5)))


def build_mapping(
    source_obj,
    target_obj,
    profile: TargetProfile,
    analysis: ArmatureAnalysis,
    body_mode: str = 'PROCEDURAL_UPPER_BODY',
    use_fingers: bool = True,
) -> List[MappingEntry]:
    """Produce a full mapping for every canonical key the mode requires."""
    entries: List[MappingEntry] = []
    keys = profile.keys_for_mode(body_mode, use_fingers)

    structural: Dict[str, tuple] = {}
    for side, hand in analysis.hands.items():
        chain = {
            f"clavicle_{side}": hand.clavicle,
            f"upperarm_{side}": hand.upperarm,
            f"lowerarm_{side}": hand.lowerarm,
            f"hand_{side}": hand.hand,
        }
        for key, bone in chain.items():
            if bone:
                structural[key] = (bone, METHOD_STRUCTURAL, 0.95)
        if use_fingers:
            for finger, source_chain in _order_finger_roots(source_obj, hand).items():
                for segment in range(3):
                    key = f"{finger}_{segment + 1:02d}_{side}"
                    idx = _chain_index(segment, len(source_chain))
                    method = METHOD_STRUCTURAL if len(source_chain) == 3 else METHOD_CHAIN
                    confidence = 0.95 if len(source_chain) == 3 else 0.7
                    structural[key] = (source_chain[idx], method, confidence)

    for key in keys:
        target_bone = _match_target(target_obj, profile, key)
        if not target_bone:
            continue
        found = (
            _match_source_exact(source_obj, key)
            or structural.get(key)
            or _match_source_by_name(source_obj, key)
        )
        if not found:
            entries.append(MappingEntry(key, "", target_bone, METHOD_HEURISTIC, 0.0))
            continue
        source_bone, method, confidence = found
        entries.append(MappingEntry(key, source_bone, target_bone, method, confidence))

    return entries


def mapping_stats(entries: Sequence[MappingEntry]) -> Dict[str, int]:
    stats = {"total": len(entries), "mapped": 0, "unmapped": 0, "low_confidence": 0}
    for entry in entries:
        if entry.source_bone:
            stats["mapped"] += 1
            if entry.confidence < 0.8:
                stats["low_confidence"] += 1
        else:
            stats["unmapped"] += 1
    return stats
