"""Scene / datablock helpers.

Every object the add-on creates is tagged and placed in a dedicated
collection so a rebuild can find and remove exactly its own leftovers,
never touching user data.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

import bpy

#: Collection holding every helper object created by the add-on.
HELPER_COLLECTION = "VM_RETARGET_HELPERS"

#: Custom property marking add-on owned datablocks.
OWNER_TAG = "svmr_owned"

#: Prefix used for temporary constraints added to the *user's* target rig.
CONSTRAINT_PREFIX = "RTG_"


def ensure_helper_collection(context) -> bpy.types.Collection:
    """Return (creating if needed) the helper collection, linked to the scene."""
    coll = bpy.data.collections.get(HELPER_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(HELPER_COLLECTION)
        coll[OWNER_TAG] = True
    if coll.name not in {c.name for c in context.scene.collection.children_recursive}:
        try:
            context.scene.collection.children.link(coll)
        except RuntimeError:
            pass
    return coll


def tag_owned(datablock) -> None:
    datablock[OWNER_TAG] = True


def is_owned(datablock) -> bool:
    try:
        return bool(datablock.get(OWNER_TAG, False))
    except (AttributeError, TypeError):
        return False


def link_to_helpers(context, obj: bpy.types.Object) -> None:
    """Move ``obj`` into the helper collection exclusively."""
    coll = ensure_helper_collection(context)
    for existing in list(obj.users_collection):
        existing.objects.unlink(obj)
    coll.objects.link(obj)
    tag_owned(obj)


def owned_objects() -> List[bpy.types.Object]:
    return [obj for obj in bpy.data.objects if is_owned(obj)]


def remove_object(obj: Optional[bpy.types.Object]) -> None:
    """Fully remove an object and its orphaned data."""
    if obj is None or obj.name not in bpy.data.objects:
        return
    data = obj.data
    data_type = obj.type
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is None or data.users:
        return
    if data_type == 'ARMATURE' and data.name in bpy.data.armatures:
        bpy.data.armatures.remove(data)
    elif data_type == 'MESH' and data.name in bpy.data.meshes:
        bpy.data.meshes.remove(data)


def clear_helpers(context) -> int:
    """Remove every helper object the add-on owns.  Returns the count."""
    removed = 0
    for obj in owned_objects():
        remove_object(obj)
        removed += 1
    coll = bpy.data.collections.get(HELPER_COLLECTION)
    if coll is not None and is_owned(coll) and not coll.objects and not coll.children:
        bpy.data.collections.remove(coll)
    return removed


def remove_owned_constraints(armature_obj) -> int:
    """Strip constraints the add-on added to a user rig.  Returns the count."""
    if armature_obj is None or armature_obj.type != 'ARMATURE':
        return 0
    removed = 0
    for pose_bone in armature_obj.pose.bones:
        for constraint in list(pose_bone.constraints):
            if constraint.name.startswith(CONSTRAINT_PREFIX):
                pose_bone.constraints.remove(constraint)
                removed += 1
    return removed


def new_empty(context, name: str, display: str = 'PLAIN_AXES', size: float = 0.05,
              color: Optional[Iterable[float]] = None) -> bpy.types.Object:
    """Create a tagged empty inside the helper collection."""
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = display
    empty.empty_display_size = size
    if color is not None:
        empty.color = tuple(color)
    link_to_helpers(context, empty)
    return empty


def new_armature(context, name: str) -> bpy.types.Object:
    """Create a tagged armature object inside the helper collection."""
    data = bpy.data.armatures.new(name)
    tag_owned(data)
    obj = bpy.data.objects.new(name, data)
    link_to_helpers(context, obj)
    obj.show_in_front = True
    data.display_type = 'STICK'
    return obj
