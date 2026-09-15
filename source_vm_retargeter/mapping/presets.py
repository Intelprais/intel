"""Saving and loading bone-mapping presets as JSON.

Presets are stored per canonical key so the same preset works for any rig
whose bones carry those names; they also carry the calibration offsets so a
user only has to align a given Source rig once.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import bpy

PRESET_DIRNAME = "source_vm_retargeter_presets"
PRESET_EXT = ".json"
FORMAT_VERSION = 1


def preset_dir(create: bool = False) -> str:
    base = bpy.utils.user_resource('CONFIG', path=PRESET_DIRNAME, create=create)
    return base


def list_presets() -> List[str]:
    directory = preset_dir(create=False)
    if not directory or not os.path.isdir(directory):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(directory)
        if f.endswith(PRESET_EXT)
    )


def preset_path(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip()
    if not safe:
        safe = "preset"
    return os.path.join(preset_dir(create=True), safe + PRESET_EXT)


def serialize(settings) -> Dict[str, Any]:
    """Build a JSON-ready dict from the add-on settings."""
    entries = []
    for item in settings.mapping:
        entries.append({
            "key": item.key,
            "source_bone": item.source_bone,
            "target_bone": item.target_bone,
            "enabled": bool(item.enabled),
            "influence": round(float(item.influence), 6),
            "method": item.method,
            "confidence": round(float(item.confidence), 4),
            "calibration_quat": [round(float(v), 8) for v in item.calibration_quat],
            "has_calibration": bool(item.has_calibration),
            "manual_offset": [round(float(v), 8) for v in item.manual_offset],
        })
    return {
        "format_version": FORMAT_VERSION,
        "target_profile": settings.target_profile,
        "body_mode": settings.body_mode,
        "use_fingers": bool(settings.use_fingers),
        "calibration_mode": settings.calibration_mode,
        "global_align_mode": settings.global_align_mode,
        "global_align_euler": [float(v) for v in settings.global_align_euler],
        "scale_mode": settings.scale_mode,
        "scale_factor": float(settings.scale_factor),
        "primary_hand": settings.primary_hand,
        "weapon": {
            "enabled": bool(settings.weapon_enabled),
            "source_reference_bone": settings.weapon_reference_bone,
            "primary_grip_bone": settings.primary_grip_bone,
            "secondary_grip_bone": settings.secondary_grip_bone,
            "follow_mode": settings.weapon_follow_mode,
        },
        "two_hand_ik": bool(settings.two_hand_ik),
        "ik_blend": float(settings.ik_blend),
        "procedural": {
            "clavicle_influence": float(settings.clavicle_influence),
            "spine_influence": float(settings.spine_influence),
            "chest_influence": float(settings.chest_influence),
            "shoulder_influence": float(settings.shoulder_influence),
            "max_spine_rotation": float(settings.max_spine_rotation),
        },
        "root_motion": settings.root_motion,
        "entries": entries,
    }


def save(settings, name: str) -> str:
    path = preset_path(name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(serialize(settings), handle, indent=2, sort_keys=False)
    return path


def load_data(name_or_path: str) -> Dict[str, Any]:
    path = name_or_path
    if not os.path.isfile(path):
        path = preset_path(name_or_path)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    version = data.get("format_version")
    if version != FORMAT_VERSION:
        raise ValueError(
            f"Unsupported preset format version {version!r} (expected {FORMAT_VERSION})"
        )
    return data


def apply(settings, data: Dict[str, Any], source_obj=None, target_obj=None) -> Dict[str, int]:
    """Write a loaded preset into the settings.

    Entries whose bones are missing on the current rigs are kept but disabled,
    so the user can see exactly what did not resolve instead of silently
    losing the mapping.
    """
    stats = {"applied": 0, "missing_source": 0, "missing_target": 0}

    for attr in ("target_profile", "body_mode", "calibration_mode",
                 "global_align_mode", "scale_mode", "primary_hand", "root_motion"):
        if attr in data:
            try:
                setattr(settings, attr, data[attr])
            except TypeError:
                pass
    if "use_fingers" in data:
        settings.use_fingers = bool(data["use_fingers"])
    if "scale_factor" in data:
        settings.scale_factor = float(data["scale_factor"])
    if "global_align_euler" in data:
        settings.global_align_euler = tuple(data["global_align_euler"])
    if "two_hand_ik" in data:
        settings.two_hand_ik = bool(data["two_hand_ik"])
    if "ik_blend" in data:
        settings.ik_blend = float(data["ik_blend"])

    weapon = data.get("weapon", {})
    if weapon:
        settings.weapon_enabled = bool(weapon.get("enabled", False))
        settings.weapon_reference_bone = weapon.get("source_reference_bone", "")
        settings.primary_grip_bone = weapon.get("primary_grip_bone", "")
        settings.secondary_grip_bone = weapon.get("secondary_grip_bone", "")
        if weapon.get("follow_mode"):
            try:
                settings.weapon_follow_mode = weapon["follow_mode"]
            except TypeError:
                pass

    proc = data.get("procedural", {})
    for attr in ("clavicle_influence", "spine_influence", "chest_influence",
                 "shoulder_influence", "max_spine_rotation"):
        if attr in proc:
            setattr(settings, attr, float(proc[attr]))

    source_bones = {b.name for b in source_obj.data.bones} if source_obj else None
    target_bones = {b.name for b in target_obj.data.bones} if target_obj else None

    settings.mapping.clear()
    for raw in data.get("entries", []):
        item = settings.mapping.add()
        item.key = raw.get("key", "")
        item.source_bone = raw.get("source_bone", "")
        item.target_bone = raw.get("target_bone", "")
        item.enabled = bool(raw.get("enabled", True))
        item.influence = float(raw.get("influence", 1.0))
        item.method = raw.get("method", "PRESET")
        item.confidence = float(raw.get("confidence", 1.0))
        quat = raw.get("calibration_quat")
        if quat and len(quat) == 4:
            item.calibration_quat = tuple(quat)
        item.has_calibration = bool(raw.get("has_calibration", False))
        offset = raw.get("manual_offset")
        if offset and len(offset) == 3:
            item.manual_offset = tuple(offset)

        if source_bones is not None and item.source_bone and item.source_bone not in source_bones:
            item.enabled = False
            stats["missing_source"] += 1
        elif target_bones is not None and item.target_bone and item.target_bone not in target_bones:
            item.enabled = False
            stats["missing_target"] += 1
        else:
            stats["applied"] += 1
    return stats
