"""Compatibility facade for the platform-owned Ableton detector.

New code should depend on copilot.platform.detection or the driver API.
This module remains for existing callers and frozen CLI contracts.
"""

from copilot.platform.detection import (
    AbletonDetection,
    _candidate_install_roots,
    _first_live_bundle,
    _first_live_exe,
    _is_live_prefs_folder,
    _latest_prefs_root,
    _live_process_running,
    _port_open,
    _prefs_version_key,
    _registry_key_values,
    _registry_uninstall,
    _reg_str,
    _start_menu_ableton_links,
    _user_library_remote_scripts,
    _user_remote_scripts_dir,
    _version_from_prefs_name,
    default_user_library_candidates,
    detect_ableton,
    live_block_status,
    prefs_search_roots,
    write_detection,
)

__all__ = [
    "AbletonDetection",
    "detect_ableton",
    "live_block_status",
    "default_user_library_candidates",
    "prefs_search_roots",
    "write_detection",
]
