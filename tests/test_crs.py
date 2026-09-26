import pytest
from pyproj import CRS

from reality_check.crs import CRSEntry, CRSLibrary, LocalGrid, same_horizontal_crs

# The WKT survey software wrote into the sample rasters: no EPSG code, "unnamed" datum.
SAMPLE_WKT = (
    'PROJCS["GDA2020 / MGA zone 53 AUSGeoid2020",GEOGCS["GDA2020",DATUM["unnamed",SPHEROID["GRS 1980",6378137,'
    '298.257222101004]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",135],'
    'PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",10000000],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)


def test_same_crs_custom_wkt_vs_epsg():
    assert same_horizontal_crs(CRS.from_wkt(SAMPLE_WKT), CRS.from_epsg(7853))
    assert not same_horizontal_crs(CRS.from_wkt(SAMPLE_WKT), CRS.from_epsg(7854))


def test_same_crs_bound_towgs84():
    # rasterio writes the sample rasters' CRS with TOWGS84, which pyproj reads as a BoundCRS.
    bound = CRS.from_wkt(SAMPLE_WKT.replace('298.257222101004]]', '298.257222101004],TOWGS84[0,0,0,0,0,0,0]]'))
    assert bound.is_bound
    assert same_horizontal_crs(bound, CRS.from_epsg(7853))


def test_same_crs_compound():
    compound = CRS.from_user_input("EPSG:7853+EPSG:5711")  # MGA53 + AHD height
    assert same_horizontal_crs(compound, CRS.from_epsg(7853))


def test_local_grid_bearing_convention():
    # Local north points to base east (bearing 90): one metre local north = one metre base east.
    g = LocalGrid("EPSG:7853", origin_grid=(0, 0), origin_base=(1000, 2000), rotation_deg=90)
    e, n, _ = g.to_base(0, 1, 0)
    assert (e, n) == pytest.approx((1001, 2000))


def test_local_grid_round_trip():
    g = LocalGrid("EPSG:7853", (5000, 10000), (514500, 6681400), rotation_deg=-12.345, scale=1.0003, z_offset=-2.1)
    x, y, z = 5123.4, 10456.7, 250.0
    assert g.from_base(*g.to_base(x, y, z)) == pytest.approx((x, y, z))


def test_library_save_export_import(tmp_path):
    lib = CRSLibrary(tmp_path / "lib.json")
    lib.add(CRSEntry("MGA53", "crs", "EPSG:7853", vertical="AHD"))
    lib.add(CRSEntry("Pit grid", "local_grid", grid=LocalGrid("EPSG:7853", (0, 0), (514000, 6681000), 10)))
    lib.save()
    assert set(CRSLibrary.load(tmp_path / "lib.json").entries) == {"MGA53", "Pit grid"}

    lib.export(tmp_path / "share.json", ["Pit grid"])
    other = CRSLibrary(tmp_path / "other.json")
    other.add(CRSEntry("Pit grid", "crs", "EPSG:28353"))
    assert other.import_file(tmp_path / "share.json") == ["Pit grid"]  # existing kept
    other.import_file(tmp_path / "share.json", overwrite=True)
    assert other.entries["Pit grid"].grid.rotation_deg == 10


def test_invalid_entry_rejected(tmp_path):
    lib = CRSLibrary(tmp_path / "lib.json")
    with pytest.raises(Exception):
        lib.add(CRSEntry("bad", "crs", "not a crs"))
