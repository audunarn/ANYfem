"""A regional splice must respect the same live source authority as a full mesh."""

import pytest
from anygeometry.entities import OrientedEdge
from anygeometry.extrusions import BezierDirectrix
from anygeometry.surfaces import ExtrudedSurface

from anyfem.mesh_jobs import MeshSettings
from anyfem.regional_dispatch import capture_regional_baseline, regional_refusal
from anyfem.regional_mesh import RegionalUpdateUnsupported
from test_mesh_publication_numbers import plates


@pytest.mark.parametrize("implicit_coons", [False, True])
def test_authored_sweep_needs_fresh_generation_cells(implicit_coons):
    app, _, _ = plates()
    project = app.project
    session = project.mesh_session()
    settings = MeshSettings.create(.5, strategy="mapped", element_order="linear")
    parent = session.generate_mesh(settings)
    before = project.mesh_numbering
    geometry = project.geometry
    directrix = ((10., 0., 0.), (10.5, 0., .5), (11., 0., 0.))
    base = geometry.add_points(directrix)
    top = geometry.add_points([(x, y+1., z) for x, y, z in directrix])
    edges = (geometry.add_spline(base[0], base[1:-1], base[-1]), geometry.add_line(base[-1], top[-1]),
             geometry.add_spline(top[-1], top[1:-1], top[0]), geometry.add_line(top[0], base[0]))
    support = None if implicit_coons else ExtrudedSurface(BezierDirectrix(directrix), (0., 1., 0.))
    face = geometry.add_face_from_loop(tuple(OrientedEdge(e, True) for e in edges),
                                       corners=(0, 1, 2, 3), surface=support)
    project.assign_plate(face, "plate")
    assert "live source-cell publication" in regional_refusal(project)
    assert capture_regional_baseline(project, settings, parent) is None
    with pytest.raises(RegionalUpdateUnsupported, match="live source-cell publication"):
        session.preview_update(settings)
    with pytest.raises(RegionalUpdateUnsupported, match="live source-cell publication"):
        session.update_mesh(settings)
    assert project.mesh_numbering == before and session.mesh is parent
