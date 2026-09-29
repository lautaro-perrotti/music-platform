"""Windows-only, fail-closed UI primitives for one owned Ableton window."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any


class WindowsSaveUI:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows UI Save is unavailable on this host")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u, k = self.user32, self.kernel32
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        u.IsWindowEnabled.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_ulonglong]
        u.EnumWindows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM,
        ]

    def observe(self, pid: int) -> dict[str, Any]:
        kernel, user = self.kernel32, self.user32
        handle = kernel.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not handle:
            return {"alive": False}
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            ):
                return {"alive": False}
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return {"alive": False}
            token = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
            if exit_code.value != 259:  # STILL_ACTIVE
                return {"alive": False, "process_token": token}
            image = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(image))
            if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
                return {"alive": True, "process_token": token}
            windows: list[dict[str, Any]] = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def visit(hwnd: int, _arg: int) -> bool:
                owner = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == pid and user.IsWindowVisible(hwnd):
                    size = user.GetWindowTextLengthW(hwnd)
                    title = ctypes.create_unicode_buffer(size + 1)
                    user.GetWindowTextW(hwnd, title, size + 1)
                    windows.append({
                        "hwnd": int(hwnd),
                        "title": title.value,
                        "enabled": bool(user.IsWindowEnabled(hwnd)),
                    })
                return True

            callback = callback_type(visit)
            user.EnumWindows(callback, 0)
            foreground = user.GetForegroundWindow()
            foreground_pid = wintypes.DWORD()
            if foreground:
                user.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
            return {
                "alive": True,
                "process_token": token,
                "executable": image.value,
                "windows": windows,
                "foreground_hwnd": int(foreground or 0),
                "foreground_pid": int(foreground_pid.value),
            }
        finally:
            kernel.CloseHandle(handle)

    def focus(self, hwnd: int) -> bool:
        return bool(self.user32.SetForegroundWindow(hwnd))

    def send_save(self, hwnd: int, pid: int) -> bool:
        # Recheck at the closest possible point to the keystrokes.
        if self.user32.GetForegroundWindow() != hwnd:
            return False
        owner = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return False
        control, s, up = 0x11, 0x53, 0x0002
        self.user32.keybd_event(control, 0, 0, 0)
        self.user32.keybd_event(s, 0, 0, 0)
        self.user32.keybd_event(s, 0, up, 0)
        self.user32.keybd_event(control, 0, up, 0)
        return True

    def close_window(self, hwnd: int) -> bool:
        # Asynchronous, graceful request to the one verified HWND. No taskkill.
        return bool(self.user32.PostMessageW(hwnd, 0x0010, 0, 0))  # WM_CLOSE
