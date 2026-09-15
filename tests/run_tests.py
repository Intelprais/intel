"""Run the add-on test-suite inside Blender.

    blender --background --python tests/run_tests.py
    # or, with the `bpy` pip module:
    python tests/run_tests.py
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

import bpy  # noqa: E402

import source_vm_retargeter  # noqa: E402


def main() -> int:
    source_vm_retargeter.register()

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    names = sys.argv[1:] or ["test_units", "test_retarget", "test_pipeline"]
    for name in names:
        if "." in name:
            suite.addTests(loader.loadTestsFromName(name))
        else:
            suite.addTests(loader.loadTestsFromName(name))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # bpy-as-a-module can hang for minutes in its exit handlers; skip them.
    os._exit(code)
