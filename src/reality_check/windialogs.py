"""Native Windows file dialogs through the Win32 API (ctypes).

Used when RealityCheck runs in a browser tab instead of its desktop window.
The dialogs are owned by the window the user just clicked in (the browser),
so they open on top of it. A dialog from a background process with no owner
(the old tkinter approach) can open behind the browser, which looks like the
Browse button doing nothing. No tkinter or .NET needed.

Call from a worker thread: each call blocks until the dialog closes.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

MAX_PATH_BUF = 32768

# GetOpenFileName / GetSaveFileName flags
OFN_OVERWRITEPROMPT = 0x00000002
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
OFN_ENABLESIZING = 0x00800000

# SHBrowseForFolder flags and messages
BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040
BFFM_INITIALIZED = 1
BFFM_SETSELECTIONW = 0x0400 + 103

COINIT_APARTMENTTHREADED = 0x2
FOLDER_DIALOG_CAPTION = "Browse For Folder"  # SHBrowseForFolder's window caption (English Windows)


class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


BFFCALLBACK = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.UINT, wintypes.LPARAM, wintypes.LPARAM)


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [
        ("hwndOwner", wintypes.HWND),
        ("pidlRoot", ctypes.c_void_p),
        ("pszDisplayName", wintypes.LPWSTR),
        ("lpszTitle", wintypes.LPCWSTR),
        ("ulFlags", wintypes.UINT),
        ("lpfn", BFFCALLBACK),
        ("lParam", wintypes.LPARAM),
        ("iImage", ctypes.c_int),
    ]


def available() -> bool:
    return os.name == "nt"


def filter_string(types: list[tuple[str, str]]) -> str:
    """tkinter-style [("PDF", "*.pdf"), ("All files", "*.*")] to the Win32 form
    "PDF (*.pdf)\\0*.pdf\\0All files (*.*)\\0*.*\\0\\0"."""
    parts = []
    for label, pattern in types:
        pats = ";".join(pattern.split())
        parts += [f"{label} ({pats})", pats]
    return "\0".join(parts) + "\0\0"


def _wbuf(text: str) -> ctypes.Array:
    """A wide-character buffer holding text exactly, embedded NULs included."""
    return (ctypes.c_wchar * len(text))(*text)


def _owner() -> int | None:
    """The window the user is working in (the browser), so the dialog opens on top of it."""
    return _user32().GetForegroundWindow() or None


_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_USER32 = None


def _user32():
    """A private user32 handle with declared argument types.

    Without argtypes ctypes passes handles as 32-bit ints; HWND_TOPMOST (-1)
    then arrives as 0xFFFFFFFF and Windows ignores the call. A private WinDLL
    keeps these declarations from affecting other libraries' ctypes.windll.
    """
    global _USER32
    if _USER32 is None:
        u = ctypes.WinDLL("user32", use_last_error=True)
        H, B, U, I = wintypes.HWND, wintypes.BOOL, wintypes.UINT, ctypes.c_int
        u.EnumWindows.argtypes, u.EnumWindows.restype = [_ENUM_PROC, wintypes.LPARAM], B
        u.GetWindowThreadProcessId.argtypes = [H, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = [H], B
        u.GetWindowTextW.argtypes, u.GetWindowTextW.restype = [H, wintypes.LPWSTR, I], I
        u.SetWindowPos.argtypes, u.SetWindowPos.restype = [H, H, I, I, I, I, U], B
        u.GetForegroundWindow.argtypes, u.GetForegroundWindow.restype = [], H
        u.AttachThreadInput.argtypes, u.AttachThreadInput.restype = [wintypes.DWORD, wintypes.DWORD, B], B
        u.BringWindowToTop.argtypes, u.BringWindowToTop.restype = [H], B
        u.SetForegroundWindow.argtypes, u.SetForegroundWindow.restype = [H], B
        u.SendMessageW.argtypes = [H, U, wintypes.WPARAM, wintypes.LPARAM]
        u.SendMessageW.restype = wintypes.LPARAM
        _USER32 = u
    return _USER32


def _bring_to_front_when_shown(title: str, timeout: float = 10.0) -> threading.Event:
    """Watch for this process's dialog called title and put it in front of every window.

    A background process may not take the foreground, so a file dialog can
    open behind the browser. Once the dialog appears it is made topmost (it
    stays above the browser until it closes) and given the focus by briefly
    attaching to the foreground window's input queue. Set the returned event
    to stop watching (the dialog has closed).
    """
    done = threading.Event()
    user32, kernel32 = _user32(), ctypes.windll.kernel32
    pid = kernel32.GetCurrentProcessId()
    HWND_TOPMOST = wintypes.HWND(-1)
    SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x1, 0x2, 0x40
    enum_proc = _ENUM_PROC

    def find() -> int | None:
        found = []

        @enum_proc
        def cb(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, buf, 512)
                if buf.value == title:
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else None

    def watch():
        end = time.monotonic() + timeout
        while not done.is_set() and time.monotonic() < end:
            hwnd = find()
            if hwnd:
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                fg = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
                me = kernel32.GetCurrentThreadId()
                attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                finally:
                    if attached:
                        user32.AttachThreadInput(me, fg_thread, False)
                return
            time.sleep(0.05)

    threading.Thread(target=watch, daemon=True).start()
    return done


def _com_init() -> bool:
    hr = ctypes.windll.ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    return hr in (0, 1)  # S_OK or S_FALSE: must be balanced with CoUninitialize


def _file_dialog(save: bool, title: str, types, initial: str = "", directory: str = "") -> str:
    comdlg32 = ctypes.windll.comdlg32
    buf = ctypes.create_unicode_buffer(MAX_PATH_BUF)
    if initial:
        buf.value = initial
    filt = _wbuf(filter_string(types))
    defext = types[0][1].split()[0].lstrip("*.") if types else ""
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = _owner()
    ofn.lpstrFilter = ctypes.cast(filt, wintypes.LPCWSTR)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = MAX_PATH_BUF
    ofn.lpstrInitialDir = directory or None
    ofn.lpstrTitle = title
    ofn.lpstrDefExt = (defext or None) if save and defext not in ("", "*") else None
    ofn.Flags = OFN_EXPLORER | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST | OFN_ENABLESIZING
    ofn.Flags |= OFN_OVERWRITEPROMPT if save else OFN_FILEMUSTEXIST

    com = _com_init()
    watcher = _bring_to_front_when_shown(title)
    try:
        ok = (comdlg32.GetSaveFileNameW if save else comdlg32.GetOpenFileNameW)(ctypes.byref(ofn))
    finally:
        watcher.set()
        if com:
            ctypes.windll.ole32.CoUninitialize()
    if ok:
        return buf.value
    err = comdlg32.CommDlgExtendedError()
    if err:
        raise OSError(f"Windows file dialog failed (CommDlgExtendedError {err:#x})")
    return ""  # cancelled


def open_file(title: str, types, directory: str = "") -> str:
    return _file_dialog(False, title, types, directory=directory)


def save_file(title: str, types, initial: str = "", directory: str = "") -> str:
    return _file_dialog(True, title, types, initial, directory)


def pick_folder(title: str, directory: str = "") -> str:
    shell32, ole32 = ctypes.windll.shell32, ctypes.windll.ole32
    display = ctypes.create_unicode_buffer(260)
    start = ctypes.create_unicode_buffer(directory) if directory else None

    @BFFCALLBACK
    def callback(hwnd, msg, lparam, data):
        if msg == BFFM_INITIALIZED and start is not None:
            _user32().SendMessageW(hwnd, BFFM_SETSELECTIONW, 1, ctypes.addressof(start))
        return 0

    info = BROWSEINFOW()
    info.hwndOwner = _owner()
    info.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    info.lpszTitle = title
    info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX
    info.lpfn = callback

    shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
    com = _com_init()
    watcher = _bring_to_front_when_shown(FOLDER_DIALOG_CAPTION)
    try:
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return ""
        path = ctypes.create_unicode_buffer(MAX_PATH_BUF)
        ok = shell32.SHGetPathFromIDListW(pidl, path)
        ole32.CoTaskMemFree(pidl)
        return path.value if ok else ""
    finally:
        watcher.set()
        if com:
            ole32.CoUninitialize()
