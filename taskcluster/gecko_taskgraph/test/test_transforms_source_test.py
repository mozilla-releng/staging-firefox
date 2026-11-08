# Any copyright is dedicated to the public domain.
# http://creativecommons.org/publicdomain/zero/1.0/

import pytest
from mozunit import main
from taskgraph.util.path import match

from gecko_taskgraph.test.conftest import FakeParameters
from gecko_taskgraph.transforms.source_test import (
    MAX_ARG_STRLEN,
    ignore_try_task_config,
    root_json_files,
    set_changed_files_env,
)

LONGEST_PATH = "d/" + "x" * (MAX_ARG_STRLEN - len("CHANGED_FILES=") - 1 - 6) + ".cpp"


@pytest.mark.parametrize(
    "job,expected",
    [
        pytest.param({"name": "no-when"}, None, id="no-when"),
        pytest.param({"name": "null-when", "when": None}, None, id="null-when"),
        pytest.param(
            {"name": "no-json", "when": {"files-changed": ["**/*.js"]}},
            ["**/*.js"],
            id="no-json",
        ),
        pytest.param(
            {
                "name": "json",
                "when": {"files-changed": ["**/*.js", "**/*.json", "**/*.jsx"]},
            },
            ["**/*.js", "*/**/*.json", *root_json_files(), "**/*.jsx"],
            id="json",
        ),
    ],
)
def test_ignore_try_task_config(run_transform, job, expected):
    jobs = list(run_transform(ignore_try_task_config, job))
    assert len(jobs) == 1
    assert (jobs[0].get("when") or {}).get("files-changed") == expected


@pytest.mark.parametrize(
    "path,expected",
    [
        ("try_task_config.json", False),
        ("package.json", True),
        ("taskcluster/try_task_config.json", True),
        ("browser/foo/bar.json", True),
        ("browser/foo/bar.jsonc", False),
    ],
)
def test_json_patterns_match(run_transform, path, expected):
    job = {"name": "json", "when": {"files-changed": ["**/*.json"]}}
    patterns = next(run_transform(ignore_try_task_config, job))["when"]["files-changed"]
    assert any(match(path, p) for p in patterns) == expected


@pytest.mark.parametrize(
    "files_changed,expected",
    [
        pytest.param(
            ["dom/b.cpp", "dom/a.cpp", "dom/a.h", "README.md"],
            "dom/a.cpp dom/b.cpp",
            id="matching",
        ),
        pytest.param(["README.md"], "", id="none-matching"),
        pytest.param(
            [LONGEST_PATH],
            LONGEST_PATH,
            id="at-limit",
        ),
        pytest.param(
            ["d" + LONGEST_PATH],
            "",
            id="over-limit",
        ),
    ],
)
def test_set_changed_files_env(run_transform, files_changed, expected):
    job = {
        "name": "clang",
        "changed-files-env": "CHANGED_FILES",
        "when": {"files-changed": ["**/*.cpp"]},
        "worker": {},
    }
    params = FakeParameters({"files_changed": files_changed, "repository_type": "git"})
    (result,) = run_transform(set_changed_files_env, job, params=params)
    assert result["worker"]["env"] == {"CHANGED_FILES": expected}
    assert "changed-files-env" not in result


@pytest.mark.parametrize(
    "job,repository_type",
    [
        pytest.param({"name": "other", "worker": {}}, "git", id="not-requested"),
        pytest.param(
            {
                "name": "clang",
                "changed-files-env": "CHANGED_FILES",
                "when": {"files-changed": ["**/*.cpp"]},
                "worker": {},
            },
            "hg",
            id="hg",
        ),
    ],
)
def test_set_changed_files_env_unset(run_transform, job, repository_type):
    params = FakeParameters({
        "files_changed": ["a.cpp"],
        "repository_type": repository_type,
    })
    (result,) = run_transform(set_changed_files_env, job, params=params)
    assert "env" not in result["worker"]
    assert "changed-files-env" not in result


if __name__ == "__main__":
    main()
