"""PyInstaller entry point.

The exe is built without a console window. Started from Explorer it opens
the GUI; output goes to %LOCALAPPDATA%\\RealityCheck\\realitycheck.log and a
crash shows a message box. Started from a terminal with arguments, it
attaches to that terminal so command line output is visible.
"""

import ctypes
import os
import sys
import traceback
from pathlib import Path

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RealityCheck"


def _attach_parent_console() -> bool:
    if ctypes.windll.kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = sys.stdout
        sys.stdin = open("CONIN$", encoding="utf-8")
        return True
    return False


def _log_to_file() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "realitycheck.log"
    stream = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream
    return path


def main() -> int:
    has_console = len(sys.argv) > 1 and _attach_parent_console()
    log = None if has_console else _log_to_file()

    from reality_check.cli import main as cli_main

    try:
        return cli_main(sys.argv[1:])
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    except Exception:
        text = traceback.format_exc()
        print(text)
        if not has_console:
            ctypes.windll.user32.MessageBoxW(
                None, f"RealityCheck stopped with an error:\n\n{text[-1500:]}\n\nLog: {log}", "RealityCheck", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
