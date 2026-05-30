#!/usr/bin/env python
"""Run baseline performance benchmarks for ``pytest --dead-fixtures``.

The benchmark intentionally executes pytest in a subprocess. That keeps the
measurement close to real CLI usage and makes the same workload usable across
pytest versions from 7.4.4 through the latest version installed in the runner.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deadfixtures_workload import (
    DEFAULT_WORKLOADS,
    WORKLOADS,
    Workload,
    generate_project,
    get_workload,
)

EXIT_CODE_ERROR = 11
REPO_ROOT = Path(__file__).resolve().parents[1]
UNUSED_HEADLINE = (
    "Hey there, I believe the following {count} fixture(s) are not being used:"
)
REPORTED_FIXTURE_RE = re.compile(r"^Fixture name: (?P<name>[^,]+), location: ", re.M)


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
    parser.add_argument(
        "--rounds",
        type=int,
        default=int(os.environ.get("DEADFIXTURES_BENCH_ROUNDS", "5")),
        help="measured rounds per workload (default: 5)",
    )
    parser.add_argument(
        "--warmups",
        type=int,
        default=int(os.environ.get("DEADFIXTURES_BENCH_WARMUPS", "1")),
        help="unmeasured warmup rounds per workload (default: 1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="optional JSON output path for baseline storage",
    )
    parser.add_argument(
        "--keep-projects",
        action="store_true",
        help="keep generated workloads on disk for inspection",
    )
    parser.add_argument(
        "--tmpdir",
        type=Path,
        default=None,
        help="directory for generated projects; defaults to a temporary directory",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="extra argument passed to the inner pytest command; repeatable",
    )
    args = parser.parse_args()
    args.case = args.case or list(DEFAULT_WORKLOADS)
    if args.rounds < 1:
        parser.error("--rounds must be >= 1")
    if args.warmups < 0:
        parser.error("--warmups must be >= 0")
    return args


def run_benchmarks(args: argparse.Namespace, workloads: list[Workload]) -> dict[str, Any]:
    tmp_root = args.tmpdir or Path(tempfile.mkdtemp(prefix="deadfixtures-bench-"))
    tmp_root.mkdir(parents=True, exist_ok=True)

    try:
        results = []
        for workload in workloads:
            project_path = generate_project(tmp_root, workload)
            for _ in range(args.warmups):
                _run_deadfixtures(project_path, args.pytest_arg)

            measurements = []
            for round_index in range(args.rounds):
                measurement = _measure_deadfixtures(
                    project_path, args.pytest_arg, round_index
                )
                _assert_workload_result(workload, measurement)
                measurements.append(measurement)

            results.append(_workload_result(workload, measurements))

        return {
            "metadata": _metadata(args),
            "workloads": results,
        }
    finally:
        if not args.keep_projects and args.tmpdir is None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _measure_deadfixtures(
    project_path: Path,
    extra_pytest_args: list[str],
    round_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = _run_deadfixtures(project_path, extra_pytest_args)
    elapsed = time.perf_counter() - started
    return {
        "round": round_index + 1,
        "seconds": elapsed,
        "returncode": result.returncode,
        "reported_fixtures": sorted(REPORTED_FIXTURE_RE.findall(result.stdout)),
        "stdout_bytes": len(result.stdout.encode()),
        "stderr_bytes": len(result.stderr.encode()),
        "stdout_head": result.stdout[:1000],
        "stdout_tail": result.stdout[-1000:],
        "stderr_head": result.stderr[:1000],
        "stderr_tail": result.stderr[-1000:],
    }


def _run_deadfixtures(
    project_path: Path,
    extra_pytest_args: list[str],
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "pytest_deadfixtures",
        "-p",
        "no:cacheprovider",
        "--dead-fixtures",
        "-q",
        *extra_pytest_args,
    ]
    return subprocess.run(
        command,
        cwd=project_path,
        env=_benchmark_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _benchmark_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)

    pythonpath = env.get("PYTHONPATH")
    repo_root = str(REPO_ROOT)
    env["PYTHONPATH"] = (
        repo_root if not pythonpath else os.pathsep.join([repo_root, pythonpath])
    )
    return env


def _assert_workload_result(workload: Workload, measurement: dict[str, Any]) -> None:
    if measurement["returncode"] != EXIT_CODE_ERROR:
        raise AssertionError(
            f"{workload.name} returned {measurement['returncode']} instead of "
            f"{EXIT_CODE_ERROR}\nSTDOUT:\n{measurement['stdout_tail']}\n"
            f"STDERR:\n{measurement['stderr_tail']}"
        )

    expected_headline = UNUSED_HEADLINE.format(count=workload.expected_dead_fixtures)
    stdout_sample = measurement["stdout_head"] + measurement["stdout_tail"]
    if expected_headline not in stdout_sample:
        raise AssertionError(
            f"{workload.name} did not report expected dead fixture count "
            f"{workload.expected_dead_fixtures}\nSTDOUT:\n"
            f"{measurement['stdout_tail']}"
        )

    expected_names = set(workload.expected_dead_fixture_names())
    reported_names = set(measurement["reported_fixtures"])
    if reported_names != expected_names:
        missing = sorted(expected_names - reported_names)
        unexpected = sorted(reported_names - expected_names)
        raise AssertionError(
            f"{workload.name} reported wrong fixture set\n"
            f"Missing expected dead fixtures: {missing[:20]}\n"
            f"Unexpected reported fixtures: {unexpected[:20]}"
        )


def _workload_result(
    workload: Workload,
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    seconds = [measurement["seconds"] for measurement in measurements]
    return {
        "name": workload.name,
        "parameters": workload.to_dict(),
        "expected_dead_fixtures": workload.expected_dead_fixtures,
        "rounds": measurements,
        "stats": {
            "min_seconds": min(seconds),
            "max_seconds": max(seconds),
            "mean_seconds": statistics.fmean(seconds),
            "median_seconds": statistics.median(seconds),
            "stdev_seconds": statistics.stdev(seconds) if len(seconds) > 1 else 0.0,
        },
    }


def _metadata(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "pytest_version": _pytest_version(),
        "pytest_deadfixtures_repo": str(REPO_ROOT),
        "rounds": args.rounds,
        "warmups": args.warmups,
        "extra_pytest_args": args.pytest_arg,
        "pytest_cache": "disabled with -p no:cacheprovider",
    }


def _pytest_version() -> str:
    try:
        import pytest
    except ImportError:
        return "not-installed"
    return pytest.__version__


def print_summary(output: dict[str, Any]) -> None:
    metadata = output["metadata"]
    print(
        "pytest {pytest_version} | python {python_version}".format(
            pytest_version=metadata["pytest_version"],
            python_version=metadata["python"].split()[0],
        )
    )
    print("case      dead fixtures  mean (s)  median (s)  min (s)  max (s)")
    print("--------  -------------  --------  ----------  -------  -------")
    for workload in output["workloads"]:
        stats = workload["stats"]
        print(
            f"{workload['name']:<8}  "
            f"{workload['expected_dead_fixtures']:>13}  "
            f"{stats['mean_seconds']:>8.4f}  "
            f"{stats['median_seconds']:>10.4f}  "
            f"{stats['min_seconds']:>7.4f}  "
            f"{stats['max_seconds']:>7.4f}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
