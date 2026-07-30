"""Winning Products scoring (app/winning.py).

The load-bearing claim on that page is that an inferred momentum and a measured
one never render the same, so most of these assert on `momentum_basis` rather
than on the score itself.
"""
from app import winning


def chart_row(asin, rank, ratings_total, rating=4.5, title=None):
    return {
        "asin": asin,
        "rank": rank,
        "position": rank,
        "title": title or f"product {asin}",
        "rating": rating,
        "ratings_total": ratings_total,
        "image": None,
        "link": f"https://www.amazon.com/dp/{asin}",
    }


# The live Kitchen chart, reduced to the shape that motivated `breakout`: an
# entrenched #1 with 203k ratings against a #2 holding almost the same rank on
# 6.8% of the review mass.
INCUMBENT_VS_RISER = {
    "bestsellers": [
        chart_row("INCUMBENT", 1, 203_137, title="Stanley Quencher"),
        chart_row("RISER", 2, 13_835, title="Owala SmoothSip"),
        chart_row("MIDDLE", 3, 72_886),
        chart_row("TAIL", 4, 147_108),
    ],
    "new_releases": [],
}


def test_riser_outranks_incumbent_on_a_single_scan():
    """The whole point of the page: chart order alone would put the incumbent
    first, and the composite must not."""
    products = winning.build(INCUMBENT_VS_RISER, category="kitchen")
    by_asin = {p.asin: p for p in products}

    assert by_asin["RISER"].score > by_asin["INCUMBENT"].score
    assert products[0].asin == "RISER"
    # ...and it did so on the inference, since no history was supplied.
    assert by_asin["RISER"].breakout > by_asin["INCUMBENT"].breakout


def test_single_scan_is_labelled_inferred_and_draws_no_line():
    products = winning.build(INCUMBENT_VS_RISER, category="kitchen")
    for p in products:
        assert p.momentum_basis == "rank_vs_depth"
        # An inference must not be reported as a measured percentage...
        assert p.momentum_pct is None
        # ...and a product seen once has no series to draw.
        assert p.rank_history == []
        assert p.snapshots == 0


def test_observed_history_supersedes_the_inference():
    """Two snapshots exist, so the row stops guessing and starts measuring."""
    history = {
        # Climbed 40 -> 10: a 75% rank improvement.
        "RISER": [
            {"scanned_at": "2026-07-01T00:00:00+00:00", "rank": 40},
            {"scanned_at": "2026-07-15T00:00:00+00:00", "rank": 22},
            {"scanned_at": "2026-07-30T00:00:00+00:00", "rank": 10},
        ],
        # Slipped 1 -> 3.
        "INCUMBENT": [
            {"scanned_at": "2026-07-01T00:00:00+00:00", "rank": 1},
            {"scanned_at": "2026-07-30T00:00:00+00:00", "rank": 3},
        ],
    }
    products = winning.build(INCUMBENT_VS_RISER, category="kitchen", history=history)
    by_asin = {p.asin: p for p in products}

    riser = by_asin["RISER"]
    assert riser.momentum_basis == "observed"
    assert riser.momentum_pct == 75.0
    assert riser.rank_history == [40, 22, 10]
    assert riser.snapshots == 3

    incumbent = by_asin["INCUMBENT"]
    assert incumbent.momentum_basis == "observed"
    assert incumbent.momentum_pct == -200.0  # 1 -> 3 is a loss, and says so

    # A product with no history keeps the inference; the two coexist per-row.
    assert by_asin["MIDDLE"].momentum_basis == "rank_vs_depth"


def test_one_snapshot_is_not_enough_to_measure():
    """A single observation is not a rate of change, and must not be treated
    as one just because the table has a row in it."""
    history = {"RISER": [{"scanned_at": "2026-07-30T00:00:00+00:00", "rank": 2}]}
    products = winning.build(INCUMBENT_VS_RISER, category="kitchen", history=history)
    riser = next(p for p in products if p.asin == "RISER")

    assert riser.momentum_basis == "rank_vs_depth"
    assert riser.momentum_pct is None
    assert riser.snapshots == 1
    # One point is carried through, but the UI needs >= 2 before it draws.
    assert riser.rank_history == [2]


def test_new_release_flag_comes_from_the_other_chart():
    charts = {
        "bestsellers": [chart_row("A", 1, 100), chart_row("B", 2, 200)],
        "new_releases": [chart_row("B", 5, 200)],
    }
    products = winning.build(charts, category="kitchen")
    by_asin = {p.asin: p for p in products}
    assert by_asin["B"].is_new_release is True
    assert by_asin["A"].is_new_release is False


def test_empty_chart_yields_nothing_rather_than_raising():
    assert winning.build({"bestsellers": [], "new_releases": []}, category="kitchen") == []


def test_score_components_reconstruct_the_score():
    """The drawer shows the three components as the reason for the number, so
    they have to actually add up to it."""
    products = winning.build(INCUMBENT_VS_RISER, category="kitchen")
    for p in products:
        expected = 100 * (
            winning.W_MOMENTUM * p.momentum_component
            + winning.W_DEMAND * p.demand_component
            + winning.W_QUALITY * p.quality_component
        )
        assert abs(expected - p.score) < 0.35
