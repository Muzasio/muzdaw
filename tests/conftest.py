"""Run all tests headless and silent: no plugin can open a window or dialog on the desktop,
and nothing can play on the speakers (MUZDAW_SILENT for preview/play processes).

Also point the config/data folders at a temp dir, so tests never touch the user's real ~/.config.
"""
import os
import tempfile

for var in ("DISPLAY", "WAYLAND_DISPLAY"):
    os.environ.pop(var, None)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["MUZDAW_SILENT"] = "1"
_tmp = tempfile.mkdtemp(prefix="muzdaw-tests-")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_tmp, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_tmp, "data")
