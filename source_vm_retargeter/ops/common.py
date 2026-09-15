"""Shared operator helpers."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

import bpy

from ..core.log import get_logger

LOG = get_logger()


def settings_of(context):
    return context.scene.svmr


def have_rigs(context) -> bool:
    settings = settings_of(context)
    return settings.source_armature is not None and settings.target_armature is not None


def fill_report(collection, entries: Iterable) -> None:
    """Replace a report CollectionProperty with ``(level, category, message)``."""
    collection.clear()
    for entry in entries:
        item = collection.add()
        if isinstance(entry, str):
            item.level, item.category, item.message = 'INFO', "", entry
        else:
            item.level = getattr(entry, "level", 'INFO')
            item.category = getattr(entry, "category", "")
            item.message = getattr(entry, "message", str(entry))


def fill_lines(collection, lines: Sequence[str], level: str = 'INFO') -> None:
    collection.clear()
    for line in lines:
        item = collection.add()
        item.level = level
        item.category = ""
        item.message = line


class ProgressGuard:
    """``window_manager.progress_*`` that also works in background mode."""

    def __init__(self, context, total: int) -> None:
        self.wm = getattr(context, "window_manager", None)
        self.total = max(1, total)
        self.active = False

    def __enter__(self) -> "ProgressGuard":
        try:
            if self.wm is not None:
                self.wm.progress_begin(0, self.total)
                self.active = True
        except (AttributeError, RuntimeError):
            self.active = False
        return self

    def update(self, value: float) -> None:
        if self.active:
            try:
                self.wm.progress_update(value)
            except (AttributeError, RuntimeError):
                pass

    def __exit__(self, *exc) -> None:
        if self.active:
            try:
                self.wm.progress_end()
            except (AttributeError, RuntimeError):
                pass
