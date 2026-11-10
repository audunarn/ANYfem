"""Remember numbers published since the project file was last saved."""

import json
from dataclasses import replace

from ..mesh_numbering import MeshNumbering
from .artifacts import ArtifactStore
from .recovery import _atomic_write


def journal_path(project_path):
    return ArtifactStore(project_path).root.resolve() / "mesh-numbering.json"


def allocation_floor(path, document_id, state):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return state
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("document_id") != document_id or data.get("version") != 1:
        raise ValueError("mesh numbering journal has an incompatible version or project identity")
    prior = MeshNumbering(data["node_high_water"], data["element_high_water"])
    return replace(state, node_high_water=max(prior.node_high_water, state.node_high_water),
                   element_high_water=max(prior.element_high_water, state.element_high_water))


def persist_allocation(path, document_id, state):
    floor = allocation_floor(path, document_id, state)
    data = {"version": 1, "document_id": document_id, "node_high_water": floor.node_high_water,
            "element_high_water": floor.element_high_water}
    _atomic_write(path, json.dumps(data, allow_nan=False).encode("utf-8"))


def saved_allocation(project, destination):
    state = project.mesh_numbering
    paths = {journal_path(destination)}
    origin = getattr(project, "_mesh_file_base", None)
    if origin is not None:
        paths.add(journal_path(origin[0]))
    for path in paths:
        state = allocation_floor(path, project.document_id, state)
    return state
