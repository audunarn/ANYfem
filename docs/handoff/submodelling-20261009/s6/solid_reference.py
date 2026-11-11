"""S6 shell-to-solid loaded study against an independent 3D CalculiX reference.

One executable, self-contained study builder / reader.  NOTHING here launches a subprocess: the
coordinator runs CalculiX on the decks that ``build`` writes (the exact argv is recorded in
``manifest.json`` as data) and then runs ``read``.

    python solid_reference.py --inventory [--preset smoke|formal]
    python solid_reference.py build --out DIR [--preset smoke|formal] [--ccx-exe PATH]
    python solid_reference.py read  --out DIR

Boundary value problem (the SAME one for the ANYfem solid child and for the CCX reference)
-----------------------------------------------------------------------------------------
* Parent: a real, solved and saved ANYfem linear-Q4 plate (1 m x 1 m, 3 x 3 faces, t = 10 mm, steel S355
  from ``anyfem.steel``), uniform pressure on every face, the supports/line loads of the submodel test
  parent.  It is saved with ``save_project`` and reopened with ``load_project``.
* Detail: planar interior rectangle [0.4, 0.6]^2 inside the centre face, centred through the thickness,
  z in [-t/2, +t/2].  Public ``cut_solid_submodel`` -> ``add_submodel`` -> ``run_submodel`` -> save -> reopen.
* Load: the parent face pressure ``p * n`` on the NAMED skin (``pressure_skin='end'``), nothing else.
* Lateral boundary: all four side faces carry the parent's displacement field (see A3).
* Reference: regular-grid C3D8 / C3D20 box on the same footprint, three preregistered refinements.  Lateral
  face nodes are fully prescribed with an independently evaluated parent field; the pressure skin interior is
  free; the skin load is applied as explicit consistent signed nodal forces.

Assumptions (named, also written to the inventory and the report)
-----------------------------------------------------------------
A1  SI units (m, N, Pa); E and nu are those of the parent's section material; isotropic, linear, small strain.
A2  The detail is centred on the shell reference plane (reference_offset = 0, as the cut enforces).
A3  Shell kinematics: u(x) = sum N_i u_i + (sum N_i theta_i) x (x - p), p = sum N_i X_i, Q4 bilinear N_i, nodal
    rotation = global rotation vector (DOFs 4..6).  This is the displacement law the child driver documents
    (``anysolver.solid_submodel``); it is re-implemented here (own shape functions, own Newton inverse mapping).
A4  "end" skin = the box face the saved child mesh names ``end``; its plane is read from the saved mesh.
A5  The traction is ``p * n`` with n the parent element normal from the diagonals' cross product and p the
    pressure assembled on the parent element (taken from the reopened parent, not from the child).
A6  Pressure on lateral-prescribed skin nodes is NOT put into the deck (it would be a load on a prescribed DOF);
    the reference interface resultant is  sum(RF) - sum(withheld skin load)  = K u - f on the prescribed DOFs,
    the same quantity the child reports (``r = K q - f``).
A7  Stress is the volume average over the central region |x-xc|<0.05, |y-yc|<0.05, full thickness.  For C3D8
    (affine bricks, 2x2x2 Gauss, equal weights) it is the mean of the element-mean integration-point stresses.
    For C3D20 the 27-point CCX ordering is not asserted, so no C3D20 stress is reported (typed ``not_computed``).
A8  Child centre values come from the child's nodal displacement table at the mesh vertex at the centre.
A9  The study compares and reports; it sets no scientific acceptance threshold.  Only integrity tolerances
    (deck, boundary-transfer repetition, reference equilibrium, region volume) are enforced and named below.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import math
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

STUDY_ID = "s6-shell-to-solid-loaded-reference"
SCHEMA = 1

# ----------------------------------------------------------------------
# preregistered study definition
# ----------------------------------------------------------------------
LINES = (0.0, 0.3, 0.7, 1.0)  # parent face grid (m)
PARENT_SIZE = 0.1
PRESSURE = 1.0e4
THICKNESS = 0.01
FOOTPRINT = ((0.4, 0.4, 0.0), (0.6, 0.4, 0.0), (0.6, 0.6, 0.0), (0.4, 0.6, 0.0))
CENTRE = (0.5, 0.5)
DETAIL_BOX = (0.4, 0.4, -THICKNESS / 2.0, 0.6, 0.6, THICKNESS / 2.0)  # x0 y0 z0 x1 y1 z1
REGION_HALF = 0.05
VOIGT = ((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))  # CCX order: xx yy zz xy xz yz
PRESETS = {
    "smoke": {"levels": ((4, 2),), "orders": (1,)},
    "formal": {"levels": ((4, 2), (8, 4), (16, 8)), "orders": (1, 2)},
}
ORDER_ELEMENT = {1: "C3D8", 2: "C3D20"}
ELEMENT_NODES = {"C3D8": 8, "C3D20": 20}
INTEGRITY = {  # enforced integrity tolerances only (A9)
    "boundary_transfer_rel": 1.0e-9,
    "reference_equilibrium_rel": 1.0e-6,
    "region_volume_rel": 1.0e-9,
    "deck_value_rel": 1.0e-12,
}
ASSUMPTIONS = (
    "A1 SI units; E, nu from the parent section material; linear isotropic small strain",
    "A2 detail centred on the shell reference plane",
    "A3 u = sum N_i u_i + (sum N_i theta_i) x (x - p), Q4 bilinear, global rotation vector; own inverse mapping",
    "A4 end skin plane read from the saved child mesh group 'end'",
    "A5 traction p*n from the reopened parent's element normal and assembled pressure",
    "A6 no skin load on prescribed nodes in the deck; reference interface force = sum(RF) - sum(withheld load)",
    "A7 region-average stress over |x-xc|<0.05,|y-yc|<0.05 full thickness; C3D8 only (C3D20 27-pt order not asserted)",
    "A8 child centre values from nodal displacement at the centre mesh vertex",
    "A9 no scientific threshold set; only integrity tolerances are enforced",
    "A10 reference equilibrium keeps the 1e-6 target; every printed CCX entry carries a half-unit-of-last-digit bound, "
    "so force and moment residuals are classified pass / fail / unresolved and the observed value is always reported",
    "A11 every finest-level child comparison (P1 and P2) takes its stress from the C3D8 finest region average",
)
DATUM = (CENTRE[0], CENTRE[1], 0.0)  # common moment datum: detail centre on the reference plane
LENGTH_SCALE = 0.2  # footprint side (m): physical scale that turns a force into a moment
DECK_REPR_REL = 5.0e-13  # half-unit of the 13th significant digit written by ``_g``
EDGES20 = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
CORNERS = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))
TOP_FACE = {"C3D8": (4, 5, 6, 7), "C3D20": (4, 5, 6, 7, 12, 13, 14, 15)}
BOTTOM_FACE = {"C3D8": (0, 1, 2, 3), "C3D20": (0, 1, 2, 3, 8, 9, 10, 11)}


class StudyError(Exception):
    """A typed failure: ``code`` is stable, ``details`` is JSON-safe."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "failed", "code": self.code, "message": self.message, "details": self.details}


def _g(value: float) -> str:
    # CCX free-format numeric fields are limited to 20 characters.  Twelve
    # fractional digits in scientific notation retain 13 significant digits.
    return format(float(value), ".12e")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def deck_name(element: str, n: int, layers: int) -> str:
    return f"ref_{element}_{n}x{n}x{layers}"


# ----------------------------------------------------------------------
# independent reference mesh (regular grid) and deck
# ----------------------------------------------------------------------
@dataclass
class RefMesh:
    element: str
    n: int
    layers: int
    m: int
    nx: int
    ny: int
    nz: int
    box: Tuple[float, ...]
    nodes: Dict[int, Tuple[float, float, float]]
    ijk: Dict[int, Tuple[int, int, int]]
    elements: List[Tuple[int, Tuple[int, ...], Tuple[int, int, int]]]  # (id, nodes, (ex, ey, ez))

    def nid(self, i: int, j: int, k: int) -> int:
        return 1 + i + (self.nx + 1) * (j + (self.ny + 1) * k)

    def lateral(self) -> List[int]:
        return sorted(n for n, (i, j, _k) in self.ijk.items() if i in (0, self.nx) or j in (0, self.ny))


def reference_mesh(element: str, n: int, layers: int, box: Sequence[float] = DETAIL_BOX) -> RefMesh:
    if element not in ELEMENT_NODES:
        raise StudyError("ELEMENT_UNSUPPORTED", f"{element!r} is not one of {sorted(ELEMENT_NODES)}")
    if n < 1 or layers < 1:
        raise StudyError("LEVEL_INVALID", "divisions and layers must be positive")
    m = 1 if element == "C3D8" else 2
    nx = ny = n * m
    nz = layers * m
    mesh = RefMesh(element, n, layers, m, nx, ny, nz, tuple(float(b) for b in box), {}, {}, [])
    x0, y0, z0, x1, y1, z1 = mesh.box
    eid = 0
    for ex in range(n):
        for ey in range(n):
            for ez in range(layers):
                base = (ex * m, ey * m, ez * m)
                idx = [(base[0] + c[0] * m, base[1] + c[1] * m, base[2] + c[2] * m) for c in CORNERS]
                if element == "C3D20":
                    for a, b in EDGES20:
                        idx.append(tuple((p + q) // 2 for p, q in zip(idx[a], idx[b])))
                ids = []
                for (i, j, k) in idx:
                    node = mesh.nid(i, j, k)
                    if node not in mesh.nodes:
                        mesh.nodes[node] = (x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny, z0 + (z1 - z0) * k / nz)
                        mesh.ijk[node] = (i, j, k)
                    ids.append(node)
                eid += 1
                mesh.elements.append((eid, tuple(ids), (ex, ey, ez)))
    return mesh


def hex_corner_jacobians(corner_xyz: np.ndarray) -> np.ndarray:
    """det J of the trilinear map of 8 corner points, evaluated at the 8 corners (positive = right-handed)."""

    nat = np.array([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], float)
    out = []
    for r, s, t in nat:
        d = np.zeros((8, 3))
        for a, (ra, sa, ta) in enumerate(nat):
            d[a] = 0.125 * np.array([ra * (1 + s * sa) * (1 + t * ta), sa * (1 + r * ra) * (1 + t * ta),
                                     ta * (1 + r * ra) * (1 + s * sa)])
        out.append(float(np.linalg.det(d.T @ np.asarray(corner_xyz, float))))
    return np.asarray(out)


def skin_side(z_end: float) -> str:
    return "top" if z_end > 0.0 else "bottom"


def skin_forces(mesh: RefMesh, side: str, traction: Sequence[float]) -> Dict[int, Tuple[float, float, float]]:
    """Consistent nodal forces of a uniform traction (force per area) on one skin; signed, summing to t * A."""

    if side not in ("top", "bottom"):
        raise StudyError("SKIN_INVALID", f"side {side!r}")
    layer = mesh.layers - 1 if side == "top" else 0
    face = (TOP_FACE if side == "top" else BOTTOM_FACE)[mesh.element]
    t = np.asarray(traction, float).reshape(3)
    fractions = [0.25] * 4 if mesh.element == "C3D8" else [-1.0 / 12.0] * 4 + [1.0 / 3.0] * 4
    forces: Dict[int, np.ndarray] = {}
    for _eid, nodes, (_ex, _ey, ez) in mesh.elements:
        if ez != layer:
            continue
        c = np.array([mesh.nodes[nodes[i]] for i in face[:4]])
        area = 0.5 * float(np.linalg.norm(np.cross(c[2] - c[0], c[3] - c[1])))
        for local, fraction in zip(face, fractions):
            forces[nodes[local]] = forces.get(nodes[local], np.zeros(3)) + fraction * area * t
    return {k: tuple(float(c) for c in v) for k, v in sorted(forces.items())}


def split_loads(forces: Mapping[int, Sequence[float]], prescribed: Sequence[int]):
    held = set(prescribed)
    applied = {k: v for k, v in forces.items() if k not in held}
    withheld = {k: v for k, v in forces.items() if k in held}
    return applied, withheld


def deck_text(mesh: RefMesh, young: float, poisson: float, prescribed: Mapping[int, Sequence[float]],
              loads: Mapping[int, Sequence[float]], title: str) -> str:
    out = ["*HEADING", title, "*NODE, NSET=NALL"]
    out += [f"{n}, {_g(x)}, {_g(y)}, {_g(z)}" for n, (x, y, z) in sorted(mesh.nodes.items())]
    out.append(f"*ELEMENT, TYPE={mesh.element}, ELSET=EALL")
    for eid, nodes, _ in mesh.elements:
        if len(nodes) == 8:
            out.append(f"{eid}, " + ", ".join(map(str, nodes)))
        else:
            out.append(f"{eid}, " + ", ".join(map(str, nodes[:15])) + ",")
            out.append(", ".join(map(str, nodes[15:])))
    ids = sorted(prescribed)
    out.append("*NSET, NSET=NPRESC")
    out += [", ".join(map(str, ids[i:i + 8])) for i in range(0, len(ids), 8)]
    out += ["*MATERIAL, NAME=MAT", "*ELASTIC", f"{_g(young)}, {_g(poisson)}", "*SOLID SECTION, ELSET=EALL, MATERIAL=MAT",
            "*STEP", "*STATIC", "*BOUNDARY"]
    for n in ids:
        out += [f"{n}, {d + 1}, {d + 1}, {_g(prescribed[n][d])}" for d in range(3)]
    out.append("*CLOAD")
    for n in sorted(loads):
        out += [f"{n}, {d + 1}, {_g(loads[n][d])}" for d in range(3) if loads[n][d] != 0.0]
    out += ["*NODE PRINT, NSET=NALL", "U", "*NODE PRINT, NSET=NPRESC, TOTALS=YES", "RF"]
    if mesh.element == "C3D8":
        out += ["*EL PRINT, ELSET=EALL", "S"]
    out.append("*END STEP")
    return "\n".join(out) + "\n"


def parse_deck(text: str) -> Dict[str, Any]:
    """Read back nodes, elements, boundary values and concentrated loads of a deck written by ``deck_text``."""

    nodes: Dict[int, Tuple[float, ...]] = {}
    elements: Dict[int, Tuple[int, ...]] = {}
    boundary: Dict[int, List[float]] = {}
    cload: Dict[int, List[float]] = {}
    material, kind, pending, element_type = None, None, [], None
    prints: List[Dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("*"):
            head = line.split(",")[0].upper()
            kind = head
            pending = []
            if head == "*NODE PRINT":
                nset = re.search(r"NSET\s*=\s*(\w+)", line, re.I)
                totals = re.search(r"TOTALS\s*=\s*(\w+)", line, re.I)
                prints.append({"nset": nset.group(1).upper() if nset else None,
                               "totals": totals.group(1).upper() if totals else "NO", "variables": []})
            if head == "*ELEMENT":
                match = re.search(r"TYPE\s*=\s*(\w+)", line, re.I)
                element_type = match.group(1).upper() if match else None
            continue
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if kind == "*NODE":
            nodes[int(parts[0])] = tuple(float(p) for p in parts[1:4])
        elif kind == "*ELEMENT":
            pending += [int(p) for p in parts]
            expected = 1 + ELEMENT_NODES.get(element_type or "", -1)
            if len(pending) == expected:
                elements[pending[0]] = tuple(pending[1:])
                pending = []
        elif kind == "*BOUNDARY":
            boundary.setdefault(int(parts[0]), [0.0, 0.0, 0.0])[int(parts[1]) - 1] = float(parts[3])
        elif kind == "*CLOAD":
            cload.setdefault(int(parts[0]), [0.0, 0.0, 0.0])[int(parts[1]) - 1] = float(parts[2])
        elif kind == "*ELASTIC":
            material = (float(parts[0]), float(parts[1]))
        elif kind == "*NODE PRINT" and prints:
            prints[-1]["variables"] += [p.upper() for p in parts]
    return {"nodes": nodes, "elements": elements, "boundary": boundary, "cload": cload, "elastic": material,
            "element_type": element_type, "node_print": prints}


# ----------------------------------------------------------------------
# CalculiX .dat reader
# ----------------------------------------------------------------------
_HEADER = re.compile(r"^\s*(displacements|forces|stresses|total force)\s*\(.*\)\s*for set\s+(\S+)\s+and time", re.I)


def print_half_ulp(token: str) -> float:
    """Half a unit of the last printed digit of a CCX numeric token (the rounding bound of that entry).

    CCX prints in E format, so the number of printed digits fixes the absolute resolution.  An exact zero is printed
    as zero and carries no rounding uncertainty.
    """

    match = re.fullmatch(r"[+-]?(\d*)\.?(\d*)(?:[EeDd]([+-]?\d+))?", token.strip())
    if not match or not (match.group(1) or match.group(2)):
        raise StudyError("CCX_OUTPUT_MALFORMED", f"not a printed number: {token!r}")
    whole, frac, exp = match.group(1), match.group(2), int(match.group(3) or 0)
    if not (whole + frac).strip("0"):
        return 0.0
    return 0.5 * 10.0 ** (exp - len(frac))


def parse_dat(text: str) -> Dict[str, Any]:
    """Read the displacement / reaction / stress blocks and the direct ``total force`` blocks (``TOTALS=YES``).

    ``forces_raw`` and ``totals[set]["raw"]`` keep the printed tokens so the rounding bound of each entry is known.
    """

    out: Dict[str, Any] = {"displacements": {}, "forces": {}, "forces_raw": {}, "stresses": {}, "totals": {}}
    section, current_set = None, None
    for raw in text.splitlines():
        match = _HEADER.match(raw)
        if match:
            section, current_set = match.group(1).lower(), match.group(2).upper()
            continue
        parts = raw.split()
        if not parts or section is None:
            continue
        try:
            values = [float(p) for p in parts]
        except ValueError:
            continue
        if section == "total force":
            if len(values) != 3:
                raise StudyError("CCX_OUTPUT_MALFORMED", f"total force row has {len(values)} fields: {raw.strip()!r}")
            out["totals"][current_set] = {"values": values, "raw": parts}
            continue
        if section in ("displacements", "forces"):
            if len(values) != 4:
                raise StudyError("CCX_OUTPUT_MALFORMED", f"{section} row has {len(values)} fields: {raw.strip()!r}")
            out[section][int(values[0])] = values[1:]
            if section == "forces":
                out["forces_raw"][int(values[0])] = parts[1:]
        elif section == "stresses":
            if len(values) != 8:
                raise StudyError("CCX_OUTPUT_MALFORMED", f"stress row has {len(values)} fields: {raw.strip()!r}")
            out["stresses"].setdefault(int(values[0]), {})[int(values[1])] = values[2:]
    return out


# ----------------------------------------------------------------------
# equilibrium accounting with print-resolution bounds
# ----------------------------------------------------------------------
def _cross_abs(a: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Componentwise bound of |a x e| for |e| <= d (all arguments non-negative)."""

    return np.array([a[1] * d[2] + a[2] * d[1], a[2] * d[0] + a[0] * d[2], a[0] * d[1] + a[1] * d[0]])


def classify(observed: float, bound: float, tolerance_abs: float) -> str:
    """pass: even the worst case is inside the target; fail: even the best case is outside; else unresolved."""

    if observed + bound <= tolerance_abs:
        return "pass"
    if observed - bound > tolerance_abs:
        return "fail"
    return "unresolved"


def _block(residual: np.ndarray, bound: np.ndarray, scale: float, tol: float) -> Dict[str, Any]:
    obs, b = float(np.linalg.norm(residual)), float(np.linalg.norm(bound))
    return {"residual": residual.tolist(), "norm": obs, "rel": obs / scale, "resolution_bound": bound.tolist(),
            "bound_norm": b, "bound_rel": b / scale, "tolerance_rel": tol, "observed_within_tolerance": obs <= tol * scale,
            "status": classify(obs, b, tol * scale)}


def equilibrium_accounting(coords: Mapping[int, Sequence[float]], rf: Mapping[int, Sequence[float]],
                           rf_raw: Mapping[int, Sequence[str]], deck_load: Mapping[int, Sequence[float]],
                           full_forces: Mapping[int, Sequence[float]], held: Sequence[int],
                           ccx_total: Optional[Mapping[str, Any]], total_requested: bool,
                           datum: Sequence[float] = DATUM, length_scale: float = LENGTH_SCALE,
                           tol: float = INTEGRITY["reference_equilibrium_rel"]) -> Dict[str, Any]:
    """Force and moment residual of the reference with worst-case printed-digit bounds.

    ``rf``/``rf_raw``: printed reactions of the prescribed nodes (values and tokens); ``deck_load``: the loads the
    deck really contains; ``full_forces``: the analytic consistent skin forces on ALL skin nodes, so the loads
    withheld from prescribed nodes are ``full_forces[k]`` for k in ``held``.  The observed residual is never
    replaced by the bound: both are reported and the status is pass / fail / unresolved against ``tol``.
    """

    datum_v = np.asarray(datum, float)
    held = sorted(held)
    zero = np.zeros(3)
    delta = {k: np.array([print_half_ulp(t) for t in rf_raw[k]]) for k in held}
    rf_v = {k: np.asarray(rf[k], float) for k in held}
    load_v = {k: np.asarray(v, float) for k, v in deck_load.items()}
    repr_v = {k: DECK_REPR_REL * np.abs(v) for k, v in load_v.items()}
    full_total = np.sum([np.asarray(v, float) for v in full_forces.values()], axis=0)
    force_scale = max(float(np.linalg.norm(full_total)), 1e-300)
    moment_scale = max(force_scale * length_scale, 1e-300)

    def arm(k: int) -> np.ndarray:
        return np.asarray(coords[k], float) - datum_v

    def err(k: int) -> np.ndarray:
        return DECK_REPR_REL * np.abs(np.asarray(coords[k], float))

    sum_rf = np.sum([rf_v[k] for k in held], axis=0)
    sum_load = np.sum(list(load_v.values()), axis=0) if load_v else zero
    bound_load = np.sum(list(repr_v.values()), axis=0) if repr_v else zero
    bound_rf = np.sum([delta[k] for k in held], axis=0)
    force_rounded = _block(sum_rf + sum_load, bound_rf + bound_load, force_scale, tol)

    m_rf = np.sum([np.cross(arm(k), rf_v[k]) for k in held], axis=0)
    m_load = np.sum([np.cross(arm(k), v) for k, v in load_v.items()], axis=0) if load_v else zero
    b_m = np.sum([_cross_abs(np.abs(arm(k)), delta[k]) + _cross_abs(err(k), np.abs(rf_v[k])) for k in held], axis=0)
    if load_v:
        b_m = b_m + np.sum([_cross_abs(np.abs(arm(k)), repr_v[k]) + _cross_abs(err(k), np.abs(v))
                            for k, v in load_v.items()], axis=0)
    moment_rounded = _block(m_rf + m_load, b_m, moment_scale, tol)

    force: Dict[str, Any] = {"scale": force_scale, "rounded_sum": force_rounded, "verdict_source": "rounded_sum"}
    if ccx_total is None:
        force["ccx_total"] = {"status": "absent_though_requested" if total_requested else "not_printed"}
    else:
        tot = np.asarray(ccx_total["values"], float)
        d_tot = np.array([print_half_ulp(t) for t in ccx_total["raw"]])
        block = _block(tot + sum_load, d_tot + bound_load, force_scale, tol)
        diff = tot - sum_rf
        allowed = d_tot + bound_rf
        block["total_force"] = tot.tolist()
        block["minus_rounded_sum"] = diff.tolist()
        block["consistent_with_rounded_sum"] = bool(np.all(np.abs(diff) <= allowed * (1.0 + 1e-9) + 1e-300))
        force["ccx_total"] = block
        force["verdict_source"] = "ccx_total"
    force["status"] = force["ccx_total"]["status"] if force["verdict_source"] == "ccx_total" else force_rounded["status"]

    wf = np.sum([np.asarray(full_forces[k], float) for k in held if k in full_forces], axis=0) if any(
        k in full_forces for k in held) else zero
    wm = np.sum([np.cross(arm(k), np.asarray(full_forces[k], float)) for k in held if k in full_forces], axis=0) if any(
        k in full_forces for k in held) else zero
    m_full = np.sum([np.cross(arm(k), np.asarray(v, float)) for k, v in full_forces.items()], axis=0)
    full_force = sum_rf - wf
    full_moment = m_rf - wm
    return {
        "datum": datum_v.tolist(), "length_scale": length_scale, "tolerance_rel": tol,
        "force": force,
        "moment": {"scale": moment_scale, "datum": datum_v.tolist(), "rounded_sum": moment_rounded,
                   "status": moment_rounded["status"],
                   "uncertainty_rel": moment_rounded["bound_rel"]},
        "withheld": {"nodes": sum(1 for k in held if k in full_forces), "force": wf.tolist(), "moment": wm.tolist(),
                     "force_fraction": float(np.linalg.norm(wf)) / force_scale},
        "reaction_uncorrected": {"force": sum_rf.tolist(), "moment": m_rf.tolist()},
        "reaction_full_support": {"force": full_force.tolist(), "moment": full_moment.tolist(),
                                  "residual_force": (full_force + full_total).tolist(),
                                  "residual_moment": (full_moment + m_full).tolist(),
                                  "defect_vs_deck_accounting_force": float(np.linalg.norm(
                                      (full_force + full_total) - (sum_rf + sum_load))),
                                  "defect_vs_deck_accounting_moment": float(np.linalg.norm(
                                      (full_moment + m_full) - (m_rf + m_load)))},
    }


# ----------------------------------------------------------------------
# independent parent field (own Q4 shape functions, own inverse mapping)
# ----------------------------------------------------------------------
def q4_shape(r: float, s: float) -> np.ndarray:
    return 0.25 * np.array([(1 - r) * (1 - s), (1 + r) * (1 - s), (1 + r) * (1 + s), (1 - r) * (1 + s)])


def q4_dshape(r: float, s: float) -> np.ndarray:
    return 0.25 * np.array([[-(1 - s), -(1 - r)], [(1 - s), -(1 + r)], [(1 + s), (1 + r)], [-(1 + s), (1 - r)]])


def q4_natural(X: np.ndarray, point: np.ndarray) -> Tuple[float, float]:
    """Newton inverse of the bilinear map of a planar quad, in the element's own plane frame."""

    e1 = X[1] - X[0]
    e1 = e1 / np.linalg.norm(e1)
    n = np.cross(X[2] - X[0], X[3] - X[1])
    n = n / np.linalg.norm(n)
    frame = np.array([e1, np.cross(n, e1)])
    P, q = (X - X[0]) @ frame.T, (point - X[0]) @ frame.T
    r = s = 0.0
    for _ in range(60):
        f = q4_shape(r, s) @ P - q
        step = np.linalg.solve(P.T @ q4_dshape(r, s), f)
        r, s = r - step[0], s - step[1]
        if float(np.max(np.abs(step))) < 1.0e-15:
            break
    return float(r), float(s)


@dataclass(frozen=True)
class ShellPatch:
    ids: Tuple[int, ...]  # element ids
    X: np.ndarray  # (E, 4, 3) nodal coordinates
    U: np.ndarray  # (E, 4, 6) nodal translations + rotations


def evaluate_patch(patch: ShellPatch, points: Sequence[Sequence[float]], reach: float = THICKNESS) -> Tuple[np.ndarray, float]:
    """Parent displacement at 3D points near the patch; ``(values (P,3), worst disagreement of shared hits)``."""

    values, spread = [], 0.0
    lo, hi = patch.X.min(axis=1) - reach, patch.X.max(axis=1) + reach
    for x in np.asarray(points, float).reshape(-1, 3):
        hits = []
        for e in np.flatnonzero(np.all((x >= lo - 1e-9) & (x <= hi + 1e-9), axis=1)):
            r, s = q4_natural(patch.X[e], x)
            if abs(r) <= 1 + 1e-9 and abs(s) <= 1 + 1e-9:
                N = q4_shape(r, s)
                hits.append(N @ patch.U[e, :, :3] + np.cross(N @ patch.U[e, :, 3:], x - N @ patch.X[e]))
        if not hits:
            raise StudyError("PARENT_LOCATE_MISS", f"no parent element of the patch owns {x.tolist()}")
        spread = max(spread, max(float(np.max(np.abs(h - hits[0]))) for h in hits))
        values.append(hits[0])
    return np.asarray(values), spread


# ----------------------------------------------------------------------
# inventory
# ----------------------------------------------------------------------
STACK = ("anyfem", "anysolver", "anysolid", "anymesher", "anygeometry", "numpy", "scipy", "h5py")


def module_sources() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in STACK:
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            spec = None
        if spec is None or not spec.origin or not Path(spec.origin).is_file():
            out[name] = {"available": False}
            continue
        try:
            from importlib import metadata

            version = metadata.version(name)
        except Exception:  # noqa: BLE001 - metadata is advisory
            version = None
        out[name] = {"available": True, "origin": spec.origin, "sha256": sha256_file(Path(spec.origin)), "version": version}
    return out


def size_estimate(levels: Sequence[Tuple[int, int]], orders: Sequence[int]) -> List[Dict[str, Any]]:
    rows = []
    for n, layers in levels:
        for element in ("C3D8", "C3D20"):
            mesh = reference_mesh(element, n, layers) if n <= 16 else None
            rows.append({"deck": deck_name(element, n, layers), "nodes": len(mesh.nodes), "elements": len(mesh.elements),
                         "dofs": 3 * len(mesh.nodes), "prescribed_nodes": len(mesh.lateral())})
        rows.append({"child": f"tet4 n={n} layers={layers}", "tets_approx": 6 * n * n * layers, "orders": list(orders)})
    return rows


def inventory(preset: str) -> Dict[str, Any]:
    if preset not in PRESETS:
        raise StudyError("PRESET_UNKNOWN", f"{preset!r} not in {sorted(PRESETS)}")
    spec = PRESETS[preset]
    return {"study": STUDY_ID, "schema": SCHEMA, "preset": preset, "presets": {k: {"levels": [list(l) for l in v["levels"]],
            "orders": list(v["orders"])} for k, v in PRESETS.items()},
            "script": {"path": str(Path(__file__).resolve()), "sha256": sha256_file(Path(__file__).resolve())},
            "sources": module_sources(), "parent": {"lines": list(LINES), "size": PARENT_SIZE, "pressure": PRESSURE,
            "thickness": THICKNESS, "footprint": [list(p) for p in FOOTPRINT]},
            "reference_elements": ORDER_ELEMENT, "integrity_tolerances": INTEGRITY, "assumptions": list(ASSUMPTIONS),
            "estimate": size_estimate(spec["levels"], spec["orders"]),
            "note": "CalculiX is supplied and run by the coordinator; this module never launches a process"}


# ----------------------------------------------------------------------
# ANYfem stack (lazy)
# ----------------------------------------------------------------------
def _stack() -> Dict[str, Any]:
    try:
        from anyfem import Project, steel
        from anyfem.document import DocumentSession
        from anyfem.io.artifacts import ArtifactStore
        from anyfem.io.project_file import load_project, save_project
        from anyfem.io.result_artifact import write_solution_artifact
        from anyfem.io.solid_submodel_artifact import read_solid_mesh, read_solid_result
        from anyfem.jobs import analysis_hash
        from anyfem.mesh_jobs import mesh_semantic_hash
        from anyfem.model import pinned, support
        from anyfem.model.records import AnalysisDefinition, JobRecord, JobStatus, MeshRecord
        from anyfem.model.submodels import SourceReference
        from anyfem.solid_submodel import cut_solid_submodel
        from anyfem.solve.run import solve_linear_static
        from anyfem.submodel_jobs import add_submodel, job_source, run_submodel, submodel_run_staleness
    except ImportError as error:
        raise StudyError("STACK_MISSING", f"the ANYfem solid stack is not importable: {error}") from error
    return dict(locals())


def build_parent(S: Mapping[str, Any]):
    project = S["Project"]("S6 solid reference parent")
    project.add_material(S["steel"]("S355", THICKNESS))
    project.add_plate_section("plate", thickness=THICKNESS, material="S355")
    g = project.geometry
    vid = {(i, j): g.add_point(float(x), float(y), 0.0) for j, y in enumerate(LINES) for i, x in enumerate(LINES)}
    h = {(i, j): g.add_line(vid[i, j], vid[i + 1, j]) for j in range(len(LINES)) for i in range(len(LINES) - 1)}
    v = {(i, j): g.add_line(vid[i, j], vid[i, j + 1]) for j in range(len(LINES) - 1) for i in range(len(LINES))}
    faces = {}
    for j in range(len(LINES) - 1):
        for i in range(len(LINES) - 1):
            face = g.add_face([h[i, j], v[i + 1, j], h[i, j + 1], v[i, j]])
            project.assign_plate(face, "plate")
            faces[i, j] = face
    case = project.load_case()
    for face in faces.values():
        case.add_pressure(project.face(face), PRESSURE)
    count: Dict[int, int] = {}
    for face in faces.values():
        for use in g.faces[face].loop:
            count[use.edge] = count.get(use.edge, 0) + 1
    for edge in sorted(e for e, c in count.items() if c == 1):
        mid = 0.5 * (np.asarray(g.vertex_position(g.edges[edge].start)) + np.asarray(g.vertex_position(g.edges[edge].end)))
        if abs(mid[0]) < 1e-9:
            project.add_support(S["pinned"](project.edge(edge)))
        else:
            project.add_support(S["support"](project.edge(edge), uz=0.0))
        if abs(mid[0] - 1) < 1e-9:
            case.add_line_load(project.edge(edge), (1.0e5, 0.0, 0.0))
        if abs(mid[1] - 1) < 1e-9:
            case.add_line_load(project.edge(edge), (2.0e4, 0.0, 0.0))
    return project, faces


def make_job(S: Mapping[str, Any], session, store):
    """A completed job made the way the workbench makes one (mesh + solve + both artifacts)."""

    project = session.project
    mesh = project.generate_mesh(PARENT_SIZE)
    model_hash = session.revision.model_hash
    preparation = dict(getattr(project, "_last_mesh_preparation", {}) or {})
    mesh_hash = S["mesh_semantic_hash"](mesh, model_hash=model_hash, mesh_input_hash="sha256:s6-mesh-input",
                                        structural_preparation=preparation)
    record = S["MeshRecord"](name="Mesh", source_model_hash=model_hash, mesh_input_hash="sha256:s6-mesh-input",
                             mesh_hash=mesh_hash, structural_preparation=preparation)
    mesh_ref = store.write_mesh(mesh, mesh_id=record.id, document_id=project.document_id, model_hash=model_hash,
                                mesh_hash=mesh_hash)
    record.artifact_id = mesh_ref.id
    analysis = S["AnalysisDefinition"](name="Linear static", target_id="default", settings={"load_case": "default"})
    with session.transaction("analysis and mesh", solver_affecting=False):
        project.analyses[analysis.id] = analysis
        project.mesh_records[record.id] = record
        project.artifacts[mesh_ref.id] = mesh_ref
    document = session.snapshot().document
    submitted = S["analysis_hash"](analysis, project.output_requests, document=document)
    solution = S["solve_linear_static"](project, mesh=mesh, load_case="default")
    job = S["JobRecord"](analysis_id=analysis.id, name="job", model_hash=model_hash, mesh_hash=mesh_hash,
                         analysis_hash=submitted, input_hash="sha256:s6-input", status=S["JobStatus"].COMPLETED)
    result_ref = S["write_solution_artifact"](store, solution, job_id=job.id, document_id=project.document_id,
                                              mesh_id=record.id, model_hash=model_hash, mesh_hash=mesh_hash,
                                              analysis_hash=submitted)
    job.result_artifact_id = result_ref.id
    with session.transaction("job", solver_affecting=False):
        project.jobs[job.id] = job
        project.artifacts[result_ref.id] = result_ref
    return job, solution


def patch_from_solution(solution: Any, face_id: int) -> ShellPatch:
    built = solution.built
    fe = built.fe_model.mesh
    u = np.asarray(solution.displacements, float)
    ids, X, U = [], [], []
    for eid in sorted(int(e) for e in built.mesh.elements_of_face[face_id]):
        node_ids = tuple(int(n) for n in fe.elements[eid].node_ids)
        if len(node_ids) != 4:
            raise StudyError("PARENT_NOT_Q4", f"parent element {eid} has {len(node_ids)} nodes")
        ids.append(eid)
        X.append([np.asarray(fe.get_node(n).coords(), float) for n in node_ids])
        rows = []
        for n in node_ids:
            dofs = list(fe.dof_manager.get_node_dofs(n))
            if len(dofs) != 6:
                raise StudyError("PARENT_DOFS", f"node {n} has {len(dofs)} DOFs")
            rows.append(u[dofs])
        U.append(rows)
    return ShellPatch(tuple(ids), np.asarray(X), np.asarray(U))


def parent_traction(solution: Any, face_id: int) -> np.ndarray:
    """``p * n`` from the reopened parent: assembled element pressure and the diagonals' normal."""

    built = solution.built
    mesh = built.mesh
    values = []
    for eid in sorted(int(e) for e in mesh.elements_of_face[face_id]):
        c = np.array([mesh.nodes[n] for n in mesh.corners_of(eid)])
        cross = np.cross(c[2] - c[0], c[3] - c[1])
        values.append(float(built.load_case.pressure_loads[eid]) * cross / np.linalg.norm(cross))
    values = np.asarray(values)
    if not np.allclose(values, values[0], rtol=1e-9, atol=1e-9 * float(np.linalg.norm(values[0]))):
        raise StudyError("PARENT_TRACTION_NONUNIFORM", "the parent pressure*normal is not uniform on the face")
    return values.mean(axis=0)


def _source_of(S: Mapping[str, Any], project: Any, job_id: str, store: Any):
    session = S["DocumentSession"](project)
    snap = session.snapshot()
    source = S["job_source"](snap.thaw(), S["SourceReference"](job_id, "default"), store, document=snap.document,
                             model_hash=snap.revision.model_hash)
    return session, source


def _end_plane(S: Mapping[str, Any], project: Any, store: Any, run: Any) -> float:
    mesh = S["read_solid_mesh"](store, project.artifacts[run.child["mesh_artifact"]["id"]])
    position = {int(b): i for i, b in enumerate(mesh.boundary_ids)}
    rows = np.unique(mesh.boundary_facets[[position[int(b)] for b in mesh.boundary_groups["end"]]].reshape(-1))
    z = mesh.coordinates[rows, 2]
    if float(np.ptp(z)) > 1e-9:
        raise StudyError("END_SKIN_NOT_PLANAR", "the saved 'end' group is not one z-plane")
    return float(z.mean())


# ----------------------------------------------------------------------
# build
# ----------------------------------------------------------------------
def build_study(out: Path, preset: str = "smoke", ccx_exe: Optional[str] = None) -> Dict[str, Any]:
    if preset not in PRESETS:
        raise StudyError("PRESET_UNKNOWN", f"{preset!r} not in {sorted(PRESETS)}")
    out = Path(out)
    if (out / "manifest.json").exists() or (out / "parent.anyfem").exists():
        raise StudyError("OUT_NOT_EMPTY", f"{out} already holds a study; evidence is never overwritten")
    out.mkdir(parents=True, exist_ok=True)
    S = _stack()
    spec = PRESETS[preset]
    path = out / "parent.anyfem"
    project, faces = build_parent(S)
    session = S["DocumentSession"](project)
    store = S["ArtifactStore"](path)
    job, _solution = make_job(S, session, store)
    face_id = faces[1, 1]
    runs = []
    for n, layers in spec["levels"]:
        for order in spec["orders"]:
            target = (0.2 / n) * (1.0 + 1.0e-9)
            cut = S["cut_solid_submodel"](session.snapshot(), source=S["SourceReference"](job.id, "default"),
                                          face_id=face_id, footprint=FOOTPRINT, target_size=target, layers=layers,
                                          order=order, pressure_skin="end", name=f"s6 n{n} l{layers} p{order}")
            with session.transaction("add solid submodel", solver_affecting=False):
                S["add_submodel"](project, cut.definition)
            published = S["run_submodel"](session, cut.definition.id, store)
            run = published.run
            mesh = S["read_solid_mesh"](store, project.artifacts[run.child["mesh_artifact"]["id"]])
            runs.append({"n": n, "layers": layers, "order": order, "element": ORDER_ELEMENT[order],
                         "definition_id": cut.definition.id, "definition_hash": run.definition_hash, "run_id": run.id,
                         "identity": run.identity, "target_size": target,
                         "child_divisions": int(len(np.unique(np.round(mesh.coordinates[:, 0], 12))) - 1),
                         "child_layers": int(len(np.unique(np.round(mesh.coordinates[:, 2], 12))) - 1),
                         "mesh_artifact": run.child["mesh_artifact"]["id"], "result_artifact": run.child["result_artifact"]["id"],
                         "notification_error": None if published.notification_error is None else str(published.notification_error)})
    S["save_project"](project, path)
    reopened = S["load_project"](path)
    store2 = S["ArtifactStore"](path)
    _session2, source = _source_of(S, reopened, job.id, store2)
    patch = patch_from_solution(source.solution, face_id)
    traction = parent_traction(source.solution, face_id)
    z_end = _end_plane(S, reopened, store2, reopened.submodel_runs[runs[0]["run_id"]])
    side = skin_side(z_end)
    definition = reopened.submodels[runs[0]["definition_id"]]
    material = definition.reconstruction["material"]
    young, poisson = float(material["elastic_modulus"]), float(material["poisson_ratio"])
    decks = []
    for n, layers in spec["levels"]:
        for element in ("C3D8", "C3D20"):
            mesh = reference_mesh(element, n, layers)
            lateral = mesh.lateral()
            values, spread = evaluate_patch(patch, [mesh.nodes[k] for k in lateral])
            prescribed = {k: tuple(float(c) for c in v) for k, v in zip(lateral, values)}
            applied, withheld = split_loads(skin_forces(mesh, side, traction), lateral)
            name = deck_name(element, n, layers)
            text = deck_text(mesh, young, poisson, prescribed, applied, f"{STUDY_ID} {name}")
            (out / f"{name}.inp").write_text(text, encoding="ascii", newline="\n")
            decks.append({"name": name, "element": element, "n": n, "layers": layers, "inp": f"{name}.inp",
                          "dat": f"{name}.dat", "sha256": hashlib.sha256(text.encode("ascii")).hexdigest(),
                          "nodes": len(mesh.nodes), "elements": len(mesh.elements), "prescribed_nodes": len(lateral),
                          "loaded_nodes": len(applied), "withheld_nodes": len(withheld), "hit_spread": spread})
    manifest = {
        "study": STUDY_ID, "schema": SCHEMA, "preset": preset, "levels": [list(l) for l in spec["levels"]],
        "orders": list(spec["orders"]), "project": "parent.anyfem", "job_id": job.id, "face_id": int(face_id),
        "material": {"elastic_modulus": young, "poisson_ratio": poisson}, "traction": traction.tolist(),
        "z_end": z_end, "skin_side": side, "box": list(DETAIL_BOX), "region_half": REGION_HALF,
        "parent_field_sha256": hashlib.sha256(np.ascontiguousarray(patch.U).tobytes()).hexdigest(),
        "runs": runs, "decks": decks, "integrity_tolerances": INTEGRITY, "assumptions": list(ASSUMPTIONS),
        "sources": module_sources(), "script_sha256": sha256_file(Path(__file__).resolve()),
        "ccx_commands": None if ccx_exe is None else [{"argv": [ccx_exe, "-i", d["name"]], "cwd": str(out.resolve()),
                                                       "expects": d["dat"]} for d in decks],
        "ccx_note": "recorded only; this module never launches CalculiX",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


# ----------------------------------------------------------------------
# read: reference outputs (no ANYfem stack needed)
# ----------------------------------------------------------------------
def _vec(a: Sequence[float], b: Sequence[float]) -> Dict[str, float]:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = float(np.linalg.norm(a - b))
    return {"abs": d, "rel": d / max(float(np.linalg.norm(b)), 1e-300)}


def reference_metrics(out: Path, manifest: Mapping[str, Any], deck: Mapping[str, Any]) -> Dict[str, Any]:
    inp, dat = out / deck["inp"], out / deck["dat"]
    if not inp.is_file():
        raise StudyError("DECK_MISSING", f"deck {inp.name} is absent")
    text = inp.read_text(encoding="ascii")
    if hashlib.sha256(text.encode("ascii")).hexdigest() != deck["sha256"]:
        raise StudyError("DECK_CHANGED", f"{inp.name} differs from the built deck")
    info = parse_deck(text)
    mesh = reference_mesh(deck["element"], deck["n"], deck["layers"], manifest["box"])
    if set(info["nodes"]) != set(mesh.nodes) or set(info["elements"]) != {e[0] for e in mesh.elements}:
        raise StudyError("DECK_MESH_MISMATCH", "deck nodes/elements differ from the preregistered grid")
    forces = skin_forces(mesh, manifest["skin_side"], manifest["traction"])
    held = set(info["boundary"])
    applied = {k: v for k, v in forces.items() if k not in held}
    if set(info["cload"]) != {k for k, v in applied.items() if any(v)} or any(
            not np.allclose(info["cload"][k], applied[k], rtol=INTEGRITY["deck_value_rel"], atol=0.0) for k in info["cload"]):
        raise StudyError("DECK_LOAD_MISMATCH", "deck concentrated loads differ from the consistent skin forces")
    if not dat.is_file():
        raise StudyError("CCX_OUTPUT_MISSING", f"{dat.name} is absent: run CalculiX on {inp.name}", expected=dat.name)
    result = parse_dat(dat.read_text(encoding="utf-8", errors="replace"))
    if set(result["displacements"]) != set(mesh.nodes):
        raise StudyError("CCX_OUTPUT_INCOMPLETE", "displacement block is missing or lacks nodes")
    if set(result["forces"]) != held:
        raise StudyError("CCX_OUTPUT_INCOMPLETE", "reaction block is missing or lacks prescribed nodes")
    nx, ny, nz = mesh.nx, mesh.ny, mesh.nz
    k_end = nz if manifest["skin_side"] == "top" else 0
    end_id, mid_id = mesh.nid(nx // 2, ny // 2, k_end), mesh.nid(nx // 2, ny // 2, nz // 2)
    for node, (x, y) in ((end_id, CENTRE), (mid_id, CENTRE)):
        if abs(mesh.nodes[node][0] - x) > 1e-12 or abs(mesh.nodes[node][1] - y) > 1e-12:
            raise StudyError("LATTICE_CENTRE", "centre node is not at the footprint centre")
    rf = {k: np.asarray(v) for k, v in result["forces"].items()}
    withheld = {k: np.asarray(v) for k, v in forces.items() if k in held}
    interface = np.zeros(3)
    moment = np.zeros(3)
    for k in held:
        reaction = rf[k] - withheld.get(k, np.zeros(3))
        interface += reaction
        moment += np.cross(mesh.nodes[k], reaction)
    total = np.sum([np.asarray(v) for v in forces.values()], axis=0)
    total_moment = np.sum([np.cross(mesh.nodes[k], v) for k, v in forces.items()], axis=0)
    scale = max(float(np.linalg.norm(total)), 1e-300)
    metrics: Dict[str, Any] = {
        "status": "ok", "nodes": len(mesh.nodes), "elements": len(mesh.elements),
        "u_end_centre": result["displacements"][end_id], "u_mid_centre": result["displacements"][mid_id],
        "interface_force": interface.tolist(), "interface_moment": moment.tolist(),
        "applied_force": total.tolist(), "applied_moment": total_moment.tolist(),
        "equilibrium_rel": float(np.linalg.norm(interface + total) / scale),
        "interface_moment_centre": (moment - np.cross(np.asarray(DATUM), interface)).tolist(),
    }
    requested = any(p["nset"] == "NPRESC" and p["totals"] in ("YES", "ONLY") for p in info["node_print"])
    metrics["accounting"] = equilibrium_accounting(
        mesh.nodes, result["forces"], result["forces_raw"], info["cload"], forces, sorted(held),
        result["totals"].get("NPRESC"), requested)
    if deck["element"] == "C3D8":
        ips = result["stresses"]
        if set(ips) != {e[0] for e in mesh.elements} or any(len(v) != 8 for v in ips.values()):
            raise StudyError("CCX_OUTPUT_INCOMPLETE", "stress block lacks elements or 8 integration points each")
        cx, cy = CENTRE
        x0, y0, _z0, x1, y1, _z1 = mesh.box
        hx, hy = (x1 - x0) / mesh.n, (y1 - y0) / mesh.n
        chosen = [e for e in mesh.elements
                  if abs(x0 + (e[2][0] + 0.5) * hx - cx) < manifest["region_half"] and abs(y0 + (e[2][1] + 0.5) * hy - cy) < manifest["region_half"]]
        if not chosen:
            raise StudyError("REGION_EMPTY", "no reference element has its centre inside the stress region")
        mean = np.mean([np.mean([ips[e[0]][ip] for ip in sorted(ips[e[0]])], axis=0) for e in chosen], axis=0)
        volume = len(chosen) * hx * hy * (mesh.box[5] - mesh.box[2]) / mesh.layers
        expected_volume = 2 * manifest["region_half"] * 2 * manifest["region_half"] * (mesh.box[5] - mesh.box[2])
        metrics["stress_region"] = {"status": "ok", "components": ["xx", "yy", "zz", "xy", "xz", "yz"],
                                    "average": mean.tolist(), "volume": volume,
                                    "volume_rel_error": abs(volume - expected_volume) / expected_volume,
                                    "aligned": bool(abs(volume - expected_volume) / expected_volume
                                                    <= INTEGRITY["region_volume_rel"])}
    else:
        metrics["stress_region"] = {"status": "not_computed", "reason": ASSUMPTIONS[6]}
    return metrics


def read_reference_outputs(out: Path, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    results: Dict[str, Any] = {}
    for deck in manifest["decks"]:
        try:
            results[deck["name"]] = reference_metrics(out, manifest, deck)
        except StudyError as error:
            results[deck["name"]] = error.as_dict()
    return results


# ----------------------------------------------------------------------
# read: children (needs the ANYfem stack)
# ----------------------------------------------------------------------
def _child_point(mesh: Any, displacement: np.ndarray, point: Sequence[float]) -> List[float]:
    d = np.linalg.norm(np.asarray(mesh.coordinates) - np.asarray(point), axis=1)
    i = int(np.argmin(d))
    if d[i] > 1e-9:
        raise StudyError("CHILD_POINT_MISSING", f"no child vertex within 1e-9 of {list(point)}", nearest=float(d[i]))
    return displacement[i].tolist()


def child_metrics(S: Mapping[str, Any], project: Any, store: Any, patch: ShellPatch, manifest: Mapping[str, Any],
                  run_row: Mapping[str, Any]) -> Dict[str, Any]:
    run = project.submodel_runs[run_row["run_id"]]
    mesh = S["read_solid_mesh"](store, project.artifacts[run.child["mesh_artifact"]["id"]])
    result = S["read_solid_result"](store, project.artifacts[run.child["result_artifact"]["id"]])
    if not np.array_equal(result["displacement_node_ids"], mesh.node_ids):
        raise StudyError("CHILD_NODE_ORDER", "result node ids differ from the mesh node ids")
    interface = result["interface"]
    dofs = np.asarray(interface["prescribed_dofs"], dtype=np.int64)
    values = np.asarray(interface["prescribed_values"], float)
    xyz = np.asarray(result["coefficient_coordinates"], float)[dofs]
    comp = np.asarray(result["coefficient_components"], dtype=np.int64)[dofs]
    unique, inverse = np.unique(np.round(xyz, 12), axis=0, return_inverse=True)
    field, spread = evaluate_patch(patch, unique)
    diff = np.abs(field[inverse.reshape(-1), comp] - values)
    scale = max(float(np.max(np.abs(values))), 1e-300)
    boundary = {"coefficients": int(len(dofs)), "locations": int(len(unique)), "max_abs": float(diff.max()),
                "max_rel": float(diff.max() / scale), "field_scale": scale, "hit_spread": spread,
                "ok": bool(diff.max() / scale <= INTEGRITY["boundary_transfer_rel"])}
    cx, cy = CENTRE
    w, S6 = np.asarray(result["recovery_weights"]), np.asarray(result["recovery_stress"])
    centroid = np.asarray(result["recovery_coordinates"]).mean(axis=1)
    half = manifest["region_half"]
    sel = (np.abs(centroid[:, 0] - cx) < half) & (np.abs(centroid[:, 1] - cy) < half)
    if not bool(sel.any()):
        raise StudyError("REGION_EMPTY", "no child cell has its centroid inside the stress region")
    volume = float(w[sel].sum())
    expected = 2 * half * 2 * half * (DETAIL_BOX[5] - DETAIL_BOX[2])
    average = [float((w[sel] * S6[sel][:, :, a, b]).sum() / volume) for a, b in VOIGT]
    ground = np.asarray(result["displacement"], float)
    return {
        "boundary": boundary,
        "u_end_centre": _child_point(mesh, ground, (cx, cy, manifest["z_end"])),
        "u_mid_centre": _child_point(mesh, ground, (cx, cy, 0.0)),
        "interface_force": np.asarray(result["interface_force"]).tolist(),
        "interface_moment": np.asarray(result["interface_moment"]).tolist(),
        "skin_force": np.asarray(interface["skin_force"]).tolist(),
        "skin_moment": np.asarray(interface["skin_moment"]).tolist(),
        "stress_region": {"components": ["xx", "yy", "zz", "xy", "xz", "yz"], "average": average, "volume": volume,
                          "volume_rel_error": abs(volume - expected) / expected,
                          "aligned": bool(abs(volume - expected) / expected <= INTEGRITY["region_volume_rel"])},
        "artifacts_verified": bool(store.verify(project.artifacts[run.child["mesh_artifact"]["id"]])
                                   and store.verify(project.artifacts[run.child["result_artifact"]["id"]])),
        "run_identity": run.identity,
    }


def read_children(out: Path, manifest: Mapping[str, Any]) -> Dict[str, Any]:
    path = out / manifest["project"]
    if not path.exists():
        raise StudyError("PROJECT_MISSING", f"{path.name} is absent")
    S = _stack()
    project = S["load_project"](path)
    store = S["ArtifactStore"](path)
    session, source = _source_of(S, project, manifest["job_id"], store)
    patch = patch_from_solution(source.solution, manifest["face_id"])
    rows: Dict[str, Any] = {}
    for row in manifest["runs"]:
        key = f"{row['element']}_{row['n']}x{row['layers']}_p{row['order']}"
        try:
            if row["run_id"] not in project.submodel_runs:
                raise StudyError("RUN_MISSING", f"run {row['run_id']} is not in the reopened project")
            rows[key] = child_metrics(S, project, store, patch, manifest, row)
            rows[key]["staleness"] = [str(r) for r in S["submodel_run_staleness"](session.snapshot(), project.submodel_runs[row["run_id"]], store)]
            rows[key]["status"] = "ok"
        except StudyError as error:
            rows[key] = error.as_dict()
    for deck in manifest["decks"]:  # the written boundary values are the independent field, repeated
        try:
            info = parse_deck((out / deck["inp"]).read_text(encoding="ascii"))
            ids = sorted(info["boundary"])
            values, _ = evaluate_patch(patch, [info["nodes"][k] for k in ids])
            worst = max(float(np.max(np.abs(values[i] - info["boundary"][k]))) for i, k in enumerate(ids))
            rows[f"deck_boundary_{deck['name']}"] = {"status": "ok", "max_abs": worst, "nodes": len(ids)}
        except (OSError, StudyError) as error:
            rows[f"deck_boundary_{deck['name']}"] = {"status": "failed", "code": getattr(error, "code", "DECK_MISSING"),
                                                      "message": str(error)}
    return rows


# ----------------------------------------------------------------------
# read: assemble
# ----------------------------------------------------------------------
def _compare(child: Mapping[str, Any], ref: Mapping[str, Any],
             stress_ref: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Child vs reference.  Moments are given about the origin (as before) and about the common datum; every
    force/moment difference also carries ``rel_physical`` (against |F| and |F|*L of the applied skin load)."""

    out = {k: _vec(child[k], ref[k]) for k in ("u_end_centre", "u_mid_centre", "interface_force", "interface_moment")}
    f_scale = max(float(np.linalg.norm(ref["applied_force"])), 1e-300)
    out["interface_force"]["rel_physical"] = out["interface_force"]["abs"] / f_scale
    out["interface_moment"]["rel_physical"] = out["interface_moment"]["abs"] / (f_scale * LENGTH_SCALE)
    child_centre = np.asarray(child["interface_moment"], float) - np.cross(np.asarray(DATUM), np.asarray(child["interface_force"], float))
    out["interface_moment_centre"] = _vec(child_centre, ref["interface_moment_centre"])
    out["interface_moment_centre"]["rel_physical"] = out["interface_moment_centre"]["abs"] / (f_scale * LENGTH_SCALE)
    if stress_ref is not None:  # C3D8 finest region average, also for P2 children
        if stress_ref.get("status") == "ok" and stress_ref["stress_region"].get("status") == "ok":
            out["stress_region"] = _vec(child["stress_region"]["average"], stress_ref["stress_region"]["average"])
            out["stress_region"]["reference"] = "C3D8 finest"
            out["stress_region"]["reference_aligned"] = stress_ref["stress_region"]["aligned"]
        else:
            out["stress_region"] = {"status": "reference_unavailable", "reference": "C3D8 finest", "failure": stress_ref}
    elif ref["stress_region"].get("status") == "ok":
        out["stress_region"] = _vec(child["stress_region"]["average"], ref["stress_region"]["average"])
    return out


def _integrity_for_reference(k: str, v: Mapping[str, Any]) -> List[Dict[str, Any]]:
    tol = INTEGRITY["reference_equilibrium_rel"]
    acc = v.get("accounting")
    if acc is None:
        return [{"check": f"reference_equilibrium {k}", "ok": v["equilibrium_rel"] <= tol, "status": "pass" if v["equilibrium_rel"] <= tol else "fail",
                 "value": v["equilibrium_rel"], "tolerance": tol, "gating": True}]
    force, rounded = acc["force"], acc["force"]["rounded_sum"]
    rows: List[Dict[str, Any]] = []
    total = force["ccx_total"]
    if force["verdict_source"] == "ccx_total":
        rows.append({"check": f"reference_equilibrium {k}", "ok": total["status"] == "pass", "status": total["status"],
                     "value": total["rel"], "bound": total["bound_rel"], "tolerance": tol, "gating": True,
                     "source": "ccx_total"})
        # observed rounded-sum discrepancy stays visible; it only gates if it is not explained by print resolution
        rows.append({"check": f"reference_equilibrium_rounded_sum {k}", "ok": rounded["status"] != "fail",
                     "status": rounded["status"], "value": rounded["rel"], "bound": rounded["bound_rel"], "tolerance": tol,
                     "gating": rounded["status"] == "fail", "source": "sum of printed per-node RF"})
        rows.append({"check": f"ccx_total_consistency {k}", "ok": total["consistent_with_rounded_sum"],
                     "status": "pass" if total["consistent_with_rounded_sum"] else "fail",
                     "value": float(np.linalg.norm(total["minus_rounded_sum"])), "tolerance": None, "gating": True})
    else:
        rows.append({"check": f"reference_equilibrium {k}", "ok": rounded["status"] == "pass", "status": rounded["status"],
                     "value": rounded["rel"], "bound": rounded["bound_rel"], "tolerance": tol, "gating": True,
                     "source": "sum of printed per-node RF"})
        if total["status"] == "absent_though_requested":
            rows.append({"check": f"ccx_total_present {k}", "ok": False, "status": "fail", "value": None, "tolerance": None,
                         "gating": True})
    m = acc["moment"]["rounded_sum"]
    rows.append({"check": f"reference_equilibrium_moment {k}", "ok": m["status"] == "pass", "status": m["status"],
                 "value": m["rel"], "bound": m["bound_rel"], "tolerance": tol, "gating": True,
                 "source": "sum of printed per-node RF x arm about the common datum"})
    stress = v.get("stress_region", {})
    if stress.get("status") == "ok":
        rows.append({"check": f"region_volume_reference {k}", "ok": stress["aligned"], "status": "pass" if stress["aligned"] else "fail",
                     "value": stress["volume_rel_error"], "tolerance": INTEGRITY["region_volume_rel"], "gating": True})
    return rows


def assemble(manifest: Mapping[str, Any], refs: Mapping[str, Any], children: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    levels = [tuple(l) for l in manifest["levels"]]
    finest = levels[-1]
    comparisons, convergence = [], {}
    for element in ("C3D8", "C3D20"):
        series = [refs.get(deck_name(element, n, l)) for n, l in levels]
        steps = []
        for (n0, l0), a, b in zip(levels[1:], series[:-1], series[1:]):
            if a and b and a.get("status") == "ok" and b.get("status") == "ok":
                steps.append({"to": f"{n0}x{n0}x{l0}", **{k: _vec(b[k], a[k]) for k in ("u_end_centre", "u_mid_centre")}})
        convergence[element] = steps
    for row in manifest["runs"]:
        key = f"{row['element']}_{row['n']}x{row['layers']}_p{row['order']}"
        child = (children or {}).get(key)
        entry: Dict[str, Any] = {"run": key, "order": row["order"], "level": [row["n"], row["layers"]]}
        if not child or child.get("status") != "ok":
            entry["status"] = "child_unavailable"
            entry["failure"] = child
        else:
            for label, (n, l) in (("same_level", (row["n"], row["layers"])), ("finest", finest)):
                ref = refs.get(deck_name(row["element"], n, l))
                stress_ref = refs.get(deck_name("C3D8", n, l)) if label == "finest" else None
                if label == "finest" and stress_ref is None:
                    stress_ref = {"status": "failed", "code": "REFERENCE_ABSENT"}
                entry[f"vs_{label}_reference"] = (_compare(child, ref, stress_ref) if ref and ref.get("status") == "ok"
                                                  else {"status": "reference_unavailable", "failure": ref})
            entry["status"] = "ok"
        comparisons.append(entry)
    failures = [{"where": k, **v} for k, v in {**refs, **(children or {})}.items() if v.get("status") == "failed"]
    integrity = []
    for k, v in (children or {}).items():
        if v.get("status") == "ok" and "boundary" in v:
            integrity.append({"check": f"boundary_transfer {k}", "ok": v["boundary"]["ok"], "value": v["boundary"]["max_rel"],
                              "tolerance": INTEGRITY["boundary_transfer_rel"]})
            integrity.append({"check": f"region_volume {k}", "ok": v["stress_region"]["aligned"],
                              "value": v["stress_region"]["volume_rel_error"], "tolerance": INTEGRITY["region_volume_rel"]})
    for k, v in refs.items():
        if v.get("status") == "ok":
            integrity.extend(_integrity_for_reference(k, v))
    unresolved = [c["check"] for c in integrity if c.get("status") == "unresolved"]
    return {"study": STUDY_ID, "schema": SCHEMA, "preset": manifest["preset"],
            "status": "complete" if not failures and all(c["ok"] for c in integrity) and integrity else "incomplete",
            "unresolved": unresolved, "failures": failures, "integrity": integrity, "reference": dict(refs), "children": dict(children or {}),
            "comparisons": comparisons, "reference_convergence": convergence, "assumptions": manifest["assumptions"],
            "sources": manifest["sources"], "script_sha256": manifest["script_sha256"],
            "parent_field_sha256": manifest["parent_field_sha256"]}


def read_study(out: Path) -> Dict[str, Any]:
    out = Path(out)
    path = out / "manifest.json"
    if not path.is_file():
        raise StudyError("STUDY_MANIFEST_MISSING", f"{path} is absent: run build first")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise StudyError("STUDY_MANIFEST_INVALID", str(error)) from error
    if manifest.get("study") != STUDY_ID or manifest.get("schema") != SCHEMA:
        raise StudyError("STUDY_MANIFEST_INVALID", "not a manifest of this study/schema")
    refs = read_reference_outputs(out, manifest)
    try:
        children: Optional[Dict[str, Any]] = read_children(out, manifest)
    except StudyError as error:
        children = {"children": error.as_dict()}
    report = assemble(manifest, refs, children)
    (out / "study_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return report


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", nargs="?", choices=("build", "read"))
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--preset", default="smoke", choices=sorted(PRESETS))
    parser.add_argument("--ccx-exe", default=None)
    args = parser.parse_args(argv)
    try:
        if args.inventory:
            print(json.dumps(inventory(args.preset), indent=2, sort_keys=True))
            return 0
        if args.command is None or not args.out:
            raise StudyError("USAGE", "give --inventory, or build/read with --out DIR")
        if args.command == "build":
            manifest = build_study(Path(args.out), args.preset, args.ccx_exe)
            print(json.dumps({"status": "built", "decks": [d["inp"] for d in manifest["decks"]],
                              "expects": [d["dat"] for d in manifest["decks"]]}, indent=2))
            return 0
        report = read_study(Path(args.out))
        print(json.dumps({"status": report["status"], "failures": report["failures"],
                          "comparisons": report["comparisons"]}, indent=2, default=str))
        return 0 if report["status"] == "complete" else 3
    except StudyError as error:
        print(json.dumps(error.as_dict(), indent=2))
        return 2
    except Exception as error:  # noqa: BLE001 - never a bare traceback without a typed record
        failure = {"status": "failed", "code": "UNEXPECTED", "message": f"{type(error).__name__}: {error}",
                   "traceback": traceback.format_exc()}
        if args.out:
            try:
                Path(args.out).mkdir(parents=True, exist_ok=True)
                (Path(args.out) / f"{args.command or 'run'}_failure.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
            except OSError:
                pass
        print(json.dumps(failure, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())
