from pathlib import Path
from textwrap import dedent

import pytest

from pytest_deadfixtures import (
    DUPLICATE_FIXTURES_HEADLINE,
    EXIT_CODE_ERROR,
    EXIT_CODE_SUCCESS,
    UNUSED_FIXTURES_FOUND_HEADLINE,
    get_best_relpath,
)


def pytester_path(pytester):
    path = getattr(pytester, "path", None)
    if path is not None:
        return Path(path)
    return Path(str(pytester.tmpdir))


def test_error_exit_code_on_dead_fixtures_found(pytester):
    pytester.makepyfile(
        """
            import pytest


            @pytest.fixture()
            def some_fixture():
                return 1
        """
    )

    result = pytester.runpytest("--dead-fixtures")

    assert result.ret == EXIT_CODE_ERROR


def test_success_exit_code_on_dead_fixtures_found(pytester):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def some_fixture():
            return 1


        def test_simple(some_fixture):
            assert 1 == some_fixture
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    assert result.ret == EXIT_CODE_SUCCESS


def test_success_exit_code_on_parametrized_fixture_found(pytester):
    pytester.makepyfile(
        """
        import pytest

        not_a_fixture_one = 1

        not_a_fixture_two = 2

        @pytest.fixture()
        def some_fixture():
            return 1

        @pytest.fixture()
        def some_other_fixture():
            return 2

        @pytest.mark.parametrize('indirect_fixture,indirect_not_a_fixture',
            [('some_fixture', not_a_fixture_one), ('some_other_fixture', not_a_fixture_two)],
        )
        def test_simple(indirect_fixture, indirect_not_a_fixture):
            assert request.getfixturevalue(indirect_fixture) == indirect_not_a_fixture
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    assert result.ret == EXIT_CODE_SUCCESS


def make_assertmode_project(pytester):
    pytester.makeconftest(
        """
        from pathlib import Path
        import sys


        def pytest_collection_finish(session):
            from _pytest.assertion.rewrite import assertstate_key

            assertstate = session.config.stash.get(assertstate_key, None)
            hook = getattr(assertstate, "hook", None)
            hook_active = hook is not None and hook in sys.meta_path
            Path("assertmode.txt").write_text(
                f"{session.config.option.assertmode}:{hook_active}"
            )
    """
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def unused_fixture():
            return 1


        def test_simple():
            assert True
    """
    )


def read_assertmode(pytester):
    return (pytester_path(pytester) / "assertmode.txt").read_text()


@pytest.mark.parametrize(
    ("args", "expected"),
    (
        ((), "plain:False"),
        (("--assert=plain",), "plain:False"),
        (("--assert=rewrite",), "rewrite:True"),
        (("--assert", "rewrite"), "rewrite:True"),
    ),
)
def test_deadfixtures_assertion_mode_command_line_contract(
    pytester, monkeypatch, args, expected
):
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    make_assertmode_project(pytester)

    result = pytester.runpytest("--dead-fixtures", *args)

    assert result.ret == EXIT_CODE_ERROR
    assert read_assertmode(pytester) == expected


def test_deadfixtures_assertion_mode_respects_env_addopts(pytester, monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "--assert=rewrite")
    make_assertmode_project(pytester)

    result = pytester.runpytest("--dead-fixtures")

    assert result.ret == EXIT_CODE_ERROR
    assert read_assertmode(pytester) == "rewrite:True"


def test_deadfixtures_assertion_mode_respects_ini_addopts(pytester, monkeypatch):
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    pytester.makeini(
        """
        [pytest]
        addopts = --assert=rewrite
    """
    )
    make_assertmode_project(pytester)

    result = pytester.runpytest("--dead-fixtures")

    assert result.ret == EXIT_CODE_ERROR
    assert read_assertmode(pytester) == "rewrite:True"


def test_dont_list_autouse_fixture(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture(autouse=True)
        def autouse_fixture():
            return 1


        def test_simple():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format(
        "autouse_fixture", "test_dont_list_autouse_fixture"
    )

    assert message not in result.stdout.str()


def test_dont_list_same_file_fixture(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def same_file_fixture():
            return 1


        def test_simple(same_file_fixture):
            assert 1 == same_file_fixture
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format(
        "same_file_fixture", "test_dont_list_same_file_fixture"
    )

    assert message not in result.stdout.str()


def test_list_same_file_unused_fixture(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def same_file_fixture():
            return 1


        def test_simple():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format(
        "same_file_fixture", "test_list_same_file_unused_fixture"
    )
    output = result.stdout.str()

    assert message in output
    assert UNUSED_FIXTURES_FOUND_HEADLINE.format(count=1) in output


def test_list_same_file_multiple_unused_fixture(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def same_file_fixture():
            return 1

        @pytest.fixture()
        def plus_same_file_fixture():
            return 2


        def test_simple():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    first = message_template.format(
        "same_file_fixture", "test_list_same_file_multiple_unused_fixture"
    )
    second = message_template.format(
        "plus_same_file_fixture", "test_list_same_file_multiple_unused_fixture"
    )
    output = result.stdout.str()

    assert first in output
    assert second in output
    assert output.index(first) < output.index(second)
    assert UNUSED_FIXTURES_FOUND_HEADLINE.format(count=2) in output


def test_list_nested_unused_fixture_path_contract(pytester):
    nested = Path(str(pytester.mkdir("nested")))
    (nested / "test_nested_contract.py").write_text(
        dedent(
            """
        import pytest


        @pytest.fixture()
        def nested_fixture():
            return 1


        def test_simple():
            assert True
    """
        ),
        encoding="utf-8",
    )

    result = pytester.runpytest("--dead-fixtures", "nested")
    output = result.stdout.str().replace("\\", "/")

    assert result.ret == EXIT_CODE_ERROR
    assert (
        "Fixture name: nested_fixture, "
        "location: nested/test_nested_contract.py:"
    ) in output


def make_function_with_filename(filename):
    namespace = {}
    source = "def fixture_func():\n    return 1\n"
    exec(compile(source, str(filename), "exec"), namespace)
    return namespace["fixture_func"]


def test_get_best_relpath_out_of_root_path_contract(tmp_path):
    curdir = tmp_path / "repo"
    curdir.mkdir()
    outside = tmp_path / "outside" / "test_external.py"
    func = make_function_with_filename(outside)

    location = get_best_relpath(func, curdir)

    assert location == f"{outside}:2"


def test_get_best_relpath_preserves_windows_style_path_contract(tmp_path):
    filename = r"C:\repo\tests\test_external.py"
    func = make_function_with_filename(filename)

    location = get_best_relpath(func, Path(tmp_path))

    assert location == r"C:\repo\tests\test_external.py:2"


def test_dont_list_conftest_fixture(pytester, message_template):
    pytester.makepyfile(
        conftest="""
        import pytest


        @pytest.fixture()
        def conftest_fixture():
            return 1
    """
    )

    pytester.makepyfile(
        """
        import pytest


        def test_conftest_fixture(conftest_fixture):
            assert 1 == conftest_fixture
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format("conftest_fixture", "conftest")

    assert message not in result.stdout.str()


def test_list_conftest_unused_fixture(pytester, message_template):
    pytester.makepyfile(
        conftest="""
        import pytest


        @pytest.fixture()
        def conftest_fixture():
            return 1
    """
    )

    pytester.makepyfile(
        """
        import pytest


        def test_conftest_fixture():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format("conftest_fixture", "conftest")

    assert message in result.stdout.str()


def test_list_conftest_multiple_unused_fixture(pytester, message_template):
    pytester.makepyfile(
        conftest="""
        import pytest


        @pytest.fixture()
        def conftest_fixture():
            return 1

        @pytest.fixture()
        def plus_conftest_fixture():
            return 2
    """
    )

    pytester.makepyfile(
        """
        import pytest


        def test_conftest_fixture():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    first = message_template.format("conftest_fixture", "conftest")
    second = message_template.format("plus_conftest_fixture", "conftest")
    output = result.stdout.str()

    assert first in output
    assert second in output
    assert output.index(first) < output.index(second)


def test_dont_list_decorator_usefixtures(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def decorator_usefixtures():
            return 1


        @pytest.mark.usefixtures('decorator_usefixtures')
        def test_decorator_usefixtures():
            assert 1 == decorator_usefixtures
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format(
        "decorator_usefixtures", "test_dont_list_decorator_usefixtures"
    )

    assert message not in result.stdout.str()


def test_write_docs_when_verbose(pytester):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def some_fixture():
            '''Blabla fixture docs'''
            return 1


        def test_simple():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures", "-v")

    assert "Blabla fixture docs" in result.stdout.str()


def test_repeated_fixtures_not_found(pytester):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def some_fixture():
            return 1


        def test_simple(some_fixture):
            assert 1 == some_fixture
    """
    )

    result = pytester.runpytest("--dup-fixtures")

    assert DUPLICATE_FIXTURES_HEADLINE not in result.stdout.str()


def test_repeated_fixtures_found(pytester):
    pytester.makepyfile(
        """
        import pytest


        class SomeClass:
            a = 1

            def spam(self):
                return 'and eggs'


        @pytest.fixture()
        def someclass_fixture():
            return SomeClass()


        @pytest.fixture()
        def someclass_samefixture():
            return SomeClass()


        def test_simple(someclass_fixture):
            assert 1 == 1

        def test_simple_again(someclass_samefixture):
            assert 2 == 2
    """
    )

    result = pytester.runpytest("--dup-fixtures")

    assert DUPLICATE_FIXTURES_HEADLINE in result.stdout.str()
    assert "someclass_samefixture" in result.stdout.str()


@pytest.mark.parametrize("directory", ("site-packages", "dist-packages", "<string>"))
def test_should_not_list_fixtures_from_unrelated_directories(
    pytester, message_template, directory
):
    pytester.tmpdir = pytester.mkdir(directory)

    pytester.makepyfile(
        conftest="""
        import pytest


        @pytest.fixture()
        def conftest_fixture():
            return 1
    """
    )

    pytester.makepyfile(
        """
        import pytest


        def test_conftest_fixture():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    message = message_template.format("conftest_fixture", f"{directory}/conftest")

    assert message not in result.stdout.str()


def test_dont_list_fixture_used_after_test_which_does_not_use_fixtures(
    pytester, message_template
):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def same_file_fixture():
            return 1

        def test_no_fixture_used():
            assert True

        def test_simple(same_file_fixture):
            assert 1 == same_file_fixture
    """
    )

    result = pytester.runpytest("--dead-fixtures")
    message = message_template.format(
        "same_file_fixture",
        "test_dont_list_fixture_used_after_test_which_does_not_use_fixtures",
    )

    assert message not in result.stdout.str()


def test_doctest_should_not_result_in_false_positive(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def same_file_fixture():
            return 1

        def something():
            ''' a doctest in a docstring
            >>> something()
            42
            '''
            return 42

        def test_simple(same_file_fixture):
            assert 1 == same_file_fixture
    """
    )

    result = pytester.runpytest("--dead-fixtures", "--doctest-modules")
    message = message_template.format(
        "same_file_fixture", "test_doctest_should_not_result_in_false_positive"
    )

    assert message not in result.stdout.str()


def test_dont_list_fixture_used_by_another_fixture(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def some_fixture():
            return 1

        @pytest.fixture()
        def a_derived_fixture(some_fixture):
            return some_fixture + 1

        def test_something(a_derived_fixture):
            assert a_derived_fixture == 2
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    for fixture_name in ["some_fixture", "a_derived_fixture"]:
        message = message_template.format(
            fixture_name,
            "test_dont_list_fixture_used_by_another_fixture",
        )
        assert message not in result.stdout.str()


def test_list_derived_fixtures_if_not_used_by_tests(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture()
        def some_fixture():
            return 1

        @pytest.fixture()
        def a_derived_fixture(some_fixture):
            return some_fixture + 1

        def test_something():
            assert True
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    # although some_fixture is used by a_derived_fixture, since neither are used by a test case,
    # they should be reported.
    for fixture_name in ["some_fixture", "a_derived_fixture"]:
        message = message_template.format(
            fixture_name,
            "test_list_derived_fixtures_if_not_used_by_tests",
        )
        assert message in result.stdout.str()


def test_imported_fixtures(pytester):
    pytester.makepyfile(
        conftest="""
        import pytest

        pytest_plugins = [
            'more_fixtures',
        ]

        @pytest.fixture
        def some_common_fixture():
            return 'ok'
    """
    )
    pytester.makepyfile(
        more_fixtures="""
        import pytest

        @pytest.fixture
        def some_fixture():
            return 1

        @pytest.fixture
        def a_derived_fixture(some_fixture):
            return some_fixture + 1

        @pytest.fixture
        def some_unused_fixture():
            return 'nope'
    """
    )
    pytester.makepyfile(
        """
        import pytest

        def test_some_common_thing(some_common_fixture):
            assert True

        def test_some_derived_thing(a_derived_fixture):
            assert True
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    for fixture_name in ["some_fixture", "a_derived_fixture", "some_common_fixture"]:
        assert fixture_name not in result.stdout.str()

    assert "some_unused_fixture" in result.stdout.str()


def test_parameterized_fixture(pytester):
    # If all fixtures are arranged in conftest file it works as expected
    pytester.makepyfile(
        conftest="""
        import pytest

        @pytest.fixture
        def some_common_fixture():
            return 1

        @pytest.fixture(params=['some_common_fixture'])
        def another_fixture(request)
            fixture_value = request.getfixturevalue(request.param)
            return fixture_value + 1
    """
    )
    pytester.makepyfile(
        """
        def test_a_thing(another_fixture):
            assert another_fixture == 2
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    # Currently these cases are recognized as a false positive, whereas they shouldn't be.
    # Due to the dynamic lookup of the fixture, this is going to be hard to recognize.
    assert "some_common_fixture" not in result.stdout.str()


def test_ignored_unused_fixture(pytester):
    pytester.makepyfile(
        """
        import pytest
        from pytest_deadfixtures import deadfixtures_ignore


        @pytest.fixture()
        @deadfixtures_ignore
        def some_fixture():
            return 1


        def test_simple():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures")

    assert "some_fixture" not in result.stdout.str()


def test_list_ignored_unused_fixture_when_flagged(pytester, message_template):
    pytester.makepyfile(
        """
        import pytest
        from pytest_deadfixtures import deadfixtures_ignore


        @pytest.fixture()
        @deadfixtures_ignore
        def some_fixture():
            return 1


        def test_simple():
            assert 1 == 1
    """
    )

    result = pytester.runpytest("--dead-fixtures", "--show-ignored-fixtures")
    message = message_template.format(
        "some_fixture", "test_list_ignored_unused_fixture_when_flagged"
    )
    assert message in result.stdout.str()
