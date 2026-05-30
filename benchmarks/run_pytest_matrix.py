#!/usr/bin/env python
"""Run dead-fixtures benchmarks across a pytest version matrix."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
import venv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_SCRIPT = REPO_ROOT / "benchmarks" / "run_deadfixtures_benchmark.py"
DEFAULT_VERSIONS_FILE = REPO_ROOT / "benchmarks" / "pytest-versions.txt"
PYPI_JSON_URL = "https://pypi.org/pypi/pytest/json"


class VersionMismatchError(RuntimeError):
    """Raised when a matrix virtualenv contains the wrong pytest version."""


def main() -> int:
    args = parse_args()
    versions = args.version or read_versions(args.versions_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.venv_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "versions": [],
        "failures": [],
    }
    if args.check_latest:
        latest = fetch_latest_pytest_version()
        summary["latest_check"] = {
            "pypi_latest": latest,
            "last_listed": versions[-1],
            "status": "passed" if versions[-1] == latest else "failed",
        }
        if versions[-1] != latest:
            raise SystemExit(
                f"pytest-versions.txt ends at {versions[-1]}, but PyPI latest is "
                f"{latest}; refresh {args.versions_file}"
            )

    for version in versions:
        print(f"\n=== pytest {version} ===")
        try:
            output_file, actual_version = run_for_version(args, version)
            summary["versions"].append(
                {
                    "pytest_version": version,
                    "actual_pytest_version": actual_version,
                    "status": "passed",
                    "output": str(output_file),
                }
            )
        except (subprocess.CalledProcessError, VersionMismatchError) as exc:
            failure = {
                "pytest_version": version,
                "status": "failed",
                "error": str(exc),
            }
            if isinstance(exc, subprocess.CalledProcessError):
                failure["returncode"] = exc.returncode
                failure["command"] = exc.cmd
            summary["failures"].append(failure)
            print(f"FAILED pytest {version}: {exc}", file=sys.stderr)
            if args.fail_fast:
                break

    summary_file = args.output_dir / "matrix-summary.json"
    summary_file.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"\nMatrix summary: {summary_file}")
    return 1 if summary["failures"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        action="append",
        help="pytest version to benchmark; repeatable; defaults to versions file",
    )
    parser.add_argument(
        "--versions-file",
        type=Path,
        default=DEFAULT_VERSIONS_FILE,
        help="file with pytest versions, one per line",
    )
    parser.add_argument(
        "--venv-dir",
        type=Path,
        default=REPO_ROOT / ".benchmarks" / "venvs",
        help="directory for reusable benchmark virtualenvs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / ".benchmarks" / "results",
        help="directory for benchmark JSON results",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=None,
        help="workload to run; repeatable; defaults to run_deadfixtures_benchmark.py",
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--recreate", action="store_true", help="recreate virtualenvs")
    parser.add_argument("--skip-install", action="store_true", help="reuse existing envs")
    parser.add_argument(
        "--check-latest",
        action="store_true",
        help="fail if the last listed pytest version is not current on PyPI",
    )
    parser.add_argument("--fail-fast", action="store_true")
    return parser.parse_args()


def read_versions(path: Path) -> list[str]:
    versions = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            versions.append(stripped)
    return versions


def run_for_version(args: argparse.Namespace, version: str) -> tuple[Path, str]:
    venv_path = args.venv_dir / f"pytest-{version}"
    python = venv_python(venv_path)

    if args.recreate and venv_path.exists():
        shutil.rmtree(venv_path)

    if not args.skip_install or not python.exists():
        create_venv(venv_path)
        install_dependencies(python, version)

    actual_version = installed_pytest_version(python)
    if actual_version != version:
        raise VersionMismatchError(
            f"{python} has pytest {actual_version}, expected {version}; "
            "rerun without --skip-install or use --recreate"
        )

    output_file = args.output_dir / f"pytest-{version}.json"
    command = [
        str(python),
        str(BENCHMARK_SCRIPT),
        "--rounds",
        str(args.rounds),
        "--warmups",
        str(args.warmups),
        "--output",
        str(output_file),
    ]
    for case in args.case or []:
        command.extend(["--case", case])

    subprocess.run(command, cwd=REPO_ROOT, check=True)
    return output_file, actual_version


def create_venv(path: Path) -> None:
    if not path.exists():
        venv.EnvBuilder(with_pip=True).create(path)


def install_dependencies(python: Path, pytest_version: str) -> None:
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "-e",
            str(REPO_ROOT),
            f"pytest=={pytest_version}",
        ],
        cwd=REPO_ROOT,
        check=True,
    )


def installed_pytest_version(python: Path) -> str:
    result = subprocess.run(
        [str(python), "-c", "import pytest; print(pytest.__version__)"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return result.stdout.strip()


def fetch_latest_pytest_version() -> str:
    with urllib.request.urlopen(PYPI_JSON_URL, timeout=15) as response:
        payload = json.load(response)
    return payload["info"]["version"]


def venv_python(path: Path) -> Path:
    if sys.platform == "win32":
        return path / "Scripts" / "python.exe"
    return path / "bin" / "python"


if __name__ == "__main__":
    raise SystemExit(main())
