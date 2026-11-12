"""Create a fresh editable project through the interchange owner's SAT reader."""
from pathlib import Path

from ..model.project import Project


def import_sat_project(path, **options):
    """Read complete supported SAT geometry without changing an open project."""
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
        part = project.geometry.add_part(
            name=project.name,
            metadata={
                "source_format": "sat",
                "source_file": Path(path).name,
                "source_sha256": result.source_sha256,
                "source_unit_scale_m": result.source_unit_scale_m,
                "import_diagnostics": list(result.diagnostics),
                "source_vertices": {str(k): v for k, v in result.source_vertex_map.items()},
                "source_edges": {str(k): list(v) for k, v in result.source_edge_map.items()},
                "source_faces": {str(k): v for k, v in result.source_face_map.items()},
            },
        )
        for source_id, face_id in result.source_face_map.items():
            project.geometry.add_sheet([face_id], part_id=part, name=f"SAT face {source_id}")
    return project, result
