"""Unit tests for the pure-logic helpers (no scene required)."""

from __future__ import annotations

import math
import unittest

from mathutils import Euler, Matrix, Quaternion, Vector

from source_vm_retargeter.core import mathx, naming
from source_vm_retargeter.mapping import auto as auto_map
from source_vm_retargeter.profiles import base as profile_base
from source_vm_retargeter.profiles import source_tables, targets


class TestNaming(unittest.TestCase):
    def test_normalize_strips_valve_prefixes(self):
        self.assertEqual(naming.normalize("ValveBiped.Bip01_R_UpperArm"), "r_upperarm")
        self.assertEqual(naming.normalize("bip_hand_L"), "hand_l")
        self.assertEqual(naming.normalize("v_weapon.Clip"), "weapon_clip")

    def test_detect_side(self):
        self.assertEqual(naming.detect_side("ValveBiped.Bip01_L_Hand"), "L")
        self.assertEqual(naming.detect_side("hand_r"), "R")
        self.assertEqual(naming.detect_side("Right_Forearm"), "R")
        self.assertIsNone(naming.detect_side("spine_02"))

    def test_tokens_split_digits(self):
        self.assertEqual(naming.tokens("spine2"), ["spine", "2"])
        self.assertEqual(naming.tokens("spine_02"), ["spine", "2"])

    def test_finger_info_valve_packed_digits(self):
        self.assertEqual(naming.finger_info("ValveBiped.Bip01_L_Finger0"),
                         ("thumb", 0, "L"))
        self.assertEqual(naming.finger_info("ValveBiped.Bip01_L_Finger12"),
                         ("index", 2, "L"))
        self.assertEqual(naming.finger_info("ValveBiped.Bip01_R_Finger42"),
                         ("pinky", 2, "R"))

    def test_finger_info_ue_style(self):
        self.assertEqual(naming.finger_info("middle_02_l"), ("middle", 1, "L"))

    def test_similarity_respects_side(self):
        self.assertEqual(naming.similarity("hand_l", "hand_r"), 0.0)
        self.assertGreater(naming.similarity("ValveBiped.Bip01_R_Hand", "hand_r"), 0.6)
        self.assertLess(naming.similarity("ValveBiped.Bip01_Head1", "index_01_r"), 0.5)


class TestMathX(unittest.TestCase):
    def test_swing_to_aligns_vectors(self):
        a = Vector((1.0, 0.2, -0.3)).normalized()
        b = Vector((-0.4, 0.9, 0.1)).normalized()
        rotated = mathx.swing_to(a, b).to_matrix() @ a
        self.assertAlmostEqual((rotated - b).length, 0.0, places=6)

    def test_swing_to_handles_opposite_vectors(self):
        a = Vector((0.0, 1.0, 0.0))
        rotated = mathx.swing_to(a, -a).to_matrix() @ a
        self.assertAlmostEqual((rotated + a).length, 0.0, places=6)

    def test_swing_to_identity_for_degenerate_input(self):
        self.assertAlmostEqual(mathx.swing_to(Vector((0, 0, 0)), Vector((0, 1, 0))).angle,
                               0.0, places=9)

    def test_quat_continuity_removes_sign_flips(self):
        base = Quaternion(Vector((0, 0, 1)), 0.1)
        flipped = Quaternion(base)
        flipped.negate()
        out = mathx.make_quat_continuous([base, flipped, base])
        for previous, current in zip(out, out[1:]):
            self.assertGreaterEqual(previous.dot(current), 0.0)

    def test_clamp_quat_angle(self):
        quat = Quaternion(Vector((0, 0, 1)), math.radians(90))
        clamped = mathx.clamp_quat_angle(quat, math.radians(20))
        self.assertAlmostEqual(math.degrees(clamped.angle), 20.0, places=3)
        small = Quaternion(Vector((0, 0, 1)), math.radians(5))
        self.assertAlmostEqual(math.degrees(mathx.clamp_quat_angle(small, math.radians(20)).angle),
                               5.0, places=3)

    def test_orthonormalize_strips_scale(self):
        rotation = Euler((0.3, 0.2, 0.1)).to_matrix()
        mat = Matrix.Diagonal((2.0, 3.0, 0.5)).to_4x4() @ rotation.to_4x4()
        rot = mathx.orthonormalize(mat)
        self.assertAlmostEqual(rot.determinant(), 1.0, places=5)
        for i in range(3):
            self.assertAlmostEqual(Vector(rot.col[i]).length, 1.0, places=5)
            for j in range(i + 1, 3):
                self.assertAlmostEqual(Vector(rot.col[i]).dot(Vector(rot.col[j])),
                                       0.0, places=5)

    def test_orthonormalize_is_identity_on_a_pure_rotation(self):
        rotation = Euler((0.3, -0.7, 1.1)).to_matrix()
        rot = mathx.orthonormalize(rotation.to_4x4())
        self.assertLess(max(abs(a - b) for r1, r2 in zip(rotation, rot)
                            for a, b in zip(r1, r2)), 1.0e-6)

    def test_angle_between_matrices_is_the_short_way_round(self):
        base = Euler((0.1, 0.2, 0.3)).to_matrix().to_4x4()
        for degrees in (1.0, 45.0, 179.0):
            turned = base @ Quaternion(Vector((0.3, -0.5, 0.8)).normalized(),
                                       math.radians(degrees)).to_matrix().to_4x4()
            measured = math.degrees(mathx.angle_between_matrices(base, turned))
            self.assertAlmostEqual(measured, degrees, places=2)
        self.assertLessEqual(mathx.angle_between_matrices(base, -1.0 * base), math.pi + 1e-6)

    def test_is_finite_matrix(self):
        self.assertTrue(mathx.is_finite_matrix(Matrix.Identity(4)))
        bad = Matrix.Identity(4)
        bad[0][0] = float("nan")
        self.assertFalse(mathx.is_finite_matrix(bad))


class TestChainConversion(unittest.TestCase):
    def test_equal_lengths_map_one_to_one(self):
        self.assertEqual([auto_map._chain_index(i, 3) for i in range(3)], [0, 1, 2])

    def test_short_source_chain_is_stretched(self):
        self.assertEqual([auto_map._chain_index(i, 2) for i in range(3)], [0, 1, 1])

    def test_long_source_chain_is_compressed(self):
        self.assertEqual([auto_map._chain_index(i, 4) for i in range(3)], [0, 2, 3])

    def test_single_bone_source_chain(self):
        self.assertEqual([auto_map._chain_index(i, 1) for i in range(3)], [0, 0, 0])


class TestProfiles(unittest.TestCase):
    def test_ue4_and_ue5_are_distinct(self):
        self.assertNotEqual(targets.UE4_MANNEQUIN.spine_chain, targets.UE5_MANNY.spine_chain)
        self.assertIn("spine_05", targets.UE5_MANNY.spine_chain)
        self.assertNotIn("spine_05", targets.UE4_MANNEQUIN.spine_chain)

    def test_arms_only_excludes_spine_and_clavicle(self):
        keys = targets.UE4_MANNEQUIN.keys_for_mode('ARMS_ONLY', True)
        self.assertNotIn("spine_02", keys)
        self.assertNotIn("clavicle_l", keys)
        self.assertIn("hand_r", keys)
        self.assertIn("index_02_l", keys)

    def test_fingers_can_be_disabled(self):
        keys = targets.UE4_MANNEQUIN.keys_for_mode('PROCEDURAL_UPPER_BODY', False)
        self.assertNotIn("index_02_l", keys)
        self.assertIn("hand_l", keys)
        self.assertIn("spine_02", keys)

    def test_key_group(self):
        self.assertEqual(profile_base.key_group("hand_l"), 'ARM_L')
        self.assertEqual(profile_base.key_group("index_02_r"), 'FINGERS_R')
        self.assertEqual(profile_base.key_group("spine_01"), 'SPINE')

    def test_source_tables_cover_every_finger(self):
        for key in profile_base.finger_keys():
            self.assertTrue(source_tables.candidates_for(key),
                            f"no source candidates for {key}")

    def test_weapon_hints_do_not_match_arm_bones(self):
        for name in ("ValveBiped.Bip01_L_Forearm", "elbow_helper", "lowerarm_r"):
            normalized = naming.normalize(name)
            self.assertFalse(
                any(hint in normalized for hint in source_tables.WEAPON_BONE_HINTS),
                f"{name} wrongly looks like a weapon bone",
            )
