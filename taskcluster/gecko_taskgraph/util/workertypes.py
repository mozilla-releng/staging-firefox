# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from mozilla_taskgraph.util.attributes import release_level as _release_level
from taskgraph.util.workertypes import get_worker_type as _get_worker_type
from taskgraph.util.workertypes import (
    worker_type_implementation as _worker_type_implementation,
)


def worker_type_implementation(graph_config, parameters, worker_type):
    """Get the worker implementation and OS for the given workerType, where the
    OS represents the host system, not the target OS, in the case of
    cross-compiles."""
    return _worker_type_implementation(graph_config, worker_type)


def get_worker_type(graph_config, parameters, worker_type):
    """
    Get the worker type provisioner and worker-type, optionally evaluating
    aliases from the graph config.
    """
    return _get_worker_type(
        graph_config,
        worker_type,
        parameters["level"],
        release_level=_release_level(graph_config["release-branches"], parameters),
        project=parameters.get("project"),
    )
