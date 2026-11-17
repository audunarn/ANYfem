"""Quad-first/legacy route ownership follows the prepared mesh onto source faces.

Regression: the drawing barge (and the barge section) mesh on bay-split work
faces.  ``elements_of_face`` was aggregated onto source faces but the merge
record's ``quad_first_face_ids`` stayed on work faces, so permanent numbering of
a GUI mesh refused it: "mesh has faces owned by neither the quad-first nor the
legacy route".
"""

from types import SimpleNamespace

import pytest

from anyfem.application.examples import floating_barge_section
from anyfem.mesh_numbering import element_numbers, number_mesh_with_maps
from anyfem.structural_preparation import _publish_route_face_ownership


@pytest.mark.parametrize("order", ["linear", "quadratic"])
def test_barge_section_quad_first_mesh_survives_permanent_numbering(order):
    project = floating_barge_section()
    mesh = project.generate_mesh(0.7, strategy="quad_first", layout_policy="adaptive", order=order)
    record = mesh.hybrid_diagnostics
    assert set(record["quad_first_face_ids"]) | set(record["legacy_face_ids"]) == set(mesh.elements_of_face)

    nodes = {node: node + 100_000 for node in mesh.nodes}
    elements = {element: element + 100_000 for element in element_numbers(mesh)}
    numbered = number_mesh_with_maps(mesh, nodes, elements, source_geometry=project.geometry)

    assert numbered.hybrid_diagnostics["quad_first_face_ids"] == record["quad_first_face_ids"]
    assert sorted(numbered.elements_of_face) == sorted(mesh.elements_of_face)
    if order == "quadratic":
        certificate = numbered.hybrid_diagnostics["high_order_geometry"]
        assert certificate["model_id"] == str(project.geometry.model_id)
        assert {row["face_id"] for row in certificate["reports"]} == set(mesh.elements_of_face)
        assert certificate["q8_count"] + certificate["t6_count"] == len(mesh.quads) + len(mesh.tris)
        assert numbered.hybrid_diagnostics["quad_first"]["high_order_geometry"] == certificate


@pytest.mark.parametrize("order", ["linear", "quadratic"])
def test_barge_section_quad_first_republishes_through_the_workbench(order):
    from anyfem.application.controller import WorkbenchController
    from anyfem.application.workflow import WorkbenchWorkflow

    class Workbench(WorkbenchWorkflow):
        def __init__(self, project):
            self.workbench = WorkbenchController(project)
            self._inspection_mesh = None
            self._mesh_submissions = {}

        def set_status(self, message, **kwargs):
            self.status = message

        def show_mesh(self):
            pass

        def show_geometry(self):
            pass

        def refresh_panels(self):
            pass

        def refresh_all(self):
            pass

        def invalidate_result_requests(self, **kwargs):
            pass

    project = floating_barge_section()
    project.set_element_order(order)
    app = Workbench(project)
    first = app.generate_mesh(0.7, strategy="quad_first", layout_policy="adaptive")
    # The second publication must take fresh permanent numbers (the GUI case).
    second = app.generate_mesh(0.7, strategy="quad_first", layout_policy="adaptive")
    assert min(second.nodes) > max(first.nodes)
    assert set(second.hybrid_diagnostics["quad_first_face_ids"]) == set(second.elements_of_face)


def _mesh(record):
    return SimpleNamespace(hybrid_diagnostics=record)


def test_exact_record_moves_to_source_faces():
    mesh = _mesh({"q4": 1, "quad_first_face_ids": [10, 11, 12], "legacy_face_ids": [13, 14],
                  "13": {"backend": "python"}, "14": {"backend": "python"}, "quad_first": {}})
    _publish_route_face_ownership(mesh, {10: (1,), 11: (1,), 12: (2,), 13: (3,), 14: (3,)})
    assert mesh.hybrid_diagnostics == {"q4": 1, "quad_first_face_ids": [1, 2], "legacy_face_ids": [3],
                                       "quad_first": {}, "3": {"backend": "python"}}


def test_inexact_records_are_left_for_numbering_to_refuse():
    split = {"quad_first_face_ids": [10], "legacy_face_ids": [11], "11": {"backend": "python"}}
    conflict = {"quad_first_face_ids": [], "legacy_face_ids": [10, 11],
                "10": {"backend": "python"}, "11": {"backend": "native"}}
    orphan = {"quad_first_face_ids": [10, 99], "legacy_face_ids": []}
    for record, owners in ((split, {10: (1,), 11: (1,)}),
                           (conflict, {10: (1,), 11: (1,)}),
                           (orphan, {10: (1,)})):
        before = {key: value for key, value in record.items()}
        mesh = _mesh(record)
        _publish_route_face_ownership(mesh, owners)
        assert mesh.hybrid_diagnostics is record and record == before


def test_meshes_without_a_merge_record_are_untouched():
    record = {"q4": 1}
    mesh = _mesh(record)
    _publish_route_face_ownership(mesh, {1: (1,)})
    assert mesh.hybrid_diagnostics is record


def test_non_planar_high_order_certificate_is_left_for_numbering_to_refuse():
    from anyfem.structural_preparation import _publish_source_high_order_certificate

    row = {"model_id": "work", "geometry_family": "cylindrical", "chart_kind": "CylindricalQuadDomain",
           "boundary_projection": "source-edge-parameter-midpoint", "interior_projection": "owner-chart-midpoint",
           "validity_status": "CERTIFIED_POSITIVE"}
    certificate = {"status": "CERTIFIED_POSITIVE", "model_id": "work", "order": "quadratic", "reports": [row],
                   "q8_count": 0, "t6_count": 0}
    record = {"high_order_geometry": certificate, "quad_first": {"high_order_geometry": certificate}}
    mesh = SimpleNamespace(is_quadratic=True, hybrid_diagnostics=record, quads={}, tris={})
    _publish_source_high_order_certificate(mesh, SimpleNamespace(model_id="source", revision=1), "work")
    assert mesh.hybrid_diagnostics is record and record["high_order_geometry"] is certificate


def test_preparation_seam_midside_is_counted_once_on_source_faces():
    """Two perpendicular plates meet on a seam with no source-edge chain (an imprinted
    intersection): its midside is interior to both faces but one unique midside."""
    import numpy as np
    from anyfem import Project
    from anyfem.structural_preparation import _source_high_order_certificate

    project = Project("seam")
    g = project.geometry
    corners = {"a": (0, 0, 0), "b": (0, 1, 0), "c": (1, 1, 0), "d": (1, 0, 0), "e": (0, 1, 1), "f": (0, 0, 1)}
    loops = {1: "abcd", 2: "afeb"}
    nodes, faces, chains = {}, {}, {}
    ids = {name: index for index, name in enumerate(corners, start=1)}
    for name, point in corners.items():
        nodes[ids[name]] = np.asarray(point, dtype=float)
    midsides, next_node = {}, 100

    def midside(first, second):
        nonlocal next_node
        key = (min(first, second), max(first, second))
        if key not in midsides:
            midsides[key] = next_node
            nodes[next_node] = 0.5 * (nodes[first] + nodes[second])
            next_node += 1
        return midsides[key]

    quads, elements_of_face = {}, {}
    for element, loop in loops.items():
        points = g.add_points([corners[name] for name in loop])
        edges = g.add_polyline(points, close=True)
        faces[element] = g.add_face(edges)
        c = [ids[name] for name in loop]
        quads[element] = (*c, *(midside(c[i], c[(i + 1) % 4]) for i in range(4)))
        elements_of_face[faces[element]] = [element]
        for index, edge in enumerate(edges):
            first, second = c[index], c[(index + 1) % 4]
            if {first, second} != {ids["a"], ids["b"]}:  # the seam keeps no source chain
                _p0, t0, _d0 = g.closest_edge_point(edge, nodes[first])
                _p1, t1, _d1 = g.closest_edge_point(edge, nodes[second])
                ordered = (first, second) if t0 < t1 else (second, first)
                chains[edge] = [ordered[0], midside(first, second), ordered[1]]
    mesh = SimpleNamespace(quads=quads, tris={}, nodes=nodes, elements_of_face=elements_of_face,
                           nodes_of_edge=chains, is_quadratic=True)
    row = {"model_id": "work", "geometry_family": "planar", "chart_kind": "PlanarQuadDomain",
           "boundary_projection": "source-edge-parameter-midpoint", "interior_projection": "chord-midpoint",
           "validity_status": "CERTIFIED_POSITIVE"}
    certificate = {"status": "CERTIFIED_POSITIVE", "model_id": "work", "order": "quadratic", "reports": [row, row],
                   "q8_count": 2, "t6_count": 0, "unique_midside_count": 7, "target_size": 1.0,
                   "route": "quad-first"}

    source = _source_high_order_certificate(mesh, certificate, g, "work")

    assert source is not None and source["model_id"] == str(g.model_id)
    assert source["unique_midside_count"] == 7 and source["unique_boundary_midside_count"] == 6
    assert [row["interior_midside_count"] for row in source["reports"]] == [1, 0]
