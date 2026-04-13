# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from mach.util import get_state_dir

HOOK_GROUP = "git-push"
STATE_DIR = Path(get_state_dir(specific_to_topsrcdir=False)) / "taskcluster"
TASKCLUSTER_ROOT_URL = "https://firefox-ci-tc.services.mozilla.com"
TASKCLUSTER_CREDENTIALS = STATE_DIR / "taskcluster_credentials.json"
TASKCLUSTER_CLI = STATE_DIR / ("taskcluster.exe" if sys.platform == "win32" else "taskcluster")


def _load_cached_credentials():
    if not TASKCLUSTER_CREDENTIALS.exists():
        return None
    try:
        with TASKCLUSTER_CREDENTIALS.open() as f:
            return json.load(f)
    except Exception:
        return None


def _delete_cached_credentials():
    if TASKCLUSTER_CREDENTIALS.exists():
        TASKCLUSTER_CREDENTIALS.unlink()


def _save_credentials(credentials):
    TASKCLUSTER_CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    with TASKCLUSTER_CREDENTIALS.open("w") as f:
        json.dump(credentials, f, indent=2, sort_keys=True)



def _bootstrap_taskcluster_cli():
    """Download the taskcluster CLI binary to the state dir and return its path."""
    os_map = {"linux": "linux", "darwin": "darwin", "win32": "windows"}
    arch_map = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}

    os_name = os_map.get(sys.platform)
    arch = arch_map.get(platform.machine().lower())
    if not os_name or not arch:
        print(
            f"error: unsupported platform {sys.platform}/{platform.machine()}",
            file=sys.stderr,
        )
        sys.exit(1)

    with urlopen(
        "https://api.github.com/repos/taskcluster/taskcluster/releases/latest"
    ) as resp:
        version = json.loads(resp.read())["tag_name"].lstrip("v")

    ext = "zip" if os_name == "windows" else "tar.gz"
    url = f"https://github.com/taskcluster/taskcluster/releases/download/v{version}/taskcluster-{os_name}-{arch}.{ext}"

    print(f"Downloading taskcluster CLI v{version}...")
    with urlopen(url) as resp:
        data = resp.read()

    TASKCLUSTER_CLI.parent.mkdir(parents=True, exist_ok=True)
    if ext == "tar.gz":
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extract("taskcluster", path=TASKCLUSTER_CLI.parent)
    else:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extract(TASKCLUSTER_CLI.name, path=TASKCLUSTER_CLI.parent)
    TASKCLUSTER_CLI.chmod(0o755)
    return str(TASKCLUSTER_CLI)


def _get_taskcluster_cli():
    """Return the path to the taskcluster CLI binary, bootstrapping if needed."""
    return shutil.which("taskcluster") or (
        str(TASKCLUSTER_CLI) if TASKCLUSTER_CLI.exists() else _bootstrap_taskcluster_cli()
    )


def _signin_via_browser(scopes):
    """Get Taskcluster credentials using the `taskcluster signin` CLI."""
    tc_cli = _get_taskcluster_cli()

    print("Opening Taskcluster login in your browser...")
    scope_args = []
    for scope in scopes:
        scope_args.append(f"-s={scope}")
    try:
        result = subprocess.run(
            [tc_cli, "signin", "--name", "mach-try", "--expires", "3h"] + scope_args,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"error: `taskcluster signin` failed:\n{e.stderr}", file=sys.stderr)
        sys.exit(1)

    credentials = {}
    for line in result.stdout.splitlines():
        for env_var, key in (
            ("TASKCLUSTER_CLIENT_ID", "clientId"),
            ("TASKCLUSTER_ACCESS_TOKEN", "accessToken"),
            ("TASKCLUSTER_CERTIFICATE", "certificate"),
        ):
            if env_var in line:
                credentials[key] = line.split("'")[1]

    if not credentials.get("clientId") or not credentials.get("accessToken"):
        print("error: did not receive Taskcluster credentials", file=sys.stderr)
        sys.exit(1)

    return credentials


def _get_credentials(scopes):
    """Return valid Taskcluster credentials as a dict.

    Checks environment variables first, then cached credentials, then
    performs a browser-based sign-in.
    """
    client_id = os.environ.get("TASKCLUSTER_CLIENT_ID")
    access_token = os.environ.get("TASKCLUSTER_ACCESS_TOKEN")
    if client_id and access_token:
        credentials = {"clientId": client_id, "accessToken": access_token}
        certificate = os.environ.get("TASKCLUSTER_CERTIFICATE")
        if certificate:
            credentials["certificate"] = certificate
        return credentials

    cached = _load_cached_credentials()
    if cached:
        return cached

    print("No valid Taskcluster credentials found, signing in...")
    credentials = _signin_via_browser(scopes)
    _save_credentials(credentials)
    return credentials


def trigger_try_hook(
    repo_url: str,
    branch: str,
    head_rev: str,
    base_rev: str,
    owner: str,
):
    """Trigger the git-push build-decision hook for a try push."""
    if ":" in repo_url and not repo_url.startswith(("http://", "https://")):
        # SSH-style URL: git@github.com:mozilla-releng/staging-firefox
        repo_path = repo_url.split(":", 1)[1]
    else:
        repo_path = urlparse(repo_url).path.strip("/")

    # The trailing slash is intentional. Normally `git-push` hookIds have the format
    # `<repo_path>/<branch>`. But for try repos, all branch names need to be supported.
    # During hook generation, this is denoted as `<repo_path>/*` but `*` is not a valid
    # character for hookIds so it gets dropped.
    hook_id = f"{repo_path}/"

    payload = {
        "sha": head_rev,
        "base_sha": base_rev,
        "ref": f"refs/heads/{branch}",
        "owner": owner,
        "base_ref": None,
    }

    tc_cli = _get_taskcluster_cli()
    scopes = [f"hooks:trigger-hook:{HOOK_GROUP}/{hook_id}"]

    def call_trigger_hook():
        credentials = _get_credentials(scopes)
        env = os.environ.copy()
        env["TASKCLUSTER_ROOT_URL"] = TASKCLUSTER_ROOT_URL
        env["TASKCLUSTER_CLIENT_ID"] = credentials["clientId"]
        env["TASKCLUSTER_ACCESS_TOKEN"] = credentials["accessToken"]
        if "certificate" in credentials:
            cert = credentials["certificate"]
            env["TASKCLUSTER_CERTIFICATE"] = (
                cert if isinstance(cert, str) else json.dumps(cert)
            )
        return subprocess.run(
            [tc_cli, "api", "hooks", "triggerHook", HOOK_GROUP, hook_id],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
        )

    result = call_trigger_hook()
    if result.returncode != 0:
        if "401" in result.stderr:
            _delete_cached_credentials()
            result = call_trigger_hook()
        if result.returncode != 0:
            print(f"error: trigger hook failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

    output = result.stdout.strip()
    if not output:
        print(
            f"error: trigger hook returned no output\nstderr: {result.stderr}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        print(
            f"error: trigger hook returned unexpected output:\n{output}\nstderr: {result.stderr}",
            file=sys.stderr,
        )
        sys.exit(1)
