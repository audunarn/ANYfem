"""Publish the reviewed drawing set together into a new, replace-safe directory."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from ...document import canonical_hash
from ...application.drawing_cache import scoped, thawed
from ...application.drawing_views import write_view_dxf
from ...application.plate_drawings import _make_dwg_copies, export_plate_drawings
from .drawing_paper import write_sheets


def _selected(view, extent):
    kind = view.projection
    if kind in ("isometric", "plan", "front", "right"):
        return kind in extent.views
    if kind == "section":
        return extent.sections
    if kind == "plate":
        return extent.plate_profiles
    return True


@scoped
def publish(destination, snapshot, views, *, part_id=None, quantity=1, density=None, converter=None, backend="oda",
            elevation_datum="support_base", include_plate_intersections=True, representation="solid",
            thickness_sides="auto", extent=None):
    from ...application.drawing_extent import DrawingExtent
    extent = DrawingExtent() if extent is None else extent
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Choose a new drawing folder. {destination} already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".anyfem-drawings-") as tmp:
        package = Path(tmp)/"issue"
        # Queued sheets outside the extent are neither drawn nor printed.
        views = [v for v in views if _selected(v, extent)]
        printable = list(views)
        index = None
        if part_id is not None:
            from ...application.plate_view import build_plate_view
            export_plate_drawings(snapshot, part_id, package, quantity=quantity,
                                  density_kg_m3=density, dwg_converter=converter, dwg_backend=backend,
                                  elevation_datum=elevation_datum,
                                  include_plate_intersections=include_plate_intersections,
                                  representation=representation, thickness_sides=thickness_sides,
                                  extent=extent)
            index = package/"Plate-index.csv" if extent.index_and_takeoff else None
            project = thawed(snapshot)
            g = project.geometry
            for sid in (g.parts[part_id].sheet_ids if extent.plate_profiles else ()):
                face = g.face_uses[g.sheets[sid].face_use_ids[0]].face_id
                printable.append(build_plate_view(project,face,include_plate_intersections=include_plate_intersections,
                                                  thickness_sides=thickness_sides,representation=representation))
        cad = package/"views"
        if views:
            cad.mkdir(parents=True)
        for i, view in enumerate(views,1):
            write_view_dxf(view,cad/f"View-{i:02d}.dxf")
        if converter and views:
            _make_dwg_copies(converter,cad,package/"DWG-views",backend=backend)
        if not printable and index is None:
            raise ValueError("The selected extent leaves no view or plate sheet to print.")
        write_sheets(package/"Drawings.pdf",printable,index_path=index,quantity=quantity)
        basis_file = package/"Drawing-basis.json"
        basis = json.loads(basis_file.read_text(encoding="utf-8")) if basis_file.exists() else {
            "project_hash":canonical_hash(snapshot.document), "profile_units":"mm",
        }
        basis.setdefault("representation", representation)
        if not extent.is_default:
            basis["extent"] = extent.to_dict()
        basis["views"] = [{"title":v.title, "drawing":f"views/View-{n:02d}.dxf",
                           "notes":list(v.notes),
                           "intersection_reference_paths":sum(p.layer=="PLATE_INTERSECTIONS" for p in v.paths)}
                          for n,v in enumerate(views,1)]
        if converter:
            basis["dwg_writer"] = "OpenCAD Studio" if backend=="studio" else "ODA File Converter"
        basis_file.write_text(json.dumps(basis,indent=2)+"\n", encoding="utf-8")
        package.rename(destination)
    return destination
