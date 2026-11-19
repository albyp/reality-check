"""Run RealityCheck on the open Metashape project: the last step of a batch.

Metashape Professional only (Standard has no Python scripting).

Use it as the last job of a batch, after the export jobs:
    Workflow > Batch Process > Add > Job type: Run Script
    Path:      <RealityCheck folder>\\scripts\\metashape\\realitycheck_batch.py
    Arguments: --template "My template" --up 1 --pdf --open

Or once, by hand: Tools > Run Script, same path and arguments.

RealityCheck finds the project's files with the template, exactly as the
Batch tab does, and writes the report and session next to the project (or to
--out). The template is the one you set up in RealityCheck's Settings tab for
your folder layout; leave --template out to use your default template.

Arguments:
    --template NAME   RealityCheck template (default: the default template in settings.json)
    --up N            the project folder is N folders above the folder holding the .psx
                      (e.g. 1 for PROJECT\\04_Agisoft\\job.psx; default 0)
    --project FOLDER  use this project folder instead of working it out from the .psx
    --out FOLDER      write all outputs here (default: inside the project folder)
    --pdf --html --csv  outputs (default: HTML)
    --open            open RealityCheck on the result for review when it is done
    --wait            wait for RealityCheck to finish before the batch goes on
                      (default: start it and let Metashape carry on)
    --exe PATH        RealityCheck.exe (default: the RealityCheck folder this script came with,
                      or the REALITYCHECK_EXE environment variable)

RealityCheck's log is %LOCALAPPDATA%\\RealityCheck\\realitycheck.log.
"""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

# Set these if the Run Script job's Arguments box is not available in your version.
REALITYCHECK_EXE = ""
DEFAULT_ARGS = ""


def _args(argv):
    ap = argparse.ArgumentParser(prog="realitycheck_batch.py")
    ap.add_argument("--template", default="")
    ap.add_argument("--up", type=int, default=0)
    ap.add_argument("--project", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--pdf", action="store_true")
    ap.add_argument("--html", action="store_true")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--exe", default="")
    if not argv:
        argv = [DEFAULT_ARGS] if DEFAULT_ARGS else []
    if len(argv) == 1 and " " in argv[0]:  # all arguments passed as one string
        argv = shlex.split(argv[0], posix=False)
    return ap.parse_args([a.strip('"') for a in argv])


def find_exe(explicit=""):
    candidates = [explicit, REALITYCHECK_EXE, os.environ.get("REALITYCHECK_EXE", "")]
    try:  # the release zip: RealityCheck\scripts\metashape\this file
        candidates.append(str(Path(__file__).resolve().parents[2] / "RealityCheck.exe"))
    except NameError:  # run without __file__
        pass
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise SystemExit("RealityCheck.exe not found. Pass --exe, set REALITYCHECK_EXE, or run this script from the "
                     "scripts\\metashape folder of the RealityCheck download.")


def project_folder(a):
    if a.project:
        return Path(a.project)
    import Metashape  # only inside Metashape

    psx = Metashape.app.document.path
    if not psx:
        raise SystemExit("Save the Metashape project first: RealityCheck works from the project's folder.")
    folder = Path(psx).parent
    for _ in range(a.up):
        folder = folder.parent
    return folder


def build_command(a, exe, folder):
    cmd = [exe, "batch", str(folder)]
    if a.template:
        cmd += ["--template", a.template]
    if a.out:
        cmd += ["--out", a.out]
    cmd += [f"--{k}" for k in ("pdf", "html", "csv", "open") if getattr(a, k)]
    return cmd


def main(argv=None):
    a = _args(sys.argv[1:] if argv is None else argv)
    exe = find_exe(a.exe)
    folder = project_folder(a)
    cmd = build_command(a, exe, folder)
    print("RealityCheck:", subprocess.list2cmdline(cmd))
    if a.wait:
        rc = subprocess.call(cmd)
        print(f"RealityCheck finished (exit code {rc}).")
        return rc
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    print("RealityCheck started. The report is written next to the project when it is done.")
    return 0


if __name__ == "__main__" or "Metashape" in sys.modules:  # Metashape may not run scripts as __main__
    main()
