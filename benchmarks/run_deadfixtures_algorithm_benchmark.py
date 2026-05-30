#!/usr/bin/env python
"""Benchmark only the dead-fixture analysis algorithm.

This runner builds an in-memory pytest-like session from the same workload
parameters used by the CLI benchmark. It intentionally excludes Python process
startup, imports, pytest command-line bootstrap, filesystem walking, test module
import, pytest collection, and terminal output rendering from the measured
section.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from deadfixtures_workload import DEFAULT_WORKLOADS, WORKLOADS, Workload, get_workload
from pytest_deadfixtures import (
    deadfixtures_ignore,
    get_fixtures,
    get_parametrized_fixtures,
    get_used_fixturesdefs,
    is_ignored_fixture,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeFixtureDef:
    """Small identity-comparable stand-in for pytest's FixtureDef."""

    __slots__ = ("argname", "func")

    def __init__(self, argname: str, func: object) -> None:
        self.argname = argname
        self.func = func


class FakeFixtureManager:
    __slots__ = ("_arg2fixturedefs",)

    def __init__(self, fixturedefs: dict[str, FakeFixtureDef]) -> None:
        self._arg2fixturedefs = {
            name: [fixturedef] for name, fixturedef in fixturedefs.items()
        }


class FakeSession:
    __slots__ = ("_fixturemanager", "items")

    def __init__(
        self, fixturedefs: dict[str, FakeFixtureDef], items: list[object]
    ) -> None:
        self._fixturemanager = FakeFixtureManager(fixturedefs)
        self.items = items


def main() -> int:
    args = parse_args()
    workloads = [get_workload(name) for name in args.case]
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
        help="workload to run; repeatable; defaults to small/medium/large",
    )
    parser.add_argument("--rounds", type=int, default=50)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    args.case = args.case or list(DEFAULT_WORKLOADS)
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")
    if args.warmups < 0:
        parser.error("--warmups must be >= 0")
    return args


def run_benchmarks(args: argparse.Namespace, workloads: list[Workload]) -> dict[str, Any]:
    results = []
    for workload in workloads:
        session = build_fake_session(workload)
        for _ in range(args.warmups):
            analyze_dead_fixtures(session)

        measurements = []
        for round_index in range(args.rounds):
            measurements.append(measure_algorithm(session, workload, round_index))
        results.append(workload_result(workload, session, measurements))

    return {"metadata": metadata(args), "workloads": results}


def build_fake_session(workload: Workload) -> FakeSession:
    fixturedefs: dict[str, FakeFixtureDef] = {}

    def add_fixture(name: str, filename: Path, ignored: bool = False) -> None:
        func = make_fixture_function(name, filename)
        if ignored:
            deadfixtures_ignore(func)
        fixturedefs[name] = FakeFixtureDef(name, func)

    conftest = REPO_ROOT / "tests" / "conftest.py"
    for index in range(workload.shared_used_fixtures):
        add_fixture(f"shared_used_fixture_{index}", conftest)
    for index in range(workload.shared_dead_fixtures):
        add_fixture(f"shared_dead_fixture_{index}", conftest)
    for index in range(workload.parametrized_fixtures):
        add_fixture(f"dynamic_fixture_{index}", conftest)
    for chain_index in range(workload.dependency_chains):
        for depth in range(workload.dependency_chain_length):
            add_fixture(f"dependency_chain_{chain_index}_{depth}", conftest)
    for index in range(workload.autouse_fixtures):
        add_fixture(f"autouse_fixture_{index}", conftest)
    for index in range(workload.ignored_fixtures):
        add_fixture(f"ignored_fixture_{index}", conftest, ignored=True)

    items = []
    autouse_names = [
        f"autouse_fixture_{index}" for index in range(workload.autouse_fixtures)
    ]
    for module_index in range(workload.modules):
        filename = REPO_ROOT / "tests" / f"test_realistic_{module_index:03d}.py"
        for index in range(workload.module_used_fixtures):
            add_fixture(f"module_{module_index}_used_fixture_{index}", filename)
        for index in range(workload.module_dead_fixtures):
            add_fixture(f"module_{module_index}_dead_fixture_{index}", filename)

        for test_index in range(workload.tests_per_module):
            suite_test_index = (module_index * workload.tests_per_module) + test_index
            used_names = [
                f"module_{module_index}_used_fixture_"
                f"{test_index % workload.module_used_fixtures}",
                f"shared_used_fixture_{suite_test_index % workload.shared_used_fixtures}",
                *dependency_chain_names(workload, suite_test_index),
                *autouse_names,
            ]
            items.append(make_item(fixturedefs, used_names))

        for index in range(workload.parametrized_fixtures):
            items.append(
                make_item(
                    fixturedefs,
                    [*autouse_names],
                    params={"fixture_name": f"dynamic_fixture_{index}"},
                )
            )

    return FakeSession(fixturedefs, items)


def dependency_chain_names(workload: Workload, suite_test_index: int) -> list[str]:
    chain_index = suite_test_index % workload.dependency_chains
    return [
        f"dependency_chain_{chain_index}_{depth}"
        for depth in range(workload.dependency_chain_length)
    ]


def make_item(
    fixturedefs: dict[str, FakeFixtureDef],
    used_names: list[str],
    params: dict[str, str] | None = None,
) -> object:
    name2fixturedefs = {name: [fixturedefs[name]] for name in used_names}
    item = SimpleNamespace(
        _fixtureinfo=SimpleNamespace(name2fixturedefs=name2fixturedefs)
    )
    if params is not None:
        item.callspec = SimpleNamespace(params=params)
    return item


def make_fixture_function(name: str, filename: Path) -> object:
    namespace: dict[str, object] = {}
    source = f"def {name}():\n    return None\n"
    exec(compile(source, str(filename), "exec"), namespace)
    func = namespace[name]
    func.__module__ = "bench_project.tests"
    return func


def measure_algorithm(
    session: FakeSession,
    workload: Workload,
    round_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = analyze_dead_fixtures(session)
    seconds = time.perf_counter() - started
    assert_expected_result(workload, result["unused_fixture_names"])
    return {"round": round_index + 1, "seconds": seconds, **result}


def analyze_dead_fixtures(session: FakeSession) -> dict[str, Any]:
    phases: dict[str, float] = {}

    started = time.perf_counter()
    used_fixtures = get_used_fixturesdefs(session)
    phases["get_used_fixturesdefs"] = time.perf_counter() - started

    started = time.perf_counter()
    available_fixtures = get_fixtures(session)
    phases["get_fixtures"] = time.perf_counter() - started

    started = time.perf_counter()
    param_fixtures = get_parametrized_fixtures(session, available_fixtures)
    phases["get_parametrized_fixtures"] = time.perf_counter() - started

    started = time.perf_counter()
    used_fixturedefs = set(used_fixtures)
    param_fixturedefs = set(param_fixtures)
    ignored_fixturedefs = {
        fixture.fixturedef
        for fixture in available_fixtures
        if is_ignored_fixture(fixture.fixturedef)
    }
    unused_fixtures = [
        fixture
        for fixture in available_fixtures
        if fixture.fixturedef not in used_fixturedefs
        and fixture.fixturedef not in param_fixturedefs
        and fixture.fixturedef not in ignored_fixturedefs
    ]
    phases["filter_unused_fixtures"] = time.perf_counter() - started

    return {
        "available_fixture_count": len(available_fixtures),
        "used_fixture_reference_count": len(used_fixtures),
        "parametrized_fixture_count": len(param_fixtures),
        "unused_fixture_count": len(unused_fixtures),
        "unused_fixture_names": sorted(fixture.argname for fixture in unused_fixtures),
        "phase_seconds": phases,
    }


def assert_expected_result(workload: Workload, unused_names: list[str]) -> None:
    expected = sorted(workload.expected_dead_fixture_names())
    if unused_names != expected:
        missing = sorted(set(expected) - set(unused_names))
        unexpected = sorted(set(unused_names) - set(expected))
        raise AssertionError(
            f"{workload.name} produced wrong unused fixture set\n"
            f"Missing: {missing[:20]}\nUnexpected: {unexpected[:20]}"
        )


def workload_result(
    workload: Workload,
    session: FakeSession,
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    seconds = [measurement["seconds"] for measurement in measurements]
    phase_names = measurements[0]["phase_seconds"].keys()
    return {
        "name": workload.name,
        "parameters": workload.to_dict(),
        "session_shape": {
            "fixturedefs": len(session._fixturemanager._arg2fixturedefs),
            "items": len(session.items),
        },
        "expected_dead_fixtures": workload.expected_dead_fixtures,
        "rounds": measurements,
        "stats": stats(seconds),
        "phase_stats": {
            phase: stats(
                [measurement["phase_seconds"][phase] for measurement in measurements]
            )
            for phase in phase_names
        },
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
        "benchmark_kind": "algorithm-only",
        "excluded_from_timing": [
            "Python process startup",
            "module imports",
            "pytest CLI bootstrap",
            "filesystem walking",
            "test module import",
            "pytest collection",
            "terminal output rendering",
        ],
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "rounds": args.rounds,
        "warmups": args.warmups,
    }


def print_summary(output: dict[str, Any]) -> None:
    print("algorithm-only dead-fixture analysis")
    print("case          fixtures  items   dead   mean (ms)  median (ms)  min (ms)")
    print("------------  --------  ------  -----  ---------  -----------  --------")
    for workload in output["workloads"]:
        shape = workload["session_shape"]
        stats_ = workload["stats"]
        print(
            f"{workload['name']:<12}  "
            f"{shape['fixturedefs']:>8}  "
            f"{shape['items']:>6}  "
            f"{workload['expected_dead_fixtures']:>5}  "
            f"{stats_['mean_seconds'] * 1000:>9.3f}  "
            f"{stats_['median_seconds'] * 1000:>11.3f}  "
            f"{stats_['min_seconds'] * 1000:>8.3f}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
