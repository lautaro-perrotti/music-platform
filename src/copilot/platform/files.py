"""Platform-owned WAV handle inspection.

Windows Restart Manager and share-mode calls are intentionally isolated here.
The audio capture layer consumes these small cross-platform functions.
"""

from __future__ import annotations

import os
from pathlib import Path


def _exclusive_open_ok(path: Path) -> dict[str, object]:
    """True when no other process holds the file (Win32 share-none open)."""
    if not path.exists():
        return {"exists": False, "exclusive": True, "error": None, "size": None}
    size = int(path.stat().st_size)
    import ctypes

    generic_rw = ctypes.c_uint32(0x80000000 | 0x40000000).value
    open_existing = 3
    share_none = 0
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path), generic_rw, share_none, None, open_existing, 0, None
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid or handle in {-1, 0xFFFFFFFF}:
        err = int(ctypes.GetLastError())
        return {"exists": True, "exclusive": False, "error": f"winerror={err}", "size": size}
    ctypes.windll.kernel32.CloseHandle(handle)
    return {"exists": True, "exclusive": True, "error": None, "size": size}


def wav_shared_read_ok(path: Path) -> dict[str, object]:
    """True when the WAV can be opened for shared read.

    Rec=0 / Device On=0 is not proof the writer released the handle. Exclusive
    (share-none) open can keep failing while a shared read already succeeds.
    """
    if not path.exists():
        return {"exists": False, "readable": False, "error": None, "size": None}
    size = int(path.stat().st_size)
    if os.name != "nt":
        try:
            with path.open("rb") as handle:
                handle.read(1)
            return {"exists": True, "readable": True, "error": None, "size": size}
        except OSError as exc:
            return {"exists": True, "readable": False, "error": str(exc), "size": size}
    import ctypes

    generic_read = ctypes.c_uint32(0x80000000).value
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path), generic_read, share_all, None, open_existing, 0, None
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid or handle in {-1, 0xFFFFFFFF}:
        err = int(ctypes.GetLastError())
        return {"exists": True, "readable": False, "error": f"winerror={err}", "size": size}
    ctypes.windll.kernel32.CloseHandle(handle)
    return {"exists": True, "readable": True, "error": None, "size": size}


def wav_lock_owners(path: Path) -> list[dict[str, object]]:
    """Best-effort Restart Manager owners. Empty when not determinable."""
    if os.name != "nt" or not path.exists():
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []
    rstrtmgr = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if int(rstrtmgr.RmStartSession(ctypes.byref(session), 0, key)) != 0:
        return []

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [
            ("dwProcessId", wintypes.DWORD),
            ("ProcessStartTime", wintypes.FILETIME),
        ]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", ctypes.c_wchar * 256),
            ("strServiceShortName", ctypes.c_wchar * 64),
            ("ApplicationType", ctypes.c_int),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    try:
        resources = (ctypes.c_wchar_p * 1)(str(path))
        if int(rstrtmgr.RmRegisterResources(session, 1, resources, 0, None, 0, None)) != 0:
            return []
        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reboot = wintypes.DWORD()
        err = int(
            rstrtmgr.RmGetList(
                session,
                ctypes.byref(needed),
                ctypes.byref(count),
                None,
                ctypes.byref(reboot),
            )
        )
        if err not in {0, 234} or int(needed.value) == 0:
            return []
        arr = (RM_PROCESS_INFO * int(needed.value))()
        count = wintypes.UINT(needed.value)
        err = int(
            rstrtmgr.RmGetList(
                session,
                ctypes.byref(needed),
                ctypes.byref(count),
                arr,
                ctypes.byref(reboot),
            )
        )
        if err != 0:
            return []
        return [
            {"pid": int(arr[i].Process.dwProcessId), "app": str(arr[i].strAppName)}
            for i in range(int(count.value))
        ]
    except Exception:
        return []
    finally:
        rstrtmgr.RmEndSession(session)
