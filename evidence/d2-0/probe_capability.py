"""D2-0 capability probe: shell result recovery with return_global=True.

READ-ONLY on the solver: builds one small element per branch, recovers with
return_global=True and records which outputs exist, their shapes, the
tensor-basis normals (from the null axis of each global family tensor),
polarity and top/bottom behaviour under a pure-bending state.

Run (Git Bash):
  export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4
  export PYTHONPATH="C:/Github/ANYfeSolver/src;C:/Github/ANYfeSolver/packages/results/src"
  timeout 300 C:/Github/ANYfem/.venv/Scripts/python.exe probe_capability.py
Writes capability.json next to this file.
"""
from __future__ import annotations

import collections
import json
import math
import os
import subprocess
import sys
import warnings
from pathlib import Path

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "4")

import numpy as np

warnings.simplefilter("ignore")

import anyfesolver
from anyfesolver.elements import ShellElement, create_shell_element
from anyfesolver.fe_core import FEMesh, Material
from anyfesolver.shell_sections import GeneralizedShellSection

OUT = Path(__file__).with_name("capability.json")
H = 0.1
E_MOD, NU = 210.0e9, 0.3
MATERIAL = Material("steel", E_MOD, NU, density=7850.0)

# ---------------------------------------------------------------- placement
_axis = np.array([1.0, 2.0, -0.5])
_axis /= np.linalg.norm(_axis)
_ang = 0.7
_K = np.array([[0, -_axis[2], _axis[1]], [_axis[2], 0, -_axis[0]], [-_axis[1], _axis[0], 0]])
R0 = np.eye(3) + math.sin(_ang) * _K + (1 - math.cos(_ang)) * (_K @ _K)
T0 = np.array([0.5, -0.3, 0.2])
N0 = R0[:, 2]            # physical +side used by the pure-bending state
AX = R0[:, 0]            # direction along which stress is compared


def section() -> GeneralizedShellSection:
    a = E_MOD * H / (1 - NU**2)
    d = E_MOD * H**3 / (12 * (1 - NU**2))
    G = E_MOD / (2 * (1 + NU))
    A = a * np.array([[1, NU, 0], [NU, 1, 0], [0, 0, (1 - NU) / 2]])
    D = d * np.array([[1, NU, 0], [NU, 1, 0], [0, 0, (1 - NU) / 2]])
    return GeneralizedShellSection(A=A, B=np.zeros((3, 3)), D=D, As=(5 / 6) * G * H * np.eye(2), name="iso")


# ------------------------------------------------------------ local geometry
def _mid(p, q):
    return tuple((np.array(p) + np.array(q)) / 2)


def topology(name: str):
    if name == "t3":
        return [(0, 0, 0), (1.2, 0, 0), (0.3, 0.9, 0)]
    if name == "q4":
        return [(0, 0, 0), (1.2, 0, 0), (1.2, 0.8, 0), (0, 0.8, 0)]
    if name == "q4w":   # warped: node 3 lifted
        return [(0, 0, 0), (1.2, 0, 0), (1.2, 0.8, 0.15), (0, 0.8, 0)]
    c = [(0, 0, 0), (1.2, 0, 0), (0.3, 0.9, 0)] if name.startswith("t6") else \
        [(0, 0, 0), (1.2, 0, 0), (1.2, 0.8, 0), (0, 0.8, 0)]
    pts = list(c)
    for i in range(len(c)):
        pts.append(_mid(c[i], c[(i + 1) % len(c)]))
    if name.endswith("c"):       # curved: z = 0.3 y^2 (corners and mid nodes on it)
        pts = [(x, y, 0.3 * y * y) for x, y, _ in pts]
    return pts


def to_global(p):
    return R0 @ np.asarray(p, float) + T0


def displacement(loc, kind: str) -> np.ndarray:
    """6 dofs per node in global axes from fields defined in the local x,y of each node."""
    k, k2, e, g, s = 0.02, 0.013, 1.0e-3, 2.0e-3, 1.5e-3
    out = []
    for (x, y, _z) in loc:
        u = np.zeros(3)
        th = np.zeros(3)
        if kind in ("bend", "mix"):
            u[2] += -k * x * x / 2
            th[1] += k * x
        if kind == "mix":
            u[2] += -k2 * y * y / 2
            th[0] += -k2 * y
            th[1] += 0.3 * k * y          # twist
            th[0] += -0.2 * k * x
            u[0] += e * x + s * y
            u[1] += 0.4 * e * y + s * x
            u[2] += g * x + 0.5 * g * y   # shear
        out.append(np.concatenate((R0 @ u, R0 @ th)))
    return np.concatenate(out)


# ----------------------------------------------------------------- branches
BRANCHES = []


def add(bid, kind, topo, sec, **kw):
    BRANCHES.append(dict(id=bid, kind=kind, topo=topo, sec=sec, **kw))


for topo, label in (("t3", "L3"), ("q4", "L4flat"), ("q4w", "L4warp"),
                    ("t6", "L6flat"), ("t6c", "L6curv"), ("q8", "L8flat"), ("q8c", "L8curv")):
    add(f"legacy-{label}-h", "legacy", topo, "h")
    add(f"legacy-{label}-g", "legacy", topo, "g")
add("e4pl-Q4flat-h", "e4pl", "q4", "h")
add("e4pl-Q4flat-g", "e4pl", "q4", "g")
add("e4pl-Q4warp-h", "e4pl", "q4w", "h")
add("e4pl-Q4warp-g", "e4pl", "q4w", "g")
add("e4pl-Q4warp-h-noref", "e4pl", "q4w", "h", ref=False)
add("e4pl-Q4warp-g-noref", "e4pl", "q4w", "g", ref=False)
add("e4pl-Q4flat-h-noref", "e4pl", "q4", "h", ref=False)
add("s3-h", "s3", "t3", "h")
add("s3-g", "s3", "t3", "g")
add("s3v2d-h", "v2d", "t3", "h")
add("s3v2d-g", "v2d", "t3", "g")

FORM = {"legacy": None, "e4pl": "e4-pl", "s3": "e4-pl-s3", "v2d": "e4-pl-s3-v2d"}


def build(br, *, polarity=1, reverse=False, normal=None):
    loc = topology(br["topo"])
    n = len(loc)
    order = list(range(n))
    if reverse:
        if n == 3:
            order = [0, 2, 1]
        elif n == 4:
            order = [0, 3, 2, 1]
        else:
            raise ValueError("reverse only for 3/4 node probes")
    loc = [loc[i] for i in order]
    mesh = FEMesh()
    for i, p in enumerate(loc, start=1):
        mesh.add_node(i, *to_global(p))
    kw = dict(thickness=H)
    if br["sec"] == "g":
        kw["shell_section"] = section()
    kind = br["kind"]
    ids = list(range(1, n + 1))
    nrm = [float(v) for v in (N0 if normal is None else normal)]
    if kind == "legacy":
        el = ShellElement(1, ids, "steel", **kw)
    else:
        if kind != "e4pl" or br.get("ref", True):
            kw["reference_normal"] = nrm
            if kind != "legacy":
                kw["director_polarity"] = polarity
        if kind == "s3":
            # create_shell_element(formulation="e4-pl-s3") resolves to V2D (elements.py:5791-5792),
            # so the qualified S3 class is only reachable by direct construction.
            from anyfesolver.e4_pl_s3_element import QualifiedE4PLS3ShellElement
            el = QualifiedE4PLS3ShellElement(1, ids, "steel", **kw)
        else:
            el = create_shell_element(1, ids, "steel", formulation=FORM[kind], **kw)
    return el, mesh, loc


# --------------------------------------------------------------- utilities
WATCH = {"compute_stresses", "_compute_generalized_section_results",
         "_recover_warped_generalized_section", "_recover_warped_homogeneous_section",
         "_recover_planar_mixed_fields", "_local_frame_and_derivatives", "_center_frame",
         "equation7_frame", "native_recovery_frames", "compute_variational_resultants",
         "_compute_stresses", "triangle_frame", "_physical_director_context"}


class Tracer:
    def __init__(self):
        self.calls = collections.Counter()

    def __call__(self, frame, event, arg):
        if event == "call":
            c = frame.f_code
            if c.co_name in WATCH and "anyfesolver" in c.co_filename.replace("\\", "/"):
                self.calls[f"{os.path.basename(c.co_filename)}:{c.co_firstlineno}:{c.co_name}"] += 1


def recover(el, mesh, loc, kind):
    tr = Tracer()
    sys.setprofile(tr)
    try:
        res = el.compute_stresses(mesh, displacement(loc, kind), MATERIAL, return_global=True)
    finally:
        sys.setprofile(None)
    return res, dict(tr.calls)


FAMILIES = ("global_membrane_resultant_tensors", "global_bending_resultant_tensors",
            "global_transverse_shear_resultants")
COMPS = ("xx", "yy", "zz", "xy", "xz", "yz")
SURF_KEYS = [f"global_{c}_{s}" for s in ("top", "bot") for c in COMPS]


def surf_tensor(r, s, i):
    g = lambda c: float(r[f"global_{c}_{s}"][i])
    return np.array([[g("xx"), g("xy"), g("xz")], [g("xy"), g("yy"), g("yz")], [g("xz"), g("yz"), g("zz")]])


def null_axis(T):
    w, v = np.linalg.eigh(0.5 * (T + T.T))
    j = int(np.argmin(np.abs(w)))
    return v[:, j]


def ang(a, b):
    c = abs(float(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b)))
    return math.degrees(math.acos(min(1.0, c)))


def sgn(x, tol=1e-12):
    return 0 if abs(x) < tol else (1 if x > 0 else -1)


def independent_frames(el, mesh, br):
    """Probe-side reference frames from the solver's own private helpers (not part of the output contract)."""
    kind = br["kind"]
    coords = np.asarray(el.get_node_coordinates(mesh), float)
    if kind in ("s3", "v2d"):
        return None, None, None
    pts = np.asarray(el.gauss_points, float)
    st = []
    for xi, eta in pts:
        _, dxi, deta = el.compute_shape_functions(float(xi), float(eta))
        st.append(ShellElement._local_frame_and_derivatives(el, coords, dxi, deta)[0])
    cen = ShellElement._center_frame(el, coords)
    pos = []
    for xi, eta in pts:
        N, _, _ = el.compute_shape_functions(float(xi), float(eta))
        pos.append(N @ coords)
    return np.array(st), cen, np.array(pos)


def describe_keys(res):
    out = {}
    for k, v in sorted(res.items()):
        if isinstance(v, np.ndarray):
            out[k] = list(v.shape)
        elif isinstance(v, (int, float, str, bool, np.integer, np.floating)):
            out[k] = v if not isinstance(v, (np.integer, np.floating)) else float(v)
        else:
            out[k] = type(v).__name__
    return out


def n_p(res):
    for k in ("membrane_resultants", "membrane_xx", "global_membrane_resultant_tensors"):
        if k in res:
            return int(np.asarray(res[k]).shape[0])
    return None


def metadata_summary(res):
    """Station / frame / director / offset related keys that are returned."""
    want = ("natural", "station", "director", "frame", "hammer", "barycentric", "offset",
            "polarity", "internal_order", "weights", "reference", "warped")
    return {k: (list(v.shape) if isinstance(v, np.ndarray) else (v if isinstance(v, (int, float, str, bool)) else type(v).__name__))
            for k, v in sorted(res.items()) if any(w in k.lower() for w in want)
            and not k.startswith(("local_", "global_"))}


def basis_normals(res, el, mesh, br, frames_ind, cen_ind):
    """Normals of each stored family (null axis) vs centre/station reference frames."""
    kind = br["kind"]
    if kind == "s3":
        from anyfesolver.e4_pl_s3_element import triangle_frame
        f = triangle_frame(np.asarray(el.get_node_coordinates(mesh), float), el.reference_normal)[0]
        st = np.array([f] * n_p(res)); cen = f
    elif kind == "v2d":
        f = np.asarray(res["frame"], float)
        st = np.array([f] * n_p(res)); cen = f
    elif frames_ind is not None and kind == "e4pl" and "natural_point_coordinates" in res:
        from anyfesolver.e4_pl_element import equation7_frame
        f = equation7_frame(np.asarray(el.get_node_coordinates(mesh), float))[0]
        st = np.array([f] * n_p(res)); cen = f
    else:
        st, cen = frames_ind, cen_ind
    out = {}
    if FAMILIES[0] not in res:
        return None
    for fam, key in (("membrane", FAMILIES[0]), ("bending", FAMILIES[1])):
        T = np.asarray(res[key], float)
        dc = [ang(null_axis(T[i]), cen[:, 2]) for i in range(len(T))]
        ds = [ang(null_axis(T[i]), st[i][:, 2]) for i in range(len(T))]
        out[fam] = dict(max_dev_deg_vs_centre_normal=max(dc), max_dev_deg_vs_station_normal=max(ds))
    S = np.asarray(res[FAMILIES[2]], float)
    out["shear"] = dict(
        max_out_of_plane_deg_vs_centre_normal=max(90 - ang(S[i], cen[:, 2]) for i in range(len(S))),
        max_out_of_plane_deg_vs_station_normal=max(90 - ang(S[i], st[i][:, 2]) for i in range(len(S))))
    out["max_centre_vs_station_normal_deg"] = max(ang(cen[:, 2], st[i][:, 2]) for i in range(len(st)))
    for fam in ("membrane", "bending"):
        d = out[fam]
        d["frame"] = classify(d["max_dev_deg_vs_centre_normal"], d["max_dev_deg_vs_station_normal"],
                              out["max_centre_vs_station_normal_deg"])
    d = out["shear"]
    d["frame"] = classify(d["max_out_of_plane_deg_vs_centre_normal"], d["max_out_of_plane_deg_vs_station_normal"],
                          out["max_centre_vs_station_normal_deg"])
    return out


def classify(dev_c, dev_s, spread):
    if spread < 1e-6:
        return "indistinguishable (frame constant over the element)" + ("" if dev_c < 1e-6 else " MISMATCH")
    if dev_c < 1e-6 and dev_s > 1e-6:
        return "centre"
    if dev_s < 1e-6 and dev_c > 1e-6:
        return "station"
    return f"neither (centre {dev_c:.3g} deg, station {dev_s:.3g} deg)"


def bend_sign_probe(br, polarity, reverse):
    """Pure-bending state with the physical +N0 side in tension. Returns sign facts."""
    el, mesh, loc = build(br, polarity=polarity, reverse=reverse)
    res, calls = recover(el, mesh, loc, "bend")
    kind = br["kind"]
    out = dict(polarity=polarity, reversed_numbering=reverse, n_p=n_p(res))
    # director (physical +side the element claims)
    director = None
    if kind in ("s3", "v2d"):
        director = el.physical_reference_director
    elif kind == "e4pl" and br.get("ref", True):
        director = np.asarray(res["physical_director"], float).reshape(-1)[:3] if "physical_director" in res else el.physical_reference_director
    # tensor-basis normal (signed) the element used
    frames_ind, cen_ind, _ = independent_frames(el, mesh, br)
    if kind == "s3":
        from anyfesolver.e4_pl_s3_element import triangle_frame
        basis = triangle_frame(np.asarray(el.get_node_coordinates(mesh), float), el.reference_normal)[0][:, 2]
        out["basis_normal_source"] = "triangle_frame(reference_normal) (private; not returned)"
    elif kind == "v2d":
        basis = np.asarray(res["frame"], float)[:, 2]
        out["basis_normal_source"] = "returned 'frame'[:,2]"
    elif kind == "e4pl" and br.get("ref", True):
        basis = np.asarray(res["physical_director"], float).reshape(-1)[:3]
        out["basis_normal_source"] = "returned 'physical_director'"
    else:
        basis = cen_ind[:, 2]
        out["basis_normal_source"] = "numbered frame from private _center_frame (not returned)"
    out["basis_normal_dot_N0"] = float(basis @ N0)
    if director is not None:
        out["director_dot_N0"] = float(np.asarray(director) @ N0)
    # M along AX
    if FAMILIES[1] in res:
        M = np.asarray(res[FAMILIES[1]], float)
        out["M_aa_global_mean"] = float(np.mean([AX @ M[i] @ AX for i in range(len(M))]))
        out["M_aa_sign"] = sgn(out["M_aa_global_mean"], 1e-3)
        out["M_positive_means_tension_on_side"] = (
            "+basis_normal" if out["M_aa_sign"] * sgn(out["basis_normal_dot_N0"]) > 0 else "-basis_normal")
        if director is not None:
            out["M_positive_means_tension_on_side_vs_director"] = (
                "+director" if out["M_aa_sign"] * sgn(out["director_dot_N0"]) > 0 else "-director")
    if "global_xx_top" in res:
        d = []
        for i in range(out["n_p"]):
            d.append(AX @ surf_tensor(res, "top", i) @ AX - AX @ surf_tensor(res, "bot", i) @ AX)
        d = float(np.mean(d))
        out["top_minus_bot_sigma_aa_mean"] = d
        out["top_side_is_plus_N0"] = bool(d > 0)
        out["top_on_plus_basis_normal"] = bool((d > 0) == (out["basis_normal_dot_N0"] > 0))
        if director is not None:
            out["top_on_plus_director"] = bool((d > 0) == (out["director_dot_N0"] > 0))
    # shear sign irrelevant for bend; local keys for polarity analysis
    out["local_bending_xx_mean"] = float(np.mean(res["bending_xx"])) if "bending_xx" in res else None
    out["local_bending_resultant_xx_mean"] = float(np.mean(np.asarray(res["bending_resultants"])[:, 0])) if "bending_resultants" in res else None
    out["calls"] = calls
    return out, res


def polarity_effect(r1, r2):
    """Compare recovered outputs between polarity +1 and -1 on the same physical state."""
    def rel(a, b):
        a, b = np.asarray(a, float), np.asarray(b, float)
        sc = max(np.max(np.abs(a)), np.max(np.abs(b)), 1e-300)
        if np.max(np.abs(a - b)) < 1e-9 * sc:
            return "same"
        if np.max(np.abs(a + b)) < 1e-9 * sc:
            return "negated"
        return "other"
    out = {}
    for k in ("membrane_resultants", "bending_resultants", "transverse_shear_resultants",
              "bending_xx", "shear_xz", "membrane_xx") + FAMILIES:
        if k in r1 and k in r2:
            out[k] = rel(r1[k], r2[k])
    if "global_xx_top" in r1:
        same = all(rel(r1[f"global_{c}_top"], r2[f"global_{c}_top"]) == "same" for c in COMPS)
        swap = all(rel(r1[f"global_{c}_top"], r2[f"global_{c}_bot"]) == "same" for c in COMPS)
        out["global_surface_top_bot"] = "same" if same else ("top/bot swapped" if swap else "other")
    return out


def surface_basis(res, el, mesh, br, frames_ind, cen_ind):
    """Which frame are the global surface tensors in? The zz entry is zero in the frame used."""
    if "global_xx_top" not in res:
        return None
    kind = br["kind"]
    n = n_p(res)
    if kind in ("s3", "v2d"):
        if kind == "v2d":
            f = np.asarray(res["frame"], float)
        else:
            from anyfesolver.e4_pl_s3_element import triangle_frame
            f = triangle_frame(np.asarray(el.get_node_coordinates(mesh), float), el.reference_normal)[0]
        st = np.array([f] * n); cen = f
    elif kind == "e4pl" and "natural_point_coordinates" in res:
        from anyfesolver.e4_pl_element import equation7_frame
        f = equation7_frame(np.asarray(el.get_node_coordinates(mesh), float))[0]
        st = np.array([f] * n); cen = f
    else:
        st, cen = frames_ind, cen_ind
    dc = ds = 0.0
    for sfc in ("top", "bot"):
        for i in range(n):
            T = surf_tensor(res, sfc, i)
            sc = max(np.max(np.abs(T)), 1e-300)
            dc = max(dc, abs(cen[:, 2] @ T @ cen[:, 2]) / sc)
            ds = max(ds, abs(st[i][:, 2] @ T @ st[i][:, 2]) / sc)
    spread = max(ang(cen[:, 2], st[i][:, 2]) for i in range(n))
    return dict(max_rel_nn_in_centre_frame=dc, max_rel_nn_in_station_frame=ds,
                frame=classify(dc * 90, ds * 90, spread) if spread >= 1e-6 else "constant frame")


def station_table(el, br, res):
    kind = br["kind"]
    if kind == "s3":
        from anyfesolver.e4_pl_s3_element import TRIANGLE_QUADRATURE
        return [list(map(float, row[:2])) for row in TRIANGLE_QUADRATURE]
    if kind == "v2d":
        return np.asarray(res["hammer_points"], float).tolist()
    if "natural_point_coordinates" in res:
        return np.asarray(res["natural_point_coordinates"], float).tolist()
    return np.asarray(el.gauss_points, float).tolist()


def mix_probe(br):
    el, mesh, loc = build(br)
    res, calls = recover(el, mesh, loc, "mix")
    frames_ind, cen_ind, pos_ind = independent_frames(el, mesh, br)
    d = dict(
        keys=describe_keys(res),
        n_p=n_p(res),
        global_family_keys_present=[k for k in FAMILIES if k in res],
        global_surface_keys_present=[k for k in SURF_KEYS if k in res],
        metadata=metadata_summary(res),
        calls=calls,
        recovery_scope=res.get("recovery_scope"),
        basis_normals=basis_normals(res, el, mesh, br, frames_ind, cen_ind),
    )
    # station positions returned vs independent
    if "physical_station_coordinates" in res:
        pos = np.asarray(res["physical_station_coordinates"], float)
        coords = np.asarray(el.get_node_coordinates(mesh), float)
        from anyfesolver.e4_pl_s3_v2d_element import HAMMER_POINTS
        bary = np.column_stack((1 - HAMMER_POINTS.sum(1), HAMMER_POINTS))
        d["station_position_check_hammer_on_external_nodes_max_abs_diff"] = float(
            np.max(np.abs(pos - bary @ coords)))
    d["station_positions_returned"] = "physical_station_coordinates" in res
    d["natural_coordinates_returned"] = [k for k in res if "natural" in k or "hammer" in k or "barycentric" in k]
    if pos_ind is not None:
        d["independent_station_positions_shape"] = list(pos_ind.shape)
    # director metadata vs frames
    for key in ("physical_director", "physical_directors"):
        if key in res:
            arr = np.asarray(res[key], float)
            d[f"{key}_shape"] = list(arr.shape)
            if key == "physical_directors" and frames_ind is not None:
                d["physical_directors_vs_station_normal_max_deg"] = max(
                    ang(arr[i], frames_ind[i][:, 2]) for i in range(len(arr)))
            if key == "physical_director" and cen_ind is not None:
                d["physical_director_vs_centre_normal_deg"] = ang(arr.reshape(-1)[:3], cen_ind[:, 2])
    d["surface_basis"] = surface_basis(res, el, mesh, br, frames_ind, cen_ind)
    d["station_table_natural_coordinates"] = station_table(el, br, res)
    return d, res


def check_claims(table, extra):
    """Compare the D2-design section-0 claims F1-F6 with what was observed."""
    out = []

    def add_claim(cid, text, ok, observed):
        out.append(dict(id=cid, claim=text, holds=bool(ok), observed=observed))

    hom = [b for b in table if b.endswith("-h") or b.endswith("-h-noref")]
    gen = [b for b in table if b.endswith("-g") or b.endswith("-g-noref")]
    f1 = {b: len(table[b]["mix"].get("global_surface_keys_present", [])) for b in hom}
    add_claim("F1", "homogeneous branches return all 12 global_{xx..yz}_{top,bot}", all(v == 12 for v in f1.values()), f1)
    expected_f2 = {b: not (b.startswith("legacy") and b.endswith("-h")) and b not in ("e4pl-Q4warp-h", "e4pl-Q4warp-h-noref") for b in table}
    obs_f2 = {b: len(table[b]["mix"].get("global_family_keys_present", [])) == 3 for b in table}
    add_claim("F2", "global resultant tensors: not by legacy-h and not by e4-pl warped-h; by all others",
              obs_f2 == expected_f2, {b: v for b, v in obs_f2.items() if v != expected_f2[b]} or "all match")
    def frame_of(b, fam):
        bn = table[b]["mix"].get("basis_normals")
        return bn[fam]["frame"] if bn else None
    add_claim("F3", "warped Q4: generalized shear in centre frame, homogeneous surface tensors in station frames",
              frame_of("e4pl-Q4warp-g", "shear") == "centre" and table["e4pl-Q4warp-h"]["mix"]["surface_basis"]["frame"] == "station",
              dict(generalized_shear=frame_of("e4pl-Q4warp-g", "shear"), generalized_membrane=frame_of("e4pl-Q4warp-g", "membrane"),
                   generalized_bending=frame_of("e4pl-Q4warp-g", "bending"),
                   homogeneous_surfaces=table["e4pl-Q4warp-h"]["mix"]["surface_basis"]["frame"],
                   native_shear_vs_legacy_centre_diff=extra.get("warped_h_native_shear_equals_legacy_centre_frame_shear_max_abs_diff")))
    pe = {b: table[b].get("polarity_effect_minus1_vs_plus1", {}) for b in ("s3-h", "s3-g", "s3v2d-h", "s3v2d-g")}
    add_claim("F3a", "S3 multiplies global M,Q by polarity (global M,Q unchanged under polarity flip); V2D director = polarity*reference_normal; V2D global M,Q not re-multiplied",
              all(pe[b].get("global_bending_resultant_tensors") == "same" for b in ("s3-h", "s3-g")) and
              all(pe[b].get("global_bending_resultant_tensors") == "negated" for b in ("s3v2d-h", "s3v2d-g")),
              {b: dict(local_M=v.get("bending_resultants"), global_M=v.get("global_bending_resultant_tensors"), global_Q=v.get("global_transverse_shear_resultants")) for b, v in pe.items()})
    tr = table["e4pl-Q4warp-h"]["mix"]["calls"]; trg = table["e4pl-Q4warp-g"]["mix"]["calls"]
    sh_cs = [k for k in tr if k.startswith("elements.py:3398")]
    sh_gen = [k for k in trg if k.startswith("elements.py:3253")]
    sh_cs_g = [k for k in trg if k.startswith("elements.py:3398")]
    add_claim("F3b", "warped qualified Q4 recovers through ShellElement.compute_stresses with a frame per recovery point",
              bool(sh_cs) and not sh_cs_g, dict(homogeneous_calls=sh_cs and {k: tr[k] for k in tr if k.startswith("elements.py:19") or k.startswith("e4_pl_element.py:3181")},
                                                 homogeneous_enters_ShellElement_compute_stresses=bool(sh_cs),
                                                 generalized_enters_ShellElement_compute_stresses=bool(sh_cs_g),
                                                 generalized_enters_ShellElement__compute_generalized_section_results=bool(sh_gen),
                                                 equation7_frame_called_for_warpage_test_only=True))
    m = {b: table[b]["mix"].get("metadata", {}) for b in table}
    add_claim("F5a", "e4-pl reports physical_director(s) and natural_point_coordinates",
              all("physical_director" in m[b] for b in m if b.startswith("e4pl") and "noref" not in b) and
              all("natural_point_coordinates" in m[b] for b in m if b.startswith("e4pl")),
              {b: sorted(k for k in m[b] if k in ("physical_director", "physical_directors", "natural_point_coordinates")) for b in m if b.startswith("e4pl")})
    add_claim("F5b", "V2D reports frame, hammer_points, external_barycentric_coordinates, physical_station_coordinates",
              all(k in m[b] for b in ("s3v2d-h", "s3v2d-g") for k in ("frame", "hammer_points", "external_barycentric_coordinates", "physical_station_coordinates")),
              {b: sorted(m[b]) for b in ("s3v2d-h", "s3v2d-g")})
    add_claim("F5c", "legacy reports none of these", all(not m[b] for b in m if b.startswith("legacy")), {b: sorted(m[b]) for b in m if b.startswith("legacy") and m[b]} or "none")
    add_claim("F6", "V2D reports moment reference and physical surface offsets",
              all("reference_surface_offset" in m[b] for b in ("s3v2d-h", "s3v2d-g")),
              dict(ordinary_homogeneous=sorted(k for k in m["s3v2d-h"] if "offset" in k), generalized=sorted(k for k in m["s3v2d-g"] if "offset" in k),
                   homogeneous_polarity_minus1=sorted(k for k in extra.get("metadata_variants", {}).get("s3v2d-h {'director_polarity': -1}", {}) if "offset" in k)))
    return out


def run():
    table = {}
    for br in BRANCHES:
        rec = dict(spec={k: v for k, v in br.items()})
        try:
            rec["mix"], _ = mix_probe(br)
        except Exception as exc:  # fail closed: record the refusal
            rec["mix"] = dict(error=f"{type(exc).__name__}: {exc}")
        sign = []
        results = {}
        variants = [(1, False)]
        if br["kind"] in ("legacy", "e4pl", "s3", "v2d") and br["topo"] in ("t3", "q4", "q4w"):
            variants.append((1, True))
        if br["kind"] in ("s3", "v2d") or (br["kind"] == "e4pl" and br.get("ref", True)):
            variants.append((-1, False))
        for pol, rev in variants:
            try:
                s, r = bend_sign_probe(br, pol, rev)
                sign.append(s)
                results[(pol, rev)] = r
            except Exception as exc:
                sign.append(dict(polarity=pol, reversed_numbering=rev, error=f"{type(exc).__name__}: {exc}"))
        rec["bend_sign"] = sign
        if (1, False) in results and (-1, False) in results:
            rec["polarity_effect_minus1_vs_plus1"] = polarity_effect(results[(1, False)], results[(-1, False)])
        # flat-reference q4 warped fixture only has warpage meaning; record warpage
        table[br["id"]] = rec
    extra = {}
    # warped Q4: native shear_* in the centre frame? (e4-pl with director vs legacy path, same numbering)
    try:
        b1 = next(b for b in BRANCHES if b["id"] == "e4pl-Q4warp-h")
        b2 = next(b for b in BRANCHES if b["id"] == "e4pl-Q4warp-h-noref")
        e1, m1, l1 = build(b1); e2, m2, l2 = build(b2)
        r1, _ = recover(e1, m1, l1, "mix"); r2, _ = recover(e2, m2, l2, "mix")
        extra["warped_h_native_shear_equals_legacy_centre_frame_shear_max_abs_diff"] = float(
            max(np.max(np.abs(r1["shear_xz"] - r2["shear_xz"])), np.max(np.abs(r1["shear_yz"] - r2["shear_yz"]))))
        extra["warped_h_native_shear_scale"] = float(np.max(np.abs(r2["shear_xz"])))
    except Exception as exc:
        extra["warped_h_native_shear_equals_legacy_centre_frame_shear_max_abs_diff"] = f"{type(exc).__name__}: {exc}"
    # V2D reference-surface modes: source normal vs faceted facet normal
    from anyfesolver.e4_pl_s3_v2d_element import NativeParityCapabilityError
    br = dict(id="v2d-modes", kind="v2d", topo="t3", sec="h")
    tilt = 0.25
    Rt = np.array([[1, 0, 0], [0, math.cos(tilt), -math.sin(tilt)], [0, math.sin(tilt), math.cos(tilt)]])
    tilted = R0 @ Rt @ np.array([0, 0, 1.0])
    modes = {}
    for name, nrm in (("faceted_or_flat_source (normal == facet normal)", N0), ("source_normal_tilted_off_facet", tilted)):
        try:
            el, mesh, loc = build(br, normal=nrm)
            res, _ = recover(el, mesh, loc, "bend")
            modes[name] = dict(ok=True, frame_normal_dot_reference_normal=float(np.asarray(res["frame"])[:, 2] @ nrm),
                               n_p=n_p(res))
        except Exception as exc:
            modes[name] = dict(ok=False, error=f"{type(exc).__name__}: {exc}")
    extra["v2d_reference_surface_modes"] = modes
    # metadata that depends on polarity / offset (V2D homogeneous takes a V2C shortcut for polarity 1, offset 0)
    meta_var = {}
    for bid, kind, kw in (("s3v2d-h", "v2d", dict(director_polarity=-1)),
                          ("s3v2d-h", "v2d", dict(reference_surface_offset=0.02)),
                          ("s3-h", "s3", dict(reference_surface_offset=0.02)),
                          ("s3v2d-g", "v2d", dict(reference_surface_offset=0.02))):
        key = f"{bid} {kw}"
        try:
            brx = next(b for b in BRANCHES if b["id"] == bid)
            el, mesh, loc = build(brx)
            if "director_polarity" in kw or "reference_surface_offset" in kw:
                kwargs = dict(thickness=H, reference_normal=[float(v) for v in N0])
                if brx["sec"] == "g":
                    kwargs["shell_section"] = section()
                kwargs.update(kw)
                if kind == "s3":
                    from anyfesolver.e4_pl_s3_element import QualifiedE4PLS3ShellElement
                    el = QualifiedE4PLS3ShellElement(1, [1, 2, 3], "steel", **kwargs)
                else:
                    el = create_shell_element(1, [1, 2, 3], "steel", formulation=FORM[kind], **kwargs)
            res, _ = recover(el, mesh, loc, "bend")
            meta_var[key] = metadata_summary(res)
        except Exception as exc:
            meta_var[key] = dict(error=f"{type(exc).__name__}: {exc}")
    extra["metadata_variants"] = meta_var
    # qualified e4-pl variants exist for 4-node (Q4) and 3-node (S3) only
    refusals = {}
    for nn in (6, 8):
        try:
            create_shell_element(1, list(range(1, nn + 1)), "steel", formulation="e4-pl", thickness=H)
            refusals[f"e4-pl with {nn} nodes"] = "accepted"
        except Exception as exc:
            refusals[f"e4-pl with {nn} nodes"] = f"{type(exc).__name__}: {exc}"
    extra["higher_order_qualified_refusals"] = refusals
    extra["s3_alias_resolution"] = type(create_shell_element(1, [1, 2, 3], "steel", formulation="e4-pl-s3", thickness=H,
                                                        reference_normal=[0.0, 0.0, 1.0])).__name__
    extra["solver_sha"] = subprocess.run(["git", "-C", "C:/Github/ANYfeSolver", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    extra["solver_module"] = anyfesolver.__file__
    extra["thickness"] = H
    extra["N0"] = N0.tolist()
    tb = {b: np.asarray(table[b]["mix"]["station_table_natural_coordinates"], float) for b in
          ("e4pl-Q4flat-h", "e4pl-Q4warp-h", "legacy-L4flat-h")}
    extra["q4_station_order"] = dict(
        flat_equals_warped=bool(np.allclose(tb["e4pl-Q4flat-h"], tb["e4pl-Q4warp-h"])),
        legacy_equals_warped=bool(np.allclose(tb["legacy-L4flat-h"], tb["e4pl-Q4warp-h"])),
        flat_table=tb["e4pl-Q4flat-h"].tolist(), warped_table=tb["e4pl-Q4warp-h"].tolist(),
        note="same index means a different natural point on the planar (equation7) path than on the warped/legacy path")
    extra["doc_claims"] = check_claims(table, extra)
    return dict(branches=table, extra=extra)


if __name__ == "__main__":
    data = run()
    OUT.write_text(json.dumps(data, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))
    print("wrote", OUT, "branches:", len(data["branches"]))
    for bid, rec in data["branches"].items():
        m = rec["mix"]
        if "error" in m:
            print(bid, "ERROR", m["error"][:120])
        else:
            print(bid, "n_p", m["n_p"], "fam", len(m["global_family_keys_present"]), "surf", len(m["global_surface_keys_present"]))
