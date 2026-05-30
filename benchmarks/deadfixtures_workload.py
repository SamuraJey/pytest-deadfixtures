"""Synthetic pytest projects used by the dead-fixtures benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from textwrap import dedent


@dataclass(frozen=True)
class Workload:
    """Size parameters for a generated pytest project."""

    name: str
    modules: int
    tests_per_module: int
    module_used_fixtures: int
    module_dead_fixtures: int
    shared_used_fixtures: int
    shared_dead_fixtures: int
    parametrized_fixtures: int
    autouse_fixtures: int
    ignored_fixtures: int
    dependency_chains: int
    dependency_chain_length: int

    @property
    def expected_dead_fixtures(self) -> int:
        return len(self.expected_dead_fixture_names())

    def expected_dead_fixture_names(self) -> list[str]:
        names = [
            f"shared_dead_fixture_{index}"
            for index in range(self.shared_dead_fixtures)
        ]
        used_module_fixture_indexes = {
            test_index % self.module_used_fixtures
            for test_index in range(self.tests_per_module)
        }
        names.extend(
            f"module_{module_index}_dead_fixture_{index}"
            for module_index in range(self.modules)
            for index in range(self.module_dead_fixtures)
        )
        names.extend(
            f"module_{module_index}_used_fixture_{index}"
            for module_index in range(self.modules)
            for index in range(self.module_used_fixtures)
            if index not in used_module_fixture_indexes
        )
        return names

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


DEFAULT_WORKLOADS = ("small", "medium", "large")

WORKLOADS = {
    "small": Workload(
        name="small",
        modules=8,
        tests_per_module=6,
        module_used_fixtures=4,
        module_dead_fixtures=2,
        shared_used_fixtures=8,
        shared_dead_fixtures=4,
        parametrized_fixtures=3,
        autouse_fixtures=2,
        ignored_fixtures=2,
        dependency_chains=2,
        dependency_chain_length=3,
    ),
    "medium": Workload(
        name="medium",
        modules=32,
        tests_per_module=10,
        module_used_fixtures=8,
        module_dead_fixtures=4,
        shared_used_fixtures=24,
        shared_dead_fixtures=12,
        parametrized_fixtures=8,
        autouse_fixtures=4,
        ignored_fixtures=4,
        dependency_chains=6,
        dependency_chain_length=4,
    ),
    "large": Workload(
        name="large",
        modules=96,
        tests_per_module=12,
        module_used_fixtures=10,
        module_dead_fixtures=6,
        shared_used_fixtures=48,
        shared_dead_fixtures=24,
        parametrized_fixtures=16,
        autouse_fixtures=6,
        ignored_fixtures=8,
        dependency_chains=12,
        dependency_chain_length=5,
    ),
    "monorepo_8k": Workload(
        name="monorepo_8k",
        modules=8000,
        tests_per_module=1,
        module_used_fixtures=2,
        module_dead_fixtures=1,
        shared_used_fixtures=64,
        shared_dead_fixtures=64,
        parametrized_fixtures=1,
        autouse_fixtures=8,
        ignored_fixtures=16,
        dependency_chains=16,
        dependency_chain_length=4,
    ),
}


def get_workload(name: str) -> Workload:
    try:
        return WORKLOADS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(WORKLOADS))
        raise ValueError(f"unknown workload {name!r}; choose one of: {choices}") from exc


def generate_project(base_path: Path, workload: Workload) -> Path:
    """Generate a realistic pytest suite and return its root directory."""

    project_path = base_path / f"deadfixtures-{workload.name}"
    tests_path = project_path / "tests"
    tests_path.mkdir(parents=True, exist_ok=True)

    (project_path / "pytest.ini").write_text(
        dedent(
            """
            [pytest]
            testpaths = tests
            python_files = test_*.py
            """
        ).lstrip(),
        encoding="utf-8",
    )
    _write_conftest(tests_path / "conftest.py", workload)

    for module_index in range(workload.modules):
        _write_test_module(
            tests_path / f"test_realistic_{module_index:03d}.py",
            workload,
            module_index,
        )

    return project_path


def _write_conftest(path: Path, workload: Workload) -> None:
    lines = [
        "import pytest",
        "from pytest_deadfixtures import deadfixtures_ignore",
        "",
    ]

    for index in range(workload.shared_used_fixtures):
        lines.extend(_fixture_lines(f"shared_used_fixture_{index}", index))

    for index in range(workload.shared_dead_fixtures):
        lines.extend(_fixture_lines(f"shared_dead_fixture_{index}", index))

    for index in range(workload.parametrized_fixtures):
        lines.extend(_fixture_lines(f"dynamic_fixture_{index}", index))

    for chain_index in range(workload.dependency_chains):
        dependency_name = ""
        for depth in range(workload.dependency_chain_length):
            name = f"dependency_chain_{chain_index}_{depth}"
            if depth == 0:
                lines.extend(_fixture_lines(name, chain_index))
            else:
                lines.extend(_dependent_fixture_lines(name, dependency_name))
            dependency_name = name

    for index in range(workload.autouse_fixtures):
        lines.extend(
            _fixture_lines(
                f"autouse_fixture_{index}",
                index,
                decorators=["@pytest.fixture(autouse=True)"],
            )
        )

    for index in range(workload.ignored_fixtures):
        lines.extend(
            _fixture_lines(
                f"ignored_fixture_{index}",
                index,
                decorators=["@pytest.fixture()", "@deadfixtures_ignore"],
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_test_module(path: Path, workload: Workload, module_index: int) -> None:
    lines = ["import pytest", ""]

    for index in range(workload.module_used_fixtures):
        lines.extend(_fixture_lines(f"module_{module_index}_used_fixture_{index}", index))

    for index in range(workload.module_dead_fixtures):
        lines.extend(_fixture_lines(f"module_{module_index}_dead_fixture_{index}", index))

    for test_index in range(workload.tests_per_module):
        suite_test_index = (module_index * workload.tests_per_module) + test_index
        module_fixture = _cycle_name(
            f"module_{module_index}_used_fixture",
            workload.module_used_fixtures,
            test_index,
        )
        shared_fixture = _cycle_name(
            "shared_used_fixture", workload.shared_used_fixtures, suite_test_index
        )
        dependency_fixture = _cycle_name(
            "dependency_chain",
            workload.dependency_chains,
            suite_test_index,
            suffix=f"_{workload.dependency_chain_length - 1}",
        )
        lines.extend(
            [
                f"def test_realistic_{module_index}_{test_index}(",
                f"    {module_fixture}, {shared_fixture}, {dependency_fixture}",
                "):",
                f"    assert {module_fixture} >= 0",
                f"    assert {shared_fixture} >= 0",
                f"    assert {dependency_fixture} >= 0",
                "",
            ]
        )

    dynamic_values = ", ".join(
        repr(f"dynamic_fixture_{index}")
        for index in range(workload.parametrized_fixtures)
    )
    lines.extend(
        [
            f"@pytest.mark.parametrize('fixture_name', [{dynamic_values}])",
            f"def test_dynamic_fixture_lookup_{module_index}(request, fixture_name):",
            "    assert request.getfixturevalue(fixture_name) >= 0",
            "",
        ]
    )

    path.write_text("\n".join(lines), encoding="utf-8")


def _cycle_name(prefix: str, count: int, index: int, suffix: str = "") -> str:
    return f"{prefix}_{index % count}{suffix}"


def _fixture_lines(
    name: str,
    value: int,
    decorators: list[str] | None = None,
) -> list[str]:
    fixture_decorators = decorators or ["@pytest.fixture()"]
    return [
        *fixture_decorators,
        f"def {name}():",
        f"    return {value}",
        "",
    ]


def _dependent_fixture_lines(name: str, dependency: str) -> list[str]:
    return [
        "@pytest.fixture()",
        f"def {name}({dependency}):",
        f"    return {dependency} + 1",
        "",
    ]
