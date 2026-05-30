# Dead Fixtures Performance Benchmarks

This directory contains two different benchmark styles:

1. **Algorithm-only benchmark**: measures just the dead-fixture analysis logic in
   Python, after imports/setup/collection are already done.
2. **CLI benchmark**: measures the real command-line path,
   `python -m pytest -p pytest_deadfixtures --dead-fixtures`.

Use the algorithm benchmark when optimizing `pytest_deadfixtures.py` internals.
Use CLI benchmarks or `hyperfine` when comparing end-user command latency.

## Scope and Isolation

These scripts are source-checkout tooling. They are included in source
distributions for contributors and CI jobs, but they are not installed as
console commands or treated as part of the runtime plugin API.

The CLI runner intentionally creates clean synthetic baselines:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` disables unrelated third-party pytest
  plugins.
- `PYTEST_ADDOPTS` and `PYTEST_PLUGINS` are removed from the benchmark
  subprocess environment.
- `-p no:cacheprovider` disables pytest cache effects.

This makes branch-to-branch plugin comparisons more stable, but it is not a
plugin-heavy integration benchmark. If you need integration evidence, run an
additional command against the target project with its normal pytest
configuration.

The workloads model suites with shared `conftest.py` fixtures, per-module
fixtures, fixture dependency chains, dead fixtures, autouse fixtures, ignored
fixtures, and dynamic fixture lookups through parametrization. Project
generation is excluded from the timed section; pytest collection plus
dead-fixture analysis and reporting are timed. The runner disables pytest cache
with `-p no:cacheprovider` so repeated rounds measure warm Python/process state,
not `.pytest_cache` effects.

## Algorithm-Only Baseline

`run_deadfixtures_algorithm_benchmark.py` builds an in-memory pytest-like session
from the workload profile, then times only:

- `get_used_fixturesdefs(session)`
- `get_fixtures(session)`
- `get_parametrized_fixtures(session, available_fixtures)`
- filtering of the unused fixture list

It excludes Python process startup, imports, pytest CLI bootstrap, filesystem
walking, test module import, pytest collection, and terminal output rendering.

```bash
python benchmarks/run_deadfixtures_algorithm_benchmark.py \
  --case medium \
  --rounds 50 \
  --warmups 5 \
  --output .benchmarks/results/current-medium-algorithm.json
```

For monorepo-scale algorithm timing:

```bash
python benchmarks/run_deadfixtures_algorithm_benchmark.py \
  --case monorepo_8k \
  --rounds 3 \
  --warmups 1 \
  --output .benchmarks/results/monorepo-8k-algorithm.json
```

The JSON contains total timing plus per-phase timing in `phase_stats`.

## Python CLI Baseline

```bash
python -m pip install -e . pytest
python benchmarks/run_deadfixtures_benchmark.py \
  --case medium \
  --rounds 5 \
  --output .benchmarks/results/current-medium.json
```

Run the default workloads (`small`, `medium`, and `large`):

```bash
python benchmarks/run_deadfixtures_benchmark.py \
  --rounds 5 \
  --output .benchmarks/results/current-all.json
```

Use `--case small`, `--case medium`, and `--case large` to select one or more
workloads. Increase `--rounds` for stable baseline numbers; keep `--warmups 1` or
higher when comparing optimization branches. The `monorepo_8k` workload is
available for large manual runs, but it is intentionally not part of the default
set.

## Hyperfine CLI Comparisons

Use `hyperfine` when you specifically want to compare CLI commands. First
generate the synthetic project once so generation is not part of the timing:

```bash
python benchmarks/generate_deadfixtures_project.py \
  --case monorepo_8k \
  --output-dir .benchmarks/generated \
  --clean
```

Then benchmark the CLI. The plugin exits with code `11` when dead fixtures are
found, so the shell wrapper converts that expected exit code into success:

```bash
REPO_ROOT=$PWD
PROJECT=.benchmarks/generated/deadfixtures-monorepo_8k

hyperfine \
  --warmup 1 \
  --runs 10 \
  --export-json .benchmarks/results/hyperfine-monorepo-8k.json \
  "cd $PROJECT && \
   env PYTHONPATH=$REPO_ROOT PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
   $REPO_ROOT/.venv/bin/python -m pytest \
     -p pytest_deadfixtures -p no:cacheprovider --dead-fixtures -q \
     >/dev/null; test \$? -eq 11"
```

To compare pytest versions, create/reuse matrix environments, then pass each
environment's Python as a separate hyperfine command:

```bash
python benchmarks/run_pytest_matrix.py \
  --version 7.4.4 \
  --version 9.0.3 \
  --case small \
  --rounds 1 \
  --warmups 0

hyperfine \
  --warmup 1 \
  --runs 10 \
  "cd $PROJECT && env PYTHONPATH=$REPO_ROOT PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
   $REPO_ROOT/.benchmarks/venvs/pytest-7.4.4/bin/python -m pytest \
   -p pytest_deadfixtures -p no:cacheprovider --dead-fixtures -q \
   >/dev/null; test \$? -eq 11" \
  "cd $PROJECT && env PYTHONPATH=$REPO_ROOT PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
   $REPO_ROOT/.benchmarks/venvs/pytest-9.0.3/bin/python -m pytest \
   -p pytest_deadfixtures -p no:cacheprovider --dead-fixtures -q \
   >/dev/null; test \$? -eq 11"
```

This measures Python startup, imports, pytest bootstrap, collection,
dead-fixture analysis, and output generation to `/dev/null`. It is intentionally
not comparable to the algorithm-only benchmark.

## Measuring Monorepo-Scale Workloads

Use `monorepo_8k` when you need a workload that resembles a very large
monorepo: 8,000 generated test modules plus `tests/conftest.py`, about 16,000
collected test items, shared fixtures, per-file fixtures, dependency chains,
ignored fixtures, autouse fixtures, and 16,064 expected dead fixtures.

Run this profile manually on a quiet machine; do not add it to every PR check.
Close CPU-heavy applications, use the same Python interpreter between branches,
and compare JSON outputs produced on the same host.

```bash
/usr/bin/time -f 'elapsed_seconds %e' \
  python benchmarks/run_deadfixtures_benchmark.py \
    --case monorepo_8k \
    --rounds 3 \
    --warmups 1 \
    --output .benchmarks/results/monorepo-8k-current.json
```

For more stable numbers, use `--rounds 10` or higher. For inspection, keep the
generated suite in an ignored directory:

```bash
python benchmarks/run_deadfixtures_benchmark.py \
  --case monorepo_8k \
  --rounds 1 \
  --warmups 0 \
  --tmpdir .benchmarks/generated \
  --keep-projects \
  --output .benchmarks/results/monorepo-8k-inspection.json

find .benchmarks/generated/deadfixtures-monorepo_8k/tests \
  -name '*.py' | wc -l
```

To compare pytest versions, run only a small version subset first:

```bash
python benchmarks/run_pytest_matrix.py \
  --version 7.4.4 \
  --version 9.0.3 \
  --case monorepo_8k \
  --rounds 3 \
  --warmups 1 \
  --skip-install
```

Run the full `pytest-versions.txt` matrix overnight or in a dedicated
performance job. Always compare `stats.median_seconds` and `stats.min_seconds`
first; use `mean_seconds` as a secondary signal because large filesystem-heavy
runs are more sensitive to background noise.

## Pytest Version Matrix

`pytest-versions.txt` contains non-yanked pytest releases from `7.4.4` through
the latest PyPI version verified on 2026-05-30 (`9.0.3`). To benchmark every
listed version in isolated virtual environments:

```bash
python benchmarks/run_pytest_matrix.py --case medium --rounds 5
```

Results are written to `.benchmarks/results/pytest-<version>.json`, and reusable
virtual environments are stored under `.benchmarks/venvs/`. Use `--recreate` to
force fresh environments or `--version 8.4.2 --version 9.0.3` for a subset.
Use `--check-latest` to fail fast when PyPI reports a newer pytest than the last
version listed in `pytest-versions.txt`.

## Interpreting Results

Track the JSON output in CI artifacts or local experiment notes, not in git.
Compare `stats.mean_seconds`, `stats.median_seconds`, and `stats.min_seconds`
between branches. Each round stores the exact reported fixture names and fails if
they differ from the generated dead-fixture set, so timing regressions and
semantic regressions are both visible. Treat regressions on `medium` as the first
signal; confirm with `large` before changing the implementation.
