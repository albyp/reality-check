import numpy as np

from reality_check.models import ControlPoint
from reality_check.overview import (MAX_STRIPS, MIN_SPACING_MM, PX_PER_MM, SHEET_H_MM, SHEET_W_MM, TEXT_MM,
                                    _median_spacing, plan_layout, render_overview)
from reality_check.session import Session

LEGEND_MM = 25


def _line(n, length, width, tall):
    t = np.linspace(0, 1, n)
    along, across = t * length, width / 2 + width * 0.3 * np.sin(t * 9)
    return (across, along) if tall else (along, across)


def test_compact_site_is_one_strip():
    xs, ys = _line(12, 300, 260, True)
    lay = plan_layout(xs, ys, LEGEND_MM)
    assert (lay.strips, lay.sheets) == (1, 1)


def test_long_north_south_site_is_cut_into_strips_side_by_side():
    xs, ys = _line(22, 1500, 50, True)
    lay = plan_layout(xs, ys, LEGEND_MM)
    assert lay.tall and lay.sheets == 1 and 1 < lay.strips <= MAX_STRIPS
    # North first: each strip starts south of the one before, and they overlap.
    tops = [b[3] for b in lay.tiles]
    assert tops == sorted(tops, reverse=True)
    assert all(lay.tiles[i][1] < lay.tiles[i + 1][3] for i in range(lay.strips - 1))
    # Bigger on paper than one strip would be: the old layout shrank the strip to the page height.
    assert lay.scale > (SHEET_H_MM - LEGEND_MM) / (np.ptp(ys) * 1.08 + 20) * 2
    # Every point falls in a strip.
    for x, y in zip(xs, ys):
        assert any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in lay.tiles)


def test_mildly_long_site_stays_one_map():
    xs, ys = _line(12, 200, 90, True)
    assert plan_layout(xs, ys, LEGEND_MM).strips == 1


def test_long_east_west_site_is_cut_into_stacked_strips():
    xs, ys = _line(18, 1200, 60, False)
    lay = plan_layout(xs, ys, LEGEND_MM)
    assert not lay.tall and lay.strips > 1
    lefts = [b[0] for b in lay.tiles]
    assert lefts == sorted(lefts)  # west first


def test_crowded_long_site_uses_more_sheets():
    xs, ys = _line(300, 8000, 30, True)
    lay = plan_layout(xs, ys, LEGEND_MM)
    assert lay.sheets > 1 and lay.per_sheet == MAX_STRIPS
    assert _median_spacing(xs, ys) * lay.scale >= MIN_SPACING_MM


def _session(xs, ys):
    points = [ControlPoint(f"P{i}", float(x) + 500000, float(y) + 7400000, 400.0) for i, (x, y) in
              enumerate(zip(xs, ys))]
    return Session(control_path="gcp.csv", points=points, datasets=[])


def test_sheets_are_drawn_at_print_size():
    # Text is drawn at a fixed size on paper, whatever the site's size or shape.
    for xs, ys in (_line(22, 1500, 50, True), _line(18, 1200, 60, False), _line(8, 40, 30, True)):
        ov = render_overview(_session(xs, ys))
        for img in ov.sheets:
            assert img.width == round(SHEET_W_MM * PX_PER_MM)
            assert img.height <= SHEET_H_MM * PX_PER_MM
    assert round(TEXT_MM * PX_PER_MM) >= 20  # about 7.5 pt
