"""Create a fresh editable project through the interchange owner's SAT reader."""
import math
from pathlib import Path

from ..model.project import Project

# Reader opt-ins: both are off unless the caller names a tolerance. The reader
# owns the admissible range and records the opt-in in face metadata and diagnostics.
OPT_IN_FIELD_LABELS = {
    "vertex_relocation_tolerance_m": "Vertex relocation tolerance (m)",
    "approximate_curve_tolerance_m": "Approximate curve tolerance (m)",
}


def parse_tolerance_text(text, label):
    """Return a positive finite float from optional user text, or None when empty."""
    if text is None or not str(text).strip():
        return None
    try:
        value = float(str(text).strip())
    except ValueError:
        raise ValueError(f"{label}: '{str(text).strip()}' is not a number.") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}: must be a positive finite number of metres.")
    return value


def opt_in_hint(message):
    """Return the field label when a refusal message names one of the opt-ins."""
    text = str(message)
    for name, label in OPT_IN_FIELD_LABELS.items():
        if name in text:
            return label
    return None


def import_sat_project(path, *, vertex_relocation_tolerance_m=None, approximate_curve_tolerance_m=None, **options):
    """Read complete supported SAT geometry without changing an open project.

    The two tolerance opt-ins default to off and are passed to the reader only
    when given; they are recorded on the imported part.
    """
    opt_ins = {}
    if vertex_relocation_tolerance_m is not None:
        opt_ins["vertex_relocation_tolerance_m"] = vertex_relocation_tolerance_m
    if approximate_curve_tolerance_m is not None:
        opt_ins["approximate_curve_tolerance_m"] = approximate_curve_tolerance_m
    options.update(opt_ins)
    try:
        from anyfileio.sat_import import read_sat
    except ImportError as error:
        raise RuntimeError(
            "SAT import requires the ANYfileIO SAT reader. Update the ANYfileIO installation."
        ) from error
    result = read_sat(Path(path), **options)
    project = Project(name=Path(path).stem, geometry=result.geometry)
    project.imported_format = "sat_geometry"
    if result.source_face_map:
        part_metadata = {
            "source_format": "sat",
            "source_file": Path(path).name,
            "source_sha256": result.source_sha256,
            "source_unit_scale_m": result.source_unit_scale_m,
            "import_diagnostics": list(result.diagnostics),
            "source_vertices": {str(k): v for k, v in result.source_vertex_map.items()},
            "source_edges": {str(k): list(v) for k, v in result.source_edge_map.items()},
            "source_faces": {str(k): v for k, v in result.source_face_map.items()},
        }
        if opt_ins:
            part_metadata["import_options"] = dict(opt_ins)
        part = project.geometry.add_part(name=project.name, metadata=part_metadata)
        for source_id, face_id in result.source_face_map.items():
            project.geometry.add_sheet([face_id], part_id=part, name=f"SAT face {source_id}")
    return project, result
