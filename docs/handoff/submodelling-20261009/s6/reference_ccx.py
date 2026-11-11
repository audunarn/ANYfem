"""Independent CalculiX 2.22 reference for the S6 qualification: the A5 stiffened panel (full stage fixture only).

STATUS: authored without any ability to execute Python or CCX.  Nothing in this file has been run.  Every
statement about CCX behaviour is an assumption that the coordinator's run must confirm or refute; see
REFERENCE.md ("Unverified assumptions") and keep failed runs as evidence.

Addendum (section 12, also NOT RUN): a separately marked physical-offset fallback (shared nodes + B31 section offsets,
rotation read from expanded-shell translations in the .frd).  It never alters sections 1-11 or ``CalibrationRecord``; see REFERENCE.md.

Scope.  A5 only: the full ANYfem stiffened model (shell + eccentric beams) against S8R + B31 in CCX.  The 3D solid
child has its own owner (solid_reference); there is no whole-child comparator and no solid block deck here.

Design rules
* Pure functions build decks / read output / compute quantities.  Only ``ccx_version`` and ``run_ccx`` start a
  process, and only the coordinator's runner calls them.  Tests never do.
* Output directories are explicit, new or empty, and never inside ``handoff`` (historical evidence).
* No threshold is tuned on results.  The only numeric screens are structural and named where used: exact
  arithmetic identities, print precision (CCX prints 6 significant digits) and GROSS-ERROR screens (factor 2) that
  catch a mechanism that is not working (the dev-04 run produced tip uz = +28810 for a 1 N load); a gross screen
  says "the mechanism works at all", never "the result is accurate".
* Node and element ids are dense (``check_deck_syntax``): CCX sizes arrays by the largest id, so an id near 10^6
  for a handful of probe nodes allocates for a million nodes.  Deck lines are limited to 132 characters.

Pressure sign.  ANYfem's assembly of the A5 pressure is +10000 N in z with moment about the origin
(+5000, -5000, -70000) (observed in the development preflight; ``anyfem_load_totals`` re-measures it every run).
The ANYfem sign is preserved.  The CCX deck does NOT use a ``*DLOAD`` P1/P2 label (its sign relative to the shell
winding was never measured); it applies explicit consistent nodal forces +p*area*w_k in z (S8R weights -1/12 corner,
+1/3 mid-side).  Calibration 4 measures what ``P1`` does and that the explicit forces point to +z and equal the
``P1`` magnitude; it is a check of the explicit loads, not a source of a label.

Shell-beam link.  Dev-04 showed that an isolated ``*EQUATION`` gauge on shell rotation dofs 4-6 gave identical
probe displacements (no rotation transmitted) and that the cantilever clamp was inadequate.  The A5 deck still
needs the beam-shell link ``u_b = u_p + theta x r, theta_b = theta_p``; calibrations 2 and 3 test exactly that
link against closed forms (Kirchhoff strip, nu = 0) for rotation about y and about x.  If they fail,
``CalibrationRecord.require_usable`` refuses production decks and A5 is INCONCLUSIVE; there is no artificial
stiffness and no fallback in this file.  The rotation gauge of A5 is the relative displacement of two ACTUAL nodes
of the deck (plate node and the beam node at offset e): ``rx = -(uy_beam - uy_plate) / e``; it is only meaningful
once calibration 3 has accepted the link.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

CCX_VERSION = "2.22"
DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")
DEFAULT_LEVELS = (10, 20, 40, 80)  # elements per side: h = 0.1, 0.05, 0.025, 0.0125 (S8R)
PRESSURE_FORCE_SIGN = 1.0  # ANYfem assembles the positive A5 pressure toward +z (preflight re-measures it)
MAX_DECK_LINE = 132
ID_DENSITY = 4.0  # largest node/element id <= ID_DENSITY * count: the deck's own id layout is ~1.3, calibration decks ~1


class PreflightError(RuntimeError):
    """A preflight check failed; the failing checks are in ``report``."""

    def __init__(self, report: "Preflight"):
        super().__init__("preflight failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in report.failed()))
        self.report = report


@dataclass
class Preflight:
    checks: List[dict] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", **data) -> bool:
        self.checks.append({"name": name, "passed": bool(passed), "detail": detail, **data})
        return bool(passed)

    def observe(self, name: str, detail: str = "", **data) -> None:
        """Record a fact that is reported but never pass/fail (``kind='observation'``)."""
        self.checks.append({"name": name, "passed": True, "kind": "observation", "detail": detail, **data})

    def failed(self) -> List[dict]:
        return [c for c in self.checks if not c["passed"]]

    def require(self) -> "Preflight":
        if self.failed():
            raise PreflightError(self)
        return self


# ----------------------------------------------------------------------------------------------
# 1. Specification
# ----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class StiffenedSpec:
    """Physical data of the A5 panel.  Build with ``spec_from_project``; direct construction is for tests."""

    E: float
    nu: float
    plate_t: float
    pressure: float  # project pressure VALUE; the deck force is PRESSURE_FORCE_SIGN * pressure * area toward +z
    bar_width: float  # extent along the beam's local y (in the plate plane): BeamSection.profile_rectangles width_y
    bar_height: float  # extent along the beam's local z (the plate normal): profile_rectangles height_z
    offset: float  # z of the beam neutral axis = project.beam_offset_vector(edge)[2]; independent of both extents
    solver_section: Mapping[str, float] = field(default_factory=dict)  # area, Iy, Iz, J of StiffenerCrossSection (read, not derived)
    offset_mode: str = "unknown"
    stiffener_x: float = 0.7
    stiffener_y: float = 0.3
    line_load_x1: float = 1.0e5  # N/m in +x on the edge x = 1
    line_load_y1: float = 2.0e4  # N/m in +x on the edge y = 1
    # edge name -> constrained dof names (translations by default; replaced by the built model's own)
    edge_dofs: Mapping[str, Tuple[str, ...]] = field(default_factory=lambda: {
        "x0": ("ux", "uy", "uz"), "x1": ("uz",), "y0": ("uz",), "y1": ("uz",)})
    edge_dofs_source: str = "assumed"  # "assumed" until replaced by supports_from_built

    @property
    def beam_area(self) -> float:
        return self.bar_width * self.bar_height

    def inertia_vertical(self) -> float:
        """Rectangle inertia for deflection along the plate normal (bending about local y): width * height^3 / 12."""
        return self.bar_width * self.bar_height ** 3 / 12.0

    def inertia_lateral(self) -> float:
        """Rectangle inertia for deflection in the plate plane (bending about local z): height * width^3 / 12."""
        return self.bar_height * self.bar_width ** 3 / 12.0


def spec_from_project(project, *, material: str = "S355", section: str = "flat bar",
                      plate_section: str = "plate", plate_thickness: Optional[float] = None) -> Tuple[StiffenedSpec, Preflight]:
    """Read the physical data from the real project through its own factories; refuse instead of guessing.

    Three physical facts are established SEPARATELY (the offset is never used to infer a dimension):

    1. local axes: ``project.beam_section_frame(edge)`` -> (axis, local_y, local_z) per stiffener edge.  The
       local z (web / thickness direction) must be the plate normal +z and the axis must lie in the plate.
    2. section: ``BeamSection.profile_rectangles()`` gives (y, z, width_y, height_z) in that frame.  For a
       ``Flatbar`` the rectangle is ``flange_width`` along local y (in the plate) by ``flange_thickness``
       along local z (the plate normal): the bar lies flat.  ``BeamSection.properties()`` (the solver's own
       ``StiffenerCrossSection``) must reproduce the area and the SET of the two rectangle inertias.
    3. eccentricity: ``project.beam_offset_vector(edge)`` must equal its own definition
       (``centroid_y*local_y + centroid_z*local_z`` automatic, ``eccentricity*local_z`` manual).  The real
       ``stiffened_project`` has (0, 0, 0.005).  Coincidences with ``plate_t/2`` and the bar half height are
       OBSERVATIONS only.
    """

    pre = Preflight()
    mat = project.materials[material]
    E, nu = float(mat.elastic_modulus), float(mat.poisson_ratio)
    plate_t = float(plate_thickness if plate_thickness is not None else _plate_thickness(project, plate_section))
    sec = project.beam_sections[section]
    case = project.load_cases["default"]
    pressures = {float(p.value) for p in case.pressures}
    pre.add("single_pressure_value", len(pressures) == 1, f"pressures={sorted(pressures)}")

    edges = sorted(e for e, name in project.edge_sections.items() if name == section)
    pre.add("six_stiffener_edges", len(edges) == 6, f"count={len(edges)}")

    # 1. local axes ------------------------------------------------------------------------------
    frames = {e: tuple(np.asarray(v, dtype=float) for v in project.beam_section_frame(e)) for e in edges}
    z_ok = all(np.allclose(lz, (0.0, 0.0, 1.0), rtol=0, atol=1e-12) for _a, _ly, lz in frames.values())
    pre.add("local_z_is_plate_normal_plus_z", bool(frames) and z_ok,
            f"local_z per edge={[f[2].tolist() for f in list(frames.values())[:2]]}...")
    in_plane = all(abs(a[2]) < 1e-12 and abs(ly[2]) < 1e-12 for a, ly, _lz in frames.values())
    pre.add("beam_axis_and_local_y_lie_in_the_plate", bool(frames) and in_plane, "axis_z and local_y_z must vanish")
    right_handed = all(np.allclose(np.cross(ly, a), -lz, rtol=0, atol=1e-12) for a, ly, lz in frames.values())
    pre.add("frame_is_local_y_eq_lz_cross_axis", bool(frames) and right_handed, "project.beam_section_frame: local_y = local_z x axis")

    # 2. section: rectangle extents from the real factory, solver properties as the cross-check ---
    rects = sec.profile_rectangles()
    pre.add("single_rectangle_profile", len(rects) == 1, f"rectangles={rects!r}")
    _ry, _rz, width_y, height_z = (float(v) for v in rects[0])
    lz0 = frames[edges[0]][2] if edges else np.array([0.0, 0.0, 1.0])
    props = sec.properties(orientation=lz0)
    area = width_y * height_z
    i_about_y = width_y * height_z ** 3 / 12.0  # bending about local y: deflection along local z (the plate normal)
    i_about_z = height_z * width_y ** 3 / 12.0  # bending about local z: deflection in the plate plane
    pre.add("solver_area_matches_rectangle", math.isclose(props["area"], area, rel_tol=1e-9), f"solver={props['area']!r} rectangle={area!r}")
    pair_ok = all(math.isclose(a, b, rel_tol=1e-9)
                  for a, b in zip(sorted((props["Iy"], props["Iz"])), sorted((i_about_y, i_about_z))))
    pre.add("solver_inertia_set_matches_rectangle", pair_ok,
            f"solver Iy={props['Iy']!r} Iz={props['Iz']!r}; rectangle about-y={i_about_y!r} about-z={i_about_z!r}")
    pre.observe("solver_inertia_naming",
                f"solver Iy equals about-local-y (z-deflection) inertia: {math.isclose(props['Iy'], i_about_y, rel_tol=1e-9)}; "
                f"solver Iz equals it: {math.isclose(props['Iz'], i_about_y, rel_tol=1e-9)}.  Which of the two the solver "
                "element uses for z-deflection is NOT read here (needs the solver element); calibration 1 only fixes CCX's side",
                Iy=props["Iy"], Iz=props["Iz"], J=props["J"], i_about_local_y=i_about_y, i_about_local_z=i_about_z)
    ori = np.asarray(props.get("orientation", (np.nan,) * 3), dtype=float)
    pre.add("solver_orientation_is_local_z", np.allclose(ori, lz0, rtol=0, atol=1e-12), f"orientation={ori.tolist()}")

    # 3. eccentricity: the project's vector against its own definition ---------------------------
    vectors = [np.asarray(project.beam_offset_vector(e), dtype=float) for e in edges]
    common = bool(vectors) and all(np.allclose(v, vectors[0], rtol=0, atol=1e-12) for v in vectors)
    pre.add("common_offset_vector", common, f"vectors={[v.tolist() for v in vectors[:2]]}...")
    pre.add("offset_is_pure_z", bool(vectors) and abs(vectors[0][0]) < 1e-12 and abs(vectors[0][1]) < 1e-12,
            "the CCX equations assume the offset is along the plate normal (+z)")
    offset = float(vectors[0][2]) if vectors else float("nan")
    mismatch = []
    for e, v in zip(edges, vectors):
        _a, ly, lz = frames[e]
        if sec.offset_mode == "automatic":
            cy, cz = sec.centroid_from_attachment()
            expected = cy * ly + cz * lz
        elif sec.offset_mode == "centerline":
            expected = np.zeros(3)
        elif sec.attachment_side == "front" and float(sec.rotation_deg) == 0.0:
            expected = float(sec.eccentricity) * lz
        else:
            continue  # back / rotated manual sections: the project's frame is the definition, nothing independent to compare
        if not np.allclose(v, expected, rtol=0, atol=1e-12):
            mismatch.append((e, v.tolist(), np.asarray(expected).tolist()))
    pre.add("offset_vector_follows_section_definition", bool(edges) and not mismatch,
            f"mode={sec.offset_mode}, eccentricity={sec.eccentricity}; mismatches={mismatch[:2]}")
    pre.observe("offset_vs_geometry_coincidences",
                f"offset={offset!r}; plate_t/2={plate_t / 2.0!r}; bar half height={height_z / 2.0!r}; "
                f"equal to plate_t/2: {math.isclose(offset, plate_t / 2.0, abs_tol=1e-15)}; "
                f"equal to bar half height: {math.isclose(offset, height_z / 2.0, abs_tol=1e-15)}.  "
                "Equal values cannot say which mechanics the project means; the definition check above does.",
                offset=offset, plate_half=plate_t / 2.0, bar_half_height=height_z / 2.0)
    spec = StiffenedSpec(E=E, nu=nu, plate_t=plate_t, pressure=pressures.pop() if len(pressures) == 1 else float("nan"),
                         bar_width=width_y, bar_height=height_z, offset=offset,
                         solver_section={k: float(props[k]) for k in ("area", "Iy", "Iz", "J")},
                         offset_mode=str(sec.offset_mode))
    return spec, pre


def _plate_thickness(project, plate_section: str) -> float:
    sections = getattr(project, "plate_sections", None)
    if sections is None or plate_section not in sections:
        raise KeyError("plate section thickness not found via project.plate_sections; pass it explicitly")
    return sections[plate_section].thickness


def coupling_report(built, spec: "StiffenedSpec", pre: Preflight) -> dict:
    """Mesh coupling of the built ANYfem model against the spec (``mesh.couplings`` as ``_add_couplings`` reads it).

    Each coupling is ``beam_node <- sum_i w_i plate_node_i`` with eccentricity ``r``.  The CCX deck ties each beam
    node to ONE coincident plate node with ``r = (0, 0, offset)``; an interpolated (multi-master) coupling, a
    non-unit weight or an eccentricity different from the spec is a preflight failure, not a choice made here.
    """

    mesh = built.mesh
    bad_masters, bad_weights, bad_ecc, bad_geom = [], [], [], []
    for cid, c in mesh.couplings.items():
        if not hasattr(c, "beam_node"):
            bad_masters.append(cid)
            continue
        masters = [(int(n), float(w)) for n, w in zip(c.plate_nodes, c.weights) if abs(float(w)) > 1e-12]
        if len(masters) != 1:
            bad_masters.append(cid)
            continue
        if abs(masters[0][1] - 1.0) > 1e-12:
            bad_weights.append(cid)
        ecc = np.asarray(c.eccentricity, dtype=float)
        if not np.allclose(ecc, (0.0, 0.0, spec.offset), rtol=0, atol=1e-12):
            bad_ecc.append((cid, ecc.tolist()))
        gap = np.asarray(mesh.nodes[int(c.beam_node)], dtype=float) - np.asarray(mesh.nodes[masters[0][0]], dtype=float)
        if not np.allclose(gap, ecc, rtol=0, atol=1e-12):
            bad_geom.append((cid, gap.tolist()))
    n = len(mesh.couplings)
    pre.add("couplings_exist", n > 0, f"count={n}")
    pre.add("couplings_single_master", not bad_masters, f"interpolated or legacy couplings={bad_masters[:5]}")
    pre.add("couplings_unit_weight", not bad_weights, f"{bad_weights[:5]}")
    pre.add("coupling_eccentricity_equals_spec_offset", not bad_ecc, f"{bad_ecc[:3]} spec offset={spec.offset!r}")
    pre.add("coupling_eccentricity_equals_node_gap", not bad_geom, f"{bad_geom[:3]}")
    return {"couplings": n}


# ----------------------------------------------------------------------------------------------
# 2. Supports and loads: closure against the built ANYfem model
# ----------------------------------------------------------------------------------------------
def _edge_names(x: float, y: float, tol: float = 1e-9) -> Tuple[str, ...]:
    names = []
    if abs(x) < tol:
        names.append("x0")
    if abs(x - 1.0) < tol:
        names.append("x1")
    if abs(y) < tol:
        names.append("y0")
    if abs(y - 1.0) < tol:
        names.append("y1")
    return tuple(names)


def supports_from_built(built) -> Tuple[Dict[Tuple[float, float, float], Tuple[str, ...]], List[dict]]:
    """Constrained dofs of the ANYfem model by node coordinate (union over boundary conditions).

    Returns ``(constraints, nonzero)``; ``nonzero`` lists prescribed non-zero values (a preflight failure).
    """

    model = built.fe_model
    out: Dict[Tuple[float, float, float], set] = {}
    nonzero = []
    for bc in model.boundary_conditions:
        for node in bc.node_ids:
            key = tuple(round(float(c), 12) for c in model.mesh.get_node(int(node)).coords())
            for name, value in bc.dof_constraints.items():
                out.setdefault(key, set()).add(str(name))
                if value not in (0, 0.0, None):
                    nonzero.append({"node": int(node), "dof": str(name), "value": float(value)})
    return {k: tuple(n for n in DOF_NAMES if n in v) for k, v in out.items()}, nonzero


def project_supports_to_edges(constraints: Mapping[Tuple[float, float, float], Sequence[str]],
                              pre: Preflight) -> Dict[str, Tuple[str, ...]]:
    """Reduce per-node constraints to one dof set per plate edge and verify the projection is lossless.

    Non-corner nodes of an edge must agree; corner nodes must carry exactly the union of their two
    edges.  Only plate-plane nodes (z = 0) are considered; offset beam nodes are not supported.
    """

    plate = {k: v for k, v in constraints.items() if abs(k[2]) < 1e-12}
    pre.add("no_support_off_plate", len(plate) == len(constraints), f"off-plate constrained nodes={len(constraints) - len(plate)}")
    per_edge: Dict[str, set] = {}
    uniform = True
    for (x, y, _z), dofs in plate.items():
        edges = _edge_names(x, y)
        if len(edges) == 1:
            per_edge.setdefault(edges[0], set()).add(tuple(dofs))
        if not edges:
            uniform = False
    pre.add("support_nodes_on_boundary", uniform, "a constrained plate node lies off the boundary" if not uniform else "")
    projected: Dict[str, Tuple[str, ...]] = {}
    for edge in ("x0", "x1", "y0", "y1"):
        sets = per_edge.get(edge, set())
        pre.add(f"support_uniform_{edge}", len(sets) == 1, f"distinct dof sets on {edge}: {sorted(sets)}")
        projected[edge] = tuple(sorted(next(iter(sets)), key=DOF_NAMES.index)) if len(sets) == 1 else ()
    for (x, y, _z), dofs in plate.items():
        edges = _edge_names(x, y)
        if len(edges) == 2:
            union = {d for e in edges for d in projected[e]}
            pre.add(f"corner_union_{edges[0]}_{edges[1]}", union == set(dofs),
                    f"corner ({x},{y}) built={sorted(dofs)} projected_union={sorted(union)}")
    return projected


def spec_with_built_supports(spec: StiffenedSpec, built, pre: Preflight) -> StiffenedSpec:
    """Replace the assumed edge dof sets by the built ANYfem model's own (lossless projection or preflight failure)."""

    constraints, nonzero = supports_from_built(built)
    pre.add("no_prescribed_nonzero_support_values", not nonzero, f"nonzero={nonzero[:3]}")
    edges = project_supports_to_edges(constraints, pre)
    return dataclasses.replace(spec, edge_dofs=edges, edge_dofs_source="built_model")


def deck_constraints(spec: StiffenedSpec, n: int) -> Dict[int, Tuple[int, ...]]:
    """Plate node -> constrained dof numbers (1-6) for a deck of ``n`` elements per side."""

    out: Dict[int, set] = {}
    m = 2 * n
    for j in range(m + 1):
        for i in range(m + 1):
            if i % 2 and j % 2:
                continue
            x, y = i / m, j / m
            dofs = {DOF_NAMES.index(d) + 1 for e in _edge_names(x, y, 1e-12) for d in spec.edge_dofs[e]}
            if dofs:
                out[plate_node(n, i, j)] = dofs
    return {k: tuple(sorted(v)) for k, v in out.items()}


def anyfem_load_totals(project, mesh, case: str = "default") -> dict:
    """Assembled nodal force/moment totals of the ANYfem model (same recipe as tests/test_submodel_cut.assembled_totals)."""

    from anyfem.solve.build import build_fe_model
    from anysolver.matrix_assembly import assemble_load_vector

    built = build_fe_model(project, mesh, case, require_loads=False, require_supports=False)
    load, _info = assemble_load_vector(built.fe_model, built.load_case)
    load = np.asarray(load, dtype=float).reshape(-1)
    six = load.reshape(-1, 6)
    force, moment_nodal, moment_r_x_f = np.zeros(3), np.zeros(3), np.zeros(3)
    for node in sorted(mesh.nodes):
        dofs = built.node_dofs(node)
        f, m = load[dofs[0:3]], load[dofs[3:6]]
        force += f
        moment_nodal += m
        moment_r_x_f += np.cross(np.asarray(mesh.nodes[node], dtype=float), f)
    return {"force": force.tolist(), "moment_dof_sum": six[:, 3:].sum(axis=0).tolist(),
            "moment_about_origin": (moment_r_x_f + moment_nodal).tolist(), "nodal_couples": moment_nodal.tolist()}


def pressure_force_z(spec: StiffenedSpec, area: float = 1.0) -> float:
    """z resultant of the pressure in the ANYfem sign convention (positive project pressure -> +z)."""

    return PRESSURE_FORCE_SIGN * spec.pressure * area


def applied_totals(spec: StiffenedSpec, area: float = 1.0) -> dict:
    """Resultant of the CCX deck's loads about the origin, derived from the spec (pressure toward +z as ANYfem assembles it)."""

    fz = pressure_force_z(spec, area)
    f1, f2 = spec.line_load_x1 * 1.0, spec.line_load_y1 * 1.0
    force = np.array([f1 + f2, 0.0, fz])
    pts = [(np.array([0.5, 0.5, 0.0]), np.array([0.0, 0.0, fz])),
           (np.array([1.0, 0.5, 0.0]), np.array([f1, 0.0, 0.0])),
           (np.array([0.5, 1.0, 0.0]), np.array([f2, 0.0, 0.0]))]
    moment = sum(np.cross(r, f) for r, f in pts)
    return {"force": force.tolist(), "moment_about_origin": np.asarray(moment).tolist()}


def check_load_closure_vs_anyfem(spec: StiffenedSpec, anyfem_totals: dict, pre: Preflight, rel: float = 1e-9):
    """The ANYfem assembled force and moment must equal the deck's.  ``rel`` is the assembly arithmetic, not a result tolerance."""

    mine = np.asarray(applied_totals(spec)["force"])
    theirs = np.asarray(anyfem_totals["force"])
    err = float(np.max(np.abs(mine - theirs)) / max(np.max(np.abs(mine)), 1e-300))
    pre.add("load_totals_match_anyfem_assembly", err <= rel, f"deck={mine.tolist()} anyfem={theirs.tolist()} rel={err:.3e}")
    if "moment_about_origin" in anyfem_totals:
        m_mine = np.asarray(applied_totals(spec)["moment_about_origin"])
        m_theirs = np.asarray(anyfem_totals["moment_about_origin"])
        m_err = float(np.max(np.abs(m_mine - m_theirs)) / max(np.max(np.abs(m_mine)), 1e-300))
        pre.add("moment_about_origin_matches_anyfem_assembly", m_err <= rel,
                f"deck={m_mine.tolist()} anyfem={m_theirs.tolist()} rel={m_err:.3e}")


# ----------------------------------------------------------------------------------------------
# 3. Shared deck pieces
# ----------------------------------------------------------------------------------------------
def _fmt(x: float) -> str:
    return repr(float(x))


def _ranges(dofs: Iterable[int]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for d in sorted(set(dofs)):
        if out and out[-1][1] == d - 1:
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def _chunks(seq, k):
    seq = list(seq)
    return [seq[i:i + k] for i in range(0, len(seq), k)]


def link_equations(beam_node: int, plate_node_id: int, ecc: Sequence[float]) -> List[str]:
    """Six ``*EQUATION`` blocks u_b = u_p + theta x r, theta_b = theta_p for the eccentricity vector ``r`` (zero terms dropped).

    (theta x r) = (ty rz - tz ry, tz rx - tx rz, tx ry - ty rx); each block is ``count`` then one line of triples.
    """

    ex, ey, ez = (float(v) for v in ecc)
    terms = {1: [(5, -ez), (6, ey)], 2: [(6, -ex), (4, ez)], 3: [(4, -ey), (5, ex)]}
    rows: List[str] = []
    for comp, extra in terms.items():
        extra = [(d, c) for d, c in extra if c != 0.0]
        fields = [f"{beam_node}, {comp}, 1.0", f"{plate_node_id}, {comp}, -1.0"] + [f"{plate_node_id}, {d}, {_fmt(c)}" for d, c in extra]
        rows += [str(len(fields)), ", ".join(fields)]
    for d in (4, 5, 6):
        rows += ["2", f"{beam_node}, {d}, 1.0, {plate_node_id}, {d}, -1.0"]
    return rows


S8R_PRESSURE_WEIGHTS = (-1.0 / 12.0,) * 4 + (1.0 / 3.0,) * 4  # consistent nodal weights of a uniform pressure, corners then mid-sides


def check_deck_syntax(text: str, *, max_line: int = MAX_DECK_LINE, density: float = ID_DENSITY) -> dict:
    """Structural review of a CCX deck: line length, equation shape, id density.  Raises ``ValueError`` listing every problem.

    * every line <= ``max_line`` characters (CCX reads 132);
    * in ``*EQUATION`` the term count equals the number of node/dof/coefficient triples on its data line;
    * the largest node id and element id are <= ``density`` x their counts (CCX sizes arrays by the largest id).
    """

    problems: List[str] = []
    keyword, expect_terms = "", None
    node_ids: List[int] = []
    element_ids: List[int] = []
    longest = 0
    for number, line in enumerate(text.splitlines(), 1):
        longest = max(longest, len(line))
        if len(line) > max_line:
            problems.append(f"line {number} has {len(line)} > {max_line} characters")
        if not line.strip() or line.startswith("**"):
            continue
        if line.startswith("*"):
            keyword, expect_terms = line.split(",")[0].strip().upper(), None
            continue
        fields = [f.strip() for f in line.split(",") if f.strip()]
        if keyword == "*NODE" and fields:
            node_ids.append(int(fields[0]))
        elif keyword == "*ELEMENT" and fields:
            element_ids.append(int(fields[0]))
        elif keyword == "*EQUATION":
            if expect_terms is None:
                expect_terms = int(fields[0])
                if len(fields) != 1:
                    problems.append(f"line {number}: equation header must hold only the term count")
            else:
                if len(fields) != 3 * expect_terms:
                    problems.append(f"line {number}: {expect_terms} terms announced, {len(fields)} fields found")
                expect_terms = None
    for what, ids in (("node", node_ids), ("element", element_ids)):
        if ids and max(ids) > density * len(ids):
            problems.append(f"{what} ids are sparse: max {max(ids)} for {len(ids)} {what}s")
        if len(ids) != len(set(ids)):
            problems.append(f"duplicate {what} ids")
    if problems:
        raise ValueError("deck syntax review failed: " + "; ".join(problems[:8]))
    return {"longest_line": longest, "nodes": len(node_ids), "max_node_id": max(node_ids, default=0),
            "elements": len(element_ids), "max_element_id": max(element_ids, default=0)}


# ----------------------------------------------------------------------------------------------
# 4. A5 deck
# ----------------------------------------------------------------------------------------------
def plate_node(n: int, i: int, j: int) -> int:
    return 1 + i + j * (2 * n + 1)


def a5_beam_lattice(spec: StiffenedSpec, n: int) -> Dict[Tuple[int, int], int]:
    """Lattice station (i, j) -> beam node id, ids dense and directly after the plate lattice (no 10^6 offset)."""

    m = 2 * n
    iy, ix = round(spec.stiffener_y * m), round(spec.stiffener_x * m)
    if iy % 2 or ix % 2:
        raise ValueError("stiffener lines must fall on element corner nodes")
    stations = [(i, iy) for i in range(0, m + 1, 2)]
    seen = set(stations)
    stations += [(ix, j) for j in range(0, m + 1, 2) if (ix, j) not in seen]
    first = (m + 1) ** 2 + 1
    return {st: first + k for k, st in enumerate(stations)}


def probe_nodes(spec: StiffenedSpec, n: int) -> dict:
    """Plate node at the centre, plate node and beam node on the stiffener line at (0.5, 0.3)."""

    iy = round(spec.stiffener_y * 2 * n)
    lattice = a5_beam_lattice(spec, n)
    return {"centre": plate_node(n, n, n), "stiffener": plate_node(n, n, iy), "beam": lattice[(n, iy)]}


def a5_deck(spec: StiffenedSpec, n: int) -> str:
    """CCX 2.22 input for the stiffened panel at ``n`` S8R elements per side (n a multiple of 10).

    The pressure is applied as explicit consistent nodal forces toward +z (``PRESSURE_FORCE_SIGN``), not as ``*DLOAD``.
    """

    if n % 10:
        raise ValueError("n must be a multiple of 10 so that the 0.3 / 0.7 stiffener lines are element edges")
    m = 2 * n
    lattice = a5_beam_lattice(spec, n)
    iy, ix = round(spec.stiffener_y * m), round(spec.stiffener_x * m)
    L: List[str] = [f"** ANYfem S6 A5 reference, ccx {CCX_VERSION}, n={n}, h={1.0 / n!r}",
                    "** GENERATED by reference_ccx.a5_deck; independent of the ANYfileIO exporter",
                    "*NODE, NSET=NPLATE"]
    for j in range(m + 1):
        for i in range(m + 1):
            if not (i % 2 and j % 2):
                L.append(f"{plate_node(n, i, j)}, {_fmt(i / m)}, {_fmt(j / m)}, 0.0")
    L.append("*NODE, NSET=NBEAM")
    for (i, j), nid in sorted(lattice.items(), key=lambda kv: kv[1]):
        L.append(f"{nid}, {_fmt(i / m)}, {_fmt(j / m)}, {_fmt(spec.offset)}")
    L.append("*ELEMENT, TYPE=S8R, ELSET=PLATE")
    eid = 0
    plate_elements: List[List[int]] = []
    for ey in range(n):
        for ex in range(n):
            i0, j0 = 2 * ex, 2 * ey
            nodes = [plate_node(n, i0, j0), plate_node(n, i0 + 2, j0), plate_node(n, i0 + 2, j0 + 2), plate_node(n, i0, j0 + 2),
                     plate_node(n, i0 + 1, j0), plate_node(n, i0 + 2, j0 + 1), plate_node(n, i0 + 1, j0 + 2), plate_node(n, i0, j0 + 1)]
            eid += 1
            plate_elements.append(nodes)
            L.append(f"{eid}, " + ", ".join(map(str, nodes)))
    belem = eid
    L.append("*ELEMENT, TYPE=B31, ELSET=STIFX")  # beams along x (on y = 0.3): section axis n1 = +y
    for i in range(0, m, 2):
        belem += 1
        L.append(f"{belem}, {lattice[(i, iy)]}, {lattice[(i + 2, iy)]}")
    L.append("*ELEMENT, TYPE=B31, ELSET=STIFY")  # beams along y (on x = 0.7): section axis n1 = +x
    for j in range(0, m, 2):
        belem += 1
        L.append(f"{belem}, {lattice[(ix, j)]}, {lattice[(ix, j + 2)]}")
    L += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{_fmt(spec.E)}, {_fmt(spec.nu)}",
          "*SHELL SECTION, ELSET=PLATE, MATERIAL=STEEL", f"{_fmt(spec.plate_t)}",
          # dim1 = bar_width along n1 (in-plane = the project's local y), dim2 = bar_height along n2 (= +z, the plate normal).
          # Assumption: verified by calibration 1 (beam frame), not by this file.
          "*BEAM SECTION, ELSET=STIFX, MATERIAL=STEEL, SECTION=RECT", f"{_fmt(spec.bar_width)}, {_fmt(spec.bar_height)}", "0.0, 1.0, 0.0",
          "*BEAM SECTION, ELSET=STIFY, MATERIAL=STEEL, SECTION=RECT", f"{_fmt(spec.bar_width)}, {_fmt(spec.bar_height)}", "1.0, 0.0, 0.0"]
    # rigid eccentric link beam node <- coincident plate node, r = (0, 0, offset); gated by calibrations 2 and 3
    L.append("** shell-beam link on rotation dofs 4-6: usable only after calibrations 2 and 3 accepted it")
    L.append("*EQUATION")
    for (i, j), b in sorted(lattice.items(), key=lambda kv: kv[1]):
        L += link_equations(b, plate_node(n, i, j), (0.0, 0.0, spec.offset))
    cons = deck_constraints(spec, n)
    L.append("*BOUNDARY")
    for node, dofs in sorted(cons.items()):
        for a, b in _ranges(dofs):
            L.append(f"{node}, {a}, {b}")
    L.append("*NSET, NSET=SUPPORT")
    L += [", ".join(map(str, chunk)) for chunk in _chunks(sorted(cons), 8)]
    ids = probe_nodes(spec, n)
    L += ["*NSET, NSET=PROBES", ", ".join(str(v) for v in ids.values())]
    # loads: explicit consistent nodal forces (pressure toward +z, ANYfem sign) and consistent edge line loads in +x
    fz: Dict[int, float] = {}
    h2 = (1.0 / n) ** 2
    pz = pressure_force_z(spec)
    for nodes in plate_elements:
        for nid, w in zip(nodes, S8R_PRESSURE_WEIGHTS):
            fz[nid] = fz.get(nid, 0.0) + pz * h2 * w
    fx: Dict[int, float] = {}
    w3 = (1.0 / 6.0, 4.0 / 6.0, 1.0 / 6.0)  # consistent nodal weights of a quadratic edge
    h = 1.0 / n
    for edge_nodes, q in (([(m, j) for j in range(m + 1)], spec.line_load_x1), ([(i, m) for i in range(m + 1)], spec.line_load_y1)):
        for e0 in range(0, m, 2):
            for k in range(3):
                nid = plate_node(n, *edge_nodes[e0 + k])
                fx[nid] = fx.get(nid, 0.0) + q * h * w3[k]
    L += ["*STEP", "*STATIC", "*CLOAD"]
    for nid in sorted(set(fx) | set(fz)):
        if nid in fx:
            L.append(f"{nid}, 1, {_fmt(fx[nid])}")
        if nid in fz:
            L.append(f"{nid}, 3, {_fmt(fz[nid])}")
    L += ["*NODE PRINT, NSET=PROBES", "U", "*NODE PRINT, NSET=SUPPORT", "RF", "*END STEP"]
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------------------------------
# 5. Calibration 1: B31 frame (pinned-pinned, no clamp)
# ----------------------------------------------------------------------------------------------
CAL_BEAM_FRAME, CAL_RY_LINK, CAL_RX_LINK, CAL_PRESSURE_SIGN = (
    "cal1_beam_frame", "cal2_shell_beam_link_ry", "cal3_shell_beam_link_rx", "cal4_pressure_sign")
CALIBRATION_ORDER = (CAL_BEAM_FRAME, CAL_RY_LINK, CAL_RX_LINK, CAL_PRESSURE_SIGN)  # cal1-cal3 and the sign check gate A5
BEAM_CAL_LENGTH, BEAM_CAL_ELEMENTS, BEAM_CAL_LOAD = 1.0, 10, 100.0
BEAM_CAL_MID = BEAM_CAL_ELEMENTS // 2 + 1
SHEAR_CORRECTION = 5.0 / 6.0  # rectangle; only used for the predicted midspan shear part, which is <= a few percent here
GROSS_SCREEN = (0.5, 2.0)  # a mechanism "works at all": measured / closed form within a factor 2 (never an accuracy claim)
REACTION_SCREEN = 1.0e-3  # reaction closure: 100 x the 6-digit print precision


def calibration_beam_frame_deck(spec: StiffenedSpec, length: float = BEAM_CAL_LENGTH, elements: int = BEAM_CAL_ELEMENTS,
                                load: float = BEAM_CAL_LOAD) -> str:
    """Calibration 1: simply supported B31 (node 1: ux uy uz rx; end node: uy uz), midspan force, two steps.

    No rotational clamp is used: dev-04 gave a cantilever tip uz of +28810 for 1 N, so the clamp did not hold.  Here the
    supports only fix translations (plus the torsion mode rx at node 1, which does not enter bending).  Section as
    STIFX (``bar_width`` along n1 = +y, ``bar_height`` along n2 = +z).  Step 1 (time 1): midspan -z; step 2 (time 2):
    midspan -y (OP=NEW).  ``check_beam_frame`` reads the midspan deflections and the support reactions.
    """

    mid, end = elements // 2 + 1, elements + 1
    L = [f"** CAL1: simply supported B31, section as STIFX (RECT bar_width x bar_height, n1 = +y); step1 mid -z, step2 mid -y",
         "*NODE"]
    L += [f"{k + 1}, {_fmt(k * length / elements)}, 0.0, 0.0" for k in range(elements + 1)]
    L += ["*ELEMENT, TYPE=B31, ELSET=BEAM"] + [f"{k + 1}, {k + 1}, {k + 2}" for k in range(elements)]
    L += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{_fmt(spec.E)}, {_fmt(spec.nu)}",
          "*BEAM SECTION, ELSET=BEAM, MATERIAL=STEEL, SECTION=RECT", f"{_fmt(spec.bar_width)}, {_fmt(spec.bar_height)}", "0.0, 1.0, 0.0",
          "*BOUNDARY", "1, 1, 4", f"{end}, 2, 3",
          "*NSET, NSET=MID", f"{mid}", "*NSET, NSET=SUPPORT", f"1, {end}",
          "*STEP", "*STATIC", "*CLOAD", f"{mid}, 3, {_fmt(-load)}", "*NODE PRINT, NSET=MID", "U", "*NODE PRINT, NSET=SUPPORT", "RF", "*END STEP",
          "*STEP", "*STATIC", "*CLOAD, OP=NEW", f"{mid}, 2, {_fmt(-load)}", "*NODE PRINT, NSET=MID", "U", "*NODE PRINT, NSET=SUPPORT", "RF", "*END STEP"]
    return "\n".join(L) + "\n"


def beam_midspan_prediction(spec: StiffenedSpec, inertia: float, length: float = BEAM_CAL_LENGTH, load: float = BEAM_CAL_LOAD) -> float:
    """Midspan deflection of a simply supported beam: P L^3 / (48 E I) + P L / (4 kappa G A)."""

    shear = spec.E / (2.0 * (1.0 + spec.nu))
    return load * length ** 3 / (48.0 * spec.E * inertia) + load * length / (4.0 * SHEAR_CORRECTION * shear * spec.beam_area)


def classify_beam_frame(mid_uz: float, mid_uy: float, spec: StiffenedSpec, length: float = BEAM_CAL_LENGTH,
                        load: float = BEAM_CAL_LOAD) -> dict:
    """Midspan deflections (z from step 1, y from step 2) against both rectangle inertias.

    ``frame_confirmed``: the z deflection is closest to the 'vertical' inertia ``bar_width*bar_height^3/12`` AND the y
    deflection to the 'lateral' one, AND each lies within the gross screen (factor 2) of its identified prediction.
    """

    cand = {"vertical_w*h^3/12": spec.inertia_vertical(), "lateral_h*w^3/12": spec.inertia_lateral()}
    pred = {k: beam_midspan_prediction(spec, inertia, length, load) for k, inertia in cand.items()}

    def closest(u):
        ratio = {k: abs(u) / v for k, v in pred.items()}
        return min(ratio, key=lambda k: abs(math.log(ratio[k]))), ratio

    best_z, ratio_z = closest(mid_uz)
    best_y, ratio_y = closest(mid_uy)
    lo, hi = GROSS_SCREEN
    inside = lo <= ratio_z[best_z] <= hi and lo <= ratio_y[best_y] <= hi
    return {"predicted_euler_plus_shear": pred, "z_step": {"measured_abs": abs(mid_uz), "over_predicted": ratio_z, "closest": best_z},
            "y_step": {"measured_abs": abs(mid_uy), "over_predicted": ratio_y, "closest": best_y}, "within_gross_screen": inside,
            "frame_confirmed": best_z.startswith("vertical") and best_y.startswith("lateral") and inside}


def check_beam_frame(blocks: Sequence["DatBlock"], spec: StiffenedSpec, length: float = BEAM_CAL_LENGTH, load: float = BEAM_CAL_LOAD) -> dict:
    """Read calibration 1: classification plus support-reaction closure for both steps (the supports must have held)."""

    uz = displacements_at(blocks, "MID", 1.0)[BEAM_CAL_MID][2]
    uy = displacements_at(blocks, "MID", 2.0)[BEAM_CAL_MID][1]
    closure = {}
    for step, comp in ((1.0, 2), (2.0, 1)):
        total = sum(r[comp] for r in reactions_at(blocks, "SUPPORT", step).values())
        closure[f"step{int(step)}"] = {"reaction_sum": total, "applied": -load, "closure_ratio": abs(total - load) / load}
    result = classify_beam_frame(uz, uy, spec, length, load)
    result["reaction_closure"] = closure
    result["supports_held"] = all(c["closure_ratio"] <= REACTION_SCREEN for c in closure.values())
    result["frame_confirmed"] = bool(result["frame_confirmed"] and result["supports_held"])
    return result


# ----------------------------------------------------------------------------------------------
# 6. Calibrations 2 and 3: shell-beam rotation link against a Kirchhoff strip (nu = 0)
# ----------------------------------------------------------------------------------------------
LINK_LENGTH, LINK_WIDTH, LINK_NX, LINK_LOAD = 0.5, 0.1, 4, 10.0
LINK_STUB_LENGTH, LINK_STUB_SIZE = 0.1, 1.0e-3  # free stub beam: EI = E*(1e-3)^4/12 ~ 1.75e-4 vs the strip's 1.75e3 (relative 1e-7)


def link_ids(nx: int = LINK_NX) -> dict:
    """Ids of the strip deck: tip mid-edge plate node, stub root (the beam node tied to it), stub end, clamp nodes, tip edge nodes."""

    stride = 2 * nx + 1
    return {"plate": 1 + 2 * nx + stride, "beam": 3 * stride + 1, "stub_end": 3 * stride + 2,
            "clamp": [1 + j * stride for j in range(3)], "tip_edge": [1 + 2 * nx + j * stride for j in range(3)]}


def calibration_rotation_link_deck(spec: StiffenedSpec, axis: str) -> str:
    """Calibration 2 (``axis='x'``, bending about y) or 3 (``axis='y'``, bending about x): the A5 shell-beam link on a strip.

    An S8R cantilever strip (L = 0.5, b = 0.1, t = spec.plate_t, nu = 0 so it is exactly a Kirchhoff beam in bending) is
    clamped on all six dofs at s = 0 and loaded by a total ``LINK_LOAD`` in +z at the free edge (consistent weights).
    A soft free B31 stub starts at a beam node standing ``spec.offset`` above the free-edge mid node and is tied to it by
    the SAME six ``*EQUATION`` blocks as the A5 deck.  The stub is only the carrier of rotation dofs 4-6 at the beam node;
    its stiffness is ~1e-7 of the strip's (no artificial stiffness anywhere).  ``check_rotation_link`` compares the printed
    offset ``u_beam - u_plate = theta x r = -e theta`` (along the strip axis) with ``theta = P L^2 / (2 E I)``; a link that
    ignores the plate rotation prints zero.  Plate rotation itself is not printed by CCX, which is why the gauge is the
    displacement of two actual nodes.  Clamp reactions are printed: a clamp that does not hold shows as non-closure.
    """

    if axis not in ("x", "y"):
        raise ValueError("axis must be 'x' or 'y'")
    nx, b, length, e = LINK_NX, LINK_WIDTH, LINK_LENGTH, spec.offset
    ids = link_ids(nx)
    stride = 2 * nx + 1

    def xyz(s: float, t: float, z: float = 0.0):
        # axis x: s along +x, t along +y.  axis y: s along +y, t along -x (+ b) so that e_s x e_t = +z (counter-clockwise shells)
        return (s, t, z) if axis == "x" else (b - t, s, z)

    L = [f"** CAL{2 if axis == 'x' else 3}: S8R Kirchhoff strip along {axis} (nu=0), clamped at s=0, tip load +z, soft B31 stub tied by A5 link equations",
         "*NODE, NSET=NSTRIP"]
    for j in range(3):
        for i in range(stride):
            if not (i % 2 and j % 2):
                x, y, z = xyz(length * i / (2 * nx), b * j / 2.0)
                L.append(f"{1 + i + j * stride}, {_fmt(x)}, {_fmt(y)}, {_fmt(z)}")
    x, y, z = xyz(length, b / 2.0, e)
    xe, ye, ze = xyz(length + LINK_STUB_LENGTH, b / 2.0, e)
    L += ["*NODE, NSET=NSTUB", f"{ids['beam']}, {_fmt(x)}, {_fmt(y)}, {_fmt(z)}", f"{ids['stub_end']}, {_fmt(xe)}, {_fmt(ye)}, {_fmt(ze)}"]
    L.append("*ELEMENT, TYPE=S8R, ELSET=STRIP")
    for ex in range(nx):
        i0 = 2 * ex
        nodes = [1 + i0, 1 + i0 + 2, 1 + i0 + 2 + 2 * stride, 1 + i0 + 2 * stride,
                 1 + i0 + 1, 1 + i0 + 2 + stride, 1 + i0 + 1 + 2 * stride, 1 + i0 + stride]
        L.append(f"{ex + 1}, " + ", ".join(map(str, nodes)))
    n1 = "0.0, 1.0, 0.0" if axis == "x" else "1.0, 0.0, 0.0"
    L += ["*ELEMENT, TYPE=B31, ELSET=STUB", f"{nx + 1}, {ids['beam']}, {ids['stub_end']}",
          "*MATERIAL, NAME=CAL", "*ELASTIC", f"{_fmt(spec.E)}, 0.0",
          "*SHELL SECTION, ELSET=STRIP, MATERIAL=CAL", _fmt(spec.plate_t),
          "*BEAM SECTION, ELSET=STUB, MATERIAL=CAL, SECTION=RECT", f"{_fmt(LINK_STUB_SIZE)}, {_fmt(LINK_STUB_SIZE)}", n1,
          "*EQUATION"]
    L += link_equations(ids["beam"], ids["plate"], (0.0, 0.0, e))
    L.append("*BOUNDARY")
    L += [f"{k}, 1, 6" for k in ids["clamp"]]
    L += ["*NSET, NSET=LINK", f"{ids['plate']}, {ids['beam']}", "*NSET, NSET=CLAMP", ", ".join(map(str, ids["clamp"])),
          "*STEP", "*STATIC", "*CLOAD"]
    for k, w in zip(ids["tip_edge"], (1.0 / 6.0, 4.0 / 6.0, 1.0 / 6.0)):
        L.append(f"{k}, 3, {_fmt(LINK_LOAD * w)}")
    L += ["*NODE PRINT, NSET=LINK", "U", "*NODE PRINT, NSET=CLAMP", "RF", "*END STEP"]
    return "\n".join(L) + "\n"


def rotation_link_expected(spec: StiffenedSpec) -> dict:
    """Closed forms of the strip (nu = 0): tip rotation, tip deflection (with Timoshenko shear), expected link offset ``-e theta``."""

    t, b, length, p, e = spec.plate_t, LINK_WIDTH, LINK_LENGTH, LINK_LOAD, spec.offset
    inertia = b * t ** 3 / 12.0
    shear = spec.E / 2.0  # nu = 0
    theta = p * length ** 2 / (2.0 * spec.E * inertia)
    w_tip = p * length ** 3 / (3.0 * spec.E * inertia) + p * length / (SHEAR_CORRECTION * shear * b * t)
    return {"theta_tip": theta, "w_tip": w_tip, "offset_along_axis": -e * theta, "inertia": inertia}


def check_rotation_link(spec: StiffenedSpec, blocks: Sequence["DatBlock"], axis: str) -> dict:
    """Read calibration 2/3.  ``link_effective``: clamp reactions close, tip deflection and link offset inside the gross screen with the right sign."""

    ids = link_ids()
    d = displacements(blocks, "LINK")
    up, ub = np.asarray(d[ids["plate"]], dtype=float), np.asarray(d[ids["beam"]], dtype=float)
    exp = rotation_link_expected(spec)
    comp = 0 if axis == "x" else 1
    offset = float((ub - up)[comp])
    rf = reactions(blocks, "CLAMP")
    closure = abs(sum(r[2] for r in rf.values()) + LINK_LOAD) / LINK_LOAD
    lo, hi = GROSS_SCREEN
    w_ratio = float(up[2] / exp["w_tip"])
    ratio = offset / exp["offset_along_axis"]
    other = [float((ub - up)[k]) for k in range(3) if k != comp]
    return {"axis": axis, "measured_offset": offset, "expected_offset": exp["offset_along_axis"], "offset_ratio": ratio,
            "other_offset_components": other, "tip_w_measured": float(up[2]), "tip_w_expected": exp["w_tip"], "tip_w_ratio": w_ratio,
            "clamp_reaction_closure": closure, "theta_gauge": -offset / spec.offset, "theta_expected": exp["theta_tip"],
            "link_effective": bool(closure <= REACTION_SCREEN and lo <= w_ratio <= hi and lo <= ratio <= hi),
            "note": "gross screens only (factor 2 / 1e-3): they show the link transmits the shell rotation, not that it is accurate"}


# ----------------------------------------------------------------------------------------------
# 7. Calibration 4: the explicit pressure loads against *DLOAD P1
# ----------------------------------------------------------------------------------------------
def calibration_pressure_sign_deck(spec: StiffenedSpec, length: float = 1.0, width: float = 0.2) -> str:
    """Calibration 4: one S8R strip clamped (dofs 1-6) at x = 0.  Step 1: explicit consistent nodal forces toward +z for p = 1.
    Step 2: ``*DLOAD`` ``P1`` with p = 1 (the explicit loads are reset by ``*CLOAD, OP=NEW`` with a zero entry).

    The A5 deck uses the explicit loads.  This deck shows (a) they deflect toward +z and (b) what P1 does for a counter-clockwise
    shell, with the magnitude ratio between the two (the consistent weights equal CCX's own pressure)."""

    area = length * width
    L = ["** CAL4: explicit +z consistent loads (step 1) vs *DLOAD P1 (step 2) on a CCW S8R strip, clamped at x=0", "*NODE",
         "1, 0.0, 0.0, 0.0", f"2, {length!r}, 0.0, 0.0", f"3, {length!r}, {width!r}, 0.0", f"4, 0.0, {width!r}, 0.0",
         f"5, {length / 2!r}, 0.0, 0.0", f"6, {length!r}, {width / 2!r}, 0.0", f"7, {length / 2!r}, {width!r}, 0.0", f"8, 0.0, {width / 2!r}, 0.0",
         "*ELEMENT, TYPE=S8R, ELSET=PLATE", "1, 1, 2, 3, 4, 5, 6, 7, 8",
         "*MATERIAL, NAME=STEEL", "*ELASTIC", f"{_fmt(spec.E)}, {_fmt(spec.nu)}", "*SHELL SECTION, ELSET=PLATE, MATERIAL=STEEL", _fmt(spec.plate_t),
         "*BOUNDARY", "1, 1, 6", "4, 1, 6", "8, 1, 6", "*NSET, NSET=TIP", "2, 3, 6",
         "*STEP", "*STATIC", "*CLOAD"]
    L += [f"{k + 1}, 3, {_fmt(PRESSURE_FORCE_SIGN * area * w)}" for k, w in enumerate(S8R_PRESSURE_WEIGHTS)]
    L += ["*NODE PRINT, NSET=TIP", "U", "*END STEP",
          "*STEP", "*STATIC", "*CLOAD, OP=NEW", "1, 3, 0.0", "*DLOAD", "PLATE, P1, 1.0", "*NODE PRINT, NSET=TIP", "U", "*END STEP"]
    return "\n".join(L) + "\n"


PRESSURE_SIGN_TIP_NODE = 6  # mid free edge of the strip


def check_pressure_sign(blocks: Sequence["DatBlock"]) -> dict:
    """Explicit loads must deflect toward +z; reports what P1 does and the magnitude ratio between the two."""

    explicit = displacements_at(blocks, "TIP", 1.0)[PRESSURE_SIGN_TIP_NODE][2]
    p1 = displacements_at(blocks, "TIP", 2.0)[PRESSURE_SIGN_TIP_NODE][2]
    lo, hi = GROSS_SCREEN
    if not (math.isfinite(explicit) and math.isfinite(p1)) or explicit == 0.0 or p1 == 0.0:
        raise ValueError(f"pressure-sign calibration gave explicit={explicit!r}, P1={p1!r}; cannot decide")
    ratio = abs(p1) / abs(explicit)
    return {"tip_uz_explicit": explicit, "tip_uz_P1": p1, "p1_acts_toward": "-z" if p1 < 0.0 else "+z",
            "explicit_points_plus_z": explicit > 0.0, "p1_over_explicit_magnitude": ratio,
            "explicit_load_verified": bool(explicit > 0.0 and lo <= ratio <= hi),
            "note": "the A5 deck uses the explicit forces; P1's direction is recorded, never used"}


@dataclass(frozen=True)
class CalibrationRecord:
    """What the coordinator's CCX calibration runs established; required to write production decks."""

    beam_frame_confirmed: bool
    rotation_link_ry: bool
    rotation_link_rx: bool
    explicit_load_verified: bool
    p1_acts_toward: str = "unknown"  # informational
    source: str = ""

    def require_usable(self) -> "CalibrationRecord":
        problems = []
        if not self.beam_frame_confirmed:
            problems.append("beam frame/supports not confirmed (calibration 1): CCX applies the other rectangle inertia or the supports did not hold")
        if not self.rotation_link_ry:
            problems.append("shell-beam link did not transmit the plate rotation about y (calibration 2)")
        if not self.rotation_link_rx:
            problems.append("shell-beam link did not transmit the plate rotation about x (calibration 3): the A5 rx gauge is not valid")
        if not self.explicit_load_verified:
            problems.append("explicit +z consistent loads not verified against *DLOAD (calibration 4)")
        if problems:
            raise ValueError("calibration refuses production decks, A5 is INCONCLUSIVE: " + "; ".join(problems))
        return self


# ----------------------------------------------------------------------------------------------
# 8. Gauge arithmetic
# ----------------------------------------------------------------------------------------------
def link_rotation_x(u_plate: Sequence[float], u_beam: Sequence[float], offset: float) -> dict:
    """Plate rotation about x from the two ACTUAL nodes of the link: ``u_b - u_p = theta x (0, 0, e)`` gives ``uy_b - uy_p = -e rx``."""

    if not offset > 0.0:
        raise ValueError(f"offset must be positive, got {offset!r}")
    d = float(u_beam[1]) - float(u_plate[1])
    return {"rx": -d / offset, "offset": offset, "differential_uy": d}


def rigid_gauge_selftest(offset: float = 0.005) -> dict:
    """Pure arithmetic (no CCX): a rigid motion u = a + w x r gives the plate node and the beam node (r + (0,0,e)); the gauge recovers w_x."""

    a, w = np.array((1.0e-3, -2.0e-3, 3.0e-3)), np.array((2.0e-4, -1.0e-4, 3.0e-4))
    rp, rb = np.array([0.5, 0.3, 0.0]), np.array([0.5, 0.3, offset])
    up, ub = a + np.cross(w, rp), a + np.cross(w, rb)
    g = link_rotation_x(up, ub, offset)
    # Forward error from the ACTUAL values.  uy = a_y + (w_z x - w_x z): two products, one subtraction, one addition -> each
    # displacement carries <= 3 eps M with M the sum of term magnitudes; the difference adds eps |diff|, the division eps |rx|.
    # Factor 2 is a safety margin on the first-order bound, not a tuned number.
    eps = float(np.finfo(float).eps)
    magnitude = abs(a[1]) + abs(w[2] * rb[0]) + abs(w[0]) * offset
    bound = 2.0 * ((6.0 * eps * magnitude + eps * abs(g["differential_uy"])) / offset + eps * abs(g["rx"]))
    return {"gauge_rx": g["rx"], "expected_rx": float(w[0]), "abs_error": abs(g["rx"] - float(w[0])), "error_bound": bound,
            "offset": offset, "term_magnitude": magnitude}


# ----------------------------------------------------------------------------------------------
# 9. Reading CCX .dat output
# ----------------------------------------------------------------------------------------------
_HEADER = re.compile(r"^\s*(?P<kind>[A-Za-z][A-Za-z .]*?)\s*\((?P<cols>[^)]*)\)\s*for set\s+(?P<set>\S+)\s+and time\s+(?P<time>\S+)", re.I)


@dataclass
class DatBlock:
    kind: str  # lower-case header words, e.g. "displacements", "forces", "stresses", "total force"
    columns: Tuple[str, ...]
    set_name: str
    time: float
    rows: List[Tuple[float, ...]]


def parse_dat(text: str) -> List[DatBlock]:
    blocks: List[DatBlock] = []
    current: Optional[DatBlock] = None
    for line in text.splitlines():
        m = _HEADER.match(line)
        if m:
            current = DatBlock(m["kind"].strip().lower(), tuple(c.strip() for c in m["cols"].split(",")),
                               m["set"].upper(), float(m["time"].replace("D", "E")), [])
            blocks.append(current)
            continue
        if current is None or not line.strip():
            continue
        try:
            current.rows.append(tuple(float(tok.replace("D", "E")) for tok in line.split()))
        except ValueError:
            current = None
    return blocks


def _last(blocks: Sequence[DatBlock], kind: str, set_name: str) -> DatBlock:
    found = [b for b in blocks if b.kind == kind and b.set_name == set_name.upper()]
    if not found:
        raise KeyError(f"no '{kind}' block for set {set_name}; present: {sorted({(b.kind, b.set_name) for b in blocks})}")
    return found[-1]


def _at(blocks: Sequence[DatBlock], kind: str, set_name: str, time: float) -> DatBlock:
    found = [b for b in blocks if b.kind == kind and b.set_name == set_name.upper() and abs(b.time - time) <= 1e-9]
    if not found:
        raise KeyError(f"no '{kind}' block for {set_name} at time {time}; present: {sorted({(b.kind, b.set_name, b.time) for b in blocks})}")
    return found[-1]


def displacements(blocks: Sequence[DatBlock], set_name: str) -> Dict[int, Tuple[float, float, float]]:
    return {int(r[0]): tuple(r[1:4]) for r in _last(blocks, "displacements", set_name).rows}


def reactions(blocks: Sequence[DatBlock], set_name: str) -> Dict[int, Tuple[float, float, float]]:
    return {int(r[0]): tuple(r[1:4]) for r in _last(blocks, "forces", set_name).rows}


def displacements_at(blocks: Sequence[DatBlock], set_name: str, time: float) -> Dict[int, Tuple[float, float, float]]:
    """Displacements of ``set_name`` from the block printed at ``time`` (multi-step decks print one block per step)."""

    return {int(r[0]): tuple(r[1:4]) for r in _at(blocks, "displacements", set_name, time).rows}


def reactions_at(blocks: Sequence[DatBlock], set_name: str, time: float) -> Dict[int, Tuple[float, float, float]]:
    return {int(r[0]): tuple(r[1:4]) for r in _at(blocks, "forces", set_name, time).rows}


# ----------------------------------------------------------------------------------------------
# 10. A5 quantities, closure and convergence
# ----------------------------------------------------------------------------------------------
def a5_quantities(blocks: Sequence[DatBlock], n: int, spec: StiffenedSpec) -> dict:
    ids = probe_nodes(spec, n)
    d = displacements(blocks, "PROBES")
    gauge = link_rotation_x(d[ids["stiffener"]], d[ids["beam"]], spec.offset)
    return {"w_center": d[ids["centre"]][2], "w_stiffener": d[ids["stiffener"]][2], "rx": gauge["rx"],
            "gauge": gauge, "probe_nodes": ids,
            "note": "rx = -(uy_beam - uy_plate)/offset from two actual nodes; valid only if calibration 3 accepted the link"}


def reaction_closure(blocks: Sequence[DatBlock], n: int, spec: StiffenedSpec) -> dict:
    """Sum of printed reactions + applied loads, force and moment about the origin (z of supported nodes = 0)."""

    rf = reactions(blocks, "SUPPORT")
    m = 2 * n
    inv = {}
    for j in range(m + 1):
        for i in range(m + 1):
            if not (i % 2 and j % 2):
                inv[plate_node(n, i, j)] = np.array([i / m, j / m, 0.0])
    force = np.zeros(3)
    moment = np.zeros(3)
    for node, f in rf.items():
        force += f
        moment += np.cross(inv[node], f)
    applied = applied_totals(spec)
    rf_f = force + np.asarray(applied["force"])
    rf_m = moment + np.asarray(applied["moment_about_origin"])
    scale_f = max(float(np.linalg.norm(applied["force"])), 1e-300)
    scale_m = max(float(np.linalg.norm(applied["moment_about_origin"])), 1e-300)
    return {"reaction_force": force.tolist(), "applied_force": applied["force"], "force_residual_ratio": float(np.linalg.norm(rf_f) / scale_f),
            "reaction_moment": moment.tolist(), "applied_moment": applied["moment_about_origin"],
            "moment_residual_ratio": float(np.linalg.norm(rf_m) / scale_m),
            "print_precision_note": "CCX prints 6 significant digits; ratios of order 1e-5 are print precision, not model error"}


def reference_convergence(levels: Sequence[Tuple[float, float]]) -> dict:
    """Observed order and Richardson estimate from >= 3 levels with constant refinement ratio.

    ``levels`` = [(h, value), ...] coarse to fine.  Uses the three finest.  Non-monotone or
    degenerate sequences get ``status`` and NO extrapolation.  The raw differences are always returned.
    """

    if len(levels) < 3:
        raise ValueError("at least three levels are required")
    (h1, f1), (h2, f2), (h3, f3) = levels[-3:]
    r12, r23 = h1 / h2, h2 / h3
    out = {"levels": [{"h": h, "value": v} for h, v in levels],
           "differences": [levels[i + 1][1] - levels[i][1] for i in range(len(levels) - 1)],
           "relative_to_finest": [(v - levels[-1][1]) / levels[-1][1] if levels[-1][1] else None for _h, v in levels]}
    if abs(r12 - r23) > 1e-12:
        return {**out, "status": "refinement_ratio_not_constant", "order": None, "estimate": None}
    d12, d23 = f1 - f2, f2 - f3
    if d12 == 0.0 or d23 == 0.0 or d12 * d23 < 0.0:
        return {**out, "status": "non_monotone_or_stalled", "order": None, "estimate": None}
    ratio = d12 / d23
    if ratio <= 1.0:
        return {**out, "status": "not_converging_in_asymptotic_range", "order": math.log(ratio) / math.log(r12), "estimate": None}
    p = math.log(ratio) / math.log(r12)
    est = f3 + (f3 - f2) / (r12 ** p - 1.0)
    return {**out, "status": "monotone", "order": p, "estimate": est,
            "estimate_minus_finest_relative": (est - f3) / est if est else None}


def a5_report(per_level: Mapping[int, dict]) -> dict:
    """Per-quantity convergence across levels ``{n: a5_quantities(...)}``; deviations are kept, never merged."""

    ns = sorted(per_level)
    rep = {"levels_n": ns, "h": [1.0 / n for n in ns], "quantities": {}}
    for q in ("w_center", "w_stiffener", "rx"):
        rep["quantities"][q] = reference_convergence([(1.0 / n, per_level[n][q]) for n in ns])
    return rep


# The ORIGINAL A5 run (s4b/test_a5_budget.py, frozen): full stiffened ANYfem model at h = 0.1 ... 0.0125, quantities read as
# node_displacement(node at (x, y, 0))[component] with POINTS = {w_centre: ((0.5, 0.5), 2), w_stiffener: ((0.5, 0.3), 2),
# rx_stiffener: ((0.5, 0.3), 3)}: component 3 is the SHELL rotation dof rx of the plate node on the stiffener line, which is exactly the
# plate node whose rotation the CCX link gauge measures (plate node + beam node at the same (x, y)).
A5_ORIGINAL_QUANTITIES = {"w_centre": ("w_center", (0.5, 0.5), 2), "w_stiffener": ("w_stiffener", (0.5, 0.3), 2),
                          "rx_stiffener": ("rx", (0.5, 0.3), 3)}


def compare_with_original_a5_run(per_level: Mapping[int, dict], budget: Mapping) -> dict:
    """CCX reference vs the ANYfem 'full' values recorded by the original A5 run (``a5_budget.json``, key ``full``).

    Matched on h = 1/n.  Relative difference (anyfem - ccx) / |ccx| per quantity and level; nothing is judged here:
    the two models use different shell elements (Q4/linear vs S8R), so the difference is a comparison, not an error.
    """

    out: dict = {}
    for orig, (ours, _xy, _comp) in A5_ORIGINAL_QUANTITIES.items():
        rows = {}
        for n, q in sorted(per_level.items()):
            full = budget.get("full", {}).get(str(1.0 / n))
            if full is None or orig not in full:
                rows[str(n)] = {"status": "no original value for h=%r" % (1.0 / n)}
                continue
            ref = q[ours]
            rows[str(n)] = {"h": 1.0 / n, "anyfem_full": full[orig], "ccx": ref,
                            "relative_difference": (full[orig] - ref) / abs(ref) if ref else None}
        out[orig] = rows
    return out


# ----------------------------------------------------------------------------------------------
# 11. Runner (the only code that starts a process)
# ----------------------------------------------------------------------------------------------
def require_output_dir(path) -> Path:
    p = Path(path).resolve()
    if "handoff" in p.parts:
        raise ValueError(f"{p}: refusing to write inside historical evidence (a 'handoff' directory)")
    if p.exists() and any(p.iterdir()):
        raise ValueError(f"{p}: output directory must be new or empty")
    p.mkdir(parents=True, exist_ok=True)
    return p


def ccx_version(exe: str, timeout: float = 30.0) -> str:
    import subprocess

    out = subprocess.run([exe, "-v"], capture_output=True, text=True, timeout=timeout)
    text = (out.stdout or "") + (out.stderr or "")
    if f"Version {CCX_VERSION}" not in text:
        raise RuntimeError(f"ccx at {exe!r} is not {CCX_VERSION}; reported: {text.strip()[:200]!r}")
    return text.strip()


def run_ccx(exe: str, deck_path: Path, timeout: float = 3600.0) -> dict:
    """Run ``ccx -i <job>`` in the deck's directory; preserve stdout/stderr whatever happens."""

    import subprocess

    deck_path = Path(deck_path)
    proc = subprocess.run([exe, "-i", deck_path.stem], cwd=deck_path.parent, capture_output=True, text=True, timeout=timeout)
    (deck_path.parent / f"{deck_path.stem}.stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (deck_path.parent / f"{deck_path.stem}.stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    return {"returncode": proc.returncode, "dat": str(deck_path.with_suffix(".dat"))}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def calibration_decks(spec: StiffenedSpec) -> Dict[str, str]:
    """All calibration decks in the order the coordinator should run them (each one is independent and gates production)."""

    return {CAL_BEAM_FRAME: calibration_beam_frame_deck(spec),
            CAL_RY_LINK: calibration_rotation_link_deck(spec, "x"),
            CAL_RX_LINK: calibration_rotation_link_deck(spec, "y"),
            CAL_PRESSURE_SIGN: calibration_pressure_sign_deck(spec)}


def write_calibration_decks(spec: StiffenedSpec, out_dir) -> dict:
    """Write only the calibration decks (syntax-reviewed first).  No production deck can be written before these have run."""

    decks = calibration_decks(spec)
    review = {name: check_deck_syntax(text) for name, text in decks.items()}
    out = require_output_dir(out_dir)
    manifest = {"ccx_version_bound": CCX_VERSION, "spec": dataclasses.asdict(spec), "order": list(CALIBRATION_ORDER), "decks": {}}
    for name, text in decks.items():
        (out / f"{name}.inp").write_text(text, encoding="utf-8")
        manifest["decks"][name] = {"sha256": sha256_text(text), "syntax": review[name]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def write_a5_decks(spec: StiffenedSpec, out_dir, levels: Sequence[int] = DEFAULT_LEVELS, *, calibration: CalibrationRecord) -> dict:
    """Production A5 decks.  ``calibration`` is mandatory and must come from executed calibration decks; every deck is syntax-reviewed."""

    calibration.require_usable()
    texts = {n: a5_deck(spec, n) for n in levels}
    review = {n: check_deck_syntax(text) for n, text in texts.items()}
    out = require_output_dir(out_dir)
    manifest = {"ccx_version_bound": CCX_VERSION, "spec": dataclasses.asdict(spec), "calibration": dataclasses.asdict(calibration), "decks": {}}
    for n, text in texts.items():
        (out / f"a5_n{n}.inp").write_text(text, encoding="utf-8")
        manifest["decks"][f"a5_n{n}"] = {"n": n, "h": 1.0 / n, "sha256": sha256_text(text), "syntax": review[n]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def read_a5(run_dir, levels: Sequence[int], spec: StiffenedSpec) -> dict:
    """Read the .dat of an executed run directory and report; missing .dat files are listed, not skipped silently."""

    run = Path(run_dir)
    per_level, missing, closure = {}, [], {}
    for n in levels:
        dat = run / f"a5_n{n}.dat"
        if not dat.is_file():
            missing.append(str(dat))
            continue
        blocks = parse_dat(dat.read_text(encoding="utf-8"))
        per_level[n] = a5_quantities(blocks, n, spec)
        closure[str(n)] = reaction_closure(blocks, n, spec)
    report = {"missing": missing, "levels": per_level, "reaction_closure": closure}
    if len(per_level) >= 3:
        report["convergence"] = a5_report(per_level)
    return report


# ----------------------------------------------------------------------------------------------
# 12. PHYSICAL-OFFSET FALLBACK ("A5-F"), separately marked.  NOT RUN by the author.
#
# Sections 1-11 are unchanged: cal2/cal3 (``*EQUATION`` link) still fail on the recorded evidence and
# ``CalibrationRecord.require_usable`` still refuses.  This section never relaxes that gate.
#
# Idea: no ``*EQUATION``.  The B31 elements SHARE the plate node ids (coincident midsurface node) and carry a physical
# section offset (``OFFSET1/OFFSET2``, normalized by the section dimension -- an ASSUMPTION the law deck measures).
# The kinematic claim to be proved is exactly the ANYfem coupling  u_arm = u_plate + theta x (0, 0, e).  Proof is by
# imposed motions, never by model agreement:
#   phys_a  offset law: pinned offset beam under axial tension must bend by P e L^2/(8 E I) (control beam, zero offset: none);
#   phys_j  strip + soft stub on a SHARED tip node: imposed translation, imposed rigid rotation, bending vs Kirchhoff.
# Rotation is read from ACTUAL expanded-shell translations (``*NODE FILE, OUTPUT=3D`` -> .frd) at +/- t/2:
#   slope = -(u_axis(top) - u_axis(bottom)) / t    (u_axis = ux for a strip along x, uy along y).
# If any gate fails the answer is INCONCLUSIVE with the numbers kept; no constant is fitted, no policy changed.
# ----------------------------------------------------------------------------------------------
PRINT_REL = 1.0e-3  # .frd prints 5 significant digits (E12.5): 100 x its resolution, same logic as REACTION_SCREEN
PHYS_SLOPE, PHYS_DELTA = 1.0e-2, 1.0e-3  # imposed rigid slope (rad) and imposed rigid translation
OFFSET_LAW_ELEMENTS, OFFSET_LAW_LOAD, OFFSET_LAW_LENGTH = 10, 1.0e4, 1.0
CAL_OFFSET_LAW = "phys_a_offset_law"
PHYS_MODES = ("translation", "rotation", "bending")
NATIVE_LEVELS = (10, 20, 40)  # resource-scale default of the comparative sequence; DEFAULT_LEVELS (assessment freeze) is untouched
NATIVE_LABEL = "COMPARATIVE native-connectivity sequence: translational QoIs only; not an ANYfem equivalence or rotation grade"


def joint_name(mode: str, axis: str) -> str:
    return f"phys_j_{mode}_{axis}"


def _strip_xyz(axis: str, s: float, t: float, z: float = 0.0):
    """Same strip coordinates as ``calibration_rotation_link_deck``: axis x -> (s, t), axis y -> (b - t, s)."""

    return (s, t, z) if axis == "x" else (LINK_WIDTH - t, s, z)


# --- .frd reading -------------------------------------------------------------------------------
def parse_frd(text: str) -> Tuple[Dict[int, Tuple[float, float, float]], List[Dict[int, Tuple[float, float, float]]]]:
    """(coordinates, [DISP block per increment]) from an ASCII .frd.  Fixed width: ' -1', I10 node id, 12-character E12.5 values."""

    coords: Dict[int, Tuple[float, float, float]] = {}
    steps: List[Dict[int, Tuple[float, float, float]]] = []
    mode, cur = None, None
    for line in text.splitlines():
        if line.startswith("    2C"):
            mode = "nodes"
        elif line.startswith(" 100C"):
            mode = "header"
        elif line.startswith(" -4"):
            if mode == "header":
                if line[5:13].strip() == "DISP":
                    cur = {}
                    steps.append(cur)
                    mode = "disp"
                else:
                    mode = "other"
        elif line.startswith(" -3"):
            mode = None
        elif line.startswith(" -1") and mode in ("nodes", "disp"):
            nid = int(line[3:13])
            vals = tuple(float(line[13 + 12 * k:25 + 12 * k]) for k in range(3))
            (coords if mode == "nodes" else cur)[nid] = vals
    return coords, steps


def frd_node_at(coords: Mapping[int, Sequence[float]], xyz: Sequence[float], tol: float = 1.0e-5) -> int:
    """Id of the single expanded node at ``xyz`` (tol = 5-digit coordinate print); none or several is a ValueError (evidence)."""

    hits = [i for i, c in coords.items() if max(abs(a - b) for a, b in zip(c, xyz)) <= tol]
    if len(hits) != 1:
        raise ValueError(f"expected exactly one expanded node at {tuple(xyz)}, found {hits[:6]}")
    return hits[0]


# --- phys_a: offset law ---------------------------------------------------------------------------
def offset_law_deck(spec: StiffenedSpec) -> str:
    """Three pinned B31 beams under axial tension P at their end node: x-beam and y-beam with ``OFFSET2 = +offset/bar_height``
    (the A5 section, n1 = +y resp. +x as in the A5 deck) and a zero-offset control.  Nodes are the reference line; if the offset
    is a physical eccentricity the end moment is P*e, the curvature is uniform and midspan uz = -P e L^2/(8 E I) (tension
    below the neutral axis sags).  If the offset is only a drawing offset every uz is ~0.  The y-beam's n2 is -z, so its sign is
    measured separately."""

    el, length, load = OFFSET_LAW_ELEMENTS, OFFSET_LAW_LENGTH, OFFSET_LAW_LOAD
    v = spec.offset / spec.bar_height
    e1 = el + 1
    L = [f"** PHYS_A: pinned offset beams under axial tension; OFFSET2={v!r} (offset/bar_height, normalization is the hypothesis)", "*NODE"]
    L += [f"{k + 1}, {_fmt(k * length / el)}, 0.0, 0.0" for k in range(e1)]
    L += [f"{e1 + k + 1}, 2.0, {_fmt(k * length / el)}, 0.0" for k in range(e1)]
    L += [f"{2 * e1 + k + 1}, {_fmt(k * length / el)}, 4.0, 0.0" for k in range(e1)]
    for name, first in (("BX", 0), ("BY", e1), ("BC", 2 * e1)):
        L += [f"*ELEMENT, TYPE=B31, ELSET={name}"] + [f"{first + k + 1}, {first + k + 1}, {first + k + 2}" for k in range(el)]
    dims = f"{_fmt(spec.bar_width)}, {_fmt(spec.bar_height)}"
    L += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{_fmt(spec.E)}, {_fmt(spec.nu)}",
          f"*BEAM SECTION, ELSET=BX, MATERIAL=STEEL, SECTION=RECT, OFFSET1=0.0, OFFSET2={_fmt(v)}", dims, "0.0, 1.0, 0.0",
          f"*BEAM SECTION, ELSET=BY, MATERIAL=STEEL, SECTION=RECT, OFFSET1=0.0, OFFSET2={_fmt(v)}", dims, "1.0, 0.0, 0.0",
          "*BEAM SECTION, ELSET=BC, MATERIAL=STEEL, SECTION=RECT", dims, "0.0, 1.0, 0.0",
          "*BOUNDARY", "1, 1, 4", f"{e1}, 2, 3",
          f"{e1 + 1}, 1, 3", f"{e1 + 1}, 5, 5", f"{2 * e1}, 1, 1", f"{2 * e1}, 3, 3",
          f"{2 * e1 + 1}, 1, 4", f"{3 * e1}, 2, 3",
          "*NSET, NSET=MIDS", f"{el // 2 + 1}, {e1 + el // 2 + 1}, {2 * e1 + el // 2 + 1}",
          "*NSET, NSET=SUPPORT", f"1, {e1}, {e1 + 1}, {2 * e1}, {2 * e1 + 1}, {3 * e1}",
          "*STEP", "*STATIC", "*CLOAD", f"{e1}, 1, {_fmt(load)}", f"{2 * e1}, 2, {_fmt(load)}", f"{3 * e1}, 1, {_fmt(load)}",
          "*NODE PRINT, NSET=MIDS", "U", "*NODE PRINT, NSET=SUPPORT", "RF", "*END STEP"]
    return "\n".join(L) + "\n"


def check_offset_law(spec: StiffenedSpec, blocks: Sequence[DatBlock]) -> dict:
    """Signed effective offsets ``e_eff_z = -uz 8 E I/(P L^2)`` of the x- and y-beam against ``spec.offset``.

    ``effective``: reactions close, control beam does not bend (|uz| <= PRINT_REL x expected), and both |e_eff|/offset lie in the
    GROSS screen (the unit of OFFSET2 is then bar_height; ``implied_unit_over_*`` documents what it was if not).
    ``sign_x``/``sign_y`` (+1/-1) are the measured direction of the offset relative to +z for the SAME OFFSET2 value."""

    el, length, load = OFFSET_LAW_ELEMENTS, OFFSET_LAW_LENGTH, OFFSET_LAW_LOAD
    e1, mid = el + 1, el // 2 + 1
    d = displacements(blocks, "MIDS")
    stiff = 8.0 * spec.E * spec.inertia_vertical() / (load * length ** 2)
    expected = 1.0 / stiff * spec.offset
    out: dict = {"expected_abs_uz": expected, "control_uz": d[2 * e1 + mid][2]}
    lo, hi = GROSS_SCREEN
    v = spec.offset / spec.bar_height
    for key, node in (("x", mid), ("y", e1 + mid)):
        e_eff = -d[node][2] * stiff
        ratio = abs(e_eff) / spec.offset
        out[key] = {"uz": d[node][2], "e_eff_z": e_eff, "ratio_to_offset": ratio, "in_screen": bool(lo <= ratio <= hi),
                    "implied_unit_over_bar_height": abs(e_eff) / v / spec.bar_height, "implied_unit_over_bar_width": abs(e_eff) / v / spec.bar_width}
        out[f"sign_{key}"] = 1 if e_eff > 0.0 else -1
    rf = reactions(blocks, "SUPPORT")
    closure = {"x": abs(rf[1][0] + rf[e1][0] + load) / load, "y": abs(rf[e1 + 1][1] + rf[2 * e1][1] + load) / load,
               "control": abs(rf[2 * e1 + 1][0] + rf[3 * e1][0] + load) / load}
    out["reaction_closure"] = closure
    out["control_straight"] = bool(abs(out["control_uz"]) <= PRINT_REL * expected)
    out["effective"] = bool(out["x"]["in_screen"] and out["y"]["in_screen"] and out["control_straight"]
                            and all(c <= REACTION_SCREEN for c in closure.values()))
    out["note"] = ("effective offset law: beam stiffness includes the section offset" if out["effective"] else
                   "INCONCLUSIVE evidence: the offset is not an elastic eccentricity of the solved beam (or OFFSET normalization differs)")
    return out


# --- phys_j: shared-node joint on a Kirchhoff strip -------------------------------------------------
def joint_ids(nx: int = LINK_NX) -> dict:
    ids = link_ids(nx)
    stride = 2 * nx + 1
    return {"tip": ids["plate"], "stub_end": 3 * stride + 1, "clamp": ids["clamp"], "tip_edge": ids["tip_edge"],
            "strip_nodes": [(1 + i + j * stride, LINK_LENGTH * i / (2 * nx)) for j in range(3) for i in range(stride) if not (i % 2 and j % 2)]}


def physical_joint_deck(spec: StiffenedSpec, axis: str, mode: str, sign: int) -> str:
    """Strip of ``calibration_rotation_link_deck`` (S8R, nu = 0) with a soft B31 stub that SHARES the free-edge mid node
    (no ``*EQUATION``); the stub section is offset by ``OFFSET2 = sign*offset/LINK_STUB_SIZE`` (``sign`` measured by phys_a for this axis).

    ``translation``: every strip node prescribed (0, 0, PHYS_DELTA), rotations free.  ``rotation``: every strip node prescribed
    (0, 0, PHYS_SLOPE*s), rotations free (a rigid rotation of the midsurface, zero energy).  ``bending``: the clamp / tip load of
    cal2/cal3.  All print the expanded model (``*NODE FILE, OUTPUT=3D``) for the +/- t/2 and stub-corner nodes."""

    if axis not in ("x", "y") or mode not in PHYS_MODES or sign not in (1, -1):
        raise ValueError("axis x|y, mode translation|rotation|bending, sign +-1")
    nx, b, length, e = LINK_NX, LINK_WIDTH, LINK_LENGTH, spec.offset
    ids, stride = joint_ids(nx), 2 * nx + 1
    xyz = lambda s, t, z=0.0: _strip_xyz(axis, s, t, z)  # noqa: E731
    L = [f"** {joint_name(mode, axis).upper()}: S8R strip, stub SHARES tip node {ids['tip']}, OFFSET2 sign {sign:+d}", "*NODE"]
    for j in range(3):
        for i in range(stride):
            if not (i % 2 and j % 2):
                L.append(f"{1 + i + j * stride}, " + ", ".join(_fmt(c) for c in xyz(length * i / (2 * nx), b * j / 2.0)))
    L.append(f"{ids['stub_end']}, " + ", ".join(_fmt(c) for c in xyz(length + LINK_STUB_LENGTH, b / 2.0)))
    L.append("*ELEMENT, TYPE=S8R, ELSET=STRIP")
    for ex in range(nx):
        i0 = 2 * ex
        nodes = [1 + i0, 1 + i0 + 2, 1 + i0 + 2 + 2 * stride, 1 + i0 + 2 * stride,
                 1 + i0 + 1, 1 + i0 + 2 + stride, 1 + i0 + 1 + 2 * stride, 1 + i0 + stride]
        L.append(f"{ex + 1}, " + ", ".join(map(str, nodes)))
    n1 = "0.0, 1.0, 0.0" if axis == "x" else "1.0, 0.0, 0.0"
    L += ["*ELEMENT, TYPE=B31, ELSET=STUB", f"{nx + 1}, {ids['tip']}, {ids['stub_end']}",
          "*MATERIAL, NAME=CAL", "*ELASTIC", f"{_fmt(spec.E)}, 0.0",
          "*SHELL SECTION, ELSET=STRIP, MATERIAL=CAL", _fmt(spec.plate_t),
          f"*BEAM SECTION, ELSET=STUB, MATERIAL=CAL, SECTION=RECT, OFFSET1=0.0, OFFSET2={_fmt(sign * e / LINK_STUB_SIZE)}",
          f"{_fmt(LINK_STUB_SIZE)}, {_fmt(LINK_STUB_SIZE)}", n1,
          "*NSET, NSET=TIP", f"{ids['tip']}", "*NSET, NSET=CLAMP", ", ".join(map(str, ids["clamp"]))]
    if mode == "bending":
        L += ["*BOUNDARY"] + [f"{k}, 1, 6" for k in ids["clamp"]] + ["*STEP", "*STATIC", "*CLOAD"]
        L += [f"{k}, 3, {_fmt(LINK_LOAD * w)}" for k, w in zip(ids["tip_edge"], (1.0 / 6.0, 4.0 / 6.0, 1.0 / 6.0))]
    else:
        L += ["*STEP", "*STATIC", "*BOUNDARY"]
        for nid, s in ids["strip_nodes"]:
            L += [f"{nid}, 1, 2, 0.0", f"{nid}, 3, 3, {_fmt(PHYS_DELTA if mode == 'translation' else PHYS_SLOPE * s)}"]
    L += ["*NODE FILE, OUTPUT=3D", "U", "*NODE PRINT, NSET=TIP", "U"]
    if mode == "bending":
        L += ["*NODE PRINT, NSET=CLAMP", "RF"]
    L.append("*END STEP")
    return "\n".join(L) + "\n"


def check_joint(spec: StiffenedSpec, frd_text: str, axis: str, mode: str, blocks: Optional[Sequence[DatBlock]] = None) -> dict:
    """Read one joint run from the expanded .frd.  Gates (all must hold for ``effective``):

    * skin slope  -(u_axis(top) - u_axis(bot))/t  vs the imposed value (translation: 0, rotation: PHYS_SLOPE; PRINT_REL) or the
      Kirchhoff closed form (bending; GROSS screen, positive);
    * arm  mean(u_axis of the 4 stub root corners at z = e +- h/2) - mid-surface u_axis  vs  -e * slope  (PRINT_REL; this is
      u_arm = u_plate + theta x r, the ANYfem coupling), measured slope used so the gate is kinematic, not a bending accuracy claim;
    * translation / rotation: uz of skin and corners equal the imposed rigid uz (PRINT_REL);
    * bending: clamp reaction closure (REACTION_SCREEN) when ``blocks`` is given.
    Raises ValueError (kept as evidence) if the expected expanded nodes are not found."""

    coords, steps = parse_frd(frd_text)
    if not steps:
        raise ValueError("no DISP result block in the .frd (was '*NODE FILE, OUTPUT=3D' honoured?)")
    u = steps[-1]
    t, e, b, length, h = spec.plate_t, spec.offset, LINK_WIDTH, LINK_LENGTH, LINK_STUB_SIZE
    at = lambda s, tt, z: np.asarray(u[frd_node_at(coords, _strip_xyz(axis, s, tt, z))], dtype=float)  # noqa: E731
    comp = 0 if axis == "x" else 1
    top, bot = at(length, b / 2.0, t / 2.0), at(length, b / 2.0, -t / 2.0)
    corners = [at(length, b / 2.0 + dt, e + dz) for dt in (-h / 2.0, h / 2.0) for dz in (-h / 2.0, h / 2.0)]
    slope = -float(top[comp] - bot[comp]) / t
    arm = float(np.mean([c[comp] for c in corners])) - 0.5 * float(top[comp] + bot[comp])
    expected_arm = -e * slope
    out: dict = {"axis": axis, "mode": mode, "slope_measured": slope, "arm_measured": arm, "arm_expected": expected_arm,
                 "uz_top": float(top[2]), "uz_bottom": float(bot[2]), "uz_corners": [float(c[2]) for c in corners]}
    scale_arm = e * PHYS_SLOPE
    if mode == "translation":
        gates = {"skin_slope_zero": abs(slope) <= PRINT_REL * PHYS_SLOPE, "arm_zero": abs(arm) <= PRINT_REL * scale_arm,
                 "uz_rigid": all(abs(v - PHYS_DELTA) <= PRINT_REL * PHYS_DELTA for v in (top[2], bot[2], *[c[2] for c in corners]))}
    elif mode == "rotation":
        uz = PHYS_SLOPE * length
        gates = {"skin_slope_is_imposed": abs(slope - PHYS_SLOPE) <= PRINT_REL * PHYS_SLOPE,
                 "arm_is_minus_e_slope": abs(arm - expected_arm) <= PRINT_REL * abs(expected_arm),
                 "arm_nonzero": abs(arm) > 0.5 * abs(e * PHYS_SLOPE),
                 "uz_rigid": all(abs(v - uz) <= PRINT_REL * uz for v in (top[2], bot[2], *[c[2] for c in corners]))}
    else:
        theta = rotation_link_expected(spec)["theta_tip"]
        lo, hi = GROSS_SCREEN
        gates = {"skin_slope_closed_form_gross": bool(lo <= slope / theta <= hi), "arm_is_minus_e_slope": abs(arm - expected_arm) <= PRINT_REL * abs(expected_arm),
                 "arm_nonzero": abs(arm) > 0.5 * abs(expected_arm)}
        out["slope_over_closed_form"] = slope / theta
        if blocks is not None:
            rf = reactions(blocks, "CLAMP")
            closure = abs(sum(r[2] for r in rf.values()) + LINK_LOAD) / LINK_LOAD
            out["clamp_reaction_closure"] = closure
            gates["clamp_closure"] = closure <= REACTION_SCREEN
    out["gates"] = {k: bool(v) for k, v in gates.items()}
    out["effective"] = bool(all(gates.values()))
    return out


@dataclass(frozen=True)
class PhysicalRecord:
    """What the executed phys_a / phys_j runs established.  ``joints`` maps ``'<mode>_<axis>'`` to the ``effective`` flag."""

    offset_law_effective: bool
    sign_x: int
    sign_y: int
    joints: Mapping[str, bool]
    source: str = ""

    def require_usable(self, calibration: CalibrationRecord) -> "PhysicalRecord":
        """Gate of the native decks.  Needs cal1 (beam frame) and cal4 (explicit loads) of the ORIGINAL record; the failed
        cal2/cal3 flags are deliberately not consulted -- the physical gates replace the ``*EQUATION`` link they tested."""

        problems = []
        if not calibration.beam_frame_confirmed:
            problems.append("beam frame/supports not confirmed (cal1)")
        if not calibration.explicit_load_verified:
            problems.append("explicit loads not verified (cal4)")
        if not self.offset_law_effective:
            problems.append("offset law not effective (phys_a)")
        for mode in PHYS_MODES:
            for axis in ("x", "y"):
                if not self.joints.get(f"{mode}_{axis}", False):
                    problems.append(f"shared-node joint gate {mode}_{axis} missing or failed (phys_j)")
        if problems:
            raise ValueError("physical fallback refuses native decks, A5-F is INCONCLUSIVE: " + "; ".join(problems))
        return self


# --- comparative native sequence ------------------------------------------------------------------------
def a5_native_deck(spec: StiffenedSpec, n: int, sign_x: int, sign_y: int) -> str:
    """A5 with B31 elements on the plate node ids (no beam nodes, no ``*EQUATION``) and ``OFFSET2 = sign*offset/bar_height``.

    Plate mesh, supports and loads are those of ``a5_deck`` (duplicated on purpose: ``a5_deck`` stays untouched).  Prints the
    plate probes (dat) and the expanded model (.frd) so the skin rotation can be observed."""

    if n % 10:
        raise ValueError("n must be a multiple of 10")
    m = 2 * n
    lattice = a5_beam_lattice(spec, n)
    iy, ix = round(spec.stiffener_y * m), round(spec.stiffener_x * m)
    L = [f"** ANYfem S6 A5 NATIVE-CONNECTIVITY, ccx {CCX_VERSION}, n={n}", f"** {NATIVE_LABEL}", "*NODE, NSET=NPLATE"]
    for j in range(m + 1):
        for i in range(m + 1):
            if not (i % 2 and j % 2):
                L.append(f"{plate_node(n, i, j)}, {_fmt(i / m)}, {_fmt(j / m)}, 0.0")
    L.append("*ELEMENT, TYPE=S8R, ELSET=PLATE")
    eid, plate_elements = 0, []
    for ey in range(n):
        for ex in range(n):
            i0, j0 = 2 * ex, 2 * ey
            nodes = [plate_node(n, i0, j0), plate_node(n, i0 + 2, j0), plate_node(n, i0 + 2, j0 + 2), plate_node(n, i0, j0 + 2),
                     plate_node(n, i0 + 1, j0), plate_node(n, i0 + 2, j0 + 1), plate_node(n, i0 + 1, j0 + 2), plate_node(n, i0, j0 + 1)]
            eid += 1
            plate_elements.append(nodes)
            L.append(f"{eid}, " + ", ".join(map(str, nodes)))
    belem = eid
    L.append("*ELEMENT, TYPE=B31, ELSET=STIFX")
    for i in range(0, m, 2):
        belem += 1
        L.append(f"{belem}, {plate_node(n, i, iy)}, {plate_node(n, i + 2, iy)}")
    L.append("*ELEMENT, TYPE=B31, ELSET=STIFY")
    for j in range(0, m, 2):
        belem += 1
        L.append(f"{belem}, {plate_node(n, ix, j)}, {plate_node(n, ix, j + 2)}")
    dims = f"{_fmt(spec.bar_width)}, {_fmt(spec.bar_height)}"
    L += ["*MATERIAL, NAME=STEEL", "*ELASTIC", f"{_fmt(spec.E)}, {_fmt(spec.nu)}",
          "*SHELL SECTION, ELSET=PLATE, MATERIAL=STEEL", f"{_fmt(spec.plate_t)}",
          f"*BEAM SECTION, ELSET=STIFX, MATERIAL=STEEL, SECTION=RECT, OFFSET1=0.0, OFFSET2={_fmt(sign_x * spec.offset / spec.bar_height)}",
          dims, "0.0, 1.0, 0.0",
          f"*BEAM SECTION, ELSET=STIFY, MATERIAL=STEEL, SECTION=RECT, OFFSET1=0.0, OFFSET2={_fmt(sign_y * spec.offset / spec.bar_height)}",
          dims, "1.0, 0.0, 0.0"]
    cons = deck_constraints(spec, n)
    L.append("*BOUNDARY")
    for node, dofs in sorted(cons.items()):
        L += [f"{node}, {a}, {b}" for a, b in _ranges(dofs)]
    L += ["*NSET, NSET=SUPPORT"] + [", ".join(map(str, c)) for c in _chunks(sorted(cons), 8)]
    ids = probe_nodes(spec, n)
    L += ["*NSET, NSET=PROBES", f"{ids['centre']}, {ids['stiffener']}"]
    fz: Dict[int, float] = {}
    pz, h2 = pressure_force_z(spec), (1.0 / n) ** 2
    for nodes in plate_elements:
        for nid, w in zip(nodes, S8R_PRESSURE_WEIGHTS):
            fz[nid] = fz.get(nid, 0.0) + pz * h2 * w
    fx: Dict[int, float] = {}
    w3, h = (1.0 / 6.0, 4.0 / 6.0, 1.0 / 6.0), 1.0 / n
    for edge_nodes, q in (([(m, j) for j in range(m + 1)], spec.line_load_x1), ([(i, m) for i in range(m + 1)], spec.line_load_y1)):
        for e0 in range(0, m, 2):
            for k in range(3):
                nid = plate_node(n, *edge_nodes[e0 + k])
                fx[nid] = fx.get(nid, 0.0) + q * h * w3[k]
    L += ["*STEP", "*STATIC", "*CLOAD"]
    for nid in sorted(set(fx) | set(fz)):
        if nid in fx:
            L.append(f"{nid}, 1, {_fmt(fx[nid])}")
        if nid in fz:
            L.append(f"{nid}, 3, {_fmt(fz[nid])}")
    L += ["*NODE FILE, OUTPUT=3D", "U", "*NODE PRINT, NSET=PROBES", "U", "*NODE PRINT, NSET=SUPPORT", "RF", "*END STEP"]
    return "\n".join(L) + "\n"


def _write_manifested(decks: Mapping[str, str], out_dir, extra: dict) -> dict:
    review = {name: check_deck_syntax(text) for name, text in decks.items()}
    out = require_output_dir(out_dir)
    manifest = {"ccx_version_bound": CCX_VERSION, **extra, "decks": {}}
    for name, text in decks.items():
        (out / f"{name}.inp").write_text(text, encoding="utf-8")
        manifest["decks"][name] = {"sha256": sha256_text(text), "syntax": review[name]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def write_offset_law_deck(spec: StiffenedSpec, out_dir) -> dict:
    return _write_manifested({CAL_OFFSET_LAW: offset_law_deck(spec)}, out_dir, {"spec": dataclasses.asdict(spec)})


def write_joint_decks(spec: StiffenedSpec, out_dir, law: Mapping) -> dict:
    """Six joint decks.  Refuses unless phys_a found an effective offset law (its signs set OFFSET2)."""

    if not law.get("effective"):
        raise ValueError("offset law not effective (phys_a): A5-F is INCONCLUSIVE, no joint decks")
    decks = {joint_name(mode, axis): physical_joint_deck(spec, axis, mode, int(law[f"sign_{axis}"])) for mode in PHYS_MODES for axis in ("x", "y")}
    return _write_manifested(decks, out_dir, {"spec": dataclasses.asdict(spec), "law": dict(law)})


def write_native_decks(spec: StiffenedSpec, out_dir, levels: Sequence[int] = NATIVE_LEVELS, *, calibration: CalibrationRecord,
                       physical: PhysicalRecord) -> dict:
    physical.require_usable(calibration)
    decks = {f"a5_native_n{n}": a5_native_deck(spec, n, physical.sign_x, physical.sign_y) for n in levels}
    return _write_manifested(decks, out_dir, {"spec": dataclasses.asdict(spec), "label": NATIVE_LABEL, "calibration": dataclasses.asdict(calibration),
                                              "physical": dataclasses.asdict(physical)})


def read_native(run_dir, levels: Sequence[int], spec: StiffenedSpec) -> dict:
    """Translational QoIs per level + convergence.  The skin rotation is an OBSERVATION (never graded or compared)."""

    run = Path(run_dir)
    per_level, missing, closure, rotation = {}, [], {}, {}
    for n in levels:
        dat = run / f"a5_native_n{n}.dat"
        if not dat.is_file():
            missing.append(str(dat))
            continue
        blocks = parse_dat(dat.read_text(encoding="utf-8"))
        ids = probe_nodes(spec, n)
        d = displacements(blocks, "PROBES")
        per_level[n] = {"w_center": d[ids["centre"]][2], "w_stiffener": d[ids["stiffener"]][2]}
        closure[str(n)] = reaction_closure(blocks, n, spec)
        frd = run / f"a5_native_n{n}.frd"
        if frd.is_file():
            try:
                coords, steps = parse_frd(frd.read_text(encoding="utf-8", errors="replace"))
                u = steps[-1]
                top = u[frd_node_at(coords, (0.5, spec.stiffener_y, spec.plate_t / 2.0))]
                bot = u[frd_node_at(coords, (0.5, spec.stiffener_y, -spec.plate_t / 2.0))]
                rotation[str(n)] = -(top[1] - bot[1]) / spec.plate_t
            except (ValueError, IndexError) as error:
                rotation[str(n)] = f"unavailable: {error}"
    report = {"label": NATIVE_LABEL, "missing": missing, "levels": per_level, "reaction_closure": closure,
              "rx_skin_observation_not_graded": rotation, "graded_quantities": ["w_center", "w_stiffener"]}
    if len(per_level) >= 3:
        ns = sorted(per_level)
        report["convergence"] = {q: reference_convergence([(1.0 / n, per_level[n][q]) for n in ns]) for q in report["graded_quantities"]}
    return report
