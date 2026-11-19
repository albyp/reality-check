import importlib.util
import json
from pathlib import Path

import pytest

from reality_check import appsettings, cli
from reality_check.updates import Release, available_update, is_newer, parse_version, summary

REPO = Path(__file__).resolve().parents[1]


def test_version_order():
    assert parse_version("v0.3.1") == (0, 3, 1, 1, 0)
    assert is_newer("0.4.0", "0.3.1")
    assert is_newer("v0.3.10", "0.3.9")
    assert not is_newer("0.3.1", "0.3.1")
    assert is_newer("0.4.0", "0.4.0.dev0")  # the release is newer than its development build
    assert not is_newer("0.3.1", "0.4.0.dev0")  # a development build is never told to go back
    assert not is_newer("nightly", "0.3.1")


def test_available_update():
    rel = Release("0.5.0", "https://example.invalid/r")
    assert available_update(True, "", "0.4.0", fetch=lambda: rel) is rel
    assert available_update(False, "", "0.4.0", fetch=lambda: rel) is None  # turned off: GitHub is not asked
    assert available_update(True, "0.5.0", "0.4.0", fetch=lambda: rel) is None  # skipped
    assert available_update(True, "0.4.5", "0.4.0", fetch=lambda: rel) is rel  # an older skip does not hide it
    assert available_update(True, "", "0.5.0", fetch=lambda: rel) is None
    assert available_update(True, "", "0.4.0", fetch=lambda: None) is None  # offline


def test_summary_joins_wrapped_bullets():
    notes = ("**Download** below.\n\n### Added\n- Update notice: at start RealityCheck asks\n"
             "  GitHub for the `latest` release.\n- Second item.\n\n### Fixed\n- A fix.\n")
    assert summary(notes) == ("ADDED\n• Update notice: at start RealityCheck asks GitHub for the latest release.\n"
                              "• Second item.\nFIXED\n• A fix.")
    assert summary(notes, lines=2).count("\n") == 1


def test_update_settings_round_trip(tmp_path):
    s = appsettings.load(tmp_path / "settings.json")
    assert s.check_updates and s.skipped_version == ""
    s.check_updates, s.skipped_version = False, "0.5.0"
    s.save()
    d = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert d["updates"] == {"check": False, "skipped": "0.5.0"}
    s2 = appsettings.load(tmp_path / "settings.json")
    assert (s2.check_updates, s2.skipped_version) == (False, "0.5.0")


def test_session_file_argument_opens_the_gui(tmp_path, monkeypatch):
    session = tmp_path / "pit_RealityCheck.rcheck.json"
    session.write_text("{}", encoding="utf-8")
    opened = []
    import reality_check.gui as gui

    monkeypatch.setattr(gui, "start_desktop", lambda open_session="": opened.append(open_session))
    assert cli.main([str(session)]) == 0
    assert opened == [str(session)]
    assert cli.main(["gui", "--open", str(tmp_path / "missing.rcheck.json")]) == 2


def _metashape_script():
    spec = importlib.util.spec_from_file_location("rc_metashape", REPO / "scripts" / "metashape" /
                                                  "realitycheck_batch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # does not run main(): not __main__, and no Metashape
    return mod


def test_metashape_script_command(tmp_path):
    ms = _metashape_script()
    a = ms._args(['--template', 'My template', '--up', '1', '--pdf', '--open'])
    assert (a.template, a.up, a.pdf, a.open, a.wait) == ("My template", 1, True, True, False)
    # Metashape may hand over the Arguments box as one string.
    b = ms._args(['--template "My template" --pdf'])
    assert (b.template, b.pdf) == ("My template", True)
    cmd = ms.build_command(a, "C:/RC/RealityCheck.exe", tmp_path)
    assert cmd == ["C:/RC/RealityCheck.exe", "batch", str(tmp_path), "--template", "My template", "--pdf", "--open"]
    # The release zip layout: RealityCheck\scripts\metashape\realitycheck_batch.py
    exe = tmp_path / "RealityCheck.exe"
    exe.write_text("", encoding="utf-8")
    assert ms.find_exe(str(exe)) == str(exe)
    with pytest.raises(SystemExit):
        ms.find_exe(str(tmp_path / "nope.exe"))


def test_metashape_script_project_folder(tmp_path):
    ms = _metashape_script()
    a = ms._args(["--project", str(tmp_path)])
    assert ms.project_folder(a) == tmp_path
