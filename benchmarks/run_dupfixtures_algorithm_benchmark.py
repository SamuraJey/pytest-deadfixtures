#!/usr/bin/env python
"""Benchmark only the duplicate-fixture analysis algorithm.

This runner builds in-memory ``CachedFixture`` records and calls the same
production helper used by ``pytest --dup-fixtures``. It intentionally excludes
pytest startup, test execution, and terminal rendering from the measured
section.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from pytest_deadfixtures import CachedFixture, _find_duplicate_fixtures


@dataclass(frozen=True)
class DupWorkload:
    name: str
    unique_fixtures: int
    duplicate_groups: int
    duplicate_group_size: int
    falsey_fixtures: int

    @property
    def fixture_count(self) -> int:
        return (
            self.unique_fixtures
            + (self.duplicate_groups * self.duplicate_group_size)
            + self.falsey_fixtures
        )

    @property
    def expected_duplicate_pairs(self) -> int:
        pairs_per_group = self.duplicate_group_size * (self.duplicate_group_size - 1)
        return self.duplicate_groups * (pairs_per_group // 2)

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


DEFAULT_WORKLOADS = ("small", "large")

WORKLOADS = {
    "small": DupWorkload(
        name="small",
        unique_fixtures=64,
        duplicate_groups=8,
        duplicate_group_size=3,
        falsey_fixtures=8,
    ),
    "large": DupWorkload(
        name="large",
        unique_fixtures=3000,
        duplicate_groups=200,
        duplicate_group_size=3,
        falsey_fixtures=200,
    ),
}


class FakeFixtureDef:
    __slots__ = ("argname",)

    def __init__(self, argname: str) -> None:
        self.argname = argname


class DuplicateValue:
    def __init__(self, value: int) -> None:
        self.value = value


def main() -> int:
    args = parse_args()
    workloads = [WORKLOADS[name] for name in args.case]
    output = run_benchmarks(args, workloads)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")

    print_summary(output)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case",
        action="append",
        choices=sorted(WORKLOADS),
        default=None,
        help="workload to run; repeatable; defaults to small/large",
    )
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    args.case = args.case or list(DEFAULT_WORKLOADS)
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")
    if args.warmups < 0:
        parser.error("--warmups must be >= 0")
    return args


def run_benchmarks(
    args: argparse.Namespace,
    workloads: list[DupWorkload],
) -> dict[str, Any]:
    results = []
    for workload in workloads:
        fixtures = build_cached_fixtures(workload)
        for _ in range(args.warmups):
            _find_duplicate_fixtures(fixtures)

        measurements = []
        for round_index in range(args.rounds):
            measurements.append(measure_algorithm(fixtures, workload, round_index))
        results.append(workload_result(workload, fixtures, measurements))

    return {"metadata": metadata(args), "workloads": results}


def build_cached_fixtures(workload: DupWorkload) -> list[CachedFixture]:
    fixtures: list[CachedFixture] = []

    for index in range(workload.unique_fixtures):
        fixtures.append(make_cached_fixture(f"unique_fixture_{index}", index + 1))

    for group_index in range(workload.duplicate_groups):
        for fixture_index in range(workload.duplicate_group_size):
            fixtures.append(
                make_cached_fixture(
                    f"duplicate_fixture_{group_index}_{fixture_index}",
                    DuplicateValue(group_index),
                )
            )

    for index in range(workload.falsey_fixtures):
        fixtures.append(make_cached_fixture(f"falsey_fixture_{index}", 0))

    return fixtures


def make_cached_fixture(name: str, result: object) -> CachedFixture:
    return CachedFixture(
        fixturedef=FakeFixtureDef(name),
        relpath=f"tests/{name}.py:1",
        result=result,
    )


def measure_algorithm(
    fixtures: list[CachedFixture],
    workload: DupWorkload,
    round_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    duplicate_pairs = _find_duplicate_fixtures(fixtures)
    seconds = time.perf_counter() - started
    assert_expected_result(workload, duplicate_pairs)
    return {
        "round": round_index + 1,
        "seconds": seconds,
        "duplicate_pair_count": len(duplicate_pairs),
    }


def assert_expected_result(
    workload: DupWorkload,
    duplicate_pairs: list[tuple[CachedFixture, CachedFixture]],
) -> None:
    expected = set(expected_duplicate_pair_names(workload))
    actual = {
        (left.fixturedef.argname, right.fixturedef.argname)
        for left, right in duplicate_pairs
    }
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise AssertionError(
            f"{workload.name} produced wrong duplicate fixture set\n"
            f"Missing: {missing[:20]}\nUnexpected: {unexpected[:20]}"
        )


def expected_duplicate_pair_names(workload: DupWorkload) -> list[tuple[str, str]]:
    names = []
    for group_index in range(workload.duplicate_groups):
        group_names = [
            f"duplicate_fixture_{group_index}_{fixture_index}"
            for fixture_index in range(workload.duplicate_group_size)
        ]
        names.extend(combinations(group_names, 2))
    return names


def workload_result(
    workload: DupWorkload,
    fixtures: list[CachedFixture],
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    seconds = [measurement["seconds"] for measurement in measurements]
    return {
        "name": workload.name,
        "parameters": workload.to_dict(),
        "session_shape": {
            "cached_fixtures": len(fixtures),
        },
        "expected_duplicate_pairs": workload.expected_duplicate_pairs,
        "rounds": measurements,
        "stats": stats(seconds),
    }


def stats(values: list[float]) -> dict[str, float]:
    return {
        "min_seconds": min(values),
        "max_seconds": max(values),
        "mean_seconds": statistics.fmean(values),
        "median_seconds": statistics.median(values),
        "stdev_seconds": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


def metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "benchmark_kind": "dup-fixtures-algorithm-only",
        "excluded_from_timing": [
            "Python process startup",
            "module imports",
            "pytest CLI bootstrap",
            "test execution",
            "terminal output rendering",
        ],
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "warmups": args.warmups,
    }


def print_summary(output: dict[str, Any]) -> None:
    print("algorithm-only duplicate-fixture analysis")
    print("case      cached fixtures  pairs  mean (ms)  median (ms)  min (ms)")
    print("--------  ---------------  -----  ---------  -----------  --------")
    for workload in output["workloads"]:
        shape = workload["session_shape"]
        stats_ = workload["stats"]
        print(
            f"{workload['name']:<8}  "
            f"{shape['cached_fixtures']:>15}  "
            f"{workload['expected_duplicate_pairs']:>5}  "
            f"{stats_['mean_seconds'] * 1000:>9.3f}  "
            f"{stats_['median_seconds'] * 1000:>11.3f}  "
            f"{stats_['min_seconds'] * 1000:>8.3f}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
