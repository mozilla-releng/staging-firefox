# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import subprocess

logger = logging.getLogger(__name__)

REMOTE_HEAD = "refs/remotes/origin/"
# A fetch against GitHub takes a few seconds here. A failed derivation only
# falls back to the reported base, so a stalled transfer must not eat the
# decision task's 30 minute budget.
GIT_TIMEOUT_SECONDS = 60
# Enough history to find ``event.before`` under a normal push without fetching
# the whole branch. The check is repeated on the complete branch history, so
# this depth only saves a fetch and never decides the outcome.
PROBE_DEPTH = 50


def default_branch_ref(repo):
    # git clone records the remote's default branch as refs/remotes/origin/HEAD.
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
    # merge-base exits 1 without shared history and 128 when rev is unknown
    # locally. Both mean rev is not an ancestor we can see.
    merge_base = repo.run(
        "merge-base", rev, descendant, return_codes=[1, 128], stderr=subprocess.DEVNULL
    ).strip()
    return merge_base == rev


def remote_tip(repo, repository, ref):
    out = repo.run("ls-remote", repository, ref, timeout=GIT_TIMEOUT_SECONDS).split()
    return out[0] if out else None


def fetch(repo, repository, refspec, *options):
    repo.run("fetch", *options, repository, refspec, timeout=GIT_TIMEOUT_SECONDS)


def graft_root_parent(repo, head_rev):
    """Return the parent recorded in the raw commit object of the graft root
    below ``head_rev``, or ``None`` unless there is exactly one candidate.

    Several candidates mean the fetched history crosses the shallow boundary
    through more than one parent, so there is no unique fork point."""
    parents = set()
    for root in repo.run("rev-list", "--max-parents=0", head_rev).split():
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
    (parent,) = parents
    return parent


def derive_base_rev(repo, head_repository, head_rev, head_ref, base_rev):
    """Return the base to use for a push of ``head_rev`` to ``head_ref``: the
    fork point off the default branch, ``head_rev`` itself when it is the tip
    of the default branch, or ``None`` to keep ``base_rev`` as reported.

    GitHub reports the commit the branch pointed at before the push as
    ``base_rev``: the null revision for a new branch, the orphaned old tip for
    a force push. On a shallow clone ``git merge-base`` cannot find the fork
    point either, because Git treats the fetched commits as graft roots and
    does not traverse their parents. ``git fetch --shallow-exclude=<default
    branch>`` fetches the pushed commits and nothing reachable from the
    default branch, so the lowest pushed commit becomes the graft root, and
    the parent recorded in its raw object is the fork point. It is fetched
    into ``repo`` at depth 1 so a tree diff against it works."""
    default_ref = default_branch_ref(repo)
    if not default_ref or head_ref == default_ref:
        # A push to the default branch reports its previous tip, which is
        # the right base.
        return None
    try:
        return _derive_base_rev(repo, head_repository, head_rev, base_rev, default_ref)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"Cannot derive base_rev for {head_rev}: {e}")
        return None


def _derive_base_rev(repo, head_repository, head_rev, base_rev, default_ref):
    has_base = bool(base_rev) and base_rev != repo.NULL_REVISION

    # A normal push adds commits on top of the reported base. Look for it in
    # recent history first, which is cheap.
    if has_base:
        fetch(repo, head_repository, head_rev, f"--depth={PROBE_DEPTH}")
        if is_ancestor(repo, base_rev, head_rev):
            return None

    # A branch pointing at the tip of the default branch has no changes of
    # its own.
    if remote_tip(repo, head_repository, default_ref) == head_rev:
        return head_rev

    # Fetch the pushed commits and nothing else. This fails when head_rev is
    # an older commit already on the default branch, which keeps the reported
    # base.
    fetch(repo, head_repository, head_rev, f"--shallow-exclude={default_ref}")

    # The branch history is complete now, so this check is exact. A normal
    # push of more than PROBE_DEPTH commits ends here.
    if has_base and is_ancestor(repo, base_rev, head_rev):
        return None

    fork_point = graft_root_parent(repo, head_rev)
    if fork_point:
        fetch(repo, head_repository, fork_point, "--depth=1")
    return fork_point
