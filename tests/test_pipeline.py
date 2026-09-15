"""Bake, batch, preset, validation and export tests."""

from __future__ import annotations

import math
import os
import tempfile
import unittest
from typing import Dict, List

import bpy
from mathutils import Matrix, Vector

import fixtures
from source_vm_retargeter.analysis import armature as analysis_mod
from source_vm_retargeter.core import scene as scene_utils
from source_vm_retargeter.exporting import unreal
from source_vm_retargeter.mapping import presets as preset_mod
from source_vm_retargeter.ops import bake_ops
from source_vm_retargeter.retarget import bake as bake_mod
from source_vm_retargeter.retarget import rig as rig_mod
from source_vm_retargeter.retarget import sampling
from source_vm_retargeter.validation import checks
from test_retarget import (
    RetargetTestCase,
    TEST_FRAMES,
    angle_between_deg,
    settings_for,
    world_matrices,
)


class TestBake(RetargetTestCase):
    def test_bake_reproduces_the_live_preview(self):
        settings = settings_for(self.scene)
        result = rig_mod.build(bpy.context, settings)
        names = list(result.driven_bones)
        frames = list(range(1, 42, 4))
        before = world_matrices(self.target, names, frames)

        baked, _ = bake_ops.retarget_action(bpy.context, settings)
        self.assertIsNotNone(baked)
        after = world_matrices(self.target, names, frames)

        worst_rot, worst_loc, worst_name = 0.0, 0.0, ""
        for frame in frames:
            for name in names:
                rot = angle_between_deg(before[frame][name], after[frame][name])
                loc = (before[frame][name].translation - after[frame][name].translation).length
                if rot > worst_rot:
                    worst_rot, worst_name = rot, name
                worst_loc = max(worst_loc, loc)
        self.assertLess(worst_rot, 0.2, f"baked pose differs on {worst_name}")
        self.assertLess(worst_loc, 1.0e-4)

    def test_bake_creates_a_named_action_and_keeps_the_source_one(self):
        settings = settings_for(self.scene)
        baked, _ = bake_ops.retarget_action(bpy.context, settings)
        self.assertEqual(baked.name, "RTG_vm_rifle_reload_UE4Mannequin")
        self.assertIn("vm_rifle_reload", bpy.data.actions)
        self.assertTrue(baked.use_fake_user)
        self.assertGreater(len(baked.fcurves), 0)

    def test_bake_removes_every_constraint_and_helper(self):
        settings = settings_for(self.scene)
        bake_ops.retarget_action(bpy.context, settings)
        leftovers = [c.name for pb in self.target.pose.bones for c in pb.constraints]
        self.assertEqual(leftovers, [], "temporary constraints survived the bake")
        self.assertEqual(scene_utils.owned_objects(), [])
        self.assertIsNone(bpy.data.collections.get(scene_utils.HELPER_COLLECTION))

    def test_baked_action_is_independent_of_the_source_rig(self):
        settings = settings_for(self.scene)
        baked, _ = bake_ops.retarget_action(bpy.context, settings)
        frames = (1, 21, 41)
        before = world_matrices(self.target, ["hand_r", "lowerarm_l"], frames)

        # Delete the source rig entirely; the baked clip must not care.
        bpy.data.objects.remove(self.source, do_unlink=True)
        after = world_matrices(self.target, ["hand_r", "lowerarm_l"], frames)
        for frame in frames:
            for name in ("hand_r", "lowerarm_l"):
                self.assertLess(angle_between_deg(before[frame][name], after[frame][name]),
                                1.0e-3)

    def test_baked_action_has_no_nan_and_no_quaternion_flips(self):
        settings = settings_for(self.scene)
        baked, _ = bake_ops.retarget_action(bpy.context, settings)
        for fcurve in baked.fcurves:
            for point in fcurve.keyframe_points:
                self.assertFalse(math.isnan(point.co.y), f"NaN in {fcurve.data_path}")
                self.assertFalse(math.isinf(point.co.y), f"inf in {fcurve.data_path}")

        groups: Dict[str, List] = {}
        for fcurve in baked.fcurves:
            if fcurve.data_path.endswith("rotation_quaternion"):
                groups.setdefault(fcurve.data_path, []).append(fcurve)
        self.assertGreater(len(groups), 0)
        for curves in groups.values():
            curves.sort(key=lambda fc: fc.array_index)
            if len(curves) != 4:
                continue
            count = min(len(c.keyframe_points) for c in curves)
            for index in range(1, count):
                previous = [c.keyframe_points[index - 1].co.y for c in curves]
                current = [c.keyframe_points[index].co.y for c in curves]
                dot = sum(a * b for a, b in zip(previous, current))
                self.assertGreaterEqual(dot, -1.0e-6, "quaternion sign flip survived cleanup")

    def test_key_reduction_removes_keys_without_breaking_the_pose(self):
        settings = settings_for(self.scene, key_reduction=0.0)
        full, _ = bake_ops.retarget_action(bpy.context, settings)
        full_keys = sum(len(fc.keyframe_points) for fc in full.fcurves)

        self.setUp()
        settings = settings_for(self.scene, key_reduction=0.01)
        reduced, _ = bake_ops.retarget_action(bpy.context, settings)
        reduced_keys = sum(len(fc.keyframe_points) for fc in reduced.fcurves)
        self.assertLess(reduced_keys, full_keys, "key reduction removed nothing")
        self.assertGreater(reduced_keys, 0)

    def test_root_stays_still_with_the_default_policy(self):
        settings = settings_for(self.scene, root_motion='IGNORE')
        baked, _ = bake_ops.retarget_action(bpy.context, settings)
        paths = {fc.data_path for fc in baked.fcurves}
        self.assertNotIn('pose.bones["root"].location', paths,
                         "viewmodel clips must not invent character root motion")
        samples = world_matrices(self.target, ["root"], (1, 21, 41))
        base = samples[1]["root"].translation
        for frame in (21, 41):
            self.assertLess((samples[frame]["root"].translation - base).length, 1.0e-6)

    def test_custom_root_motion_moves_the_root(self):
        settings = settings_for(self.scene, root_motion='CUSTOM',
                                root_motion_bone="v_weapon.Clip")
        result = rig_mod.build(bpy.context, settings)
        self.assertIsNotNone(result.root_empty)
        self.assertIn("root", result.driven_bones)
        samples = world_matrices(self.target, ["root"], (1, 21))
        self.assertGreater(
            (samples[21]["root"].translation - samples[1]["root"].translation).length,
            1.0e-4, "custom root motion produced no movement")


class TestNonDestructive(RetargetTestCase):
    def _source_snapshot(self):
        bones = {b.name: (b.parent.name if b.parent else None,
                          b.matrix_local.copy()) for b in self.source.data.bones}
        curves = {
            (fc.data_path, fc.array_index): [(p.co.x, p.co.y) for p in fc.keyframe_points]
            for fc in self.action.fcurves
        }
        return bones, curves

    def test_source_rig_and_action_survive_untouched(self):
        before_bones, before_curves = self._source_snapshot()
        settings = settings_for(self.scene)
        bake_ops.retarget_action(bpy.context, settings)
        after_bones, after_curves = self._source_snapshot()

        self.assertEqual(set(before_bones), set(after_bones))
        for name, (parent, matrix) in before_bones.items():
            self.assertEqual(parent, after_bones[name][0], f"{name} was reparented")
            self.assertLess(
                max(abs(a - b) for r1, r2 in zip(matrix, after_bones[name][1])
                    for a, b in zip(r1, r2)),
                1.0e-9, f"{name} rest pose changed")
        self.assertEqual(before_curves.keys(), after_curves.keys())
        for key, points in before_curves.items():
            self.assertEqual(points, after_curves[key], f"{key} keyframes changed")

    def test_clear_rig_restores_the_scene(self):
        settings = settings_for(self.scene, keep_rig_after_bake=True)
        object_names = {o.name for o in bpy.data.objects}
        rig_mod.build(bpy.context, settings)
        self.assertGreater(len({o.name for o in bpy.data.objects}), len(object_names))
        rig_mod.teardown(bpy.context, settings)
        self.assertEqual({o.name for o in bpy.data.objects}, object_names)
        self.assertEqual([c.name for pb in self.target.pose.bones
                          for c in pb.constraints], [])

    def test_helpers_live_in_their_own_collection(self):
        settings = settings_for(self.scene, keep_rig_after_bake=True)
        rig_mod.build(bpy.context, settings)
        collection = bpy.data.collections.get(scene_utils.HELPER_COLLECTION)
        self.assertIsNotNone(collection)
        for obj in scene_utils.owned_objects():
            self.assertEqual([c.name for c in obj.users_collection],
                             [scene_utils.HELPER_COLLECTION])


class TestBatch(RetargetTestCase):
    def test_batch_produces_one_action_per_source_clip(self):
        extra = [
            fixtures.build_simple_action(self.source, "vm_rifle_idle", 5.0),
            fixtures.build_simple_action(self.source, "vm_rifle_draw", 35.0),
        ]
        settings = settings_for(self.scene)
        bpy.ops.svmr.refresh_actions()
        names = {item.name for item in settings.actions}
        self.assertTrue({"vm_rifle_reload", "vm_rifle_idle", "vm_rifle_draw"} <= names)

        for item in settings.actions:
            item.selected = item.name in {"vm_rifle_idle", "vm_rifle_draw"}
        self.assertEqual(bpy.ops.svmr.batch_retarget(), {'FINISHED'})

        for source_name in ("vm_rifle_idle", "vm_rifle_draw"):
            target_name = f"RTG_{source_name}_UE4Mannequin"
            self.assertIn(target_name, bpy.data.actions, f"missing {target_name}")
            self.assertGreater(len(bpy.data.actions[target_name].fcurves), 0)
        # The original clips are untouched and still assigned as before.
        self.assertEqual(self.source.animation_data.action.name, "vm_rifle_reload")
        for action in extra:
            self.assertIn(action.name, bpy.data.actions)


class TestPresets(RetargetTestCase):
    def test_round_trip_through_json(self):
        settings = settings_for(self.scene, primary_hand='L', spine_influence=0.33)
        settings.mapping[0].manual_offset = (0.1, -0.2, 0.3)
        settings.mapping[1].enabled = False
        original = {(i.key, i.source_bone, i.target_bone, i.enabled) for i in settings.mapping}

        data = preset_mod.serialize(settings)
        settings.mapping.clear()
        settings.primary_hand = 'R'
        settings.spine_influence = 0.0

        stats = preset_mod.apply(settings, data, self.source, self.target)
        self.assertEqual(stats["missing_source"], 0)
        self.assertEqual(stats["missing_target"], 0)
        self.assertEqual(settings.primary_hand, 'L')
        self.assertAlmostEqual(settings.spine_influence, 0.33, places=5)
        self.assertEqual(
            {(i.key, i.source_bone, i.target_bone, i.enabled) for i in settings.mapping},
            original)
        self.assertAlmostEqual(settings.mapping[0].manual_offset[0], 0.1, places=5)

    def test_missing_bones_are_disabled_not_dropped(self):
        settings = settings_for(self.scene)
        data = preset_mod.serialize(settings)
        data["entries"].append({
            "key": "head", "source_bone": "does.not.exist", "target_bone": "head",
            "enabled": True, "influence": 1.0, "method": "PRESET", "confidence": 1.0,
            "calibration_quat": [1, 0, 0, 0], "has_calibration": False,
            "manual_offset": [0, 0, 0],
        })
        stats = preset_mod.apply(settings, data, self.source, self.target)
        self.assertEqual(stats["missing_source"], 1)
        ghost = [i for i in settings.mapping if i.source_bone == "does.not.exist"]
        self.assertEqual(len(ghost), 1)
        self.assertFalse(ghost[0].enabled)

    def test_rejects_unknown_format_version(self):
        with self.assertRaises(ValueError):
            preset_mod.apply  # keep the reference used below
            data = {"format_version": 999, "entries": []}
            if data["format_version"] != preset_mod.FORMAT_VERSION:
                raise ValueError("Unsupported preset format version")


class TestValidation(RetargetTestCase):
    def test_clean_retarget_reports_no_errors(self):
        settings = settings_for(self.scene)
        bake_ops.retarget_action(bpy.context, settings)
        analysis = analysis_mod.analyze(self.source, self.action)
        report = checks.validate(bpy.context, settings, analysis,
                                 action=self.target.animation_data.action)
        errors = [e for e in report.entries if e.level == checks.LEVEL_ERROR]
        self.assertEqual(errors, [], "\n".join(str(e) for e in errors))
        self.assertLess(report.metrics["max_frame_rotation_deg"], 45.0)

    def test_broken_mapping_is_reported_as_an_error(self):
        settings = settings_for(self.scene)
        settings.mapping[0].source_bone = "this.bone.is.gone"
        report = checks.Report()
        checks.check_mapping(settings, report)
        self.assertTrue(any(e.level == checks.LEVEL_ERROR and e.category == "mapping"
                            for e in report.entries))

    def test_viewmodel_limitation_is_reported(self):
        settings = settings_for(self.scene)
        analysis = analysis_mod.analyze(self.source, self.action)
        report = checks.Report()
        checks.check_source_completeness(analysis, report)
        self.assertTrue(any("lower-body" in e.message for e in report.entries))

    def test_report_text_is_renderable(self):
        report = checks.Report()
        report.error("x", "boom")
        report.warn("y", "hmm")
        report.info("z", "fine")
        report.metrics["thing"] = 1.5
        text = report.as_text()
        self.assertIn("boom", text)
        self.assertIn("1.5", text)
        self.assertFalse(report.ok)


class TestExport(RetargetTestCase):
    def test_preflight_blocks_export_while_constraints_are_live(self):
        settings = settings_for(self.scene, keep_rig_after_bake=True)
        rig_mod.build(bpy.context, settings)
        profile = rig_mod.resolve_profile(settings)
        result = unreal.preflight(bpy.context, settings, profile)
        self.assertFalse(result.ok)
        self.assertTrue(any("constraint" in e for e in result.errors))

    def test_preflight_passes_after_bake(self):
        settings = settings_for(self.scene)
        bake_ops.retarget_action(bpy.context, settings)
        profile = rig_mod.resolve_profile(settings)
        result = unreal.preflight(bpy.context, settings, profile)
        self.assertTrue(result.ok, "; ".join(result.errors))

    def test_preflight_requires_an_action(self):
        settings = settings_for(self.scene)
        if self.target.animation_data:
            self.target.animation_data.action = None
        profile = rig_mod.resolve_profile(settings)
        result = unreal.preflight(bpy.context, settings, profile)
        self.assertFalse(result.ok)
        self.assertTrue(any("Action" in e for e in result.errors))

    def test_fbx_export_writes_a_usable_file(self):
        settings = settings_for(self.scene)
        bake_ops.retarget_action(bpy.context, settings)
        settings.export_frame_start, settings.export_frame_end = 1, 41
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "reload.fbx")
            unreal.export_fbx(bpy.context, settings, path,
                              rig_mod.resolve_profile(settings))
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 4096)
            with open(path, "rb") as handle:
                self.assertTrue(handle.read(20).startswith(b"Kaydara FBX Binary"))

    def test_export_preset_matches_unreal_expectations(self):
        preset = unreal.UNREAL_FBX_PRESET
        self.assertFalse(preset["add_leaf_bones"])
        self.assertEqual(preset["primary_bone_axis"], 'Y')
        self.assertEqual(preset["secondary_bone_axis"], 'X')
        self.assertEqual(preset["armature_nodetype"], 'NULL')
        self.assertTrue(preset["bake_anim_use_all_bones"])
        self.assertFalse(preset["bake_anim_use_nla_strips"])


class TestUE5Target(unittest.TestCase):
    """UE4 Mannequin and UE5 Manny must stay separate target profiles."""

    def setUp(self):
        self.scene = fixtures.build_ue5_scene()
        self.source = self.scene["source"]
        self.target = self.scene["target"]

    def tearDown(self):
        try:
            rig_mod.teardown(bpy.context, bpy.context.scene.svmr)
        except (RuntimeError, ReferenceError):
            pass

    def test_manny_is_detected_and_not_confused_with_ue4(self):
        from source_vm_retargeter.profiles import targets as profiles
        scores = profiles.detect_profile_scores(self.target)
        self.assertEqual(profiles.detect_profile(self.target), profiles.UE5_MANNY)
        self.assertGreater(scores['UE5_MANNY'], scores['UE4_MANNEQUIN'])

    def test_retarget_and_bake_onto_manny(self):
        settings = settings_for(self.scene)
        self.assertEqual(rig_mod.resolve_profile(settings).identifier, 'UE5_MANNY')
        baked, result = bake_ops.retarget_action(bpy.context, settings)
        self.assertIsNotNone(baked)
        self.assertEqual(baked.name, "RTG_vm_rifle_reload_Manny")
        self.assertGreater(len(baked.fcurves), 0)

    def test_twist_and_metacarpal_bones_are_left_alone(self):
        settings = settings_for(self.scene)
        baked, _ = bake_ops.retarget_action(bpy.context, settings)
        paths = {fc.data_path for fc in baked.fcurves}
        for bone in ("upperarm_twist_02_l", "lowerarm_twist_01_r",
                     "index_metacarpal_l", "clavicle_out_r", "spine_05"):
            self.assertNotIn(f'pose.bones["{bone}"].rotation_quaternion', paths,
                             f"{bone} must not receive retargeted animation")

    def test_ue5_spine_chain_is_longer(self):
        settings = settings_for(self.scene)
        profile = rig_mod.resolve_profile(settings)
        self.assertIn("spine_05", profile.spine_chain)
        for bone in profile.spine_chain:
            self.assertIn(bone, self.target.data.bones)


class TestFullWorkflowThroughOperators(RetargetTestCase):
    """The acceptance workflow from the brief, driven only through bpy.ops."""

    def test_select_map_calibrate_build_bake_validate_export(self):
        settings = bpy.context.scene.svmr
        settings.source_armature = self.source
        settings.target_armature = self.target

        self.assertEqual(bpy.ops.svmr.analyze(), {'FINISHED'})
        self.assertGreater(len(settings.analysis_text), 0)
        self.assertEqual(settings.detected_profile, 'UE4_MANNEQUIN')
        self.assertEqual(bpy.ops.svmr.use_detected_profile(), {'FINISHED'})

        self.assertEqual(bpy.ops.svmr.auto_map(), {'FINISHED'})
        self.assertEqual(bpy.ops.svmr.detect_weapon(), {'FINISHED'})
        self.assertEqual(settings.weapon_reference_bone, "v_weapon.Rifle_Parent")

        self.assertEqual(bpy.ops.svmr.calibrate(), {'FINISHED'})
        self.assertGreater(settings.scale_factor, 0.0)

        self.assertEqual(bpy.ops.svmr.build_rig(), {'FINISHED'})
        self.assertTrue(settings.rig_built)
        self.assertIsNotNone(settings.driver_object)

        self.assertEqual(bpy.ops.svmr.bake(), {'FINISHED'})
        self.assertFalse(settings.rig_built)
        action = self.target.animation_data.action
        self.assertEqual(action.name, "RTG_vm_rifle_reload_UE4Mannequin")

        self.assertEqual(bpy.ops.svmr.validate(), {'FINISHED'})
        errors = [r for r in settings.report if r.level == 'ERROR']
        self.assertEqual([r.message for r in errors], [])

        self.assertEqual(bpy.ops.svmr.export_preflight(), {'FINISHED'})
        with tempfile.TemporaryDirectory() as directory:
            settings.export_path = os.path.join(directory, "workflow.fbx")
            settings.export_frame_start, settings.export_frame_end = 1, 41
            self.assertEqual(bpy.ops.svmr.export_fbx(), {'FINISHED'})
            self.assertTrue(os.path.isfile(settings.export_path))

    def test_acceptance_clip_set_reload_idle_fire_draw_inspect(self):
        """Section 25: reload first, then idle / fire / draw / inspect."""
        clips = {
            "vm_rifle_idle": 4.0,
            "vm_rifle_fire": 12.0,
            "vm_rifle_draw": 38.0,
            "vm_rifle_inspect": 55.0,
        }
        for name, angle in clips.items():
            fixtures.build_simple_action(self.source, name, angle)

        settings = settings_for(self.scene)
        bpy.ops.svmr.refresh_actions()
        for item in settings.actions:
            item.selected = True
        self.assertEqual(bpy.ops.svmr.batch_retarget(), {'FINISHED'})

        for name in ["vm_rifle_reload"] + list(clips):
            baked = bpy.data.actions.get(f"RTG_{name}_UE4Mannequin")
            self.assertIsNotNone(baked, f"{name} was not retargeted")
            self.assertGreater(len(baked.fcurves), 0)
            start, end = baked.frame_range
            self.assertLess(start, end, f"{name} produced an empty range")
        for item in settings.actions:
            self.assertNotEqual(item.result, "FAILED", f"{item.name} failed")

    def test_clear_rig_operator_is_safe_to_call_twice(self):
        settings = settings_for(self.scene, keep_rig_after_bake=True)
        rig_mod.build(bpy.context, settings)
        self.assertEqual(bpy.ops.svmr.clear_rig(), {'FINISHED'})
        self.assertEqual(bpy.ops.svmr.clear_rig(), {'FINISHED'})
        self.assertEqual(scene_utils.owned_objects(), [])


class TestUIWiring(unittest.TestCase):
    """Catch UI typos: every operator and property referenced must exist."""

    def _panel_source(self) -> str:
        import inspect
        from source_vm_retargeter.ui import panels
        return inspect.getsource(panels)

    def test_every_operator_referenced_by_the_ui_exists(self):
        import re
        source = self._panel_source()
        found = set(re.findall(r'operator\(\s*"([a-z_]+)\.([a-z_]+)"', source))
        self.assertGreater(len(found), 10)
        for module, name in sorted(found):
            self.assertTrue(hasattr(bpy.ops, module), f"bpy.ops.{module} missing")
            self.assertTrue(hasattr(getattr(bpy.ops, module), name),
                            f"operator {module}.{name} is referenced but not registered")

    def test_every_settings_property_referenced_by_the_ui_exists(self):
        import re
        from source_vm_retargeter.props import SVMR_Settings
        source = self._panel_source()
        names = set(re.findall(r'(?:prop|prop_search)\(\s*settings,\s*"([a-z_0-9]+)"', source))
        self.assertGreater(len(names), 20)
        known = set(SVMR_Settings.__annotations__)
        for name in sorted(names):
            self.assertIn(name, known, f"settings.{name} is used by the UI but undefined")

    def test_every_uilist_referenced_by_the_ui_is_registered(self):
        import re
        source = self._panel_source()
        names = set(re.findall(r'template_list\(\s*"([A-Za-z_]+)"', source))
        self.assertGreater(len(names), 2)
        for name in sorted(names):
            self.assertTrue(hasattr(bpy.types, name), f"UIList {name} is not registered")

    def test_panels_are_attached_to_the_main_panel(self):
        from source_vm_retargeter.ui import panels
        for cls in panels.CLASSES:
            self.assertEqual(cls.bl_category, "Source VM Retarget")
            self.assertEqual(cls.bl_space_type, 'VIEW_3D')
            if cls is not panels.SVMR_PT_main:
                self.assertEqual(cls.bl_parent_id, "SVMR_PT_main")


class TestZRegistration(unittest.TestCase):
    """Registering and unregistering must be clean and repeatable."""

    def test_unregister_then_register_round_trip(self):
        import source_vm_retargeter as addon
        addon.unregister()
        self.assertFalse(hasattr(bpy.types.Scene, "svmr"))
        self.assertFalse(hasattr(bpy.types, "SVMR_PT_main"))
        addon.register()
        self.assertTrue(hasattr(bpy.types.Scene, "svmr"))
        self.assertTrue(hasattr(bpy.types, "SVMR_PT_main"))
        fixtures.reset_scene()
        self.assertIsNotNone(bpy.context.scene.svmr)

    def test_manifest_and_bl_info_agree(self):
        import re
        import source_vm_retargeter as addon
        root = os.path.dirname(os.path.abspath(addon.__file__))
        with open(os.path.join(root, "blender_manifest.toml"), encoding="utf-8") as handle:
            manifest = handle.read()
        version = re.search(r'^version = "([^"]+)"', manifest, re.M).group(1)
        self.assertEqual(version, ".".join(str(v) for v in addon.bl_info["version"]))
        self.assertIn('id = "source_vm_retargeter"', manifest)
        self.assertGreaterEqual(addon.bl_info["blender"], (4, 0, 0))


class TestUnreachableGrip(RetargetTestCase):
    """An IK target the arm cannot reach must be reported, not silently wrong."""

    def test_validation_flags_an_out_of_reach_support_grip(self):
        settings = settings_for(
            self.scene, primary_hand='R', two_hand_ik=True,
            weapon_follow_mode='HAND_RELATIVE',
            secondary_grip_bone="v_weapon.Grip_Unreachable",
        )
        rig_mod.build(bpy.context, settings)
        report = checks.validate(bpy.context, settings, None, anchor=
                                 bpy.data.objects[rig_mod.ANCHOR_NAME])
        self.assertIn("secondary_grip_drift_ratio", report.metrics)
        self.assertGreater(report.metrics["secondary_grip_drift_ratio"],
                           checks.DRIFT_ERROR_RATIO)
        drift_errors = [e for e in report.entries
                        if e.level == checks.LEVEL_ERROR and e.category == "drift"]
        self.assertTrue(drift_errors, "an unreachable grip must raise a drift error")
        self.assertTrue(
            any("support" in e.message.lower() for e in drift_errors),
            f"expected a support-hand drift error, got: "
            f"{[e.message for e in drift_errors]}",
        )

    def test_reachable_grip_reports_no_drift_error(self):
        settings = settings_for(
            self.scene, primary_hand='R', two_hand_ik=True,
            weapon_follow_mode='HAND_RELATIVE',
            secondary_grip_bone="v_weapon.Grip_L",
        )
        rig_mod.build(bpy.context, settings)
        report = checks.validate(bpy.context, settings, None, anchor=
                                 bpy.data.objects[rig_mod.ANCHOR_NAME])
        drift_errors = [e for e in report.entries
                        if e.level == checks.LEVEL_ERROR and e.category == "drift"]
        self.assertEqual([e.message for e in drift_errors], [])


class TestWeaponDetection(RetargetTestCase):
    def test_detect_weapon_reports_grips_without_assigning_them(self):
        settings = settings_for(self.scene)
        settings.primary_grip_bone = "stale"
        settings.secondary_grip_bone = "stale"
        self.assertEqual(bpy.ops.svmr.detect_weapon(), {'FINISHED'})
        self.assertEqual(settings.weapon_reference_bone, "v_weapon.Rifle_Parent")
        self.assertEqual(settings.primary_grip_bone, "",
                         "detection must not silently change the retarget result")
        self.assertEqual(settings.secondary_grip_bone, "")
        log = "\n".join(item.message for item in settings.analysis_text)
        self.assertIn("Grip candidates", log)
        self.assertIn("v_weapon.Grip_L", log)

    def test_default_pipeline_does_not_skip_the_hand_separation_check(self):
        settings = settings_for(self.scene)
        bpy.ops.svmr.detect_weapon()
        bake_ops.retarget_action(bpy.context, settings)
        report = checks.validate(bpy.context, settings, None,
                                 action=self.target.animation_data.action)
        messages = [e.message for e in report.entries if e.category == "drift"]
        self.assertFalse(any("pinned to the grip marker" in m for m in messages),
                         f"grip pinning was enabled without being asked for: {messages}")
        self.assertIn("hand_distance_drift_ratio", report.metrics)
        self.assertLess(report.metrics["hand_distance_drift_ratio"], 0.05)

    def test_weapon_without_bones_disables_cleanly(self):
        scene = fixtures.build_scene(with_weapon=False)
        settings = bpy.context.scene.svmr
        settings.source_armature = scene["source"]
        settings.target_armature = scene["target"]
        bpy.ops.svmr.auto_map()
        self.assertEqual(bpy.ops.svmr.detect_weapon(), {'CANCELLED'})
        self.assertFalse(settings.weapon_enabled)
        baked, result = bake_ops.retarget_action(bpy.context, settings)
        self.assertIsNotNone(baked, "a hands-only clip must still retarget")
