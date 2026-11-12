"""Publish the reviewed drawing set together into a new, replace-safe directory."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json

from ...document import canonical_hash
from ...application.drawing_views import write_view_dxf
from ...application.plate_drawings import _make_dwg_copies, export_plate_drawings
from .drawing_paper import write_sheets


def publish(destination, snapshot, views, *, part_id=None, quantity=1, density=None, converter=None, backend="oda"):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Choose a new drawing folder. {destination} already exists.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".anyfem-drawings-") as tmp:
        package = Path(tmp)/"issue"
        printable = list(views)
        index = None
        if part_id is not None:
            from ...application.plate_view import build_plate_view
            export_plate_drawings(snapshot, part_id, package, quantity=quantity,
                                  density_kg_m3=density, dwg_converter=converter, dwg_backend=backend)
            index = package/"Plate-index.csv"
            project = snapshot.thaw()
            g = project.geometry
            for sid in g.parts[part_id].sheet_ids:
                face = g.face_uses[g.sheets[sid].face_use_ids[0]].face_id
                printable.append(build_plate_view(project,face))
        cad = package/"views"
        cad.mkdir(parents=True)
        for i, view in enumerate(views,1):
            write_view_dxf(view,cad/f"View-{i:02d}.dxf")
        if converter:
            _make_dwg_copies(converter,cad,package/"DWG-views",backend=backend)
        write_sheets(package/"Drawings.pdf",printable,index_path=index,quantity=quantity)
        basis_file = package/"Drawing-basis.json"
        basis = json.loads(basis_file.read_text(encoding="utf-8")) if basis_file.exists() else {
            "project_hash":canonical_hash(snapshot.document), "profile_units":"mm",
        }
        basis["views"] = [{"title":v.title, "drawing":f"views/View-{n:02d}.dxf",
                           "notes":list(v.notes)} for n,v in enumerate(views,1)]
        if converter:
            basis["dwg_writer"] = "OpenCAD Studio" if backend=="studio" else "ODA File Converter"
        basis_file.write_text(json.dumps(basis,indent=2)+"\n", encoding="utf-8")
        package.rename(destination)
    return destination
