"""Drive the real Windows file dialogs: open each one, fill it in or cancel it from
another thread, and check what comes back."""

import ctypes
import os
import threading
import time
import uuid

import pytest

from reality_check import windialogs

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows dialogs")

WM_COMMAND = 0x0111
IDOK, IDCANCEL = 1, 2
FILENAME_COMBO = 1148  # cmb13: the file name box in Explorer-style dialogs
FOLDER_EDIT = 0x3744  # edit box in SHBrowseForFolder with BIF_EDITBOX

user32 = ctypes.windll.user32
user32.FindWindowW.restype = ctypes.c_void_p
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SetDlgItemTextW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]


def _drive(title: str, action, timeout: float = 20.0) -> None:
    """Wait for the dialog window called title and run action(hwnd), repeating
    until the dialog closes (its controls may not be ready on the first try)."""
    end = time.time() + timeout
    seen = False
    while time.time() < end:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            seen = True
            time.sleep(0.4)
            action(hwnd)
            time.sleep(0.6)
        elif seen:
            return  # dialog closed
        else:
            time.sleep(0.05)
    raise AssertionError(f"dialog {title!r} {'did not close' if seen else 'never appeared'}")


def _run(fn, *args):
    out = {}

    def target():
        try:
            out["value"] = fn(*args)
        except Exception as e:  # noqa: BLE001
            out["error"] = e

    t = threading.Thread(target=target)
    t.start()
    return t, out


def test_filter_string():
    assert windialogs.filter_string([("PDF", "*.pdf"), ("Point cloud", "*.las *.laz")]) == (
        "PDF (*.pdf)\0*.pdf\0Point cloud (*.las;*.laz)\0*.las;*.laz\0\0")


def test_open_dialog_returns_typed_file(tmp_path):
    target = tmp_path / "gcp.csv"
    target.write_text("A,1,2,3\n")
    title = f"RC test open {uuid.uuid4().hex[:6]}"
    t, out = _run(windialogs.open_file, title, [("Control points", "*.csv"), ("All files", "*.*")], str(tmp_path))

    def fill(hwnd):
        user32.SetDlgItemTextW(hwnd, FILENAME_COMBO, str(target))
        user32.PostMessageW(hwnd, WM_COMMAND, IDOK, 0)

    _drive(title, fill)
    t.join(15)
    assert "error" not in out and out["value"].lower() == str(target).lower()


def test_save_dialog_adds_extension(tmp_path):
    title = f"RC test save {uuid.uuid4().hex[:6]}"
    # Default name without an extension: the dialog pre-fills it and adds .pdf on OK.
    t, out = _run(windialogs.save_file, title, [("PDF", "*.pdf")], "Pit 3_RealityCheck", str(tmp_path))
    _drive(title, lambda hwnd: user32.PostMessageW(hwnd, WM_COMMAND, IDOK, 0))
    t.join(15)
    assert "error" not in out and out["value"].lower() == str(tmp_path / "Pit 3_RealityCheck.pdf").lower()


def test_cancel_returns_empty_and_dialog_is_in_front(tmp_path):
    title = f"RC test cancel {uuid.uuid4().hex[:6]}"
    t, out = _run(windialogs.open_file, title, [("GeoTIFF", "*.tif *.tiff")], str(tmp_path))
    topmost = []

    def check_and_cancel(hwnd):
        time.sleep(0.5)  # give the watcher time to raise the dialog
        topmost.append(bool(user32.GetWindowLongW(hwnd, -20) & 0x8))  # GWL_EXSTYLE & WS_EX_TOPMOST
        user32.PostMessageW(hwnd, WM_COMMAND, IDCANCEL, 0)

    _drive(title, check_and_cancel)
    t.join(15)
    assert out == {"value": ""}
    assert topmost and topmost[0], "dialog was not brought in front of other windows"


def test_folder_dialog_returns_typed_folder(tmp_path):
    title = "Browse For Folder"  # SHBrowseForFolder's window caption; the title argument is the prompt text
    t, out = _run(windialogs.pick_folder, "Pick a project folder", str(tmp_path))

    def fill(hwnd):
        user32.SetDlgItemTextW(hwnd, FOLDER_EDIT, str(tmp_path))
        user32.PostMessageW(hwnd, WM_COMMAND, IDOK, 0)

    _drive(title, fill)
    t.join(15)
    assert "error" not in out and out["value"].lower() == str(tmp_path).lower()
