# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess

import mozunit
import pytest

from mozversioncontrol import get_repository_object
from mozversioncontrol.errors import MissingUpstreamRepo


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
    """A repository whose `main` moved on after `code-review/D1` branched off."""
    path = tmp_path / "origin"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")

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

    git(path, "checkout", "-qb", "code-review/D1", base)
    (path / "c.py").write_bytes(b"c\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "stack1")
    (path / "b.cpp").write_bytes(b"b\nd\n")
    git(path, "commit", "-qam", "stack2")
    head = git(path, "rev-parse", "HEAD")

    git(path, "checkout", "-qb", "merged")
    (path / "d.py").write_bytes(b"d\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "stack3")
    git(path, "merge", "-q", "-m", "merge main", "main")
    git(path, "checkout", "-q", "main")

    return path, base, head


@pytest.fixture
def shallow_checkout(tmp_path, origin):
    """The checkout `run-task --gecko-shallow-clone` produces for a push of
    `code-review/D1` when `GECKO_BASE_REV` names the branch point."""
    path, base, head = origin
    url = path.as_uri()
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", "--depth=1", "--no-checkout", url, str(checkout))
    git(checkout, "fetch", "-q", "--depth=1", url, base)
    git(checkout, "fetch", "-q", "--depth=1", url, head, "refs/heads/code-review/D1")
    git(checkout, "checkout", "-qf", "-B", "code-review/D1", head)
    return checkout, base, head


def test_outgoing_files_on_shallow_checkout(shallow_checkout):
    checkout, base, head = shallow_checkout
    vcs = get_repository_object(checkout)

    assert vcs.head_rev == head
    assert sorted(vcs.get_outgoing_files("ADM", upstream=base)) == ["b.cpp", "c.py"]
    assert sorted(vcs.get_outgoing_files("A", upstream=base)) == ["c.py"]
    assert sorted(vcs.get_outgoing_files("M", upstream=base)) == ["b.cpp"]


def test_base_ref_without_boundary(shallow_checkout):
    checkout, base, head = shallow_checkout
    vcs = get_repository_object(checkout)

    assert vcs.base_ref == head


def test_shallow_checkout_requires_upstream(shallow_checkout, monkeypatch):
    checkout, base, head = shallow_checkout
    vcs = get_repository_object(checkout)
    monkeypatch.delenv("GECKO_BASE_REV", raising=False)

    with pytest.raises(MissingUpstreamRepo):
        vcs.get_outgoing_files("ADM")


def test_ci_checkout_uses_its_base_rev(shallow_checkout, monkeypatch):
    checkout, base, head = shallow_checkout
    vcs = get_repository_object(checkout)
    monkeypatch.setenv("GECKO_BASE_REV", base)
    monkeypatch.setenv("GECKO_HEAD_REV", head)

    assert sorted(vcs.get_outgoing_files("ADM")) == ["b.cpp", "c.py"]
    assert vcs.base_ref == base
    assert sorted(vcs.get_outgoing_files("A", upstream=head)) == []


def test_default_branch_ci_checkout_uses_its_base_rev(tmp_path, origin, monkeypatch):
    """A push to `main`: the shallow clone's `origin/main` is HEAD itself, so
    nothing looks outgoing until the push's base is taken into account."""
    path, base, head = origin
    url = path.as_uri()
    main_rev = git(path, "rev-parse", "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", "--depth=1", "--no-checkout", url, str(checkout))
    git(checkout, "fetch", "-q", "--depth=1", url, base)
    git(checkout, "checkout", "-qf", "-B", "main", main_rev)
    vcs = get_repository_object(checkout)

    monkeypatch.delenv("GECKO_BASE_REV", raising=False)
    assert vcs.get_outgoing_files("ADM") == []
    assert vcs.base_ref == main_rev

    monkeypatch.setenv("GECKO_BASE_REV", base)
    monkeypatch.setenv("GECKO_HEAD_REV", main_rev)
    assert vcs.get_outgoing_files("ADM") == ["m.txt"]
    assert vcs.base_ref == base


def test_other_repository_ignores_ci_base_rev(shallow_checkout, monkeypatch):
    checkout, base, head = shallow_checkout
    vcs = get_repository_object(checkout)
    monkeypatch.setenv("GECKO_BASE_REV", base)
    monkeypatch.setenv("GECKO_HEAD_REV", "0123456789abcdef0123456789abcdef01234567")

    with pytest.raises(MissingUpstreamRepo):
        vcs.get_outgoing_files("ADM")
    assert vcs.base_ref == head


def test_connected_shallow_clone_walks_history(tmp_path, origin):
    path, base, head = origin
    clone = tmp_path / "clone"
    git(
        tmp_path,
        "clone",
        "-q",
        "--depth=3",
        "--branch",
        "code-review/D1",
        path.as_uri(),
        str(clone),
    )
    git(
        clone,
        "fetch",
        "-q",
        "--depth=2",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
    )
    vcs = get_repository_object(clone)

    assert vcs.is_shallow
    assert sorted(vcs.get_outgoing_files("ADM", upstream="origin/main")) == [
        "b.cpp",
        "c.py",
    ]
    assert vcs.get_outgoing_files("ADM") == []


def test_shallow_clone_with_truncated_merge_parent(tmp_path, origin):
    """A depth 3 clone of `merged` reaches `main` through the merge but cuts
    the stack's own history at `stack2`, so `git log` would list every file."""
    path, base, head = origin
    clone = tmp_path / "clone"
    git(
        tmp_path,
        "clone",
        "-q",
        "--depth=3",
        "--branch",
        "merged",
        path.as_uri(),
        str(clone),
    )
    git(
        clone,
        "fetch",
        "-q",
        "--depth=1",
        "origin",
        "+refs/heads/main:refs/remotes/origin/main",
    )
    vcs = get_repository_object(clone)

    assert vcs.is_shallow
    assert sorted(vcs.get_outgoing_files("ADM", upstream="origin/main")) == [
        "b.cpp",
        "c.py",
        "d.py",
    ]


def test_diverged_upstream_on_full_clone(tmp_path, origin):
    path, base, head = origin
    clone = tmp_path / "clone"
    git(
        tmp_path, "clone", "-q", "--branch", "code-review/D1", path.as_uri(), str(clone)
    )
    vcs = get_repository_object(clone)

    assert not vcs.is_shallow
    assert sorted(vcs.get_outgoing_files("ADM", upstream="origin/main")) == [
        "b.cpp",
        "c.py",
    ]


if __name__ == "__main__":
    mozunit.main()
