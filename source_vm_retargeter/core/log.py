"""Logging for the Source VM Retargeter add-on.

A single named logger is used across the package.  In addition to normal
stream logging, messages emitted while a *capture* is active are collected so
they can be shown inside Blender's UI (validation report, operator reports).
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, List, Tuple

LOGGER_NAME = "source_vm_retargeter"

_handler: logging.Handler | None = None


class _CaptureHandler(logging.Handler):
    """Collects formatted records into a list of ``(levelname, message)``."""

    def __init__(self) -> None:
        super().__init__()
        self.records: List[Tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelname, record.getMessage()))


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def configure(level: int = logging.INFO) -> None:
    """Attach a single stream handler to the package logger (idempotent)."""
    global _handler
    logger = get_logger()
    logger.setLevel(level)
    logger.propagate = False
    if _handler is None:
        _handler = logging.StreamHandler()
        _handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    if _handler not in logger.handlers:
        logger.addHandler(_handler)


def teardown() -> None:
    logger = get_logger()
    if _handler is not None and _handler in logger.handlers:
        logger.removeHandler(_handler)


@contextmanager
def capture() -> Iterator[List[Tuple[str, str]]]:
    """Context manager collecting log records emitted inside the block."""
    handler = _CaptureHandler()
    logger = get_logger()
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
