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
    assert riser.momentum_positions == 30  # 40 -> 10
    assert riser.rank_history == [40, 22, 10]
    assert riser.snapshots == 3

    incumbent = by_asin["INCUMBENT"]
    assert incumbent.momentum_basis == "observed"
    assert incumbent.momentum_positions == -2  # 1 -> 3 is a loss, and says so

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


def test_rank_movement_is_symmetric():
    """1 -> 3 and 3 -> 1 are the same two-place movement and must report the
    same magnitude. Dividing by the starting rank reported -200% against +67%,
    which made an ordinary wobble at the top of a chart look like a collapse."""
    charts = {
        "bestsellers": [chart_row(f"A{i}", i, 1000 * i) for i in range(1, 51)],
        "new_releases": [],
    }
    charts["bestsellers"][0]["asin"] = "TOP"

    down = winning.build(
        charts,
        category="kitchen",
        history={"TOP": [{"scanned_at": "a", "rank": 1}, {"scanned_at": "b", "rank": 3}]},
    )
    up = winning.build(
        charts,
        category="kitchen",
        history={"TOP": [{"scanned_at": "a", "rank": 3}, {"scanned_at": "b", "rank": 1}]},
    )
    fell = next(p for p in down if p.asin == "TOP")
    rose = next(p for p in up if p.asin == "TOP")

    assert fell.momentum_positions == -2
    assert rose.momentum_positions == 2
    assert fell.momentum_pct == -rose.momentum_pct
    # ...and expressed against the chart's depth, not the starting rank.
    assert rose.momentum_pct == 4.0  # 2 places on a 50-row chart


def test_momentum_component_is_bounded_and_neutral_when_still():
    """A product that held its exact position is neither rewarded nor punished."""
    charts = {
        "bestsellers": [chart_row(f"A{i}", i, 1000 * i) for i in range(1, 51)],
        "new_releases": [],
    }
    charts["bestsellers"][10]["asin"] = "STILL"
    products = winning.build(
        charts,
        category="kitchen",
        history={"STILL": [{"scanned_at": "a", "rank": 11}, {"scanned_at": "b", "rank": 11}]},
    )
    still = next(p for p in products if p.asin == "STILL")
    assert still.momentum_positions == 0
    assert still.momentum_component == 0.5
    for p in products:
        assert 0.0 <= p.momentum_component <= 1.0


def test_missing_review_count_does_not_promote_a_row():
    """A chart row Amazon didn't annotate must not climb the page for it.

    `ratings_total or 0` placed "not published" at the bottom of the review-mass
    scale, which the day-one inference reads as the strongest possible evidence
    of a riser. Measured against the real Kitchen fixture, blanking one row's
    count moved it from position #21 to #9.
    """
    charts = winning._load_fixture()
    assert charts["bestsellers"], "fixture must be present for this test"

    baseline = winning.build(charts, category="kitchen")
    target = baseline[20].asin  # the row the audit measured
    baseline_pos = [p.asin for p in baseline].index(target)

    blanked = {
        "bestsellers": [
            {**r, "ratings_total": None} if r.get("asin") == target else r
            for r in charts["bestsellers"]
        ],
        "new_releases": charts.get("new_releases", []),
    }
    after = winning.build(blanked, category="kitchen")
    after_pos = [p.asin for p in after].index(target)

    # Losing evidence must never improve a row's position.
    assert after_pos >= baseline_pos
    moved = next(p for p in after if p.asin == target)
    assert moved.momentum_basis == "none"
    assert moved.breakout is None


def test_unscoreable_momentum_sits_at_the_neutral_midpoint():
    charts = {
        "bestsellers": [chart_row("A", 1, None), chart_row("B", 2, None)],
        "new_releases": [],
    }
    for p in winning.build(charts, category="kitchen"):
        assert p.momentum_basis == "none"
        assert p.momentum_component == 0.5
        assert p.breakout is None
