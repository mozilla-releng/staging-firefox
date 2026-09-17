# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from types import SimpleNamespace

import pytest
from mozunit import main
from taskgraph.util.vcs import Repository, get_repository

from gecko_taskgraph.util import git as git_util
from gecko_taskgraph.util.git import derive_base_rev

BRANCH = "code-review/D1"


def git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    """A repository whose `main` moved on after `code-review/D1` branched off.

    `old_tip` is an earlier tip of that branch, `previous` a rebuilt stack that
    a force push orphaned (still served by sha, as GitHub does), `merge_tip` a
    stack that merged `main` back in and `on_main` a branch pointing at the tip
    of `main`."""
    path = tmp_path / "origin"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "uploadpack.allowAnySHA1InWant", "true")

    (path / "a.cpp").write_bytes(b"a\n")
    (path / "b.cpp").write_bytes(b"b\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base1")
    (path / "a.cpp").write_bytes(b"a2\n")
    git(path, "commit", "-qam", "base2")
    base = git(path, "rev-parse", "HEAD")
    (path / "m.txt").write_bytes(b"m\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "main3")
    main_rev = git(path, "rev-parse", "HEAD")
    git(path, "branch", "on_main", main_rev)

    git(path, "checkout", "-qb", "previous", base)
    (path / "try_task_config.json").write_bytes(b"{}\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "previous stack")
    previous = git(path, "rev-parse", "HEAD")

    git(path, "checkout", "-qb", BRANCH, base)
    (path / "c.py").write_bytes(b"c\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "stack1")
    old_tip = git(path, "rev-parse", "HEAD")
    (path / "b.cpp").write_bytes(b"b\nd\n")
    git(path, "commit", "-qam", "stack2")
    head = git(path, "rev-parse", "HEAD")

    git(path, "checkout", "-qb", "merged", head)
    git(path, "merge", "-q", "-m", "merge main", "main")
    merge_tip = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "-q", "main")
    git(path, "branch", "-qD", "previous")

    return SimpleNamespace(
        path=path,
        url=path.as_uri(),
        base=base,
        head=head,
        old_tip=old_tip,
        previous=previous,
        main_rev=main_rev,
        merge_tip=merge_tip,
    )


def shallow_checkout(tmp_path, origin, branch, head, base_rev=None):
    """The checkout `run-task --gecko-shallow-clone` produces when
    `GECKO_BASE_REV` is `base_rev` (unset or the null revision when None)."""
    checkout = tmp_path / "checkout"
    git(
        tmp_path, "clone", "-q", "--depth=1", "--no-checkout", origin.url, str(checkout)
    )
    if base_rev:
        git(checkout, "fetch", "-q", "--depth=1", origin.url, base_rev)
    git(checkout, "fetch", "-q", "--depth=1", origin.url, head, f"refs/heads/{branch}")
    git(checkout, "checkout", "-qf", "-B", branch, head)
    return get_repository(str(checkout))


def test_new_branch(tmp_path, origin):
    repo = shallow_checkout(tmp_path, origin, BRANCH, origin.head)
    head_ref = f"refs/heads/{BRANCH}"

    base = derive_base_rev(
        repo, origin.url, origin.head, head_ref, Repository.NULL_REVISION
    )

    assert base == origin.base
    assert repo.get_changed_files(rev=origin.head, base=base) == ["b.cpp", "c.py"]


def test_force_push(tmp_path, origin):
    repo = shallow_checkout(tmp_path, origin, BRANCH, origin.head, origin.previous)
    head_ref = f"refs/heads/{BRANCH}"

    base = derive_base_rev(repo, origin.url, origin.head, head_ref, origin.previous)

    assert base == origin.base
    assert repo.get_changed_files(rev=origin.head, base=base) == ["b.cpp", "c.py"]


def test_fast_forward_push_keeps_base_rev(tmp_path, origin):
    repo = shallow_checkout(tmp_path, origin, BRANCH, origin.head, origin.old_tip)
    head_ref = f"refs/heads/{BRANCH}"

    assert (
        derive_base_rev(repo, origin.url, origin.head, head_ref, origin.old_tip) is None
    )


def test_fast_forward_push_longer_than_probe(tmp_path, origin, monkeypatch):
    monkeypatch.setattr(git_util, "PROBE_DEPTH", 1)
    repo = shallow_checkout(tmp_path, origin, BRANCH, origin.head, origin.old_tip)
    head_ref = f"refs/heads/{BRANCH}"

    assert (
        derive_base_rev(repo, origin.url, origin.head, head_ref, origin.old_tip) is None
    )


def test_default_branch(tmp_path, origin):
    repo = shallow_checkout(tmp_path, origin, "main", origin.main_rev, origin.base)

    assert (
        derive_base_rev(
            repo, origin.url, origin.main_rev, "refs/heads/main", origin.base
        )
        is None
    )


def test_head_is_default_branch_tip(tmp_path, origin):
    repo = shallow_checkout(tmp_path, origin, "on_main", origin.main_rev)

    base = derive_base_rev(
        repo,
        origin.url,
        origin.main_rev,
        "refs/heads/on_main",
        Repository.NULL_REVISION,
    )

    assert base == origin.main_rev
    assert repo.get_changed_files(rev=origin.main_rev, base=base) == []


def test_merge_in_stack_is_ambiguous(tmp_path, origin, caplog):
    repo = shallow_checkout(tmp_path, origin, "merged", origin.merge_tip)

    assert (
        derive_base_rev(
            repo,
            origin.url,
            origin.merge_tip,
            "refs/heads/merged",
            Repository.NULL_REVISION,
        )
        is None
    )
    assert "fork point candidates" in caplog.text


def test_unreachable_repository(tmp_path, origin, caplog):
    repo = shallow_checkout(tmp_path, origin, BRANCH, origin.head)
    missing = (tmp_path / "missing").as_uri()

    assert (
        derive_base_rev(
            repo, missing, origin.head, f"refs/heads/{BRANCH}", Repository.NULL_REVISION
        )
        is None
    )
    assert "Cannot derive base_rev" in caplog.text


def test_fetch_timeout(tmp_path, origin, monkeypatch, caplog):
    repo = shallow_checkout(tmp_path, origin, BRANCH, origin.head)
    run = repo.run

    def slow_run(*args, **kwargs):
        if args[0] == "fetch":
            raise subprocess.TimeoutExpired(args, kwargs.get("timeout"))
        return run(*args, **kwargs)

    monkeypatch.setattr(repo, "run", slow_run)

    assert (
        derive_base_rev(
            repo,
            origin.url,
            origin.head,
            f"refs/heads/{BRANCH}",
            Repository.NULL_REVISION,
        )
        is None
    )
    assert "Cannot derive base_rev" in caplog.text


if __name__ == "__main__":
    main()
