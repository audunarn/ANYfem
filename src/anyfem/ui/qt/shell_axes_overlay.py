"""Presentation geometry for the shell local-axes overlay.

Arrows show the frame the solver's stress recovery reports (see
``anyfem.mesh_orientation``), at the element centroid, using the recovery
point nearest the element's natural centre.  Turned elements are orange;
ambiguous, inconsistent and mixed-face elements purple; unchecked elements get
no arrows and are counted in the caption.

Scope (option A): the check reads the model a static solve of the project as
it stands would build; it leaves the mesh unchanged and performs on the
project only the section-assignment normalisation that solve's build
performs.  Analysis-time imperfections are not included (D2).
"""
import numpy as np

ARROW_FRACTION = 0.35
COLOURS = dict(x='#c0392b', x_warn='#ff8c00', x_doubt='#8e44ad',
               normal='#2a6fdb', normal_warn='#c71585')


def prepare_shell_axes(project, mesh, cancel=None):
    """Return (batches, caption, stats); batches is a list of (MeshArrays, colour)."""
    from ...mesh_orientation import project_orientation_report
    from .glyph_preparation import arrow_batch
    report = project_orientation_report(project, mesh, cancel=cancel)
    turned, reversed_ = set(report.turned), set(report.reversed)
    doubtful = set(report.ambiguous) | set(report.inconsistent) | set(report.mixed)
    inconsistent = set(report.inconsistent)
    groups = {key: ([], []) for key in COLOURS}
    for eid, axes in sorted(report.element_axes.items()):
        frame = axes.display_frame()
        length = ARROW_FRACTION * axes.size
        c = np.asarray(axes.centroid, dtype=float)
        key = 'x_warn' if eid in turned else 'x_doubt' if eid in doubtful else 'x'
        groups[key][0].append(c); groups[key][1].append(c + length * np.asarray(frame.x_axis, dtype=float))
        key = 'normal_warn' if eid in reversed_ or eid in inconsistent else 'normal'
        groups[key][0].append(c); groups[key][1].append(c + length * np.asarray(frame.normal, dtype=float))
    batches = []
    stats = {key: len(starts) for key, (starts, _e) in groups.items()}
    for key, (starts, ends) in groups.items():
        if starts:
            batch = arrow_batch(starts, ends)
            if batch is not None:
                batches.append((batch, COLOURS[key]))
    stats.update(unchecked=len(report.unchecked), mixed=len(report.mixed), elements=len(report.element_axes),
                 arrows=sum(stats[k] for k in COLOURS))
    caption = report.summary().splitlines()[0]
    return batches, caption, stats
