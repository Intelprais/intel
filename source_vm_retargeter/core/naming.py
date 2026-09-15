"""Bone-name normalisation, side detection and fuzzy matching.

Different Source games ship wildly different bone names
(``ValveBiped.Bip01_R_Hand``, ``bip_hand_R``, ``v_hand_r``, ``Hand.R`` ...),
so every comparison goes through :func:`normalize` first.
"""

from __future__ import annotations

import difflib
import re
from typing import List, Optional, Set

#: Prefixes stripped before comparison (longest first).
STRIP_PREFIXES = (
    "valvebiped.bip01",
    "valvebiped.",
    "bip001",
    "bip01",
    "bip_",
    "bip",
    "vm_",
    "v_",
    "def_",
    "def-",
    "ctrl_",
    "mixamorig:",
    "root_",
)

_SEPARATORS = re.compile(r"[\s\.\-:/\\]+")
_MULTI_US = re.compile(r"_+")
_DIGIT_GROUP = re.compile(r"(\d+)")

LEFT_TOKENS = {"l", "lt", "left", "lft"}
RIGHT_TOKENS = {"r", "rt", "right", "rgt"}

SIDE_LEFT = "L"
SIDE_RIGHT = "R"

#: Canonical finger names in the order UE uses them.
FINGER_NAMES = ("thumb", "index", "middle", "ring", "pinky")

#: Alternative spellings seen in Source rigs.
FINGER_ALIASES = {
    "finger0": "thumb",
    "finger1": "index",
    "finger2": "middle",
    "finger3": "ring",
    "finger4": "pinky",
    "little": "pinky",
    "pink": "pinky",
}


def _strip_prefix(name: str) -> str:
    lowered = name.lower()
    for prefix in STRIP_PREFIXES:
        if lowered.startswith(prefix):
            return lowered[len(prefix):]
    return lowered


def normalize(name: str) -> str:
    """Lower-case, punctuation-free, prefix-free form of a bone name.

    ``"ValveBiped.Bip01_R_UpperArm"`` -> ``"r_upperarm"``.
    """
    text = _strip_prefix(name)
    text = _SEPARATORS.sub("_", text)
    text = _MULTI_US.sub("_", text).strip("_")
    return text


def tokens(name: str) -> List[str]:
    """Split a normalised name into comparable tokens.

    Digits are separated from letters so ``spine2`` and ``spine_02`` produce
    the same token stream.
    """
    norm = normalize(name)
    out: List[str] = []
    for chunk in norm.split("_"):
        if not chunk:
            continue
        for part in _DIGIT_GROUP.split(chunk):
            if not part:
                continue
            if part.isdigit():
                out.append(str(int(part)))
            else:
                out.append(part)
    return out


def detect_side(name: str) -> Optional[str]:
    """Return ``"L"``, ``"R"`` or ``None`` for a bone name."""
    toks = tokens(name)
    for tok in toks:
        if tok in LEFT_TOKENS:
            return SIDE_LEFT
        if tok in RIGHT_TOKENS:
            return SIDE_RIGHT
    # Fall back to glued suffixes such as "handl" / "handr".
    norm = normalize(name)
    for suffix, side in (("left", SIDE_LEFT), ("right", SIDE_RIGHT)):
        if norm.endswith(suffix) or norm.startswith(suffix):
            return side
    return None


def strip_side(name: str) -> str:
    """Normalised name with side tokens removed."""
    return "_".join(
        tok for tok in tokens(name)
        if tok not in LEFT_TOKENS and tok not in RIGHT_TOKENS
    )


def canonical_tokens(name: str) -> Set[str]:
    """Token set with finger aliases resolved, used for heuristic matching."""
    result: Set[str] = set()
    for tok in tokens(name):
        result.add(FINGER_ALIASES.get(tok, tok))
    return result


def similarity(a: str, b: str) -> float:
    """Heuristic 0..1 similarity between two bone names.

    Combines token overlap (which survives reordering such as ``r_hand`` vs
    ``hand_r``) with a sequence ratio on the side-stripped names.
    """
    ta = canonical_tokens(a)
    tb = canonical_tokens(b)
    if not ta or not tb:
        return 0.0

    side_a = detect_side(a)
    side_b = detect_side(b)
    if side_a and side_b and side_a != side_b:
        return 0.0

    overlap = len(ta & tb) / float(len(ta | tb))
    ratio = difflib.SequenceMatcher(None, strip_side(a), strip_side(b)).ratio()
    score = 0.6 * overlap + 0.4 * ratio
    if side_a and side_b and side_a == side_b:
        score = min(1.0, score + 0.05)
    return score


def finger_info(name: str) -> Optional[tuple]:
    """Identify a finger bone.

    Returns ``(finger_name, segment_index, side)`` or ``None``.  Handles both
    the UE style (``index_02_l``) and the Valve style (``L_Finger11``) where
    the first digit selects the finger and the rest the segment.
    """
    side = detect_side(name)
    toks = tokens(name)
    resolved = [FINGER_ALIASES.get(t, t) for t in toks]

    for idx, tok in enumerate(resolved):
        if tok in FINGER_NAMES:
            segment = None
            for later in resolved[idx + 1:]:
                if later.isdigit():
                    segment = int(later)
                    break
            if segment is None:
                segment = 1
            # UE numbers finger segments from 1, some rigs from 0.
            return tok, max(0, segment - 1) if segment > 0 else 0, side

    # Valve style: "finger" followed by a packed digit group, e.g. Finger12.
    norm = normalize(name)
    match = re.search(r"finger(\d+)", norm)
    if match:
        digits = match.group(1)
        finger_idx = int(digits[0])
        segment = int(digits[1:]) if len(digits) > 1 else 0
        if 0 <= finger_idx < len(FINGER_NAMES):
            return FINGER_NAMES[finger_idx], segment, side
    return None
