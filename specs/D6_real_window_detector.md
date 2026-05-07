# D6 — Real MC window detector module

Implement `bin/mc_window_detector.py` with `find_minecraft_window_rect()`
that returns (x, y, w, h, dpi_ratio) of the Minecraft window OR None.
Uses Quartz (macOS) / win32api (Windows) / xprop (Linux). NO mock, NO
hardcoded coords. Pure stdlib + platform-native APIs.

Tests: pytest fixture verifies on the host platform.
