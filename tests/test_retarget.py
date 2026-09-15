"""End-to-end tests: mapping -> calibration -> retarget -> bake -> export."""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from typing import Dict, List, Sequence

import bpy
from mathutils import Matrix, Quaternion, Vector

import fixtures
from source_vm_retargeter.analysis import armature as analysis_mod
from source_vm_retargeter.core import mathx
from source_vm_retargeter.core import scene as scene_utils
from source_vm_retargeter.exporting import unreal
from source_vm_retargeter.mapping import presets as preset_mod
from source_vm_retargeter.profiles import targets as target_profiles
from source_vm_retargeter.retarget import bake as bake_mod
from source_vm_retargeter.retarget import calibration as calib_mod
from source_vm_retargeter.retarget import rig as rig_mod
from source_vm_retargeter.retarget import sampling
from source_vm_retargeter.validation import checks

TEST_FRAMES = (1, 11, 21, 31, 41)
#: Target arm length in metres, used to express drift as a ratio.
TARGET_ARM_LENGTH = 0.57


def settings_for(scene, **overrides):
    settings = bpy.context.scene.svmr
    settings.source_armature = scene["source"]
    settings.target_armature = scene["target"]
    bpy.ops.svmr.auto_map()
    settings.weapon_enabled = True
    settings.weapon_reference_bone = "v_weapon.Rifle_Parent"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def world_matrices(obj, names: Sequence[str], frames: Sequence[int]) -> Dict[int, Dict[str, Matrix]]:
    with sampling.preserved_frame(bpy.context):
        return sampling.sample_world_matrices(bpy.context, obj, list(names), list(frames))


def angle_between_deg(a: Matrix, b: Matrix) -> float:
    return math.degrees(mathx.angle_between_matrices(a, b))


class RetargetTestCase(unittest.TestCase):
    """Base class giving every test a fresh scene."""

    with_weapon = True

    def setUp(self):
        self.scene = fixtures.build_scene(with_weapon=self.with_weapon)
        self.source = self.scene["source"]
        self.target = self.scene["target"]
        self.action = self.scene["action"]

    def tearDown(self):
        settings = bpy.context.scene.svmr
        try:
            rig_mod.teardown(bpy.context, settings)
        except (RuntimeError, ReferenceError):
            pass


# --------------------------------------------------------------------------
# analysis and mapping
# --------------------------------------------------------------------------

class TestAnalysis(RetargetTestCase):
    def test_detects_both_hands_with_five_fingers(self):
        analysis = analysis_mod.analyze(self.source, self.action)
        self.assertEqual(set(analysis.hands), {"l", "r"})
        for side, hand in analysis.hands.items():
            self.assertEqual(hand.hand, f"ValveBiped.Bip01_{side.upper()}_Hand")
            self.assertEqual(len(hand.finger_roots), 5,
                             f"{side} hand should expose five finger chains")
            self.assertEqual(hand.upperarm, f"ValveBiped.Bip01_{side.upper()}_UpperArm")
            self.assertEqual(hand.clavicle, f"ValveBiped.Bip01_{side.upper()}_Clavicle")

    def test_spine_is_not_mistaken_for_a_hand(self):
        analysis = analysis_mod.analyze(self.source, self.action)
        hand_names = {h.hand for h in analysis.hands.values()}
        self.assertNotIn("ValveBiped.Bip01_Spine2", hand_names)

    def test_detects_weapon_and_its_parts(self):
        analysis = analysis_mod.analyze(self.source, self.action)
        self.assertEqual(analysis.weapon_root, "v_weapon.Rifle_Parent")
        self.assertEqual(analysis.weapon_parts.get("magazine"), "v_weapon.Clip")
        self.assertEqual(analysis.weapon_parts.get("bolt"), "v_weapon.Bolt")
        self.assertEqual(analysis.weapon_parts.get("trigger"), "v_weapon.Trigger")
        for bone in analysis.weapon_bones:
            self.assertNotIn("Bip01_L", bone)
            self.assertNotIn("Bip01_R", bone)

    def test_reports_that_legs_are_missing(self):
        analysis = analysis_mod.analyze(self.source, self.action)
        self.assertFalse(analysis.has_legs)
        self.assertTrue(any("viewmodel" in note for note in analysis.notes))

    def test_target_profile_detection(self):
        scores = target_profiles.detect_profile_scores(self.target)
        self.assertEqual(target_profiles.detect_profile(self.target),
                         target_profiles.UE4_MANNEQUIN)
        self.assertGreater(scores['UE4_MANNEQUIN'], scores['UE5_MANNY'])


class TestAutoMapping(RetargetTestCase):
    def test_every_key_maps_with_high_confidence(self):
        settings = settings_for(self.scene)
        self.assertEqual(len(settings.mapping), 43)
        unmapped = [i.key for i in settings.mapping if not i.source_bone]
        self.assertEqual(unmapped, [], "every canonical key should find a source bone")
        weak = [i.key for i in settings.mapping if i.confidence < 0.9]
        self.assertEqual(weak, [], "no key should rely on a weak heuristic here")

    def test_specific_pairs(self):
        settings = settings_for(self.scene)
        expected = {
            "hand_r": "ValveBiped.Bip01_R_Hand",
            "upperarm_l": "ValveBiped.Bip01_L_UpperArm",
            "lowerarm_r": "ValveBiped.Bip01_R_Forearm",
            "clavicle_l": "ValveBiped.Bip01_L_Clavicle",
            "spine_02": "ValveBiped.Bip01_Spine1",
            "thumb_03_l": "ValveBiped.Bip01_L_Finger02",
            "pinky_01_r": "ValveBiped.Bip01_R_Finger4",
        }
        actual = {i.key: i.source_bone for i in settings.mapping}
        for key, bone in expected.items():
            self.assertEqual(actual.get(key), bone, f"wrong source bone for {key}")

    def test_no_left_right_crossover(self):
        settings = settings_for(self.scene)
        for item in settings.mapping:
            if item.key.endswith("_l"):
                self.assertIn("_L_", item.source_bone, f"{item.key} mapped to a right bone")
            elif item.key.endswith("_r"):
                self.assertIn("_R_", item.source_bone, f"{item.key} mapped to a left bone")

    def test_fingers_can_be_switched_off(self):
        settings = settings_for(self.scene, use_fingers=False)
        bpy.ops.svmr.auto_map()
        keys = {i.key for i in settings.mapping}
        self.assertNotIn("index_02_l", keys)
        self.assertIn("hand_l", keys)


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

class TestCalibration(RetargetTestCase):
    def test_scale_matches_the_fixture_proportions(self):
        settings = settings_for(self.scene)
        pairs = rig_mod.collect_pairs(settings)
        calibration = rig_mod.compute_calibration(bpy.context, settings, pairs)
        expected = 0.57 / ((0.28 + 0.25) * fixtures.SOURCE_SCALE)
        self.assertAlmostEqual(calibration.scale, expected, places=4)

    def test_global_alignment_cancels_the_source_world_rotation(self):
        settings = settings_for(self.scene)
        pairs = rig_mod.collect_pairs(settings)
        calibration = rig_mod.compute_calibration(bpy.context, settings, pairs)
        # The source object is rotated ~115 degrees; G must undo it so that the
        # shoulder axis of both rigs points the same way.
        source_axis = (
            self.source.matrix_world @ self.source.data.bones[
                "ValveBiped.Bip01_R_UpperArm"].head_local
            - self.source.matrix_world @ self.source.data.bones[
                "ValveBiped.Bip01_L_UpperArm"].head_local
        ).normalized()
        target_axis = (
            self.target.data.bones["upperarm_r"].head_local
            - self.target.data.bones["upperarm_l"].head_local
        ).normalized()
        aligned = calibration.global_rotation @ source_axis
        self.assertGreater(aligned.dot(target_axis), 0.999)

    def test_auto_align_points_target_bones_along_the_source(self):
        settings = settings_for(self.scene, calibration_mode='AUTO_ALIGN')
        pairs = rig_mod.collect_pairs(settings)
        calibration = rig_mod.compute_calibration(bpy.context, settings, pairs)
        for pair in pairs:
            source_dir = Vector(calibration.source_calibration[pair.key].col[1]).normalized()
            calib_dir = Vector(
                calibration.target_calibration[pair.key].to_matrix().col[1]
            ).normalized()
            self.assertGreater(
                source_dir.dot(calib_dir), 0.999,
                f"{pair.key}: retarget pose does not follow the source bone direction",
            )

    def test_rest_to_rest_keeps_the_target_rest_orientation(self):
        settings = settings_for(self.scene, calibration_mode='REST_TO_REST')
        pairs = rig_mod.collect_pairs(settings)
        calibration = rig_mod.compute_calibration(bpy.context, settings, pairs)
        for pair in pairs:
            rest = mathx.orthonormalize(
                self.target.matrix_world
                @ self.target.data.bones[pair.target_bone].matrix_local
            )
            self.assertLess(
                angle_between_deg(rest.to_4x4(),
                                  calibration.target_calibration[pair.key].to_matrix().to_4x4()),
                1.0e-3,
            )


# --------------------------------------------------------------------------
# the retarget itself
# --------------------------------------------------------------------------

class TestRetargetMath(RetargetTestCase):
    def _build_fk_only(self):
        settings = settings_for(
            self.scene, two_hand_ik=False, weapon_follow_mode='NONE',
            drive_ue_ik_bones=False,
        )
        result = rig_mod.build(bpy.context, settings)
        self.assertIsNotNone(result.driver_obj)
        return settings, result

    def test_target_delta_equals_source_delta_in_target_space(self):
        """The core property: P_t = G P_s K  =>  delta_t == G delta_s G^-1."""
        settings, result = self._build_fk_only()
        calibration = result.calibration
        pairs = result.pairs
        source = world_matrices(self.source, [p.source_bone for p in pairs], TEST_FRAMES)
        target = world_matrices(self.target, [p.target_bone for p in pairs], TEST_FRAMES)

        worst = 0.0
        for frame in TEST_FRAMES:
            for pair in pairs:
                source_aligned = (calibration.global_rotation
                                  @ mathx.orthonormalize(source[frame][pair.source_bone]))
                delta_source = source_aligned @ calib_mod.inv_rot(
                    calibration.source_calibration[pair.key])
                delta_target = (
                    mathx.orthonormalize(target[frame][pair.target_bone])
                    @ calib_mod.inv_rot(
                        calibration.target_calibration[pair.key].to_matrix())
                )
                worst = max(worst,
                            angle_between_deg(delta_source.to_4x4(), delta_target.to_4x4()))
        self.assertLess(worst, 0.25, f"worst orientation error {worst:.4f} deg")

    def test_rotation_magnitude_is_preserved(self):
        """A 30 degree source bend must stay a 30 degree target bend."""
        settings, result = self._build_fk_only()
        calibration = result.calibration
        pair = next(p for p in result.pairs if p.key == "lowerarm_r")
        source = world_matrices(self.source, [pair.source_bone], TEST_FRAMES)
        target = world_matrices(self.target, [pair.target_bone], TEST_FRAMES)

        for frame in TEST_FRAMES:
            source_aligned = (calibration.global_rotation
                              @ mathx.orthonormalize(source[frame][pair.source_bone]))
            delta_source = source_aligned @ calib_mod.inv_rot(
                calibration.source_calibration[pair.key])
            delta_target = (
                mathx.orthonormalize(target[frame][pair.target_bone])
                @ calib_mod.inv_rot(calibration.target_calibration[pair.key].to_matrix())
            )
            self.assertAlmostEqual(
                math.degrees(delta_source.to_quaternion().angle),
                math.degrees(delta_target.to_quaternion().angle),
                places=2,
                msg=f"rotation magnitude changed at frame {frame}",
            )

    def test_no_bone_flips(self):
        settings, result = self._build_fk_only()
        frames = list(range(1, 42))
        names = [p.target_bone for p in result.pairs]
        samples = world_matrices(self.target, names, frames)
        worst, worst_name = 0.0, ""
        for previous, current in zip(frames, frames[1:]):
            for name in names:
                angle = angle_between_deg(samples[previous][name], samples[current][name])
                if angle > worst:
                    worst, worst_name = angle, name
        self.assertLess(worst, 45.0, f"{worst_name} jumps {worst:.1f} deg in one frame")

    def test_fingers_actually_move(self):
        settings, result = self._build_fk_only()
        names = [f"index_{i:02d}_l" for i in (1, 2, 3)]
        samples = world_matrices(self.target, names, (1, 21))
        moved = max(angle_between_deg(samples[1][n], samples[21][n]) for n in names)
        self.assertGreater(moved, 5.0, "finger curl was not transferred")

    def test_target_hierarchy_is_untouched(self):
        before = {b.name: (b.parent.name if b.parent else None)
                  for b in self.target.data.bones}
        before_rest = {b.name: b.matrix_local.copy() for b in self.target.data.bones}
        self._build_fk_only()
        after = {b.name: (b.parent.name if b.parent else None)
                 for b in self.target.data.bones}
        self.assertEqual(before, after, "the Unreal skeleton hierarchy changed")
        for name, matrix in before_rest.items():
            self.assertLess(
                max(abs(a - b) for r1, r2 in zip(matrix, self.target.data.bones[name].matrix_local)
                    for a, b in zip(r1, r2)),
                1.0e-6,
                f"rest pose of {name} changed",
            )


class TestTwoHandIK(RetargetTestCase):
    def _grip_drift(self, two_hand_ik: bool) -> float:
        settings = settings_for(self.scene, two_hand_ik=two_hand_ik,
                                weapon_follow_mode='HAND_RELATIVE', primary_hand='R')
        result = rig_mod.build(bpy.context, settings)
        self.assertIsNotNone(result.driver_obj)
        scale = result.calibration.scale
        frames = list(range(1, 42, 2))

        source = world_matrices(
            self.source, ["ValveBiped.Bip01_L_Hand", "v_weapon.Rifle_Parent"], frames)
        target = world_matrices(self.target, ["hand_l"], frames)
        anchor = bpy.data.objects[rig_mod.ANCHOR_NAME]

        worst = 0.0
        with sampling.preserved_frame(bpy.context):
            for frame in frames:
                bpy.context.scene.frame_set(frame)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                anchor_world = anchor.evaluated_get(depsgraph).matrix_world
                expected = (
                    source[frame]["ValveBiped.Bip01_L_Hand"].translation
                    - source[frame]["v_weapon.Rifle_Parent"].translation
                ).length * scale
                actual = (target[frame]["hand_l"].translation
                          - anchor_world.translation).length
                worst = max(worst, abs(actual - expected))
        rig_mod.teardown(bpy.context, settings)
        return worst

    def test_ik_keeps_the_support_hand_on_the_weapon(self):
        drift = self._grip_drift(two_hand_ik=True)
        ratio = drift / TARGET_ARM_LENGTH
        self.assertLess(ratio, 0.01,
                        f"support hand slides {ratio * 100:.2f}% of arm length")

    def test_ik_beats_plain_fk_for_grip_stability(self):
        ik_drift = self._grip_drift(two_hand_ik=True)
        fk_drift = self._grip_drift(two_hand_ik=False)
        self.assertLessEqual(ik_drift, fk_drift + 1.0e-6,
                             "two-hand IK should not be worse than FK")

    def test_weapon_anchor_follows_the_primary_hand(self):
        settings = settings_for(self.scene, weapon_follow_mode='HAND_RIGID',
                                primary_hand='R')
        rig_mod.build(bpy.context, settings)
        anchor = bpy.data.objects[rig_mod.ANCHOR_NAME]
        frames = (1, 21, 41)
        relative = []
        with sampling.preserved_frame(bpy.context):
            for frame in frames:
                bpy.context.scene.frame_set(frame)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                evaluated = self.target.evaluated_get(depsgraph)
                hand = evaluated.matrix_world @ evaluated.pose.bones["hand_r"].matrix
                relative.append(hand.inverted_safe()
                                @ anchor.evaluated_get(depsgraph).matrix_world)
        base = relative[0]
        for matrix in relative[1:]:
            self.assertLess((matrix.translation - base.translation).length, 1.0e-4)
            self.assertLess(angle_between_deg(base, matrix), 0.05)


class TestUnrealIKBones(RetargetTestCase):
    def test_ik_hand_bones_track_the_hands(self):
        settings = settings_for(self.scene, drive_ue_ik_bones=True, ik_gun_hand='R')
        rig_mod.build(bpy.context, settings)
        frames = (1, 21, 41)
        samples = world_matrices(
            self.target, ["hand_l", "hand_r", "ik_hand_l", "ik_hand_r", "ik_hand_gun"],
            frames)
        for frame in frames:
            for ik_bone, hand in (("ik_hand_l", "hand_l"),
                                  ("ik_hand_r", "hand_r"),
                                  ("ik_hand_gun", "hand_r")):
                self.assertLess(
                    (samples[frame][ik_bone].translation
                     - samples[frame][hand].translation).length,
                    1.0e-4, f"{ik_bone} does not sit on {hand} at frame {frame}")
                self.assertLess(
                    angle_between_deg(samples[frame][ik_bone], samples[frame][hand]),
                    0.05, f"{ik_bone} orientation differs from {hand}")


class TestProceduralUpperBody(RetargetTestCase):
    def _disable_torso_mapping(self, settings):
        for item in settings.mapping:
            if item.key in ("spine_01", "spine_02", "spine_03", "clavicle_l", "clavicle_r"):
                item.enabled = False

    def test_procedural_drives_unmapped_torso_within_limits(self):
        settings = settings_for(self.scene, body_mode='PROCEDURAL_UPPER_BODY',
                                spine_influence=0.15, chest_influence=0.25,
                                clavicle_influence=0.3, shoulder_influence=0.3)
        self._disable_torso_mapping(settings)
        rig_mod.build(bpy.context, settings)

        spine = self.target.pose.bones["spine_03"]
        self.assertTrue(any(c.name.startswith(rig_mod.C_PROC) for c in spine.constraints),
                        "chest did not receive a procedural constraint")
        self.assertTrue(any(c.name.startswith(rig_mod.C_LIMIT) for c in spine.constraints),
                        "chest is missing its rotation clamp")

        limit = settings.max_spine_rotation
        samples = world_matrices(self.target, ["spine_03", "clavicle_l"], (1, 21, 41))
        rest = mathx.orthonormalize(
            self.target.matrix_world @ self.target.data.bones["spine_03"].matrix_local)
        moved = max(angle_between_deg(rest.to_4x4(), samples[f]["spine_03"])
                    for f in (1, 21, 41))
        self.assertGreater(moved, 0.5, "chest did not move at all")
        # Limit Rotation clamps each local Euler axis, so the combined angle can
        # reach at most sqrt(3) * limit.
        self.assertLess(moved, math.degrees(limit) * 1.74 + 1.0,
                        "chest rotated past the configured maximum")

    def test_mapped_bones_are_never_overridden_by_procedural(self):
        settings = settings_for(self.scene, body_mode='PROCEDURAL_UPPER_BODY',
                                spine_influence=0.5, chest_influence=0.5)
        rig_mod.build(bpy.context, settings)
        spine = self.target.pose.bones["spine_03"]
        self.assertFalse(any(c.name.startswith(rig_mod.C_PROC) for c in spine.constraints),
                         "real spine animation was overridden by the procedural pass")

    def test_arms_only_leaves_the_torso_alone(self):
        settings = settings_for(self.scene, body_mode='ARMS_ONLY')
        bpy.ops.svmr.auto_map()
        result = rig_mod.build(bpy.context, settings)
        self.assertNotIn("spine_02", result.driven_bones)
        self.assertNotIn("clavicle_l", result.driven_bones)
        self.assertIn("hand_r", result.driven_bones)
        spine = self.target.pose.bones["spine_02"]
        self.assertEqual(len(spine.constraints), 0)


class TestGripMarkers(RetargetTestCase):
    """Sections 9/10: explicit PRIMARY GRIP / SECONDARY GRIP bones."""

    def test_secondary_grip_bone_pins_the_support_hand_to_the_marker(self):
        settings = settings_for(
            self.scene, primary_hand='R', two_hand_ik=True,
            weapon_follow_mode='HAND_RELATIVE', secondary_grip_bone="v_weapon.Grip_L",
        )
        result = rig_mod.build(bpy.context, settings)
        self.assertEqual(result.warnings, [])
        scale = result.calibration.scale
        frames = (1, 11, 21, 31, 41)
        source = world_matrices(
            self.source, ["v_weapon.Grip_L", "v_weapon.Rifle_Parent"], frames)
        target = world_matrices(self.target, ["hand_l"], frames)
        anchor = bpy.data.objects[rig_mod.ANCHOR_NAME]

        worst = 0.0
        with sampling.preserved_frame(bpy.context):
            for frame in frames:
                bpy.context.scene.frame_set(frame)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                anchor_world = anchor.evaluated_get(depsgraph).matrix_world
                expected = (source[frame]["v_weapon.Grip_L"].translation
                            - source[frame]["v_weapon.Rifle_Parent"].translation
                            ).length * scale
                actual = (target[frame]["hand_l"].translation
                          - anchor_world.translation).length
                worst = max(worst, abs(actual - expected))
        self.assertLess(worst / TARGET_ARM_LENGTH, 0.01,
                        "support hand did not land on the grip marker")

    def test_primary_grip_bone_seats_the_weapon_in_the_hand(self):
        settings = settings_for(
            self.scene, primary_hand='R', weapon_follow_mode='HAND_RELATIVE',
            primary_grip_bone="v_weapon.Grip_R",
        )
        result = rig_mod.build(bpy.context, settings)
        self.assertEqual(result.warnings, [])
        scale = result.calibration.scale
        anchor = bpy.data.objects[rig_mod.ANCHOR_NAME]
        source = world_matrices(
            self.source, ["v_weapon.Grip_R", "v_weapon.Rifle_Parent"], (1, 21, 41))

        with sampling.preserved_frame(bpy.context):
            for frame in (1, 21, 41):
                bpy.context.scene.frame_set(frame)
                depsgraph = bpy.context.evaluated_depsgraph_get()
                anchor_world = anchor.evaluated_get(depsgraph).matrix_world
                evaluated = self.target.evaluated_get(depsgraph)
                hand = (evaluated.matrix_world
                        @ evaluated.pose.bones["hand_r"].matrix).translation
                weapon = source[frame]["v_weapon.Rifle_Parent"]
                grip_local = (weapon.inverted_safe()
                              @ source[frame]["v_weapon.Grip_R"].translation) * scale
                grip_world = anchor_world @ grip_local
                self.assertLess((grip_world - hand).length / TARGET_ARM_LENGTH, 0.005,
                                f"grip marker is not in the hand at frame {frame}")

    def test_missing_grip_bone_warns_and_falls_back(self):
        settings = settings_for(self.scene, secondary_grip_bone="v_weapon.NoSuchBone")
        result = rig_mod.build(bpy.context, settings)
        self.assertIsNotNone(result.driver_obj, "build must still succeed")
        self.assertTrue(any("grip bone" in w for w in result.warnings))


class TestNeutralFullBody(RetargetTestCase):
    def test_lower_body_is_keyed_at_rest(self):
        settings = settings_for(self.scene, body_mode='NEUTRAL_FULL_BODY')
        bpy.ops.svmr.auto_map()
        result = rig_mod.build(bpy.context, settings)
        for bone in ("pelvis", "thigh_l", "calf_r", "foot_l", "root"):
            self.assertIn(bone, result.driven_bones,
                          f"{bone} should be keyed at rest for a full-body clip")
        rest = world_matrices(self.target, ["thigh_l", "foot_r"], (1, 21, 41))
        base_thigh = self.target.matrix_world @ self.target.data.bones["thigh_l"].matrix_local
        for frame in (1, 21, 41):
            self.assertLess(
                (rest[frame]["thigh_l"].translation - base_thigh.translation).length,
                1.0e-6, "leg motion was invented")

    def test_arms_only_does_not_key_the_lower_body(self):
        settings = settings_for(self.scene, body_mode='ARMS_ONLY')
        bpy.ops.svmr.auto_map()
        result = rig_mod.build(bpy.context, settings)
        for bone in ("thigh_l", "calf_r", "foot_l"):
            self.assertNotIn(bone, result.driven_bones)
