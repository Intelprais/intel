"""UILists for the bone mapping and the batch Action list."""

from __future__ import annotations

from typing import List

import bpy
from bpy.types import UIList

from ..profiles.base import key_group

_METHOD_ICONS = {
    'STRUCTURAL': 'OUTLINER_OB_ARMATURE',
    'EXACT': 'CHECKMARK',
    'NORMALIZED': 'CHECKMARK',
    'HEURISTIC': 'QUESTION',
    'CHAIN': 'IPO_EASE_IN_OUT',
    'MANUAL': 'GREASEPENCIL',
    'PRESET': 'PRESET',
}


class SVMR_UL_mapping(UIList):
    """One row per canonical bone: enable, key, source bone, target bone."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        settings = context.scene.svmr
        source = settings.source_armature

        row = layout.row(align=True)
        row.prop(item, "enabled", text="")

        key_row = row.row()
        key_row.scale_x = 0.9
        key_row.alert = item.enabled and not item.source_bone
        key_row.label(text=item.key, icon=_METHOD_ICONS.get(item.method, 'DOT'))

        if source is not None:
            row.prop_search(item, "source_bone", source.data, "bones", text="",
                            icon='BONE_DATA')
        else:
            row.prop(item, "source_bone", text="")

        target_row = row.row()
        target_row.scale_x = 0.8
        target_row.enabled = False
        target_row.label(text=item.target_bone or "-")

        if item.enabled and item.source_bone and item.confidence < 0.7:
            row.label(text="", icon='ERROR')

    def filter_items(self, context, data, propname):
        settings = context.scene.svmr
        items = getattr(data, propname)
        flags: List[int] = []
        wanted = settings.mapping_filter

        for item in items:
            visible = True
            if wanted != 'ALL' and key_group(item.key) != wanted:
                visible = False
            if settings.hide_unmapped and not item.source_bone:
                visible = False
            flags.append(self.bitflag_filter_item if visible else 0)
        return flags, []


class SVMR_UL_actions(UIList):
    """Source Actions offered for batch retargeting."""

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.name, icon='ACTION')
        if item.result:
            sub = row.row()
            sub.enabled = False
            sub.label(text=item.result,
                      icon='CHECKMARK' if item.result != "FAILED" else 'ERROR')


class SVMR_UL_report(UIList):
    """Validation report lines."""

    _ICONS = {'ERROR': 'CANCEL', 'WARNING': 'ERROR', 'INFO': 'INFO'}

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        row = layout.row(align=True)
        row.alert = item.level == 'ERROR'
        label = f"{item.category}: {item.message}" if item.category else item.message
        row.label(text=label, icon=self._ICONS.get(item.level, 'DOT'))


CLASSES = (SVMR_UL_mapping, SVMR_UL_actions, SVMR_UL_report)
