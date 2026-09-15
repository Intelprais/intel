# Source VM Retargeter

A Blender add-on that transfers **first-person viewmodel animation** (weapon +
hands) from Source-engine models onto the **UE4 Mannequin** or **UE5
Manny/Quinn** skeleton, and exports it ready for Unreal.

It is transform-based: no Euler angle is ever copied from one rig to the other.
Bone axes, rolls, rest poses, proportions, hierarchies and units are all allowed
to differ.

> **Read this first:** a Source viewmodel contains arms, hands and a weapon.
> It does **not** contain pelvis, legs, feet, a full spine or character root
> motion. This add-on will never invent them, and it says so in its validation
> report. See [Known Limitations](#known-limitations).

Русская версия: [`docs/README.ru.md`](docs/README.ru.md)

---

## Table of contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Supported Blender versions](#supported-blender-versions)
- [Supported targets](#supported-targets)
- [Source import workflow](#source-import-workflow)
- [Bone mapping](#bone-mapping)
- [Retarget pose (calibration)](#retarget-pose-calibration)
- [Weapon setup](#weapon-setup)
- [Two-hand IK](#two-hand-ik)
- [Procedural upper body](#procedural-upper-body)
- [Root motion](#root-motion)
- [Bake](#bake)
- [Batch processing](#batch-processing)
- [Validation](#validation)
- [Unreal export](#unreal-export)
- [How the retarget works](#how-the-retarget-works)
- [Architecture](#architecture)
- [Running the tests](#running-the-tests)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)

---

## Installation

**Blender 4.2 and newer (Extension):**

1. Zip the `source_vm_retargeter/` folder.
2. `Edit > Preferences > Get Extensions > Install from Disk...` and pick the zip.

**Blender 4.0 / 4.1 (legacy add-on):**

1. Zip the `source_vm_retargeter/` folder.
2. `Edit > Preferences > Add-ons > Install...` and pick the zip, then enable
   *Source VM Retargeter*.

The panel appears in the 3D viewport sidebar (<kbd>N</kbd>) under
**Source VM Retarget**.

The add-on has no third-party dependencies. It writes only two things outside
the .blend: bone-mapping presets (in Blender's user config directory) and the
FBX files you ask it to export.

---

## Quick start

Put your Unreal rig and your imported Source rig in the same scene (collections
named `UE` and `Source` are a convenient convention, but not required), then:

1. **Source** - pick the imported Source viewmodel armature.
2. **Target** - pick the Unreal Mannequin armature. Press **Analyse Rigs**;
   the skeleton profile is detected automatically.
3. **Auto Detect Mapping**.
4. **Detect Weapon** (skip for hands-only clips).
5. **Build / Preview Retarget** - the result is live in the viewport. Scrub the
   timeline; there is no background handler and nothing is baked yet.
6. **Bake Retarget** - produces `RTG_<source action>_<profile>` on the Unreal
   rig and removes every temporary constraint and helper.
7. **Validate**.
8. **Export FBX For Unreal**.

Nothing above modifies your Source rig, your Source Actions, or the Unreal
skeleton's hierarchy and rest pose.

---

## Supported Blender versions

| Blender | Status |
| --- | --- |
| 4.2 - 4.5 LTS | Supported, installs as an Extension (`blender_manifest.toml`) |
| 4.0 - 4.1 | Supported, installs as a legacy add-on (`bl_info`) |
| 3.x | Not supported |

Developed and tested against Blender 4.5 LTS.

---

## Supported targets

Target skeletons are described by **profiles**, so UE4 and UE5 are never
treated as the same rig.

| Profile | Skeleton | Notes |
| --- | --- | --- |
| `UE4_MANNEQUIN` | `SK_Mannequin` | `spine_01..03`, single twist bones |
| `UE5_MANNY` | `SKM_Manny` / `SKM_Quinn` | `spine_01..05`, metacarpals, dual twist bones, `clavicle_out`/`clavicle_scap` |

Both profiles know Unreal's non-deforming IK bones (`ik_hand_root`,
`ik_hand_gun`, `ik_hand_l/r`, `ik_foot_*`). Set **Target Profile** to *Auto
Detect* to have the right one chosen from the bone names.

Twist bones, metacarpals and `clavicle_out`/`clavicle_scap` never receive
retargeted animation - in Unreal they are driven by the engine, and writing to
them would fight the AnimBP.

---

## Source import workflow

The retargeter is deliberately separate from any decompiler. It works on an
armature that is *already in your scene*, however it got there:

```
MDL  ->  Crowbar (or another decompiler)  ->  SMD / DMX
     ->  Blender Source Tools (or any compatible importer)
     ->  Source VM Retargeter
```

If you already have the rig as SMD/DMX/FBX/.blend, skip straight to the
add-on. Nothing in the retarget path depends on Blender Source Tools being
installed.

**Units.** Source rigs are usually in Hammer units and Unreal rigs in metres or
centimetres. You do not need to rescale anything: the add-on measures the arm
chains on both rigs and derives the conversion factor itself (shown under
*Retarget Pose > Scale*). It uses that factor for IK targets, the weapon anchor
and root motion, and never applies it to your objects.

---

## Bone mapping

Everything is keyed by a *canonical bone key* (`hand_r`, `index_02_l`,
`spine_03`, ...). **Auto Detect Mapping** resolves each key with four
strategies, strongest first:

1. **Exact** - a literal name from the built-in Source naming tables
   (`ValveBiped.*`, `bip_*`, and more).
2. **Structural** - the arm and finger chains found by analysing the rig's
   shape. A hand is a bone with at least three short, non-branching, terminal
   child chains, so this works on rigs whose names have never been seen before,
   in any language.
3. **Normalised** - a table name after prefix/punctuation normalisation
   (`ValveBiped.Bip01_R_UpperArm` and `bip-upperarm.R` normalise alike).
4. **Heuristic** - token-overlap similarity above a threshold. Left/right
   crossover is impossible: a side mismatch scores zero.

Rows show the method as an icon and flag anything below 70% confidence. Edit
any row by hand and press **Mark As Manual** to protect it from the next
Auto Detect.

**Finger chains of different lengths** are converted proportionally rather than
dropped: a 2-bone Source finger maps onto UE's 3 segments, and a 4-bone chain
compresses onto 3.

**Presets** save the mapping *and* the calibration as JSON, keyed by canonical
name, so one preset covers every rig that shares a naming scheme. Bones missing
on the current rigs are kept but disabled, so you can see exactly what did not
resolve.

**Body Mode** controls which keys are mapped:

| Mode | Retargets | Lower body |
| --- | --- | --- |
| `ARMS_ONLY` | arms + fingers | untouched, unkeyed |
| `PROCEDURAL_UPPER_BODY` | arms + fingers + spine/clavicle/head | untouched, unkeyed |
| `NEUTRAL_FULL_BODY` | as above | keyed **at its rest pose**, so the exported clip is full-body |

`NEUTRAL_FULL_BODY` does not invent leg motion - it writes the neutral pose
explicitly so Unreal receives a complete take instead of an upper-body-only one.

---

## Retarget pose (calibration)

The Source bind pose is rarely the Unreal bind pose. Calibration computes, per
bone, the constant correction that makes them agree.

| Mode | Use when |
| --- | --- |
| **Auto Align** (default) | Anything. Each target bone's reference orientation is swung onto the source bone's direction, absorbing A-pose vs T-pose and unusual viewmodel bind poses. |
| **Rest To Rest** | The two rigs were authored in the same pose. |
| **Manual** | Pose the Unreal rig by hand so it matches the source, then press **Capture Retarget Pose**. |

Auto Align measures each bone's **limb direction from the chain** (the vector
to the next joint), not from the bone's own +Y axis. Both SMD and Unreal FBX
imports keep engine-native bone rotations, so +Y is usually *perpendicular* to
the limb and differs between the two rigs - calibrating on it puts the elbow
tens of degrees out.

Additional controls:

- **Source Pose** - which pose is the neutral.
  - *Auto* (default) - uses a frame of the source Action when that Action holds
    bones away from their rest offsets, otherwise the rest pose. SMD stores an
    absolute transform per bone per frame, so decompiled Source clips routinely
    place a joint somewhere the reference pose does not; calibrating against the
    rest pose would then measure the wrong limb geometry.
  - *Rest Pose* / *Action Frame* - force one or the other.
- **Global Align** - the rig-to-rig orientation, derived from the shoulder axis
  and the spine. Set it manually if your rigs have no usable spine.
- **Scale** - auto (measured from arm chains) or manual.
- **Manual Offset** - a per-bone Euler tweak on top of everything else.

The Unreal skeleton is never re-posed or re-rolled to make this work. Only
correction offsets are computed.

---

## Weapon setup

**Detect Weapon** finds the weapon root and labels the parts it recognises
(magazine/clip, bolt, slide, trigger, charging handle, hammer, cylinder,
attachments). It never assumes all of them exist, and it never assigns anything
that would silently change your result - grip candidates are reported, and you
opt in.

- **Primary Hand** - which hand holds the weapon.
- **Weapon Reference** - the source bone the weapon geometry follows.
- **Weapon Follows**
  - *Hand (Source Relative)* (default) - the weapon follows the retargeted
    primary hand and keeps the source's per-frame weapon-to-hand motion, so a
    reload that tilts the gun still tilts it.
  - *Hand (Rigid)* - locked rigidly to the hand; zero grip slide.
  - *None* - no anchor.
- **Grip markers** (optional, bones **on the weapon**)
  - *Primary* - the weapon is seated so this point lands exactly in the
    retargeted hand.
  - *Secondary* - two-hand IK pins the support hand to this point instead of
    following the source hand.
  Leave both empty to reproduce the source animation exactly.
- **Drive UE IK Bones** - keys `ik_hand_gun`, `ik_hand_l` and `ik_hand_r` onto
  the hands. Unreal's weapon attachment reads those bones, so leave this on.
  Choose which hand `ik_hand_gun` follows.

---

## Two-hand IK

FK-retargeting both arms independently makes the support hand slide on the
weapon, because the two rigs' shoulders are not in the same place. Two-Hand IK
solves that:

- the **primary** hand is pure FK from the source;
- the **weapon anchor** follows that hand;
- the **support** hand is an IK target on the weapon, sampled per frame from the
  source's own hand-to-weapon relationship (or pinned to a grip marker);
- the support elbow follows the FK bend direction by default, or an explicit
  **pole target** whose angle is solved automatically.

**IK / FK Blend** blends the whole thing back towards plain FK. The helper
empties (`VM_RTG_IK_HAND_*`, `VM_RTG_POLE_*`, `VM_RTG_WEAPON_ANCHOR`) are
colour-coded and visible in the viewport for debugging.

If the grip is out of the target arm's reach, validation reports it as an error
rather than letting the hand drift silently.

---

## Procedural upper body

Viewmodel arms often move much more than a real torso would allow. In
`PROCEDURAL_UPPER_BODY` / `NEUTRAL_FULL_BODY`, a fraction of the hand's rotation
is distributed onto the torso:

| Setting | Drives | Default |
| --- | --- | --- |
| Shoulder Influence | clavicles, from the same-side upper arm | 0.25 |
| Clavicle Influence | clavicles, from the same-side hand | 0.10 |
| Chest Influence | `spine_03`, from the primary hand | 0.10 |
| Spine Influence | `spine_01`, `spine_02`, from the primary hand | 0.05 |
| Max Spine Rotation | hard clamp on every procedural bone | 20 deg |

Defaults are deliberately conservative: the goal is a more natural pose, not a
different animation.

**Bones that the source actually animates are never overridden.** If your Source
rig has a real spine and it is mapped, the real animation wins and the
procedural pass skips those bones.

---

## Root motion

| Policy | Behaviour |
| --- | --- |
| **Ignore** (default) | The Unreal root stays perfectly still. Correct for viewmodel clips. |
| **Copy If Available** | Copies translation from the mapped source root/pelvis, converted into target units. |
| **Custom Bone** | Same, from a bone you choose. |

The default never produces a `root.location` F-Curve at all, so nothing in
Unreal mistakes a viewmodel clip for a root-motion clip.

---

## Bake

**Bake Retarget** samples the live rig into a new Action and then removes every
constraint and helper, so the result is standalone - you can delete the Source
rig entirely and the clip still plays.

| Setting | Meaning |
| --- | --- |
| Use Action Range | Take the frame range from the source Action |
| Frame Start / End | Manual range |
| Sampling Rate | How often the source is sampled when building IK/weapon curves |
| Bake Step | Bake every N frames |
| Visual Keying | Bake the evaluated (constrained) result |
| Quaternion Cleanup | Remove +/-180 degree flips from the baked curves |
| Clean Curves | Blender's own redundant-key removal |
| Key Reduction | Tolerance for Douglas-Peucker key decimation (0 = off) |
| Override FPS | Set the scene FPS used for the bake/export |
| Name | Override the generated Action name |
| Keep Rig After Bake | Leave the retarget rig in place for further tweaking |

The source Action is never modified, and the generated Action gets a fake user
so it survives a save/reload.

---

## Batch processing

**Actions > Refresh Action List** finds every Action that animates the source
rig (ignoring previously retargeted `RTG_*` ones). Tick the ones you want and
press **Batch Retarget**.

Each clip is built, baked and cleaned independently, producing
`RTG_<name>_<profile>`, with per-row OK/FAILED feedback. From the UI it runs as
a modal operator with progress in the status bar and <kbd>Esc</kbd> to cancel,
so Blender never freezes without telling you what it is doing. From a script it
runs synchronously.

---

## Validation

**Validate** samples both rigs and measures, rather than guesses:

- missing or unmapped bones, and animated source bones that are not retargeted;
- Unreal hierarchy integrity and missing `ik_*` bones;
- NaN / infinite transforms;
- extreme single-frame rotations (flip detection);
- unit scale, non-uniform and mirrored object scale, non-unit bone scale keys;
- hand-separation drift and support-hand grip drift, as a ratio of arm length;
- weapon drift relative to the primary hand;
- frame range and FPS.

Results are `ERROR` / `WARNING` / `INFO` with numeric metrics, and can be saved
to a text file.

---

## Unreal export

The safest pipeline is the one the add-on uses: **export the original Unreal
armature with a freshly baked Action on it.** No bones are added, renamed or
reparented, so the clip imports straight onto your existing Skeleton asset.

**Check Export** runs a pre-flight first and refuses to export if:

- temporary retarget constraints are still on the rig (the clip would depend on
  the Source rig);
- the target has no active Action;
- helper bones somehow ended up in the target skeleton.

Export settings match what Unreal expects: `add_leaf_bones=False`,
primary bone axis `Y`, secondary `X`, `armature_nodetype='NULL'`, all bones
baked, NLA strips off. Helper objects are always excluded from the selection.

In Unreal, import as **Animation** against your existing Skeleton.

---

## How the retarget works

For every mapped bone the retarget is one constant rotation:

```
P_target(f) = G · P_source(f) · K(bone)
```

- `P_source(f)` - the source bone's **world** matrix at frame `f`;
- `G` - a single rotation aligning the two rigs' body frames, derived from the
  shoulder axis and the spine;
- `K(bone)` - the constant offset from calibration,
  `K = (G · A_source)⁻¹ · A_target`, where `A_*` are the two rigs' orientations
  in a visually identical pose.

Because everything happens in world space and relative to a calibration pose,
differing bone rolls, axis conventions and rest poses cancel out. The property
that matters is:

```
delta_target(f) = G · delta_source(f) · G⁻¹
```

i.e. a 30-degree source bend is a 30-degree target bend, in the target's own
body frame. That identity is asserted by the test-suite across every mapped
bone and frame.

`G · P_source · K` is exactly what Blender's bone parenting computes, so no
Python runs per frame. The add-on builds a small helper armature where:

- `src_<key>` copies the source bone's world rotation into pose space
  (Copy Rotation, `WORLD -> POSE`), and the helper object's own matrix is `G`;
- `drv_<key>` is its child whose rest matrix is `rest(src) · K`.

The Unreal bone then just Copy-Rotations from `drv_<key>`. The preview is live
with no frame-change handler, and the bake is a plain sample of the same rig -
so **what you preview is exactly what you bake**.

Translation is never copied bone-to-bone: arm proportions differ, so positions
are derived from the target's own kinematics, and only IK targets, the weapon
anchor and root motion use the measured unit scale.

---

## Architecture

```
source_vm_retargeter/
├── __init__.py            registration; bl_info + blender_manifest.toml
├── props.py               all settings (PropertyGroups)
├── prefs.py               add-on preferences
├── core/                  logging, matrix/quaternion maths, naming, bones, scene
├── profiles/              canonical keys, UE4/UE5 target profiles, Source tables
├── analysis/              read-only rig inspection (hands, fingers, weapon)
├── mapping/               auto-mapping and JSON presets
├── retarget/              calibration, rig construction, sampling, baking
├── validation/            measured checks and the report
├── exporting/             Unreal FBX preset and pre-flight
├── ops/                   operators
└── ui/                    N-panel and UILists
```

Every helper object lives in the `VM_RETARGET_HELPERS` collection and is tagged,
and every temporary constraint is named `RTG_*`, so cleanup removes exactly what
the add-on created and nothing else.

---

## Verified on real data

Checked against a decompiled Source first-person hands viewmodel (39 bones,
ValveBiped naming, no weapon, 44 clips) retargeted onto a UE4 Mannequin in the
same scene, with the pose compared by **joint angle** - measured from bone head
positions only, so the check is independent of bone conventions and of the
retarget maths itself:

| Clip | Frames | Bake | Worst elbow error | Worst wrist error |
| --- | --- | --- | --- | --- |
| `fc5_idle` | 0-60 | 0.40 s | 0.000 deg | 0.000 deg |
| `fc5_draw` | 0-2 | 0.10 s | 0.000 deg | 0.000 deg |
| `fc5_inspect2` | 0-275 | 1.22 s | 0.000 deg | 0.505 deg |
| `fc5_punchLeft` | 0-13 | 0.11 s | 0.000 deg | 0.001 deg |
| `fc5_run1` | 0-57 | 0.24 s | 0.020 deg | 0.019 deg |
| `fc5_swimIdle` | 0-110 | 0.42 s | 0.000 deg | 0.000 deg |

Auto-mapping resolved 39 of 43 canonical keys at full confidence; the four it
left alone (`spine_01`, `spine_02`, `neck_01`, `head`) genuinely do not exist on
that rig. The residual on `fc5_inspect2` is the one clip that animates real bone
translation, which a rotation-only retarget cannot reproduce.

## Running the tests

The suite builds both rigs from scratch - a UE4 Mannequin-like target in metres
and a ValveBiped viewmodel in Hammer units, rotated in world space, with a
different bind pose and a weapon - so it needs no assets.

```bash
blender --background --python tests/run_tests.py
```

It also runs against the `bpy` PyPI module:

```bash
pip install bpy==4.5.14
python tests/run_tests.py
```

107 tests cover naming, the maths helpers, rig analysis, auto-mapping,
calibration, the retarget identity above, flips, two-hand IK, grip markers,
procedural torso, baking, non-destructiveness, batch, presets, validation,
UE5, export, UI wiring, engine-native bone axes and source bone translation.

---

## Troubleshooting

**The elbow or wrist is off by tens of degrees, but nothing errors.** The rig's
bone axes do not run along the limbs (normal for SMD and FBX imports). Make sure
*Retarget Pose* is on *Auto Align*, which measures limb direction from the chain.
If it persists, the source Action may hold bones off their rest offsets - set
*Source Pose* to *Action Frame*, or leave it on *Auto*.

**Arms point the wrong way.** *Global Align* could not find a body frame -
usually because both upper arms are not mapped. Map them, or set *Global Align*
to *Manual* and dial in the rotation.

**The whole target is tiny or enormous.** The unit scale is measured from the
arm chains; if the arm mapping is wrong the scale will be too. Check
*Retarget Pose > Scale*, or switch it to *Manual*.

**The pose is rotated by a constant amount.** Your Source bind pose differs from
the Unreal one. Use *Auto Align*, or pose the Unreal rig to match and press
*Capture Retarget Pose*.

**The support hand slides on the weapon.** Turn on *Two-Hand IK*. If it is
already on, check the *Weapon Reference* bone, and run *Validate* - it reports
the slide as a percentage of arm length.

**Validation reports the support hand "misses the grip".** The IK target is out
of the target arm's reach. Move the grip marker, or check the unit scale.

**A bone flips 180 degrees.** Run *Validate* to find which bone and frame, then
enable *Quaternion Cleanup* on the bake. If it persists, the source bone itself
flips - check the source curve.

**Unreal rejects the FBX, or the clip is offset.** Run *Check Export*. The most
common cause is exporting while the retarget constraints are still live - bake
first.

**The exported clip drifts across the level.** Set *Root Motion* to *Ignore*.

**Fingers do not move.** Enable *Retarget Fingers* and re-run Auto Detect. If
the source finger chains are shorter than three bones, they are converted
proportionally - check the mapping rows marked `CHAIN`.

---

## Known limitations

- **A viewmodel has no lower body.** Source viewmodels animate arms, hands and a
  weapon. They contain no pelvis, legs, feet, full spine or character root
  motion. This add-on will not fabricate them. `PROCEDURAL_UPPER_BODY` adds a
  plausible, clamped torso reaction derived from the arms - it is a pose aid,
  not recovered motion - and `NEUTRAL_FULL_BODY` writes the legs at their rest
  pose so the clip is full-body without pretending to be locomotion. Layering
  the retargeted upper body over an existing locomotion clip is left to Unreal's
  AnimBP (or a future NLA-based mode).
- Rotation only. Bone-to-bone translation is never transferred; squash/stretch
  and scale animation on the source are not retargeted. Source clips do animate
  bone translation (limbs stretching by a few percent), and validation reports
  it as INFO - joint angles still match, but limb stretching does not carry over.
- Mirrored (negatively scaled) armatures are de-mirrored, because a mirror is
  not a rotation. Validation warns when it sees one.
- Auto-mapping needs both upper arms to resolve the global alignment. Hands-only
  rigs with no arm chain require a manual mapping and a manual global rotation.
- Finger chain conversion between different lengths is proportional. A 2-into-3
  conversion leaves the last two target segments collinear.
- Only UE4 Mannequin and UE5 Manny/Quinn ship as profiles. Other skeletons work
  through manual mapping but have no built-in profile.
- MDL decompilation is out of scope; bring an already-imported armature.

---

## License

GPL-3.0-or-later, matching Blender's add-on requirements.

The add-on ships no game assets and is neutral about where your rigs come from.
