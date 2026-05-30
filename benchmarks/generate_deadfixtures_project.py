#!/usr/bin/env python
"""Generate a synthetic pytest project for external CLI benchmarking tools."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from deadfixtures_workload import WORKLOADS, generate_project, get_workload


def main() -> int:
    args = parse_args()
    workload = get_workload(args.case)
    project_path = args.output_dir / f"deadfixtures-{workload.name}"
    if args.clean and project_path.exists():
        shutil.rmtree(project_path)
    project_path = generate_project(args.output_dir, workload)
    py_files = sum(1 for _ in project_path.rglob("*.py"))
    print(project_path)
    print(f"python_files={py_files}")
    print(f"expected_dead_fixtures={workload.expected_dead_fixtures}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(WORKLOADS), default="medium")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".benchmarks/generated"),
        help="directory that will contain deadfixtures-<case>",
    )
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
