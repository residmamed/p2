"""Winning Products — Amazon category charts, ranked by who is climbing.

Distinct from the Trending tab, which is the Pinterest -> object-detect -> crop
visual flow and answers a different question entirely.

WHY CHARTS AND NOT SEARCH
-------------------------
Every other retail path in this app is query-driven: the user types a keyword.
That cannot produce a discovery feed, because the user has to already know what
to look for. This page instead scans a fixed universe -- Amazon's own category
best-seller and new-release charts -- so it can surface a product nobody
searched for.

It is also the only affordable shape. Measured against the live API:

    type=bestsellers   1 credit -> 50 ranked products   (0.02 credits/product)
    type=product       1 credit ->  1 product           (1.00 credits/product)

Per-product enrichment is ~50x the cost of chart data for fields the grid
mostly doesn't need, so this module never makes a per-product call. The price
column the mock showed is the casualty: charts don't publish price, and buying
it would cost more than every other signal on the page combined.

WHERE MOMENTUM COMES FROM
-------------------------
A chart position is a demand ranking Amazon computed. Its *derivative* is what
this page is for, and a derivative needs two observations. So:

  observed        rank moved between two of our own snapshots. The real thing.
                  Available only once app/snapshot_store.py has run twice.
  rank_vs_depth   an inference available from a single scan (below).
  none            no evidence. The row says so and draws no line.

Which one produced a number is carried on every row as `momentum_basis`, for
the same reason Product.rank_basis and SourcingResult.match_basis exist: an
inference and a measurement must never render identically.

THE DAY-ONE INFERENCE
---------------------
On the live Kitchen chart, rank 1 carried 203,137 ratings and rank 2 carried
13,835 -- near-identical standing on 6.8% of the accumulated review mass. A
product holding a high rank on a thin review base is either new or
accelerating; an incumbent's rank is propped up by years of reviews it can't
lose overnight. So: rank percentile minus review-mass percentile, within the
same chart.

It is an inference and is labelled one. A thin review base can also mean a
category where buyers simply don't review, and this measure cannot tell those
apart. It ranks candidates within one chart cohort; it is not a growth rate,
and it is superseded by `observed` the moment two snapshots exist.

Two things that sounded good and died on contact with the data:
  - new-release/best-seller overlap: exactly 1 product of 98 appeared on both
    Kitchen charts, so "new AND already selling" is too rare to rank with.
  - per-product `sales_volume`: returned "10K+ bought in past month" for
    B0DF472VMZ on one call and None for the same ASIN minutes later. Not
    dependable per-product; the equivalent figure is reliable on the
    search/chart endpoints, which is where app/rainforest.py already reads it.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel

from . import snapshot_store
from .config import settings
from .product_images import best_image

RAINFOREST_URL = "https://api.rainforestapi.com/request"
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "kitchen_charts.json"

# One category for now. Each additional one costs 2 credits per scan (a
# best-seller chart and a new-release chart), so the list is config, not code.
CATEGORIES = {
    "kitchen": {
        "label": "Home & Kitchen",
        "bestsellers": "https://www.amazon.com/gp/bestsellers/kitchen/",
        "new_releases": "https://www.amazon.com/gp/new-releases/kitchen/",
    },
}

# Composite weights. Deliberately few and deliberately visible: the drawer
# renders this exact breakdown, so a number on the page can always be taken
# apart into the things that made it.
W_MOMENTUM = 0.45   # is it climbing (or inferred to be)
W_DEMAND = 0.35     # where it stands right now
W_QUALITY = 0.20    # does the rating support buying it


class WinningProduct(BaseModel):
    asin: str
    title: str
    rank: int
    image: Optional[str] = None
    link: Optional[str] = None
    rating: Optional[float] = None
    ratings_total: Optional[int] = None
    category: str
    category_label: str
    is_new_release: bool = False

    score: float
    # "observed" | "rank_vs_depth" | "none" -- see module docstring.
    momentum_basis: str
    # Rank movement between the oldest and newest snapshot, as a share of the
    # chart's depth. Present only when momentum_basis == "observed"; None is
    # "not measured", never 0.
    momentum_pct: Optional[float] = None
    # The same movement in raw chart places (positive = climbed). Carried
    # alongside the percentage because "moved up 30 places" is the sentence a
    # buyer actually reasons with, and a percentage of chart depth is not.
    momentum_positions: Optional[int] = None
    # The 0-1 single-scan inference. Present when it was computable at all, and
    # used for the score only when momentum_basis == "rank_vs_depth".
    breakout: Optional[float] = None
    # Observed chart positions, oldest first. The sparkline draws this and
    # nothing else, so a product we've seen once has an empty list and the UI
    # draws no line rather than a flat one.
    rank_history: list[int] = []
    snapshots: int = 0

    # Component 0-1 values behind `score`, so the drawer shows its work.
    demand_component: float = 0.0
    quality_component: float = 0.0
    momentum_component: float = 0.0


class WinningResponse(BaseModel):
    products: list[WinningProduct]
    category: str
    category_label: str
    scanned_at: Optional[str] = None
    snapshots: int = 0
    source: str  # "fixture" | "live"
    latency_ms: int = 0
    warnings: list[str] = []


def _percentile_map(values: list[float]) -> dict[float, float]:
    """Value -> 0-1 percentile within `values`, ties sharing a percentile."""
    if not values:
        return {}
    ordered = sorted(set(values))
    if len(ordered) == 1:
        return {ordered[0]: 0.5}
    return {v: i / (len(ordered) - 1) for i, v in enumerate(ordered)}


async def _fetch_chart(url: str) -> list[dict]:
    """One Rainforest chart call. `amazon_domain` must be omitted when `url` is
    given -- passing both is a 400 ("cannot be combined")."""
    params = {"api_key": settings.rainforest_api_key, "type": "bestsellers", "url": url}
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.get(RAINFOREST_URL, params=params)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("request_info", {}).get("success"):
        message = payload.get("request_info", {}).get("message", "unknown error")
        raise RuntimeError(f"Rainforest chart request failed: {message}")
    return payload.get("bestsellers") or []


def _load_fixture() -> dict[str, list[dict]]:
    if not FIXTURE_PATH.exists():
        return {"bestsellers": [], "new_releases": []}
    return json.loads(FIXTURE_PATH.read_text())


async def load_charts(category: str, *, live: bool) -> tuple[dict[str, list[dict]], list[str]]:
    """Chart rows for a category, from the API or the captured fixture.

    The fixture is a real scan, saved to disk, not hand-written data -- so the
    page renders the same shapes either way and a demo can run without
    spending credits on every reload.
    """
    warnings: list[str] = []
    if not live:
        charts = _load_fixture()
        if not charts.get("bestsellers"):
            warnings.append(
                "No fixture found and live fetching is off - "
                "run a scan with ?live=true to populate it."
            )
        return charts, warnings

    config = CATEGORIES[category]
    charts: dict[str, list[dict]] = {}
    for chart in ("bestsellers", "new_releases"):
        try:
            charts[chart] = await _fetch_chart(config[chart])
        except Exception as e:  # noqa: BLE001 - surfaced to the user, not swallowed
            charts[chart] = []
            warnings.append(f"[{chart}] live fetch failed ({type(e).__name__}: {e})")
    return charts, warnings


def build(
    charts: dict[str, list[dict]],
    *,
    category: str,
    history: Optional[dict[str, list[dict]]] = None,
) -> list[WinningProduct]:
    """Score one category's chart into ranked WinningProducts."""
    bestsellers = [r for r in charts.get("bestsellers", []) if r.get("asin")]
    if not bestsellers:
        return []

    label = CATEGORIES.get(category, {}).get("label", category)
    new_release_asins = {r["asin"] for r in charts.get("new_releases", []) if r.get("asin")}
    history = history or {}
    n = len(bestsellers)

    review_pct = _percentile_map([float(r.get("ratings_total") or 0) for r in bestsellers])
    ratings = [float(r["rating"]) for r in bestsellers if r.get("rating") is not None]
    best_rating = max(ratings) if ratings else 5.0
    worst_rating = min(ratings) if ratings else 0.0
    rating_span = (best_rating - worst_rating) or 1.0

    out: list[WinningProduct] = []
    for row in bestsellers:
        rank = row.get("rank") or row.get("position") or n
        # 1.0 at rank 1, 0.0 at the bottom of the chart.
        demand = 1.0 - ((rank - 1) / (n - 1)) if n > 1 else 1.0

        rating = row.get("rating")
        quality = ((rating - worst_rating) / rating_span) if rating is not None else 0.0

        # Single-scan inference: standing, minus the review mass propping it up.
        depth = review_pct.get(float(row.get("ratings_total") or 0), 0.5)
        breakout = max(0.0, min(1.0, (demand - depth + 1.0) / 2.0))

        # Observed history wins whenever it exists.
        points = [h["rank"] for h in history.get(row["asin"], [])]
        momentum_pct: Optional[float] = None
        momentum_positions: Optional[int] = None
        if len(points) >= 2:
            basis = "observed"
            first, last = points[0], points[-1]
            # Rank is inverted: moving from 40 to 10 is a gain of 30 places.
            momentum_positions = first - last
            # Expressed against the chart's own depth, NOT against the starting
            # rank. Dividing by `first` looks natural and is badly asymmetric at
            # the top of a chart: 1 -> 3 reads as -200% while the identical
            # movement back, 3 -> 1, reads as +67%. Against chart depth the two
            # are -4% and +4% on a 50-row chart, which is what actually happened
            # -- a two-place wobble near the top of a list of fifty.
            momentum_pct = round((momentum_positions / n) * 100, 1) if n else 0.0
            # Bounded to [-100, +100] by construction, so this maps cleanly onto
            # 0-1 with "didn't move" landing at 0.5 rather than at an end.
            momentum_component = max(0.0, min(1.0, (momentum_pct + 100) / 200))
        else:
            basis = "rank_vs_depth"
            momentum_component = breakout

        score = round(
            100 * (W_MOMENTUM * momentum_component + W_DEMAND * demand + W_QUALITY * quality),
            1,
        )

        out.append(
            WinningProduct(
                asin=row["asin"],
                title=row.get("title") or "",
                rank=rank,
                # Charts serve a 300px grid thumbnail (._AC_UL300_SR300,200_.);
                # the same rewrite the retail cards use gets the full upload
                # without a second request.
                image=best_image(row.get("image"), site="amazon"),
                link=row.get("link"),
                rating=rating,
                ratings_total=row.get("ratings_total"),
                category=category,
                category_label=label,
                is_new_release=row["asin"] in new_release_asins,
                score=score,
                momentum_basis=basis,
                momentum_pct=momentum_pct,
                momentum_positions=momentum_positions,
                breakout=round(breakout, 3),
                rank_history=points,
                snapshots=len(points),
                demand_component=round(demand, 3),
                quality_component=round(quality, 3),
                momentum_component=round(momentum_component, 3),
            )
        )

    out.sort(key=lambda p: p.score, reverse=True)
    return out


async def winning_products(
    *, category: str = "kitchen", live: bool = False, record: bool = True
) -> WinningResponse:
    started = datetime.now(timezone.utc)
    charts, warnings = await load_charts(category, live=live)

    scanned_at: Optional[str] = None
    if live and record:
        for chart in ("bestsellers", "new_releases"):
            if charts.get(chart):
                scanned_at, _ = snapshot_store.record_scan(
                    charts[chart], category=category, chart=chart, scanned_at=scanned_at
                )

    history = snapshot_store.history_for_category(category)
    products = build(charts, category=category, history=history)
    times = snapshot_store.scan_times(category)

    if len(times) < 2:
        warnings.append(
            f"Momentum is inferred, not measured: {len(times)} snapshot(s) recorded so far. "
            "Rank movement becomes available once this category has been scanned twice."
        )

    return WinningResponse(
        products=products,
        category=category,
        category_label=CATEGORIES.get(category, {}).get("label", category),
        scanned_at=times[-1] if times else None,
        snapshots=len(times),
        source="live" if live else "fixture",
        latency_ms=int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
        warnings=warnings,
    )
