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


INDEX_COLUMNS = (
    "Plate", "Sheet ID", "Material", "Thickness mm", "Holes", "Nominal density (kg/m3)",
    "Net area (m2)", "Net mass per plate (kg)", "Quantity required", "Extended net mass (kg)",
    "Lower edge Z above support base (mm)", "Upper edge Z above support base (mm)", "Profile file",
)
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


def export_plate_drawings(snapshot: ProjectSnapshot, part_id: int, destination: str | Path, *,
                          quantity: int = 1, density_kg_m3: float | None = None,
                          dwg_converter: str | Path | None = None, dwg_backend: str = "oda") -> Path:
    """Write a new directory atomically. Dimensions are mm; mass excludes contingency.

    A density override applies only to this takeoff. Omitting it uses each plate's
    assigned material density. Z is measured from the lowest material edge of the Part.
    """
    from .plate_profiles import build_plate_profiles

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
    profiles = tuple(build_plate_profiles(project, part_id))
    if not profiles:
        raise ValueError(f"Part {part_id} has no physical plates")
    base_z = min(p.z_min_m for p in profiles)
    scope = part.name or f"Part {part.id}"
    index = []
    stock = defaultdict(list)
    marks = set()
    filenames = set()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".plate-drawings-") as temporary:
        package = Path(temporary) / "package"
        drawing_dir = package / "profiles"
        drawing_dir.mkdir(parents=True)
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
            mass = plate.area_m2 * plate.thickness_m * density
            stock[plate.thickness_m].append((plate.area_m2, mass, density))
            plate.write_dxf(drawing_dir / filename)
            index.append((plate.mark, plate.sheet_id, plate.material, f"{plate.thickness_m*1000:g}",
                          plate.hole_count, f"{density:.12g}", f"{plate.area_m2:.9f}", f"{mass:.6f}",
                          quantity, f"{mass*quantity:.6f}", f"{(plate.z_min_m-base_z)*1000:.3f}",
                          f"{(plate.z_max_m-base_z)*1000:.3f}", f"profiles/{filename}"))
        _csv(package / "Plate-index.csv", INDEX_COLUMNS, index)
        rows = [_takeoff_row(scope, f"{gauge*1000:g}", quantity, entries)
                for gauge, entries in sorted(stock.items())]
        rows.append(_takeoff_row(scope, "All", quantity, [pair for entries in stock.values() for pair in entries]))
        _csv(package / "Weight-takeoff.csv", TAKEOFF_COLUMNS, rows)
        if dwg_converter is not None:
            _make_dwg_copies(dwg_converter, drawing_dir, package / "DWG", backend=dwg_backend)
        manifest = {
            "project": project.name, "document_id": str(project.document_id),
            "project_hash": canonical_hash(snapshot.document), "part_id": part.id,
            "part": scope, "quantity": quantity, "profile_units": "mm", "base_global_z_m": base_z,
            "density_basis": "assigned materials" if density_kg_m3 is None else "takeoff override",
            "override_kg_m3": density_kg_m3, "mass_basis": "net cut area x nominal thickness x density",
            "formats": ["DXF", "DWG AC1032"] if dwg_converter is not None else ["DXF"],
        }
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
    parser.add_argument("--dwg-converter", type=Path, help="ODA File Converter executable for optional DWG copies")
    args = parser.parse_args(argv)
    try:
        snapshot = DocumentSession(load_project(args.project)).snapshot()
        output = export_plate_drawings(snapshot, args.part, args.destination,
                                       quantity=args.quantity, density_kg_m3=args.density,
                                       dwg_converter=args.dwg_converter)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")
    print(output)


if __name__ == "__main__":
    main()
