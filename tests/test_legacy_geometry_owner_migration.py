"""Legacy ANYfem geometry migrates without mutating kernel-owned stores."""

from __future__ import annotations

import pytest

from anyfem.io.project_file import project_from_dict


def test_format_two_noncontiguous_ids_migrate_through_public_owner_operations() -> None:
    document = {
        "anyfem": {"format": 2},
        "name": "legacy owner migration",
        "geometry": {
            "vertices": [
                {"id": 2, "position": [0.0, 0.0, 0.0]},
                {"id": 5, "position": [2.0, 0.0, 0.0]},
                {"id": 8, "position": [2.0, 1.0, 0.0]},
                {"id": 11, "position": [0.0, 1.0, 0.0]},
            ],
            "edges": [
                {"id": 3, "start": 2, "end": 5, "curve": {"kind": "line"}},
                {"id": 7, "start": 5, "end": 8, "curve": {"kind": "line"}},
                {"id": 9, "start": 8, "end": 11, "curve": {"kind": "line"}},
                {"id": 12, "start": 11, "end": 2, "curve": {"kind": "line"}},
            ],
            "faces": [
                {
                    "id": 4,
                    "loop": [[3, True], [7, True], [9, True], [12, True]],
                    "corners": [0, 1, 2, 3],
                }
            ],
            "next_id": {"vertex": 12, "edge": 13, "face": 5},
        },
    }

    project = project_from_dict(document)
    geometry = project.geometry

    assert sorted(geometry.vertices) == [2, 5, 8, 11]
    assert sorted(geometry.edges) == [3, 7, 9, 12]
    assert sorted(geometry.faces) == [4]
    assert [(item.edge, item.forward) for item in geometry.faces[4].loop] == [
        (3, True),
        (7, True),
        (9, True),
        (12, True),
    ]
    assert geometry.id_state()["vertex"] == 12
    assert geometry.id_state()["edge"] == 13
    assert geometry.id_state()["face"] == 5

    with pytest.raises(TypeError):
        geometry.vertices[99] = geometry.vertices[2]

    assert geometry.add_point(3.0, 0.0, 0.0) == 12
