"""Export 1:1 planar plate profiles and a net stock takeoff for one structural Part."""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from anyfem.document import DocumentSession, ProjectSnapshot, canonical_hash
from anyfem.io import load_project

from .drawing_cache import scoped


INDEX_COLUMNS = (
    "Plate", "Sheet ID", "Material", "Thickness mm", "Holes", "Nominal density (kg/m3)",
    "Net area (m2)", "Net mass per plate (kg)", "Quantity required", "Extended net mass (kg)",
    "Lower edge Z above support base (mm)", "Upper edge Z above support base (mm)", "Profile file",
)
# Appended to the plate index whenever thickness sides are resolved (not for
# the historical all-centre request, whose files stay as they were).
SIDE_COLUMNS = ("Thickness side", "Side source")
SIDE_RULE = ("auto: a plate whose same-Part neighbours (sharing a topological vertex or edge) lie only on +n "
             "is front (thickness 0..t along +n), only on -n back (-t..0), otherwise centre (-t/2..t/2); "
             "+n is the planar drawing normal (largest world component positive) or the outward radial "
             "direction of a curved strip")
CURVED_MASS_BASIS = ("curved strips: reference area x (R + band middle)/R x nominal thickness x density, "
                     "i.e. the mid-thickness surface area")
TAKEOFF_COLUMNS = (
    "Scope", "Thickness (mm)", "Number of supports", "Unique plate marks", "Plates required",
    "Net area per support (m2)", "Net mass per support (kg)", "Extended net area (m2)",
    "Extended net mass (kg)", "Nominal density (kg/m3)",
)


def _csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)


def _filename(mark, sheet_id):
    # Preserve the actual shop mark in the index even if it cannot be a filename.
    reserved = re.fullmatch(r"CON|NUL|PRN|AUX|COM[1-9]|LPT[1-9]", mark.split(".")[0], re.I)
    if (not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9 ._-]{0,119}", mark)
            or mark.endswith((".", " ")) or reserved):
        return f"sheet-{sheet_id}.dxf"
    return f"{mark}.dxf"


def _takeoff_row(scope, gauge, quantity, entries):
    area = math.fsum(a for a, _, _ in entries)
    mass = math.fsum(m for _, m, _ in entries)
    densities = {d for _, _, d in entries}
    basis = f"{densities.pop():.12g}" if len(densities) == 1 else "Mixed (see plate index)"
    return (scope, gauge, quantity, len(entries), quantity * len(entries),
            f"{area:.9f}", f"{mass:.6f}", f"{quantity * area:.9f}", f"{quantity * mass:.6f}", basis)


def _make_dwg_copies(executable, source, target, *, backend="oda"):
    tool = Path(executable).resolve(strict=True)
    target.mkdir()
    inputs = sorted(source.glob("*.dxf"))
    if backend=="studio":
        name = "OpenCAD Studio"
        commands = [[str(tool),"--export",str(f),str(target/(f.stem+".dwg")),"--target-version","2018"]
                    for f in inputs]
    elif backend=="oda":
        name = "ODA File Converter"
        commands = [[str(tool), str(source), str(target), "ACAD2018", "DWG", "0", "0", "*.dxf"]]
    else:
        raise ValueError(f"Unknown DWG converter {backend}")
    try:
        for command in commands:
            result = subprocess.run(command, cwd=target, capture_output=True, timeout=120,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                detail = result.stderr.decode(errors="replace").strip()[-600:]
                raise ValueError(f"{name} returned {result.returncode} during DWG conversion. {detail}")
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"{name} timed out while writing DWG files") from error
    expected = {file.stem for file in inputs}
    files = list(target.glob("*.dwg"))
    if expected != {file.stem for file in files}:
        raise ValueError(f"{name} wrote {len(files)} of {len(expected)} expected DWG files.")
    for file in files:
        with file.open("rb") as stream:
            if stream.read(6) != b"AC1032":
                raise ValueError(f"Invalid AutoCAD 2018 DWG output for {file.stem}")


@scoped
def export_plate_drawings(snapshot: ProjectSnapshot, part_id: int, destination: str | Path, *,
                          quantity: int = 1, density_kg_m3: float | None = None,
                          dwg_converter: str | Path | None = None, dwg_backend: str = "oda",
                          elevation_datum: str = "support_base", include_plate_intersections: bool = True,
                          representation: str = "solid", thickness_sides="auto", extent=None,
                          write_assembly_views: bool = False) -> Path:
    """Write a new directory atomically. Dimensions are mm; mass excludes contingency.

    A density override applies only to this takeoff. Omitting it uses each plate's
    assigned material density. Elevations use the selected drawing datum.
    ``thickness_sides`` (``auto``, ``centre`` or a Sheet-to-side mapping) places
    plate thickness for elevations and references; ``representation`` selects
    solid or ``midsurface_2d`` reference drawings. Profiles and mass do not
    depend on either. ``extent`` (a :class:`DrawingExtent`) leaves out the
    per-plate profiles (and their DWG copies) or the index and takeoff CSVs
    when deselected; the default writes everything. A non-default extent is
    recorded in the Drawing-basis manifest. ``write_assembly_views=True`` also
    writes the extent's assembly views as ``views/View-NN.dxf`` (listed in the
    manifest ``views``); the default leaves them to the caller. With it, an
    extent that writes nothing at all is refused before anything is created.
    """
    from .drawing_extent import DrawingExtent
    from .plate_profiles import build_plate_profiles
    from .thickness_side import check_representation, resolve_sides
    check_representation(representation)
    extent = DrawingExtent() if extent is None else extent
    if not isinstance(extent, DrawingExtent):
        raise ValueError("extent must be a DrawingExtent")
    if write_assembly_views and not (extent.views or extent.plate_profiles or extent.index_and_takeoff):
        raise ValueError("The selected extent writes nothing: select assembly views, plate profiles "
                         "or the index and takeoff (sections need the workbench queue)")

    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("Quantity must be a positive integer")
    if density_kg_m3 is not None and (not math.isfinite(density_kg_m3) or density_kg_m3 <= 0):
        raise ValueError("Takeoff density must be positive and finite")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Choose a new export folder: {destination}")
    project = snapshot.thaw()
    part = project.geometry.parts.get(part_id)
    if part is None:
        raise ValueError(f"No structural Part with ID {part_id}")
    if part.member_ids:
        raise ValueError("This plate-only takeoff requires a Part without structural members")
    legacy = thickness_sides is None or (isinstance(thickness_sides, str) and thickness_sides == "centre")
    sides = resolve_sides(project, thickness_sides)
    if legacy:
        table = {}
    else:
        from .thickness_side import normal_label, resolve_thickness_sides
        table = resolve_thickness_sides(
            project, part_id, None if isinstance(thickness_sides, str) else thickness_sides)
    profiles = tuple(build_plate_profiles(project, part_id, thickness_sides=sides))
    if not profiles:
        raise ValueError(f"Part {part_id} has no physical plates")
    from .drawing_datum import elevation_origin
    base_z = elevation_origin(profiles, elevation_datum)
    index_columns = INDEX_COLUMNS
    if elevation_datum == "global":
        index_columns = tuple(
            {"Lower edge Z above support base (mm)": "Lower global Z (mm)",
             "Upper edge Z above support base (mm)": "Upper global Z (mm)"}.get(name, name)
            for name in INDEX_COLUMNS
        )
    if not legacy:
        index_columns += SIDE_COLUMNS
    scope = part.name or f"Part {part.id}"
    index = []
    stock = defaultdict(list)
    marks = set()
    filenames = set()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".plate-drawings-") as temporary:
        package = Path(temporary) / "package"
        drawing_dir = package / "profiles"
        package.mkdir(parents=True)
        if extent.plate_profiles:
            drawing_dir.mkdir()
        for plate in profiles:
            filename = _filename(plate.mark, plate.sheet_id)
            if plate.mark.casefold() in marks or filename.casefold() in filenames:
                raise ValueError(f"Ambiguous plate mark or profile filename: {plate.mark}")
            marks.add(plate.mark.casefold())
            filenames.add(filename.casefold())
            material = project.materials[plate.material]
            density = material.density if density_kg_m3 is None else density_kg_m3
            if not math.isfinite(density) or density <= 0:
                raise ValueError(f"Plate {plate.mark}: specify a positive takeoff density for {plate.material}")
            mass = plate.mass_area_m2 * plate.thickness_m * density
            stock[plate.thickness_m].append((plate.area_m2, mass, density))
            if extent.plate_profiles:
                plate.write_dxf(drawing_dir / filename)
            if extent.plate_profiles and include_plate_intersections:
                from .plate_view import build_plate_view
                from .drawing_views import write_view_dxf
                sheet = project.geometry.sheets[plate.sheet_id]
                face = project.geometry.face_uses[sheet.face_use_ids[0]].face_id
                view = build_plate_view(project, face, include_stiffeners=False,
                                        include_plate_intersections=True,
                                        thickness_sides="centre" if legacy else sides,
                                        representation=representation)
                write_view_dxf(view, drawing_dir / filename, preserve_contours=True)
            row = (plate.mark, plate.sheet_id, plate.material, f"{plate.thickness_m*1000:g}",
                   plate.hole_count, f"{density:.12g}", f"{plate.area_m2:.9f}", f"{mass:.6f}",
                   quantity, f"{mass*quantity:.6f}", f"{(plate.z_min_m-base_z)*1000:.3f}",
                   f"{(plate.z_max_m-base_z)*1000:.3f}",
                   f"profiles/{filename}" if extent.plate_profiles else "")
            if not legacy:
                _, source, reason = table[plate.sheet_id]
                row += (plate.side_text, f"{source}: {reason}")
            index.append(row)
        if extent.index_and_takeoff:
            _csv(package / "Plate-index.csv", index_columns, index)
            rows = [_takeoff_row(scope, f"{gauge*1000:g}", quantity, entries)
                    for gauge, entries in sorted(stock.items())]
            rows.append(_takeoff_row(scope, "All", quantity, [pair for entries in stock.values() for pair in entries]))
            _csv(package / "Weight-takeoff.csv", TAKEOFF_COLUMNS, rows)
        if dwg_converter is not None and extent.plate_profiles:
            _make_dwg_copies(dwg_converter, drawing_dir, package / "DWG", backend=dwg_backend)
        view_entries = None
        if write_assembly_views and extent.views:
            from .drawing_assembly import assembly_views
            from .drawing_views import write_view_dxf
            assembled = assembly_views(project, part_id, quantity, density_kg_m3, views=extent.views,
                                       elevation_datum=elevation_datum, thickness_sides=thickness_sides,
                                       representation=representation)
            (package / "views").mkdir()
            view_entries = []
            for number, view in enumerate(assembled, 1):
                write_view_dxf(view, package / "views" / f"View-{number:02d}.dxf")
                view_entries.append({"title": view.title, "drawing": f"views/View-{number:02d}.dxf",
                                     "notes": list(view.notes),
                                     "intersection_reference_paths":
                                         sum(p.layer == "PLATE_INTERSECTIONS" for p in view.paths)})
        manifest = {
            "project": project.name, "document_id": str(project.document_id),
            "project_hash": canonical_hash(snapshot.document), "part_id": part.id,
            "part": scope, "quantity": quantity, "profile_units": "mm", "base_global_z_m": base_z,
            "elevation_datum": elevation_datum,
            "profile_intersection_references": include_plate_intersections,
            "support_base_global_z_m": min(plate.z_min_m for plate in profiles),
            "density_basis": "assigned materials" if density_kg_m3 is None else "takeoff override",
            "override_kg_m3": density_kg_m3, "mass_basis": "net cut area x nominal thickness x density",
            "formats": ["DXF", "DWG AC1032"] if dwg_converter is not None else ["DXF"],
        }
        if not extent.is_default:
            manifest["extent"] = extent.to_dict()
        if view_entries is not None:
            manifest["views"] = view_entries
        if not legacy or representation != "solid":
            manifest["representation"] = representation
        if not legacy:
            manifest["thickness_side_rule"] = SIDE_RULE
            manifest["thickness_sides"] = [
                {"plate": plate.mark, "sheet_id": plate.sheet_id, "side": plate.thickness_side,
                 "normal": "outward radial" if plate._strip is not None else normal_label(plate.normal),
                 "band_mm": [round(1000*b, 9) for b in plate.band_m],
                 "source": table[plate.sheet_id][1], "reason": table[plate.sheet_id][2]}
                for plate in profiles]
        if any(plate._strip is not None for plate in profiles):
            manifest["curved_mass_basis"] = CURVED_MASS_BASIS
        (package / "Drawing-basis.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        package.rename(destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="native .anyfem project")
    parser.add_argument("destination", type=Path, help="new output directory")
    parser.add_argument("--part", type=int, required=True, help="structural Part ID")
    parser.add_argument("--quantity", type=int, default=1, help="number of identical assemblies")
    parser.add_argument("--density", type=float, help="takeoff density override, kg/m³")
    parser.add_argument("--elevation-datum", choices=("support_base", "global"), default="support_base",
                        help="support_base sets the lowest material edge to Z=0; global uses model elevations")
    parser.add_argument("--no-plate-intersections", action="store_false", dest="plate_intersections",
                        help="omit intersection reference marks from profile drawings")
    parser.add_argument("--representation", choices=("solid", "midsurface_2d"), default="solid",
                        help="solid plates, or 2D reference surfaces without thickness")
    parser.add_argument("--thickness-side", action="append", default=[], metavar="NAME=SIDE",
                        help="per-plate side override (Sheet name or ID; auto, centre, front or back); repeatable")
    parser.add_argument("--views", default=None, metavar="LIST",
                        help="also write these assembly views as DXF (views/): all, or a comma list of "
                             "iso, plan, front, right; omitted, no assembly views are written")
    parser.add_argument("--no-sections", action="store_false", dest="sections",
                        help="leave out the requested section views")
    parser.add_argument("--no-plate-profiles", action="store_false", dest="plate_profiles",
                        help="leave out the per-plate profile drawings (DXF/DWG)")
    parser.add_argument("--no-index", action="store_false", dest="index_and_takeoff",
                        help="leave out the plate index and weight takeoff")
    parser.add_argument("--dwg-converter", type=Path, help="ODA File Converter executable for optional DWG copies")
    args = parser.parse_args(argv)
    overrides = {}
    for item in args.thickness_side:
        name, separator, side = item.rpartition("=")
        if not separator or not name or side not in ("auto", "centre", "front", "back"):
            parser.error(f"--thickness-side expects NAME=auto|centre|front|back, not {item!r}")
        overrides[name] = side
    from .drawing_extent import DrawingExtent, parse_views
    try:
        extent = DrawingExtent(views=parse_views("all" if args.views is None else args.views),
                               sections=args.sections,
                               plate_profiles=args.plate_profiles, index_and_takeoff=args.index_and_takeoff)
        if args.views is None and not (args.plate_profiles or args.index_and_takeoff):
            raise ValueError("Nothing to generate: give --views, or keep the plate profiles or the index")
    except ValueError as error:
        parser.error(str(error))
    try:
        snapshot = DocumentSession(load_project(args.project)).snapshot()
        output = export_plate_drawings(snapshot, args.part, args.destination,
                                       quantity=args.quantity, density_kg_m3=args.density,
                                       elevation_datum=args.elevation_datum,
                                       include_plate_intersections=args.plate_intersections,
                                       dwg_converter=args.dwg_converter, representation=args.representation,
                                       thickness_sides=overrides or "auto", extent=extent,
                                       write_assembly_views=args.views is not None)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    print(output)


if __name__ == "__main__":
    main()
