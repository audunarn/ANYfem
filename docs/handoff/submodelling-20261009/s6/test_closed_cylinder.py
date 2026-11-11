"""A6 closed-end pressurised cylinder: a real cut child driven from the parent, against closed forms.

NOT RUN by the author (no execution available); the coordinator runs it on the recorded dependencies.

Parent: the open ``tube`` of ``tests/test_submodel_cylinder.py`` (R = 1, L = 4, t = 0.01, p = 1e5, bottom ring
axially supported) made a CLOSED-END tube by membrane axial end thrust: a line load (0, 0, pR/2) N/m on the
four top-ring edges.  That is the pressure on an end cap carried as a membrane resultant (the cap itself is not
modelled), reacted by the bottom ring's uz support.  The membrane state is then

    sigma_theta = p R / t,   sigma_z = p R / (2 t),   w = p R^2 (1 - nu/2) / (E t)       (radial, mid-length)

Child: ``cut_submodel`` of the box (0.5, -0.5, 1.5)-(1.5, 0.5, 2.5) from the existing cylinder cut test, solved
through ``stage_submodel_run`` (the real driven child), under both policies.  Nothing here is a surrogate.

Quantities compared with the closed forms
* radial w at (angle 0, z = 2): ``u . e_r``;
* hoop stress: NOT read from the solver's recovery (its field layout is not asserted here; whether it is
  available and under which keys is recorded in the report).  It is evaluated from the child's nodal
  displacements with the isotropic plane-stress membrane law
  sigma_theta = E/(1-nu^2) (u_r/R + nu e_z), e_z by a central difference of u_z along the generatrix.  This is a
  displacement-derived membrane stress, stated as such; it carries the same facet content as w.
* facet error is reported separately and is NOT subtracted: the child's chord sagitta R(1 - cos(dtheta/2)), the
  solver's own ``cylindrical`` facet diagnostics, and the change between two child sizes.

Pre-registered criteria (set before any run; not tuned): |w/w_closed - 1| <= 0.5 % (the A6 limit already used by
``test_pressurised_tube_child_meets_the_closed_form``), hoop stress the same 0.5 %, free-body ratios <= 1e-12 (as in
the existing tests), facet offset within the solver's own bound, and the child closer to the closed-end value than
to the open-tube value pR^2/(Et) (which shows the thrust really reached the child).  If the coordinator wishes other
limits they belong to the acceptance owner, not to this file.

Run: ``python docs/handoff/submodelling-20261009/s4a/run_tests.py docs/handoff/submodelling-20261009/s6/test_closed_cylinder.py -s --basetemp=<writable dir>``
Optional report directory: set ``ANYFEM_S6_REPORT_DIR`` to a new/empty directory outside any ``handoff`` folder.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3] / "tests"))

pytest.importorskip("anysolver.submodel_transfer")
pytest.importorskip("anymesher.submodel_correspondence")

from anygeometry.volume_cut import BoxVolume  # noqa: E402
from anyfem.submodel_jobs import discard_staged_run, stage_submodel_run, submodel_snapshot  # noqa: E402
from test_submodel_cut import Parent  # noqa: E402
from test_submodel_cylinder import L, P, R, T, tube  # noqa: E402

BOX = BoxVolume((0.5, -0.5, 1.5), (1.5, 0.5, 2.5))
LIMIT_W = 0.005
LIMIT_HOOP = 0.005
SIZES = (0.1, 0.05)
PARENT_SIZE = 0.25


def closed_end_tube(size):
    """The cylinder-test tube plus membrane end thrust (0, 0, pR/2) N/m on the top-ring edges; solved by ``Parent``."""

    project = tube(size)[0]
    g = project.geometry
    top = []
    for edge_id, edge in g.edges.items():
        ends = [np.asarray(g.vertex_position(v)) for v in (edge.start, edge.end)]
        if all(abs(x[2] - L) < 1e-9 and abs(np.linalg.norm(x[:2]) - R) < 1e-9 for x in ends):
            top.append(edge_id)
    assert len(top) == 4, f"expected four top-ring edges, found {len(top)}"
    case = project.load_case()
    for edge_id in top:
        case.add_line_load(project.edge(edge_id), (0.0, 0.0, P * R / 2.0))
    return project, top


@pytest.fixture(scope="module")
def closed_parent(tmp_path_factory):
    project, top = closed_end_tube(PARENT_SIZE)
    parent = Parent(tmp_path_factory.mktemp("a6-closed"), project, {}, mesh=project.generate_mesh(PARENT_SIZE))
    material = project.materials["S355"]
    return parent, top, float(material.elastic_modulus), float(material.poisson_ratio)


def closed_forms(E, nu):
    return {"w_closed": P * R * R * (1.0 - nu / 2.0) / (E * T), "w_open": P * R * R / (E * T),
            "hoop": P * R / T, "axial": P * R / (2.0 * T)}


def generatrix(mesh, z_lo=1.5, z_hi=2.5):
    nodes = [(float(x[2]), n) for n, x in mesh.nodes.items()
             if abs(x[1]) < 1e-9 and x[0] > 0 and abs(math.hypot(x[0], x[1]) - R) < 1e-9 and z_lo - 1e-9 <= x[2] <= z_hi + 1e-9]
    return sorted(nodes)


def membrane_state(result, mesh, E, nu, z=2.0):
    """w, e_z and the plane-stress membrane stresses at (angle 0, z) from the child's own displacements."""

    line = generatrix(mesh)
    k = min(range(len(line)), key=lambda i: abs(line[i][0] - z))
    assert 0 < k < len(line) - 1, "the evaluation point must have axial neighbours in the child"
    (za, na), (z0, n0), (zb, nb) = line[k - 1], line[k], line[k + 1]
    u = lambda n: np.asarray(result.solution.node_displacement(n), dtype=float)  # noqa: E731
    w = float(u(n0)[0])  # angle 0: e_r = e_x
    e_z = (u(nb)[2] - u(na)[2]) / (zb - za)
    e_t = w / R
    c = E / (1.0 - nu * nu)
    return {"z": z0, "w": w, "e_z": e_z, "sigma_theta": c * (e_t + nu * e_z), "sigma_z": c * (e_z + nu * e_t)}


def facet_report(mesh, result):
    """Chord sagitta of the child's circumferential spacing and the solver's own facet diagnostics (not subtracted)."""

    angles = sorted({round(math.atan2(x[1], x[0]), 9) for x in mesh.nodes.values()
                     if abs(math.hypot(x[0], x[1]) - R) < 1e-9 and abs(x[2] - 2.0) < 1e-9})
    steps = np.diff(angles)
    dtheta = float(steps.max())
    return {"child_dtheta_max": dtheta, "child_sagitta_over_R": 1.0 - math.cos(dtheta / 2.0),
            "solver_cylindrical": {k: v for k, v in result.diagnostics["cylindrical"].items() if isinstance(v, (int, float))}}


GLOBAL_COMPONENT_NAMES = (("sxx", "syy", "szz", "sxy", "syz", "sxz"), ("sigma_xx", "sigma_yy", "sigma_zz", "sigma_xy", "sigma_yz", "sigma_xz"),
                          ("xx", "yy", "zz", "xy", "yz", "xz"))


def solver_stress_probe(solution, mesh=None, z=2.0):
    """The solver's own recovered stress field at (angle 0, z): keys offered, and the hoop/axial stress when readable.

    At angle 0 the hoop direction is global y and the axial direction global z, so ``hoop = syy`` and ``axial = szz`` of the
    GLOBAL stress tensor -- but only if the recovery offers global components under a recognised name set and as plain
    numbers.  Averaged over the shell elements that touch the node at (R, 0, z).  Anything else (local-frame components,
    per-surface dictionaries, a different layout) is recorded as ``hoop_unavailable_reason`` with the keys, never guessed.
    This replaces the displacement-derived membrane stress as the PRIMARY hoop sensor when it is readable.
    """

    try:
        recovered = solution.stresses()
        table = recovered.element_stresses
        first = next(iter(table.values()))
    except Exception as error:  # noqa: BLE001 - the failure itself is the evidence
        return {"available": False, "error": f"{type(error).__name__}: {error}"}
    out = {"available": True, "keys": sorted(map(str, first.keys())) if hasattr(first, "keys") else str(type(first)),
           "value_types": {str(k): type(v).__name__ for k, v in first.items()} if hasattr(first, "items") else None}
    if mesh is None or not hasattr(first, "keys"):
        out["hoop_unavailable_reason"] = "no mesh given or the per-element record is not a mapping"
        return out
    names = next((n for n in GLOBAL_COMPONENT_NAMES if all(k in first for k in n)), None)
    if names is None:
        out["hoop_unavailable_reason"] = "no recognised global component names among the keys"
        return out
    centre = min((n for n, x in mesh.nodes.items() if abs(x[1]) < 1e-9 and x[0] > 0 and abs(float(x[2]) - z) < 1e-9),
                 key=lambda n: abs(mesh.nodes[n][0] - R), default=None)
    elements = [e for conn in (mesh.quads, mesh.tris) for e, nodes in conn.items() if centre in nodes]
    try:
        values = np.array([[float(table[e][k]) for k in names] for e in elements if e in table])
    except (TypeError, ValueError, KeyError) as error:
        out["hoop_unavailable_reason"] = f"components are not plain numbers: {type(error).__name__}: {error}"
        return out
    if not len(values):
        out["hoop_unavailable_reason"] = f"no recovered element touches node {centre}"
        return out
    mean = values.mean(axis=0)
    out.update({"component_names": list(names), "elements_averaged": len(values), "sigma_theta": float(mean[1]), "sigma_z": float(mean[2]),
                "spread_theta": float(values[:, 1].max() - values[:, 1].min())})
    return out


def write_report(name, report, tmp_path, *, final=False):
    (tmp_path / f"{name}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    directory = os.environ.get("ANYFEM_S6_REPORT_DIR")
    if directory and final:
        path = Path(directory).resolve()
        assert "handoff" not in path.parts, "report directory must not be inside historical evidence"
        path.mkdir(parents=True, exist_ok=True)
        with open(path / f"{name}.json", "x", encoding="utf-8") as handle:  # 'x': never overwrite
            handle.write(json.dumps(report, indent=2, default=str))


# ----------------------------------------------------------------------
def test_the_parent_carries_the_end_thrust(closed_parent):
    """Preflight of the fixture: thrust present, closed forms usable.  Reports; asserts only structural facts."""

    parent, top, E, nu = closed_parent
    case = parent.project.load_cases["default"]
    # only the count is asserted: the line-load record's value attribute is not verified here
    assert len(list(case.line_loads)) == 4
    assert 0.0 < nu < 0.5 and E > 0.0
    forms = closed_forms(E, nu)
    print(f"closed forms: {forms}; project E = {E:.6g}, nu = {nu}; test_submodel_cylinder uses E = 2.1e11")
    assert forms["w_closed"] < forms["w_open"]


@pytest.mark.parametrize("policy", ["drive_all", "release_normal_rotation"])
def test_closed_end_child_meets_the_membrane_closed_forms(closed_parent, tmp_path, policy):
    parent, _top, E, nu = closed_parent
    forms = closed_forms(E, nu)
    report = {"policy": policy, "parent_size": PARENT_SIZE, "closed_forms": forms, "E": E, "nu": nu, "children": {}}
    try:
        _run_sizes(parent, policy, forms, E, nu, report, tmp_path)
    finally:  # the report, including a failing row, is kept as evidence
        write_report(f"a6_closed_{policy}", report, tmp_path, final=True)


def _run_sizes(parent, policy, forms, E, nu, report, tmp_path):
    for size in SIZES:
        cut = parent.cut(BOX, policy=policy, target_size=size, name=f"a6 {policy} {size}")
        assert cut.report.carried["pressures"] == 2
        parent.register(cut.definition)
        staged = stage_submodel_run(submodel_snapshot(parent.session), cut.definition.id, parent.store)
        try:
            result = staged.result
            mesh = result.solution.built.mesh
            state = membrane_state(result, mesh, E, nu)
            body = result.diagnostics["free_body"]
            cyl = result.diagnostics["cylindrical"]
            row = {"state": state, "w_error": state["w"] / forms["w_closed"] - 1.0,
                   "w_error_vs_open_form": state["w"] / forms["w_open"] - 1.0,
                   "hoop_error": state["sigma_theta"] / forms["hoop"] - 1.0,
                   "axial_error_reported_only": state["sigma_z"] / forms["axial"] - 1.0,
                   "free_body": {k: body[k] for k in ("force_ratio", "moment_ratio")},
                   "facet": facet_report(mesh, result), "solver_stress_recovery": solver_stress_probe(result.solution, mesh)}
            recovery = row["solver_stress_recovery"]
            if "sigma_theta" in recovery:
                row["hoop_error_solver_recovery"] = recovery["sigma_theta"] / forms["hoop"] - 1.0
            report["children"][str(size)] = row
            print(f"{policy} h={size}: w error {row['w_error']:+.3%}, hoop error {row['hoop_error']:+.3%}, "
                  f"facet sagitta/R {row['facet']['child_sagitta_over_R']:.2e}")
            assert math.isfinite(state["w"]) and math.isfinite(state["sigma_theta"])
            assert body["force_ratio"] <= 1e-12 and body["moment_ratio"] <= 1e-12
            assert 0.0 < cyl["max_facet_offset"] <= cyl["max_facet_bound"] + cyl["max_float_allowance"]
            assert abs(row["w_error"]) <= LIMIT_W, row
            assert abs(row["hoop_error"]) <= LIMIT_HOOP, row
            if "hoop_error_solver_recovery" in row:  # the solver's own recovered hoop field, when its layout is readable
                assert abs(row["hoop_error_solver_recovery"]) <= LIMIT_HOOP, row
            else:
                print(f"  solver hoop recovery not readable: {recovery.get('hoop_unavailable_reason') or recovery.get('error')}")
            assert abs(row["w_error"]) < abs(row["w_error_vs_open_form"]), "child follows the open-tube value: thrust did not arrive"
        finally:
            discard_staged_run(staged)
    # facet content separated by child size: the change of w between sizes is reported, the sagitta ratio shows its scale
    w = [report["children"][str(s)]["state"]["w"] for s in SIZES]
    report["w_change_between_child_sizes"] = abs(w[0] - w[1]) / forms["w_closed"]
