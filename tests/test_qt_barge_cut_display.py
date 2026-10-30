"""Real renderer receives semantic outline identity around the barge openings."""
import numpy as np
from test_qt_geometry_actions import qapp, window
from anyfem import commands
from anyfem.application.examples import floating_barge_large
from anyfem.ui.scene import build_geometry_scene


def test_barge_trim_triangles_render_only_owner_boundaries(window, qapp, tmp_path):
    from any3dview.retained_viewer import _surface_outline_loops
    project = floating_barge_large()
    face = next(i for i, f in project.geometry.faces.items() if len(f.holes) == 3)
    result = commands.CommandStack(project).run(commands.CutPlate(
        face, origin=(0, 0, .2), normal=(0, 0, 1)))
    window._set_project(project)
    qapp.processEvents()
    patch = next(p for p in build_geometry_scene(project).faces
                 if set(p.outline_groups or ()) == set(result.target_faces))
    canvas = window.viewport.canvas
    canvas.clear()
    window.viewport._draw_face_patch(patch)
    qapp.processEvents()
    entry = next(e for e in canvas._entries.values()
                 if e["handle"].mesh.triangle_count > 100)
    mesh = entry["handle"].mesh
    groups = entry["outline_groups"]
    assert groups is not None and len(set(groups)) == 2
    loops = list(_surface_outline_loops(mesh, np.arange(mesh.triangle_count),
                                      groups, entry.get("outline_vertex_ids")))
    # Lower perimeter + upper perimeter + three rounded opening perimeters.
    # The many connecting triangulation diagonals produce no extra outlines.
    assert len(loops) == 5
    canvas.fit_to_scene()
    qapp.processEvents()
    assert window.grab().save(str(tmp_path / "barge-cut-outline.png"))
