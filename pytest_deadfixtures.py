"""
Some functions are basically copy n' paste version of code already in pytest.
Precisely the get_fixtures, get_used_fixturesdefs and write_docstring functions.
"""

import os
import shlex
import sys
from collections import namedtuple
from itertools import combinations
from pathlib import Path
from textwrap import dedent

import _pytest.config
from _pytest.compat import getlocation

DUPLICATE_FIXTURES_HEADLINE = "\n\nYou may have some duplicate fixtures:"
UNUSED_FIXTURES_FOUND_HEADLINE = (
    "Hey there, I believe the following {count} fixture(s) are not being used:"
)
UNUSED_FIXTURES_NOT_FOUND_HEADLINE = "Cool, every declared fixture is being used."

IGNORED_FIXTURES_HEADLINE = "Ignored fixture(s) {count}:"
IGNORED_FIXTURES_ATTR = "_deadfixtures_ignore"

EXIT_CODE_ERROR = 11
EXIT_CODE_SUCCESS = 0

AvailableFixture = namedtuple("AvailableFixture", "relpath, argname, fixturedef")

CachedFixture = namedtuple("CachedFixture", "fixturedef, relpath, result")

DeadFixtureAnalysis = namedtuple(
    "DeadFixtureAnalysis",
    (
        "used_fixtures, available_fixtures, parametrized_fixtures, "
        "ignored_fixtures, unused_fixtures"
    ),
)


def pytest_addoption(parser):
    group = parser.getgroup("deadfixtures")
    group.addoption(
        "--dead-fixtures",
        action="store_true",
        dest="deadfixtures",
        default=False,
        help="Show fixtures not being used",
    )
    group.addoption(
        "--dup-fixtures",
        action="store_true",
        dest="showrepeated",
        default=False,
        help="Show duplicated fixtures",
    )
    group.addoption(
        "--show-ignored-fixtures",
        action="store_true",
        default=False,
        help="Show fixtures ignored with `deadfixtures_ignore` mark",
    )


def pytest_cmdline_main(config):
    if config.option.deadfixtures:
        disable_assertion_rewriting(config)
        config.option.show_fixture_doc = config.option.verbose
        config.option.verbose = -1
        if _show_dead_fixtures(config):
            return EXIT_CODE_ERROR
        return EXIT_CODE_SUCCESS


def disable_assertion_rewriting(config):
    """Disable assertion rewriting for dead-fixture collection.

    The plugin only needs pytest to import modules and build fixture metadata;
    tests are not executed in ``--dead-fixtures`` mode. Pytest's assertion
    rewriting can dominate collection time for large suites, especially when
    bytecode writes are disabled in benchmarks. If the user explicitly chose an
    assertion mode, respect that choice.
    """
    if has_explicit_assert_mode(config):
        return

    config.option.assertmode = "plain"
    try:
        from _pytest.assertion.rewrite import assertstate_key
    except ImportError:
        return

    assertstate = config.stash.get(assertstate_key, None)
    hook = getattr(assertstate, "hook", None)
    if hook is None:
        return

    if hook in sys.meta_path:
        sys.meta_path.remove(hook)
    assertstate.hook = None
    assertstate.mode = "plain"


def has_explicit_assert_mode(config):
    if has_assert_mode_arg(config.invocation_params.args):
        return True

    env_addopts = os.environ.get("PYTEST_ADDOPTS")
    if env_addopts and has_assert_mode_arg(shlex.split(env_addopts)):
        return True

    try:
        addopts = config.getini("addopts")
    except ValueError:
        addopts = ()
    return has_assert_mode_arg(addopts)


def has_assert_mode_arg(args):
    args = list(args)
    for index, arg in enumerate(args):
        if arg.startswith("--assert="):
            return True
        if arg == "--assert" and index + 1 < len(args):
            return True
    return False


def _show_dead_fixtures(config):
    from _pytest.main import wrap_session

    return wrap_session(config, show_dead_fixtures)


def get_best_relpath(func, curdir):
    return getlocation(func, curdir)


def deadfixtures_ignore(func):
    """Decorator to mark fixtures that should be ignored by the plugin."""
    setattr(func, IGNORED_FIXTURES_ATTR, True)
    return func


def is_ignored_fixture(fixturedef):
    """Check if a fixture is marked as ignored."""
    return getattr(fixturedef.func, IGNORED_FIXTURES_ATTR, False)


def get_fixtures(session):
    available = []
    seen = set()
    fm = session._fixturemanager
    curdir = Path.cwd()

    for fixturedefs in fm._arg2fixturedefs.values():
        assert fixturedefs is not None
        if not fixturedefs:
            continue
        for fixturedef in fixturedefs:
            module = fixturedef.func.__module__
            if module.startswith("_pytest.") or module.startswith("pytest_"):
                continue

            loc = getlocation(fixturedef.func, curdir)
            if (fixturedef.argname, loc) in seen:
                continue

            seen.add((fixturedef.argname, loc))

            if (
                "site-packages" not in loc
                and "dist-packages" not in loc
                and "<string>" not in loc
            ):
                available.append(AvailableFixture(loc, fixturedef.argname, fixturedef))

    available.sort(key=lambda a: a.relpath)
    return available


def get_used_fixturesdefs(session):
    fixturesdefs = []
    for test_function in session.items:
        try:
            info = test_function._fixtureinfo
        except AttributeError:
            # doctests items have no _fixtureinfo attribute
            continue
        if not info.name2fixturedefs:
            # this test item does not use any fixtures
            continue

        for _, fixturedefs in sorted(info.name2fixturedefs.items()):
            if fixturedefs is None:
                continue
            fixturesdefs.append(fixturedefs[-1])
    return fixturesdefs


def get_parametrized_fixtures(session, available_fixtures):
    params_values = set()
    unhashable_params_values = []
    for test_function in session.items:
        try:
            for v in test_function.callspec.params.values():
                try:
                    params_values.add(v)
                except TypeError:
                    unhashable_params_values.append(v)
        except AttributeError:
            continue
    return [
        available.fixturedef
        for available in filter(
            lambda x: x.fixturedef.argname in params_values
            or x.fixturedef.argname in unhashable_params_values,
            available_fixtures,
        )
    ]


def analyze_dead_fixtures(session):
    used_fixtures = get_used_fixturesdefs(session)
    available_fixtures = get_fixtures(session)
    param_fixtures = get_parametrized_fixtures(session, available_fixtures)
    return build_dead_fixture_analysis(
        used_fixtures, available_fixtures, param_fixtures
    )


def build_dead_fixture_analysis(
    used_fixtures, available_fixtures, parametrized_fixtures
):
    used_fixturedefs = set(used_fixtures)
    parametrized_fixturedefs = set(parametrized_fixtures)
    ignored_fixturedefs = {
        fixture.fixturedef
        for fixture in available_fixtures
        if is_ignored_fixture(fixture.fixturedef)
    }

    ignored_fixtures = [
        fixture
        for fixture in available_fixtures
        if fixture.fixturedef in ignored_fixturedefs
    ]

    unused_fixtures = [
        fixture
        for fixture in available_fixtures
        if fixture.fixturedef not in used_fixturedefs
        and fixture.fixturedef not in parametrized_fixturedefs
        and fixture.fixturedef not in ignored_fixturedefs
    ]

    return DeadFixtureAnalysis(
        used_fixtures,
        available_fixtures,
        parametrized_fixtures,
        ignored_fixtures,
        unused_fixtures,
    )


def write_docstring(tw, doc):
    INDENT = "    "
    doc = doc.rstrip()
    if "\n" in doc:
        firstline, rest = doc.split("\n", 1)
    else:
        firstline, rest = doc, ""

    if firstline.strip():
        tw.line(INDENT + firstline.strip())

    if rest:
        for line in dedent(rest).split("\n"):
            tw.write(INDENT + line + "\n")


def write_fixtures(tw, fixtures, write_docs):
    for fixture in fixtures:
        tplt = "Fixture name: {}, location: {}"
        tw.line(tplt.format(fixture.argname, fixture.relpath))
        doc = fixture.fixturedef.func.__doc__ or ""
        if write_docs and doc:
            write_docstring(tw, doc)


cached_fixtures = []


def pytest_fixture_post_finalizer(fixturedef):
    if getattr(fixturedef, "cached_result", None):
        curdir = Path.cwd()
        loc = getlocation(fixturedef.func, curdir)

        cached_fixtures.append(
            CachedFixture(fixturedef, loc, fixturedef.cached_result[0])
        )


def same_fixture(one, two):
    def result_same_type(a, b):
        return isinstance(a.result, type(b.result))

    def same_result(a, b):
        if not a.result or not b.result:
            return False
        if hasattr(a.result, "__dict__") or hasattr(b.result, "__dict__"):
            return a.result.__dict__ == b.result.__dict__
        return a.result == b.result

    def same_loc(a, b):
        return a.relpath == b.relpath

    return (
        result_same_type(one, two) and same_result(one, two) and not same_loc(one, two)
    )


def pytest_sessionfinish(session, exitstatus):
    if exitstatus or not session.config.getvalue("showrepeated"):
        return exitstatus

    tw = _pytest.config.create_terminal_writer(session.config)

    duplicated_fixtures = []
    for a, b in combinations(cached_fixtures, 2):
        if same_fixture(a, b):
            duplicated_fixtures.append((a, b))

    if duplicated_fixtures:
        tw.line(DUPLICATE_FIXTURES_HEADLINE, red=True)
        msg = "Fixture name: {}, location: {}"
        for a, b in duplicated_fixtures:
            tw.line(msg.format(a.fixturedef.argname, a.relpath))
            tw.line(msg.format(b.fixturedef.argname, b.relpath))


def show_dead_fixtures(config, session):
    session.perform_collect()
    tw = _pytest.config.create_terminal_writer(config)
    show_fixture_doc = config.getvalue("show_fixture_doc")
    show_ignored = config.getvalue("show_ignored_fixtures")

    analysis = analyze_dead_fixtures(session)

    tw.line()
    if analysis.unused_fixtures:
        tw.line(
            UNUSED_FIXTURES_FOUND_HEADLINE.format(
                count=len(analysis.unused_fixtures)
            ),
            red=True,
        )
        write_fixtures(tw, analysis.unused_fixtures, show_fixture_doc)
    else:
        tw.line(UNUSED_FIXTURES_NOT_FOUND_HEADLINE, green=True)

    # Show ignored fixtures if requested
    if show_ignored and analysis.ignored_fixtures:
        tw.line()
        tw.line(
            IGNORED_FIXTURES_HEADLINE.format(count=len(analysis.ignored_fixtures)),
            yellow=True,
        )
        write_fixtures(tw, analysis.ignored_fixtures, show_fixture_doc)

    return analysis.unused_fixtures
