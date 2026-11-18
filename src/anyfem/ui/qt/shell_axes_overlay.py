"""Presentation-only geometry for the shell local-axes overlay (read-only on project and mesh)."""
import numpy as np

ARROW_FRACTION = 0.35
COLOURS = dict(x='#c0392b', x_warn='#ff8c00', normal='#2a6fdb', normal_warn='#c71585')


def prepare_shell_axes(project, mesh):
    """Return (batches, caption, stats); batches is a list of (MeshArrays, colour)."""
    from ...mesh_orientation import element_axes, project_orientation_report
    from .glyph_preparation import arrow_batch
    report = project_orientation_report(project, mesh)
    policy = project.shell_formulation_policy
    normals = None
    if policy.s3 == "e4-pl-s3-v2d" and any(len(n) == 3 for n in mesh.tris.values()):
        from ...solve.build import _qualified_s3_owner_normals
        _ids, normals = _qualified_s3_owner_normals(project, mesh)
    unchecked = report.unchecked
    axis_bad, normal_bad = set(report.axis_minority), set(report.normal_minority)
    groups = {key: ([], []) for key in COLOURS}
    skipped = 0
    for face in report.faces:
        for eid in mesh.elements_of_face[face.face_id]:
            if (eid not in mesh.quads and eid not in mesh.tris) or eid in unchecked:
                continue
            try:
                axes = element_axes(mesh, eid, policy, normals)
            except ValueError:
                skipped += 1
                continue
            length = ARROW_FRACTION * axes.size
            c = np.asarray(axes.centroid, dtype=float)
            key = 'x_warn' if eid in axis_bad else 'x'
            groups[key][0].append(c); groups[key][1].append(c + length * np.asarray(axes.x_axis, dtype=float))
            if axes.normal is not None:
                key = 'normal_warn' if eid in normal_bad else 'normal'
                groups[key][0].append(c); groups[key][1].append(c + length * np.asarray(axes.normal, dtype=float))
    batches = []
    stats = {key: len(starts) for key, (starts, _e) in groups.items()}
    for key, (starts, ends) in groups.items():
        if starts:
            batch = arrow_batch(starts, ends)
            if batch is not None:
                batches.append((batch, COLOURS[key]))
    stats.update(skipped=skipped, unchecked=len(unchecked), arrows=sum(stats[k] for k in COLOURS))
    caption = report.summary().splitlines()[0]
    if skipped:
        caption += f" {skipped} more skipped (degenerate)."
    return batches, caption, stats
