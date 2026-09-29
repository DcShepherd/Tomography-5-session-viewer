"""Test-session isolation from the reviewer's real configuration.

Several tests build a ``MainWindow`` and close it or toggle its panels, and
those paths write the application's settings file. Without this redirect every
test run overwrote the real settings under ``%APPDATA%`` (theme, panel
visibility, last tab, recent sessions, and window geometry) with test values.

The config directory is resolved from ``APPDATA`` / ``XDG_CONFIG_HOME`` at call
time (``settings_service._config_dir``), so pointing both at a throwaway folder
before any test runs is enough. Set at import so it precedes collection.
"""

from __future__ import annotations

import os
import tempfile

TEST_CONFIG_ROOT = tempfile.mkdtemp(prefix="tomoapp-test-config-")
os.environ["APPDATA"] = TEST_CONFIG_ROOT
os.environ["XDG_CONFIG_HOME"] = TEST_CONFIG_ROOT

# Motion follows the operating system's reduced-motion setting (plan F4). Pin
# it to "allowed" so results do not depend on the machine running the tests;
# tests that exercise reduced motion set the variable or app property.
os.environ["TOMOAPP_REDUCE_MOTION"] = "0"
