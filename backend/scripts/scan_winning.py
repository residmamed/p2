"""Record one Winning Products scan into the snapshot database.

This is the cron entry point. Momentum on that page is the difference between
two of these runs, so the schedule *is* the feature -- nothing else in the app
produces the data, and it cannot be backfilled later.

    # seed history from the captured fixture (free, no credits)
    python -m scripts.scan_winning --category kitchen

    # a real scan (2 Rainforest credits per category)
    python -m scripts.scan_winning --category kitchen --live

Suggested crontab, once credits allow -- daily is enough, since chart positions
don't move meaningfully faster than that:

    0 6 * * *  cd /path/to/backend && .venv/bin/python -m scripts.scan_winning --live
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import snapshot_store, winning  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default="kitchen", choices=list(winning.CATEGORIES))
    parser.add_argument(
        "--live",
        action="store_true",
        help="Fetch fresh charts from Rainforest (2 credits/category) instead of the fixture.",
    )
    args = parser.parse_args()

    charts, warnings = await winning.load_charts(args.category, live=args.live)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if not charts.get("bestsellers"):
        print("No chart rows - nothing recorded.", file=sys.stderr)
        return 1

    stamp = None
    total = 0
    for chart in ("bestsellers", "new_releases"):
        if not charts.get(chart):
            continue
        stamp, written = snapshot_store.record_scan(
            charts[chart], category=args.category, chart=chart, scanned_at=stamp
        )
        total += written
        print(f"  {chart}: {written} rows")

    scans = snapshot_store.scan_times(args.category)
    print(f"recorded {total} rows at {stamp} ({'live' if args.live else 'fixture'})")
    print(f"{args.category} now has {len(scans)} snapshot(s)")
    if len(scans) < 2:
        print("momentum stays inferred until a second scan exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
