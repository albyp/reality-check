import pytest

from reality_check.control import load_control
from reality_check.models import Role


def test_hash_header_survey_names(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("#Label,X/Easting,Y/Northing,Z/Altitude\n924-01,514521.45,6681455.436,220.1\n")
    [cp] = load_control(p)
    assert (cp.id, cp.x, cp.y, cp.z, cp.role) == ("924-01", 514521.45, 6681455.436, 220.1, Role.UNKNOWN)


def test_no_header(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("P1,1,2,3\nP2,4,5,6\n")
    assert [c.id for c in load_control(p)] == ["P1", "P2"]


def test_whitespace_and_reordered_columns(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Northing Easting RL Name\n2 1 3 P1\n")
    [cp] = load_control(p)
    assert (cp.id, cp.x, cp.y, cp.z) == ("P1", 1, 2, 3)


def test_role_column(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("id,e,n,z,used\nA,1,2,3,yes\nB,1,2,3,no\nC,1,2,3,\n")
    assert [c.role for c in load_control(p)] == [Role.GCP, Role.CHECKPOINT, Role.UNKNOWN]


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("A,1,2,3\nA,4,5,6\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_control(p)


def test_missing_column_reported(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("id,e,n\nA,1,2\n")
    with pytest.raises(ValueError, match="z"):
        load_control(p)
