# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.


import unittest
from unittest.mock import MagicMock, patch

from mozunit import main
from mozversioncontrol.errors import MissingUpstreamRepo

from gecko_taskgraph import files_changed

PARAMS = {
    "head_repository": "https://hg.mozilla.org/mozilla-central",
    "head_rev": "a14f88a9af7a",
}

FILES_CHANGED = [
    "devtools/client/debugger/index.html",
    "devtools/client/inspector/test/browser_inspector_highlighter-eyedropper-events.js",
    "devtools/client/inspector/test/head.js",
    "devtools/client/themes/rules.css",
    "devtools/client/webconsole/test/browser_webconsole_output_06.js",
    "devtools/server/actors/highlighters/eye-dropper.js",
    "devtools/server/actors/object.js",
    "docshell/base/nsDocShell.cpp",
    "dom/tests/mochitest/general/test_contentViewer_overrideDPPX.html",
    "taskcluster/scripts/builder/build-l10n.sh",
]


def test_get_changed_files(responses):
    url = f"{PARAMS['head_repository']}/json-pushchangedfiles/{PARAMS['head_rev']}"
    responses.add(responses.GET, url, status=200, json={"files": FILES_CHANGED})
    assert (
        sorted(
            files_changed.get_changed_files(
                PARAMS["head_repository"], PARAMS["head_rev"]
            )
        )
        == FILES_CHANGED
    )


class TestCheck(unittest.TestCase):
    def setUp(self):
        patcher = patch(
            "gecko_taskgraph.files_changed.get_changed_files",
            return_value=FILES_CHANGED,
        )
        self.mock_get_changed_files = patcher.start()
        self.addCleanup(patcher.stop)

    def test_check_no_params(self):
        self.assertTrue(files_changed.check({}, ["ignored"]))

    def test_check_no_match(self):
        self.assertFalse(files_changed.check(PARAMS, ["nosuch/**"]))

    def test_check_match(self):
        self.assertTrue(files_changed.check(PARAMS, ["devtools/**"]))


def fake_vcs(monkeypatch, **outgoing):
    vcs = MagicMock()
    vcs.get_outgoing_files = MagicMock(**outgoing)
    monkeypatch.setattr(files_changed, "get_repository_object", lambda repo: vcs)
    return vcs


def test_locally_changed_files(monkeypatch):
    fake_vcs(monkeypatch, return_value=["a.py", "b.py"])

    assert files_changed._get_locally_changed_files("/repo") == {"a.py", "b.py"}


def test_locally_changed_files_without_history(monkeypatch):
    fake_vcs(monkeypatch, side_effect=MissingUpstreamRepo("shallow"))

    assert files_changed._get_locally_changed_files("/repo") == set()


if __name__ == "__main__":
    main()
