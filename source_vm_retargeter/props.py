"""Property groups holding all add-on state (stored in the .blend scene)."""

from __future__ import annotations

import math

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup

from .profiles import targets as target_profiles
from .profiles.base import key_group

_TARGET_PROFILE_ITEMS = target_profiles.enum_items()

BODY_MODE_ITEMS = [
    ('ARMS_ONLY', "Arms Only",
     "Retarget only the arms and fingers. The torso stays in its rest pose"),
    ('PROCEDURAL_UPPER_BODY', "Procedural Upper Body",
     "Retarget the arms and distribute a conservative fraction of their motion "
     "onto clavicles, spine and chest"),
    ('NEUTRAL_FULL_BODY', "Neutral Full Body Preview",
     "Like Procedural Upper Body, but keeps the legs in their neutral pose for "
     "a full-body preview. No leg motion is invented"),
]

CALIBRATION_ITEMS = [
    ('AUTO_ALIGN', "Auto Align",
     "Rotate each target bone's reference orientation onto the source bone's "
     "direction. Handles A-pose vs T-pose and unusual viewmodel bind poses"),
    ('REST_TO_REST', "Rest To Rest",
     "Assume both rigs share the same rest pose. Use when the source was "
     "already authored in the target's pose"),
    ('MANUAL', "Manual",
     "Use the retarget pose captured from the posed target rig"),
]

GROUP_ITEMS = [
    ('ROOT', "Root", ""),
    ('SPINE', "Spine", ""),
    ('HEAD', "Head", ""),
    ('ARM_L', "Left Arm", ""),
    ('ARM_R', "Right Arm", ""),
    ('FINGERS_L', "Left Fingers", ""),
    ('FINGERS_R', "Right Fingers", ""),
]


def _is_armature(self, obj) -> bool:
    return obj is not None and obj.type == 'ARMATURE'


class SVMR_BoneMapItem(PropertyGroup):
    """One canonical bone: source bone -> target bone plus its calibration."""

    key: StringProperty(name="Key", description="Canonical bone key")
    source_bone: StringProperty(name="Source", description="Bone on the Source armature")
    target_bone: StringProperty(name="Target", description="Bone on the Unreal armature")
    enabled: BoolProperty(name="Enabled", default=True,
                          description="Retarget this bone")
    influence: FloatProperty(name="Influence", default=1.0, min=0.0, max=1.0,
                             description="Blend between the target's rest pose and the "
                                         "retargeted rotation")
    method: StringProperty(name="Method", default="")
    confidence: FloatProperty(name="Confidence", default=0.0, min=0.0, max=1.0)
    calibration_quat: FloatVectorProperty(
        name="Retarget Pose", size=4, default=(1.0, 0.0, 0.0, 0.0), subtype='QUATERNION',
        description="Target bone world orientation in the retarget pose",
    )
    has_calibration: BoolProperty(name="Has Calibration", default=False)
    manual_offset: FloatVectorProperty(
        name="Manual Offset", size=3, default=(0.0, 0.0, 0.0), subtype='EULER',
        description="Extra correction applied in the target bone's local space",
    )

    @property
    def group(self) -> str:
        return key_group(self.key) if self.key else 'ROOT'


class SVMR_ActionItem(PropertyGroup):
    """One source Action offered for batch retargeting."""

    name: StringProperty(name="Action")
    selected: BoolProperty(name="Selected", default=False)
    result: StringProperty(name="Result", default="")


class SVMR_ReportItem(PropertyGroup):
    level: StringProperty(default="INFO")
    category: StringProperty(default="")
    message: StringProperty(default="")


class SVMR_Settings(PropertyGroup):
    """All retargeter settings for one scene."""

    # ---- source / target ------------------------------------------------
    source_armature: PointerProperty(
        name="Source", type=bpy.types.Object, poll=_is_armature,
        description="Imported Source-engine viewmodel armature",
    )
    target_armature: PointerProperty(
        name="Target", type=bpy.types.Object, poll=_is_armature,
        description="Unreal Mannequin armature (never modified structurally)",
    )
    target_profile: EnumProperty(
        name="Target Profile", items=_TARGET_PROFILE_ITEMS, default='AUTO',
    )
    detected_profile: StringProperty(name="Detected", default="")

    # ---- mapping --------------------------------------------------------
    mapping: CollectionProperty(type=SVMR_BoneMapItem)
    mapping_index: IntProperty(default=0)
    mapping_filter: EnumProperty(
        name="Show", items=[('ALL', "All", "")] + GROUP_ITEMS, default='ALL',
    )
    hide_unmapped: BoolProperty(name="Only Mapped", default=False)

    body_mode: EnumProperty(name="Body Mode", items=BODY_MODE_ITEMS,
                            default='PROCEDURAL_UPPER_BODY')
    use_fingers: BoolProperty(name="Retarget Fingers", default=True)

    # ---- calibration ----------------------------------------------------
    calibration_mode: EnumProperty(name="Retarget Pose", items=CALIBRATION_ITEMS,
                                   default='AUTO_ALIGN')
    calibration_pose_source: EnumProperty(
        name="Source Pose", default='AUTO',
        items=[
            ('AUTO', "Auto",
             "Use a frame of the source Action when it places bones away from "
             "their rest offsets (SMD clips normally do), otherwise the rest pose"),
            ('REST', "Rest Pose", "Use the source armature's bind pose as the neutral"),
            ('FRAME', "Action Frame", "Use one frame of the source Action as the neutral"),
        ],
    )
    calibration_frame: IntProperty(name="Calibration Frame", default=1)
    global_align_mode: EnumProperty(
        name="Global Align", default='AUTO',
        items=[
            ('AUTO', "Auto", "Derive the rig-to-rig orientation from shoulders and spine"),
            ('NONE', "None", "Assume both rigs already face the same way"),
            ('MANUAL', "Manual", "Use the rotation entered below"),
        ],
    )
    global_align_euler: FloatVectorProperty(
        name="Global Rotation", size=3, default=(0.0, 0.0, 0.0), subtype='EULER',
    )
    scale_mode: EnumProperty(
        name="Scale", default='AUTO',
        items=[
            ('AUTO', "Auto", "Measure the unit scale from the arm chain lengths"),
            ('MANUAL', "Manual", "Use the factor entered below"),
        ],
    )
    scale_factor: FloatProperty(name="Scale Factor", default=1.0, min=1.0e-6,
                                description="Source -> target unit conversion")

    # ---- weapon ---------------------------------------------------------
    primary_hand: EnumProperty(
        name="Primary Hand", default='R',
        items=[('L', "Left", "Left hand holds the weapon"),
               ('R', "Right", "Right hand holds the weapon")],
    )
    weapon_enabled: BoolProperty(name="Weapon", default=True)
    weapon_reference_bone: StringProperty(
        name="Weapon Reference",
        description="Source bone the weapon geometry follows (weapon root / grip)",
    )
    primary_grip_bone: StringProperty(
        name="Primary Grip",
        description="Optional marker bone ON THE WEAPON that the primary hand "
                    "holds. When set, the weapon is seated so this point lands "
                    "exactly in the retargeted hand. Leave empty to keep the "
                    "source hand-to-weapon relationship",
    )
    secondary_grip_bone: StringProperty(
        name="Secondary Grip",
        description="Optional marker bone ON THE WEAPON that the support hand "
                    "holds. When set, two-hand IK pins the support hand to this "
                    "point instead of following the source hand. Leave empty to "
                    "reproduce the source animation exactly",
    )
    weapon_follow_mode: EnumProperty(
        name="Weapon Follows", default='HAND_RELATIVE',
        items=[
            ('HAND_RELATIVE', "Hand (Source Relative)",
             "Weapon follows the retargeted primary hand and keeps the source's "
             "per-frame weapon-to-hand motion"),
            ('HAND_RIGID', "Hand (Rigid)",
             "Weapon is locked rigidly to the primary hand - zero grip slide"),
            ('NONE', "None", "Do not create a weapon anchor"),
        ],
    )
    weapon_object: PointerProperty(
        name="Weapon Object", type=bpy.types.Object,
        description="Optional weapon object to preview on the anchor",
    )
    drive_ue_ik_bones: BoolProperty(
        name="Drive UE IK Bones", default=True,
        description="Key ik_hand_gun / ik_hand_l / ik_hand_r so weapon attachment "
                    "works in Unreal",
    )
    ik_gun_hand: EnumProperty(
        name="ik_hand_gun Follows", default='R',
        items=[('L', "Left Hand", ""), ('R', "Right Hand", "")],
    )

    # ---- two-hand IK ----------------------------------------------------
    two_hand_ik: BoolProperty(
        name="Two-Hand IK", default=True,
        description="Pin the secondary hand to the weapon instead of retargeting it "
                    "independently (prevents sliding)",
    )
    ik_blend: FloatProperty(name="IK / FK Blend", default=1.0, min=0.0, max=1.0)
    use_elbow_pole: BoolProperty(
        name="Use Elbow Pole", default=False,
        description="Control the secondary elbow with a pole target. When off, the "
                    "solver keeps the FK bend direction, which is usually closer to "
                    "the source animation",
    )

    # ---- procedural upper body -----------------------------------------
    shoulder_influence: FloatProperty(name="Shoulder Influence", default=0.25,
                                      min=0.0, max=1.0)
    clavicle_influence: FloatProperty(name="Clavicle Influence", default=0.10,
                                      min=0.0, max=1.0)
    chest_influence: FloatProperty(name="Chest Influence", default=0.10, min=0.0, max=1.0)
    spine_influence: FloatProperty(name="Spine Influence", default=0.05, min=0.0, max=1.0)
    max_spine_rotation: FloatProperty(
        name="Max Spine Rotation", default=math.radians(20.0), min=0.0,
        max=math.radians(90.0), subtype='ANGLE',
    )

    # ---- root motion ----------------------------------------------------
    root_motion: EnumProperty(
        name="Root Motion", default='IGNORE',
        items=[
            ('IGNORE', "Ignore",
             "Keep the Unreal root perfectly still - correct for viewmodel clips"),
            ('COPY_IF_AVAILABLE', "Copy If Available",
             "Copy translation from the mapped source root/pelvis when it exists"),
            ('CUSTOM', "Custom Bone", "Copy translation from the bone chosen below"),
        ],
    )
    root_motion_bone: StringProperty(name="Root Bone")

    # ---- frame range / bake --------------------------------------------
    use_action_range: BoolProperty(name="Use Action Range", default=True)
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=60)
    sample_step: IntProperty(name="Sampling Rate", default=1, min=1, max=10,
                             description="Sample the source every N frames when building "
                                         "IK/weapon helper curves")
    bake_step: IntProperty(name="Bake Step", default=1, min=1, max=10)
    visual_keying: BoolProperty(name="Visual Keying", default=True)
    clean_curves: BoolProperty(name="Clean Curves", default=False)
    quaternion_cleanup: BoolProperty(name="Quaternion Cleanup", default=True)
    key_reduction: FloatProperty(
        name="Key Reduction", default=0.0, min=0.0, max=1.0, precision=4,
        description="Tolerance for removing redundant keyframes. 0 disables it",
    )
    use_bake_fps: BoolProperty(name="Override FPS", default=False)
    bake_fps: IntProperty(name="FPS", default=30, min=1, max=240)
    keep_rig_after_bake: BoolProperty(
        name="Keep Rig After Bake", default=False,
        description="Leave the retarget rig in place so you can keep tweaking it",
    )
    action_name_override: StringProperty(name="Action Name")

    # ---- batch ----------------------------------------------------------
    actions: CollectionProperty(type=SVMR_ActionItem)
    actions_index: IntProperty(default=0)

    # ---- validation / report -------------------------------------------
    validation_step: IntProperty(name="Validation Step", default=1, min=1, max=20)
    report: CollectionProperty(type=SVMR_ReportItem)
    report_index: IntProperty(default=0)
    analysis_text: CollectionProperty(type=SVMR_ReportItem)

    # ---- export ---------------------------------------------------------
    export_path: StringProperty(name="Path", subtype='FILE_PATH',
                                default="//retargeted.fbx")
    export_include_mesh: BoolProperty(name="Include Mesh", default=False)
    export_frame_start: IntProperty(name="Start", default=1)
    export_frame_end: IntProperty(name="End", default=60)

    # ---- runtime state --------------------------------------------------
    driver_object: PointerProperty(name="Driver Rig", type=bpy.types.Object)
    stashed_target_action: PointerProperty(name="Stashed Action", type=bpy.types.Action)
    rig_built: BoolProperty(default=False)
    driven_bones: StringProperty(default="")
    preset_name: StringProperty(name="Preset Name", default="my_source_rig")


CLASSES = (
    SVMR_BoneMapItem,
    SVMR_ActionItem,
    SVMR_ReportItem,
    SVMR_Settings,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.svmr = PointerProperty(type=SVMR_Settings)


def unregister() -> None:
    if hasattr(bpy.types.Scene, "svmr"):
        del bpy.types.Scene.svmr
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
