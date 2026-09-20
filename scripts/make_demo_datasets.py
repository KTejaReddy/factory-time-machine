"""Generate two example CSV datasets for the multi-dataset demo.

These are *example inputs*, not analysis results: the app profiles them, detects
their capabilities and computes everything else itself. They exist so the case-file
workflow (Dataset A -> analysis -> Dataset B -> switch back) can be demonstrated
without needing a real plant export.

Usage::

    python scripts/make_demo_datasets.py [--out data/cache/demo]

Dataset A is a wide-ish long-format export with station rows, costs and downtime.
Dataset B uses different column names and deliberately different values, so the
isolation test (A's bottleneck is not B's) is meaningful.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, header: list, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def dataset_a(seed: int = 20260920) -> tuple:
    """Station-shift export: 4 stations, 180 shifts each (720 rows)."""
    rng = random.Random(seed)
    header = [
        "batch_id",
        "station",
        "cycle_time_s",
        "station_util",
        "queue_len",
        "downtime_hours",
        "scrap_count",
        "rework_count",
        "scrap_cost",
        "downtime_cost",
        "unit_value",
    ]
    # Station 3 is the constraint in A: highest utilisation, longest queue.
    profile = {
        "Cutting":   dict(util=0.72, cycle=41.0, queue=6.5, down=0.55, scrap=3.1, rework=2.4),
        "Welding":   dict(util=0.86, cycle=52.0, queue=18.4, down=1.05, scrap=5.6, rework=4.1),
        "Assembly":  dict(util=0.94, cycle=64.0, queue=31.2, down=1.35, scrap=8.9, rework=7.3),
        "Packing":   dict(util=0.61, cycle=28.0, queue=3.1, down=0.30, scrap=1.4, rework=0.9),
    }
    rows = []
    for batch in range(1, 181):
        for station, spec in profile.items():
            jitter = lambda amp: 1.0 + rng.uniform(-amp, amp)  # noqa: E731
            rows.append(
                [
                    f"B{batch:04d}",
                    station,
                    round(spec["cycle"] * jitter(0.08), 2),
                    round(min(0.999, spec["util"] * jitter(0.04)), 4),
                    round(max(0.0, spec["queue"] * jitter(0.35)), 2),
                    round(max(0.0, spec["down"] * jitter(0.45)), 3),
                    max(0, round(spec["scrap"] * jitter(0.5))),
                    max(0, round(spec["rework"] * jitter(0.5))),
                    round(118.0 * jitter(0.05), 2),
                    round(340.0 * jitter(0.05), 2),
                    round(96.0 * jitter(0.03), 2),
                ]
            )
    return header, rows


def dataset_b(seed: int = 7717) -> tuple:
    """A different-looking export: machine rows, different units, its own constraint."""
    rng = random.Random(seed)
    header = [
        "shift_id",
        "machine",
        "Cycle_Time_min",
        "util_pct",
        "queue_parts",
        "downtime_min",
        "defects",
        "rework_units",
        "unit_price",
    ]
    # Press 2 is the constraint in B, and the whole plant runs at a different level.
    profile = {
        "Press 1": dict(util=0.55, cycle=12.0, queue=12.0, down=14.0, defects=6.0, rework=3.0),
        "Press 2": dict(util=0.91, cycle=17.5, queue=48.0, down=39.0, defects=19.0, rework=11.0),
        "Press 3": dict(util=0.48, cycle=9.4, queue=5.0, down=8.0, defects=2.0, rework=1.0),
    }
    rows = []
    for shift in range(1, 241):
        for machine, spec in profile.items():
            jitter = lambda amp: 1.0 + rng.uniform(-amp, amp)  # noqa: E731
            rows.append(
                [
                    f"S{shift:04d}",
                    machine,
                    round(spec["cycle"] * jitter(0.06), 3),
                    round(min(99.9, spec["util"] * 100.0 * jitter(0.03)), 2),
                    round(max(0.0, spec["queue"] * jitter(0.4)), 1),
                    round(max(0.0, spec["down"] * jitter(0.4)), 1),
                    max(0, round(spec["defects"] * jitter(0.4))),
                    max(0, round(spec["rework"] * jitter(0.4))),
                    round(21.5 * jitter(0.04), 2),
                ]
            )
    return header, rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(PROJECT_ROOT / "data" / "cache" / "demo"))
    args = parser.parse_args()
    out = Path(args.out)
    a_header, a_rows = dataset_a()
    b_header, b_rows = dataset_b()
    _write(out / "factory_batch_a.csv", a_header, a_rows)
    _write(out / "factory_batch_b.csv", b_header, b_rows)
    print(f"wrote {out / 'factory_batch_a.csv'} ({len(a_rows)} rows)")
    print(f"wrote {out / 'factory_batch_b.csv'} ({len(b_rows)} rows)")


if __name__ == "__main__":
    main()
