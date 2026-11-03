"""Report document details (title block, title strip, sign-off) and points outside the data."""

import base64

from reality_check import appsettings
from reality_check.document import DocumentInfo, Signatory
from reality_check.overview import render_overview
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import build_html
from reality_check.review import OUTSIDE_NOTE, disable_outside_data, point_results
from reality_check.session import Session, Settings

PNG_1PX = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


def _session(control_path, dem_path, **doc):
    s = Settings(document=DocumentInfo(**doc))
    return run(control_path, make_datasets([], [dem_path], []), s, progress=lambda m: None)


def _html(res) -> str:
    r = ChipRenderer(res.session, res.windows)
    try:
        return build_html(res.session, r)
    finally:
        r.close()


def test_document_fields_reach_the_report_and_the_session(tmp_path, control_path, dem_path):
    logo = tmp_path / "logo.png"
    logo.write_bytes(PNG_1PX)
    res = _session(control_path, dem_path, client="Pit Owner Pty Ltd", document_number="RC-0042", revision="B",
                   date="2026-11-03", company="Albyte Surveys", logo=str(logo), client_logo=str(tmp_path / "gone.png"),
                   signatories=[Signatory("Prepared by", "A. Palmer", "Surveyor"), Signatory("Checked by")])
    html = _html(res)
    assert "<b>Pit Owner Pty Ltd</b>" in html and "<b>RC-0042</b>" in html and "<b>2026-11-03</b>" in html
    assert "class='tb logo-l'" in html and "data:image/png;base64," in html  # a missing client logo is left out
    assert '"RC-0042  ·  REV B  ·  " "SHEET " counter(page)' in html  # running reference and sheet number
    assert "CLIENT  PIT OWNER PTY LTD" in html and "ALBYTE SURVEYS" in html  # title strip
    assert "<table class='sign'>" in html and "A. Palmer" in html
    assert html.count("<td class='role'>") == 1  # rows with no name or position are left out

    path = tmp_path / "s.rcheck.json"
    res.session.save(path)
    doc = Session.load(path).settings.document
    assert doc.document_number == "RC-0042" and doc.signatories[0].name == "A. Palmer"


def test_appendix_numbers_sections_and_sheets(control_path, dem_path):
    html = _html(_session(control_path, dem_path, appendix="B", appendix_title="Survey QA"))
    assert "<span class='num'>B1</span>" in html and '"SHEET B-" counter(page)' in html
    assert "APPENDIX B — SURVEY QA" in html
    assert "<table class='sign'>" not in html  # no signatories, no sign-off


def test_settings_keep_company_details_only(tmp_path):
    app = appsettings.AppSettings(tmp_path / "settings.json")
    app.document = DocumentInfo(company="Albyte Surveys", client="Not a default", document_number="RC-1",
                                signatories=[Signatory("Approved by", "B. Boss")])
    app.save()
    doc = appsettings.load(tmp_path / "settings.json").document
    assert doc.company == "Albyte Surveys" and doc.signatories[0].name == "B. Boss"
    assert doc.client == "" and doc.document_number == ""
    assert DocumentInfo.from_dict({"company": "X", "from_a_newer_version": 1}).company == "X"


def test_cli_reads_document_details(tmp_path, control_path, dem_path):
    from reality_check.cli import main

    doc = tmp_path / "doc.json"
    doc.write_text('{"client": "Pit Co", "document_number": "RC-7", '
                   '"signatories": [{"role": "Prepared by", "name": "A. Palmer"}]}', encoding="utf-8")
    out = tmp_path / "out"
    assert main(["run", "--control", control_path, "--dem", dem_path, "--out", str(out), "--document", str(doc)]) == 0
    html = (out / "gcp_RealityCheck.html").read_text(encoding="utf-8")
    assert "<b>RC-7</b>" in html and "A. Palmer" in html
    assert Session.load(out / "gcp_RealityCheck.rcheck.json").settings.document.client == "Pit Co"


def test_points_outside_the_data_leave_the_map_and_can_be_disabled(control_path, dem_path):
    s = _session(control_path, dem_path).session
    assert [pr.point.id for pr in point_results(s) if pr.outside_data] == ["OUT"]
    assert render_overview(s).left_out == ["OUT"]

    assert disable_outside_data(s) == ["OUT"]
    assert not s.point("OUT").enabled and s.point("OUT").note == OUTSIDE_NOTE
    assert s.point("A").enabled and disable_outside_data(s) == []
