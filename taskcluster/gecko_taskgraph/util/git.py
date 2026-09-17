# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import subprocess

logger = logging.getLogger(__name__)

REMOTE_HEAD = "refs/remotes/origin/"
FETCH_TIMEOUT_SECONDS = 300
PROBE_DEPTH = 50


def default_branch_ref(repo):
    try:
        ref = repo.run(
            "symbolic-ref", f"{REMOTE_HEAD}HEAD", stderr=subprocess.DEVNULL
        ).strip()
    except subprocess.CalledProcessError:
        return None
    if not ref.startswith(REMOTE_HEAD):
        return None
    return f"refs/heads/{ref[len(REMOTE_HEAD) :]}"


def is_ancestor(repo, rev, descendant):
    merge_base = repo.run(
        "merge-base", rev, descendant, return_codes=[1, 128], stderr=subprocess.DEVNULL
    ).strip()
    return merge_base == rev


def remote_tip(repo, repository, ref):
    out = repo.run("ls-remote", repository, ref, timeout=FETCH_TIMEOUT_SECONDS).split()
    return out[0] if out else None


def derive_base_rev(repo, head_repository, head_rev, head_ref, base_rev):
    """Return the commit where the history of ``head_rev`` forks off the
    default branch of ``head_repository``, fetched into ``repo`` at depth 1,
    or ``head_rev`` itself when it is the tip of the default branch.

    Returns ``None`` when ``base_rev`` should be kept as is: ``head_ref`` is
    the default branch, ``base_rev`` is an ancestor of ``head_rev`` (a fast
    forward push), or the fork point cannot be determined."""
    default_ref = default_branch_ref(repo)
    if not default_ref or head_ref == default_ref:
        return None

    has_base = bool(base_rev) and base_rev != repo.NULL_REVISION
    try:
        if has_base:
            repo.run(
                "fetch",
                f"--depth={PROBE_DEPTH}",
                head_repository,
                head_rev,
                timeout=FETCH_TIMEOUT_SECONDS,
            )
            if is_ancestor(repo, base_rev, head_rev):
                return None

        if remote_tip(repo, head_repository, default_ref) == head_rev:
            return head_rev

        repo.run(
            "fetch",
            f"--shallow-exclude={default_ref}",
            head_repository,
            head_rev,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        if has_base and is_ancestor(repo, base_rev, head_rev):
            return None

        roots = repo.run("rev-list", "--max-parents=0", head_rev).split()
        parents = set()
        for root in roots:
            for line in repo.run("cat-file", "-p", root).splitlines():
                if not line:
                    break
                if line.startswith("parent "):
                    parents.add(line.split()[1])
        if len(parents) != 1:
            logger.warning(
                f"Cannot derive base_rev for {head_rev}: "
                f"fork point candidates {sorted(parents)}"
            )
            return None
        (fork_point,) = parents
        repo.run(
            "fetch",
            "--depth=1",
            head_repository,
            fork_point,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Cannot derive base_rev for {head_rev}: {e}")
        return None

    return fork_point
