"""Automatic validation of a retarget result.

Everything reported here is *measured*, never guessed: the source and target
rigs are sampled over the frame range and compared.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import bpy
from mathutils import Matrix, Quaternion, Vector

from ..core import bones as bone_utils
from ..core import mathx
from ..profiles.base import TargetProfile
from ..retarget import calibration as calib_mod
from ..retarget import rig as rig_mod
from ..retarget import sampling

LEVEL_ERROR = 'ERROR'
LEVEL_WARNING = 'WARNING'
LEVEL_INFO = 'INFO'

_LEVEL_ORDER = {LEVEL_ERROR: 0, LEVEL_WARNING: 1, LEVEL_INFO: 2}

# Thresholds, expressed in the units they are measured in.
FLIP_WARNING_DEG = 60.0
FLIP_ERROR_DEG = 120.0
FIDELITY_INFO_DEG = 2.0
FIDELITY_WARNING_DEG = 10.0
DRIFT_WARNING_RATIO = 0.02
DRIFT_ERROR_RATIO = 0.05


@dataclass
class Entry:
    level: str
    category: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.category}: {self.message}"


@dataclass
class Report:
    entries: List[Entry] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def add(self, level: str, category: str, message: str) -> None:
        self.entries.append(Entry(level, category, message))

    def error(self, category: str, message: str) -> None:
        self.add(LEVEL_ERROR, category, message)

    def warn(self, category: str, message: str) -> None:
        self.add(LEVEL_WARNING, category, message)

    def info(self, category: str, message: str) -> None:
        self.add(LEVEL_INFO, category, message)

    def counts(self) -> Dict[str, int]:
        out = {LEVEL_ERROR: 0, LEVEL_WARNING: 0, LEVEL_INFO: 0}
        for entry in self.entries:
            out[entry.level] = out.get(entry.level, 0) + 1
        return out

    def sorted_entries(self) -> List[Entry]:
        return sorted(self.entries, key=lambda e: _LEVEL_ORDER.get(e.level, 9))

    def as_text(self) -> str:
        counts = self.counts()
        header = (
            "Source VM Retargeter - validation report\n"
            f"{counts[LEVEL_ERROR]} error(s), {counts[LEVEL_WARNING]} warning(s), "
            f"{counts[LEVEL_INFO]} info\n"
        )
        body = "\n".join(str(e) for e in self.sorted_entries())
        metrics = "\n".join(f"  {k} = {v:.5g}" for k, v in sorted(self.metrics.items()))
        return f"{header}\n{body}\n\nMetrics:\n{metrics}\n"

    @property
    def ok(self) -> bool:
        return self.counts()[LEVEL_ERROR] == 0


# --------------------------------------------------------------------------
# static checks
# --------------------------------------------------------------------------

def check_mapping(settings, report: Report) -> None:
    source_obj = settings.source_armature
    target_obj = settings.target_armature
    if source_obj is None or target_obj is None:
        report.error("setup", "Source and/or Target armature is not set.")
        return

    src_bones = {b.name for b in source_obj.data.bones}
    tgt_bones = {b.name for b in target_obj.data.bones}
    missing_source: List[str] = []
    missing_target: List[str] = []
    unmapped: List[str] = []
    low_confidence: List[str] = []

    for item in settings.mapping:
        if not item.enabled:
            continue
        if not item.source_bone:
            unmapped.append(item.key)
            continue
        if item.source_bone not in src_bones:
            missing_source.append(f"{item.key} -> {item.source_bone}")
        if item.target_bone and item.target_bone not in tgt_bones:
            missing_target.append(f"{item.key} -> {item.target_bone}")
        if item.confidence < 0.7:
            low_confidence.append(item.key)

    if missing_source:
        report.error("mapping", f"Source bones not found: {', '.join(missing_source[:8])}"
                                + (" ..." if len(missing_source) > 8 else ""))
    if missing_target:
        report.error("mapping", f"Target bones not found: {', '.join(missing_target[:8])}"
                                + (" ..." if len(missing_target) > 8 else ""))
    if unmapped:
        report.warn("mapping", f"{len(unmapped)} canonical bone(s) have no source bone: "
                               f"{', '.join(unmapped[:8])}"
                               + (" ..." if len(unmapped) > 8 else ""))
    if low_confidence:
        report.info("mapping", f"{len(low_confidence)} mapping(s) came from heuristics - "
                               f"review: {', '.join(low_confidence[:8])}"
                               + (" ..." if len(low_confidence) > 8 else ""))

    action = rig_mod.active_source_action(settings)
    animated = set(bone_utils.animated_bone_names(source_obj, action))
    mapped_sources = {i.source_bone for i in settings.mapping if i.enabled and i.source_bone}
    stray = sorted(animated - mapped_sources)
    if stray:
        report.info("mapping", f"{len(stray)} animated source bone(s) are not retargeted "
                               f"(weapon/helper bones are expected here): "
                               f"{', '.join(stray[:8])}"
                               + (" ..." if len(stray) > 8 else ""))


def check_hierarchy(settings, profile: TargetProfile, report: Report) -> None:
    """The Unreal skeleton must keep its own hierarchy - verify we kept it."""
    target_obj = settings.target_armature
    if target_obj is None:
        return
    bones = target_obj.data.bones
    expected_parents = [
        ("hand_l", "lowerarm_l"), ("lowerarm_l", "upperarm_l"),
        ("hand_r", "lowerarm_r"), ("lowerarm_r", "upperarm_r"),
    ]
    for child_key, parent_key in expected_parents:
        child = profile.bone(child_key)
        parent = profile.bone(parent_key)
        if not child or not parent or child not in bones or parent not in bones:
            continue
        actual = bones[child].parent
        if actual is None or actual.name != parent:
            report.error(
                "hierarchy",
                f"'{child}' should be parented to '{parent}' but is parented to "
                f"'{actual.name if actual else 'None'}'.",
            )
    missing_ik = [n for n in profile.ik_bones.values() if n not in bones]
    if missing_ik and settings.drive_ue_ik_bones:
        report.warn("hierarchy", f"Unreal IK bones missing from the target rig: "
                                 f"{', '.join(sorted(missing_ik))}. Weapon attachment in "
                                 f"engine relies on ik_hand_gun.")


def check_frame_settings(context, settings, report: Report) -> None:
    start, end = rig_mod.effective_frame_range(settings)
    if end < start:
        report.error("frames", f"Frame range is inverted ({start} -> {end}).")
    elif end == start:
        report.warn("frames", f"Frame range covers a single frame ({start}).")
    else:
        report.info("frames", f"Frame range {start}-{end} ({end - start + 1} frames).")

    scene_fps = context.scene.render.fps / max(1.0, context.scene.render.fps_base)
    report.info("fps", f"Scene FPS is {scene_fps:.3f}.")
    if settings.use_bake_fps and abs(scene_fps - settings.bake_fps) > 1e-4:
        report.warn(
            "fps",
            f"Bake FPS is set to {settings.bake_fps} but the scene runs at "
            f"{scene_fps:.3f}; keys stay on the same frame numbers, so playback "
            f"speed will change on export.",
        )


def check_source_completeness(analysis, report: Report) -> None:
    if analysis is None:
        return
    if not analysis.has_legs:
        report.info(
            "source",
            "Source has no leg bones - lower-body motion cannot be recovered from a "
            "viewmodel and is left in the target's rest pose.",
        )
    if not analysis.has_spine:
        report.info("source", "Source has no spine chain; use PROCEDURAL_UPPER_BODY "
                              "to synthesise a plausible torso.")
    if len(analysis.hands) < 2:
        report.warn("source", f"Only {len(analysis.hands)} hand(s) detected on the source rig.")


# --------------------------------------------------------------------------
# sampled checks
# --------------------------------------------------------------------------

def _sample_pair(context, settings, pairs, frames):
    source_obj = settings.source_armature
    target_obj = settings.target_armature
    src_names = sorted({p.source_bone for p in pairs})
    tgt_names = sorted({p.target_bone for p in pairs})
    with sampling.preserved_frame(context):
        source = sampling.sample_world_matrices(context, source_obj, src_names, frames)
        target = sampling.sample_world_matrices(context, target_obj, tgt_names, frames)
    return source, target


def check_numerics(target_samples, report: Report) -> None:
    bad: List[str] = []
    for frame, bones in target_samples.items():
        for name, matrix in bones.items():
            if not mathx.is_finite_matrix(matrix):
                bad.append(f"{name}@{frame}")
    if bad:
        report.error("numerics", f"NaN/inf transforms on {len(bad)} sample(s): "
                                 f"{', '.join(bad[:6])}"
                                 + (" ..." if len(bad) > 6 else ""))
    else:
        report.info("numerics", "No NaN or infinite transforms found.")


def check_flips(target_samples, frames, report: Report) -> float:
    worst = 0.0
    worst_label = ""
    for previous, current in zip(frames, frames[1:]):
        for name, matrix in target_samples[current].items():
            before = target_samples[previous].get(name)
            if before is None:
                continue
            angle = math.degrees(mathx.angle_between_matrices(before, matrix))
            if angle > worst:
                worst, worst_label = angle, f"{name} ({previous}->{current})"
    if worst >= FLIP_ERROR_DEG:
        report.error("flips", f"Bone rotation jumps {worst:.1f} deg in one frame on "
                              f"{worst_label} - almost certainly a flip.")
    elif worst >= FLIP_WARNING_DEG:
        report.warn("flips", f"Largest single-frame rotation is {worst:.1f} deg on "
                             f"{worst_label}; verify it is intentional.")
    else:
        report.info("flips", f"Largest single-frame rotation is {worst:.1f} deg - no flips.")
    return worst


def check_fidelity(source_samples, target_samples, pairs, frames,
                   calibration, report: Report) -> float:
    """Compare the target against the analytic ``G @ P_source @ K`` result."""
    total, count, worst, worst_name = 0.0, 0, 0.0, ""
    for frame in frames:
        for pair in pairs:
            src = source_samples[frame].get(pair.source_bone)
            tgt = target_samples[frame].get(pair.target_bone)
            if src is None or tgt is None:
                continue
            expected = calib_mod.retarget_world_rotation(calibration, pair.key, src)
            angle = math.degrees(mathx.angle_between_matrices(expected.to_4x4(), tgt))
            total += angle
            count += 1
            if angle > worst:
                worst, worst_name = angle, pair.target_bone
    if not count:
        report.warn("fidelity", "No comparable samples - cannot measure retarget fidelity.")
        return 0.0
    mean = total / count
    if mean >= FIDELITY_WARNING_DEG:
        report.warn("fidelity", f"Mean orientation error {mean:.2f} deg (worst {worst:.2f} deg "
                                f"on {worst_name}). IK or procedural influence is pulling the "
                                f"pose away from the source.")
    elif mean >= FIDELITY_INFO_DEG:
        report.info("fidelity", f"Mean orientation error {mean:.2f} deg (worst {worst:.2f} deg "
                                f"on {worst_name}) - expected when two-hand IK is on.")
    else:
        report.info("fidelity", f"Mean orientation error {mean:.2f} deg - FK match is exact.")
    return mean


def _grip_marker(settings) -> Optional[str]:
    """The secondary grip marker bone, when it is set and actually exists."""
    name = settings.secondary_grip_bone
    source_obj = settings.source_armature
    if not (settings.two_hand_ik and settings.weapon_enabled and name and source_obj):
        return None
    return name if name in source_obj.data.bones else None


def check_hand_drift(source_samples, target_samples, pairs, frames, calibration,
                     report: Report, settings=None) -> Dict[str, float]:
    """Sliding check: the distance between the hands must track the source."""
    metrics: Dict[str, float] = {}
    by_key = {p.key: p for p in pairs}
    left, right = by_key.get("hand_l"), by_key.get("hand_r")
    if left is None or right is None:
        report.info("drift", "Both hands must be mapped to measure grip drift; skipped.")
        return metrics

    marker = _grip_marker(settings) if settings is not None else None
    if marker:
        report.info(
            "drift",
            f"Support hand is pinned to the grip marker '{marker}', so it "
            f"intentionally departs from the source hand; the hand-separation "
            f"check is skipped in favour of the grip check below.",
        )
        return metrics

    reference = None
    worst = 0.0
    for frame in frames:
        s_l = source_samples[frame].get(left.source_bone)
        s_r = source_samples[frame].get(right.source_bone)
        t_l = target_samples[frame].get(left.target_bone)
        t_r = target_samples[frame].get(right.target_bone)
        if None in (s_l, s_r, t_l, t_r):
            continue
        expected = (s_l.translation - s_r.translation).length * calibration.scale
        actual = (t_l.translation - t_r.translation).length
        if reference is None:
            reference = max(expected, 1.0e-6)
        worst = max(worst, abs(actual - expected))

    if reference is None:
        return metrics
    ratio = worst / reference
    metrics["hand_distance_drift"] = worst
    metrics["hand_distance_drift_ratio"] = ratio
    if ratio >= DRIFT_ERROR_RATIO:
        report.error("drift", f"Hand separation drifts by up to {ratio * 100:.1f}% of the "
                              f"source distance - the second hand slides on the weapon. "
                              f"Enable Two-Hand IK or check the mapping.")
    elif ratio >= DRIFT_WARNING_RATIO:
        report.warn("drift", f"Hand separation drifts by up to {ratio * 100:.1f}%; "
                             f"a small amount is normal when arm proportions differ.")
    else:
        report.info("drift", f"Hand separation drift {ratio * 100:.2f}% - grip is stable.")
    return metrics


def check_weapon_drift(context, settings, frames, report: Report,
                       anchor: Optional[bpy.types.Object]) -> Dict[str, float]:
    """The weapon must stay rigid relative to the primary hand."""
    metrics: Dict[str, float] = {}
    if anchor is None:
        return metrics
    target_obj = settings.target_armature
    primary = settings.primary_hand.lower()
    hand_bone = None
    for item in settings.mapping:
        if item.key == f"hand_{primary}":
            hand_bone = item.target_bone
            break
    if not hand_bone or hand_bone not in target_obj.pose.bones:
        return metrics

    relative: List[Matrix] = []
    with sampling.preserved_frame(context):
        for frame in frames:
            context.scene.frame_set(int(frame))
            depsgraph = context.evaluated_depsgraph_get()
            hand_world = (
                target_obj.evaluated_get(depsgraph).matrix_world
                @ target_obj.evaluated_get(depsgraph).pose.bones[hand_bone].matrix
            )
            anchor_world = anchor.evaluated_get(depsgraph).matrix_world
            relative.append(hand_world.inverted_safe() @ anchor_world)

    if len(relative) < 2:
        return metrics
    base = relative[0]
    worst_pos = max((m.translation - base.translation).length for m in relative)
    worst_rot = max(math.degrees(mathx.angle_between_matrices(base, m)) for m in relative)
    metrics["weapon_drift_location"] = worst_pos
    metrics["weapon_drift_rotation_deg"] = worst_rot

    if settings.weapon_follow_mode == 'HAND_RIGID':
        if worst_pos > 1.0e-4 or worst_rot > 0.1:
            report.warn("weapon", f"Weapon anchor is supposed to be rigid but moves "
                                  f"{worst_pos:.4g} units / {worst_rot:.2f} deg relative to "
                                  f"the primary hand.")
        else:
            report.info("weapon", "Weapon anchor is rigid relative to the primary hand.")
    else:
        report.info("weapon", f"Weapon moves {worst_pos:.4g} units / {worst_rot:.2f} deg "
                              f"relative to the primary hand (source-driven, expected).")
    return metrics


def check_secondary_grip_drift(context, settings, calibration, frames, report: Report,
                               anchor: Optional[bpy.types.Object]) -> Dict[str, float]:
    """Does the support hand stay on the weapon, frame by frame?

    Measured as the difference between the target hand-to-weapon distance and
    the same distance in the source, scaled into target units.
    """
    metrics: Dict[str, float] = {}
    if anchor is None:
        return metrics
    source_obj = settings.source_armature
    target_obj = settings.target_armature
    secondary = 'l' if settings.primary_hand == 'R' else 'r'

    pair = next((i for i in settings.mapping
                 if i.key == f"hand_{secondary}" and i.enabled and i.source_bone), None)
    primary = next((i for i in settings.mapping
                    if i.key == f"hand_{settings.primary_hand.lower()}" and i.source_bone),
                   None)
    if pair is None or primary is None:
        return metrics

    reference = settings.weapon_reference_bone or primary.source_bone
    if reference not in source_obj.data.bones:
        reference = primary.source_bone
    if pair.target_bone not in target_obj.pose.bones:
        return metrics

    # When a grip marker is configured the support hand is meant to sit on the
    # marker, not where the source hand was - so that is what we measure.
    marker = _grip_marker(settings)
    probe = marker or pair.source_bone
    label = f"the grip marker '{marker}'" if marker else "the weapon"

    arm_length = bone_utils.chain_length_world(
        target_obj,
        [i.target_bone for i in settings.mapping
         if i.key in (f"upperarm_{secondary}", f"lowerarm_{secondary}")],
    )
    if arm_length < 1.0e-9:
        return metrics

    worst = 0.0
    with sampling.preserved_frame(context):
        for frame in frames:
            context.scene.frame_set(int(frame))
            depsgraph = context.evaluated_depsgraph_get()
            source_eval = source_obj.evaluated_get(depsgraph)
            target_eval = target_obj.evaluated_get(depsgraph)
            expected = (
                (source_eval.matrix_world @ source_eval.pose.bones[probe].matrix).translation
                - (source_eval.matrix_world @ source_eval.pose.bones[reference].matrix).translation
            ).length * calibration.scale
            actual = (
                (target_eval.matrix_world @ target_eval.pose.bones[pair.target_bone].matrix).translation
                - anchor.evaluated_get(depsgraph).matrix_world.translation
            ).length
            worst = max(worst, abs(actual - expected))

    ratio = worst / arm_length
    metrics["secondary_grip_drift"] = worst
    metrics["secondary_grip_drift_ratio"] = ratio
    if ratio >= DRIFT_ERROR_RATIO:
        report.error("drift", f"The {secondary.upper()} (support) hand misses {label} by "
                              f"up to {ratio * 100:.1f}% of its arm length. The IK target "
                              f"is probably out of reach - move the grip, or check the "
                              f"unit scale and the weapon reference bone.")
    elif ratio >= DRIFT_WARNING_RATIO:
        report.warn("drift", f"The {secondary.upper()} (support) hand slides "
                             f"{ratio * 100:.1f}% against {label}.")
    else:
        report.info("drift", f"Support-hand grip on {label} is stable "
                             f"({ratio * 100:.2f}% slide).")
    return metrics


def check_scale(settings, calibration, report: Report) -> None:
    for obj, label in ((settings.source_armature, "Source"),
                       (settings.target_armature, "Target")):
        if obj is None:
            continue
        scale = obj.matrix_world.to_scale()
        if obj.matrix_world.determinant() < 0.0:
            report.warn("scale", f"{label} armature is mirrored (negative object scale); "
                                 f"rotations cannot be mirrored, so the retarget will "
                                 f"de-mirror them. Apply a real mirror instead.")
        spread = max(scale) - min(scale)
        if spread > 1.0e-4:
            report.warn("scale", f"{label} armature has non-uniform object scale "
                                 f"({scale.x:.4f}, {scale.y:.4f}, {scale.z:.4f}); "
                                 f"apply it before retargeting for predictable results.")
    if calibration is not None:
        report.info("scale", f"Source->target unit scale is {calibration.scale:.4f}.")
        if calibration.scale <= 0.0 or not math.isfinite(calibration.scale):
            report.error("scale", "Computed unit scale is invalid.")


def check_action_scale_keys(action, report: Report) -> None:
    if action is None:
        return
    offenders = set()
    for fcurve in action.fcurves:
        if not fcurve.data_path.endswith(".scale"):
            continue
        for point in fcurve.keyframe_points:
            if abs(point.co.y - 1.0) > 1.0e-3:
                offenders.add(fcurve.data_path)
                break
    if offenders:
        report.warn("scale", f"{len(offenders)} bone(s) carry non-unit scale keys; "
                             f"Unreal ignores bone scale on most import settings.")


def validate(context, settings, analysis=None, action=None,
             anchor: Optional[bpy.types.Object] = None) -> Report:
    """Run every check and return the report."""
    report = Report()
    profile = rig_mod.resolve_profile(settings)

    check_mapping(settings, report)
    check_hierarchy(settings, profile, report)
    check_frame_settings(context, settings, report)
    check_source_completeness(analysis, report)
    check_action_scale_keys(action, report)

    pairs = rig_mod.collect_pairs(settings)
    if not pairs:
        report.error("setup", "Nothing to validate - the bone mapping is empty.")
        return report

    start, end = rig_mod.effective_frame_range(settings)
    frames = sampling.frame_list(start, end, max(1, int(settings.validation_step)))
    calibration = rig_mod.compute_calibration(context, settings, pairs)
    check_scale(settings, calibration, report)

    source_samples, target_samples = _sample_pair(context, settings, pairs, frames)
    check_numerics(target_samples, report)
    report.metrics["max_frame_rotation_deg"] = check_flips(target_samples, frames, report)
    report.metrics["mean_orientation_error_deg"] = check_fidelity(
        source_samples, target_samples, pairs, frames, calibration, report
    )
    report.metrics.update(
        check_hand_drift(source_samples, target_samples, pairs, frames, calibration,
                         report, settings)
    )
    report.metrics.update(check_weapon_drift(context, settings, frames, report, anchor))
    report.metrics.update(
        check_secondary_grip_drift(context, settings, calibration, frames, report, anchor)
    )
    report.metrics["sampled_frames"] = float(len(frames))
    return report
