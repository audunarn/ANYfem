"""S6 benchmark driver for the A5 CCX reference: staged, explicit, never run by the author.

NOT RUN (no execution was available when this was written).  Every stage is a pure function plus a thin CLI; only the
coordinator starts subprocesses (CalculiX) -- this file never does.  Output directories must be new or empty and never
inside a ``handoff`` folder (``reference_ccx.require_output_dir``).

Scope: the A5 full stiffened stage fixture only.  The 3D solid child belongs to the solid_reference owner; the earlier
whole-child CCX comparator and the clamped 3D block decks were removed (a different boundary value problem from A5, they
could contribute nothing to A5 or to the solid assessment).

Stages (each reads what the previous one wrote):

1. ``stage-a5``          build the REAL ``stiffened_project`` through the supported assembly (generate_mesh ->
                         build_fe_model), run the per-case checks (stiffness / frames / offset / couplings / force /
                         moment / support), write ``report/a5_input_report.json``, ``spec.json`` and the calibration decks.
2. (coordinator)         run ccx on ``calibration/cal1..cal4`` (each separately; keep stdout/stderr and the .dat of failures).
3. ``read-calibration``  read the calibration .dat files -> ``calibration.json`` (the ``CalibrationRecord``).  A refusal means
                         A5 is INCONCLUSIVE: no production deck is written and nothing is bypassed.
4. ``a5-decks``          production A5 decks; refuses unless the record is usable.
5. (coordinator)         run ccx on ``a5_n*.inp``.
6. ``read-a5``           per-quantity convergence + comparison with the original A5 run (``s4b/a5_budget.json``).

Physical-offset fallback (separately marked, also NOT RUN): ``stage-physical``, ``physical-joint-decks``, ``read-physical``,
``native-decks``, ``read-native`` -- see REFERENCE.md "Physical-offset fallback" for the order and the ccx runs between them.

Invocation (from the worktree root, with the repository's recorded dependencies)::

    python docs/handoff/submodelling-20261009/s6/benchmark.py stage-a5 --out <new dir>
    python docs/handoff/submodelling-20261009/s6/benchmark.py read-calibration --run-dir <dir with cal*.dat> --spec <out>/spec.json --out <new dir>
    python docs/handoff/submodelling-20261009/s6/benchmark.py a5-decks --spec <out>/spec.json --calibration <cal>/calibration.json --out <new dir>
    python docs/handoff/submodelling-20261009/s6/benchmark.py read-a5 --run-dir <dir with a5_n*.dat> --spec <out>/spec.json --out <new dir>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Dict, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[3] / "tests"))

import reference_ccx as R  # noqa: E402

PARENT_SIZE = 0.1  # the A5 coarse parent (s4b/test_a5_budget.py)
BUDGET_JSON = HERE.parent / "s4b" / "a5_budget.json"


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_spec(path) -> R.StiffenedSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data["edge_dofs"] = {k: tuple(v) for k, v in data["edge_dofs"].items()}
    return R.StiffenedSpec(**data)


# ----------------------------------------------------------------------------------------------
# stage 1: the real A5 project through the supported assembly
# ----------------------------------------------------------------------------------------------
def a5_input_report(spec: R.StiffenedSpec, pre: R.Preflight, totals: dict, couplings: dict) -> dict:
    """Per-case record: stiffness, offset, force, moment, support -- each next to the number it was checked against."""

    applied = R.applied_totals(spec)
    return {
        "case": "default",
        "pressure_convention": {"anyfem_sign": "positive project pressure assembles toward +z (measured by anyfem_load_totals)",
                                "deck": "explicit consistent nodal forces toward +z, no *DLOAD label", "sign": R.PRESSURE_FORCE_SIGN},
        "stiffness": {"rectangle_width_local_y": spec.bar_width, "rectangle_height_local_z": spec.bar_height,
                      "rectangle_area": spec.beam_area, "rectangle_I_about_local_y": spec.inertia_vertical(),
                      "rectangle_I_about_local_z": spec.inertia_lateral(), "solver_section": dict(spec.solver_section),
                      "plate_t": spec.plate_t, "E": spec.E, "nu": spec.nu},
        "offset": {"mode": spec.offset_mode, "z": spec.offset, "couplings": couplings},
        "force": {"deck": applied["force"], "anyfem_assembled": totals["force"]},
        "moment": {"deck_about_origin": applied["moment_about_origin"], "anyfem_assembled_about_origin": totals.get("moment_about_origin"),
                   "anyfem_nodal_couples": totals.get("nodal_couples")},
        "support": {"edge_dofs": {k: list(v) for k, v in spec.edge_dofs.items()}, "source": spec.edge_dofs_source},
        "preflight": pre.checks, "preflight_failed": [c["name"] for c in pre.failed()],
    }


def stage_a5(out_dir, parent_size: float = PARENT_SIZE) -> dict:
    from anyfem.solve.build import build_fe_model
    from test_submodel_cut import stiffened_project

    out = R.require_output_dir(out_dir)
    project, _faces, _stiffeners = stiffened_project()
    mesh = project.generate_mesh(parent_size)
    built = build_fe_model(project, mesh, "default", require_loads=False, require_supports=False)
    spec, pre = R.spec_from_project(project)
    coupling = R.coupling_report(built, spec, pre)
    spec = R.spec_with_built_supports(spec, built, pre)
    totals = R.anyfem_load_totals(project, mesh)
    R.check_load_closure_vs_anyfem(spec, totals, pre)
    report = a5_input_report(spec, pre, totals, coupling)
    (out / "report").mkdir()
    _dump(out / "report" / "a5_input_report.json", report)  # written even when the preflight fails: the failure is evidence
    _dump(out / "spec.json", dataclasses.asdict(spec))
    if pre.failed():
        print("PREFLIGHT FAILED:", [c["name"] for c in pre.failed()])
        return {"ok": False, "failed": [c["name"] for c in pre.failed()], "out": str(out)}
    R.write_calibration_decks(spec, out / "calibration")
    print(f"preflight passed; calibration decks in {out / 'calibration'} (run cal1..cal4, each separately)")
    return {"ok": True, "out": str(out)}


# ----------------------------------------------------------------------------------------------
# stage 3: calibration readout
# ----------------------------------------------------------------------------------------------
def read_calibration(run_dir, spec: R.StiffenedSpec, out_dir) -> dict:
    run, out = Path(run_dir), R.require_output_dir(out_dir)
    result: Dict[str, object] = {"missing": [], "errors": {}}

    def blocks(name):
        dat = run / f"{name}.dat"
        if not dat.is_file():
            result["missing"].append(str(dat))
            return None
        try:
            return R.parse_dat(dat.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError) as error:  # an unreadable .dat is a finding, not a crash
            result["errors"][name] = f"{type(error).__name__}: {error}"
            return None

    def check(key, name, function):
        parsed = blocks(name)
        if parsed is None:
            return None
        try:
            result[key] = function(parsed)
        except (KeyError, ValueError) as error:  # missing block / zero response: keep the message as evidence
            result["errors"][name] = f"{type(error).__name__}: {error}"
            return None
        return result[key]

    frame = check("beam_frame", R.CAL_BEAM_FRAME, lambda b: R.check_beam_frame(b, spec))
    ry = check("rotation_link_ry", R.CAL_RY_LINK, lambda b: R.check_rotation_link(spec, b, "x"))
    rx = check("rotation_link_rx", R.CAL_RX_LINK, lambda b: R.check_rotation_link(spec, b, "y"))
    sign = check("pressure_sign", R.CAL_PRESSURE_SIGN, R.check_pressure_sign)
    if frame and ry and rx and sign:
        record = R.CalibrationRecord(bool(frame["frame_confirmed"]), bool(ry["link_effective"]), bool(rx["link_effective"]),
                                     bool(sign["explicit_load_verified"]), str(sign["p1_acts_toward"]), source=str(run))
        result["record"] = dataclasses.asdict(record)
        try:
            record.require_usable()
            result["usable"] = True
        except ValueError as error:
            result["usable"] = False
            result["refusal"] = str(error)
    else:
        result["usable"] = False
        result["refusal"] = "calibration incomplete: " + ", ".join(
            k for k, v in (("cal1", frame), ("cal2", ry), ("cal3", rx), ("cal4", sign)) if not v) + "; A5 is INCONCLUSIVE until all four are read"
    _dump(out / "calibration.json", result)
    return result


def load_calibration(path) -> R.CalibrationRecord:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "record" not in data:
        raise ValueError(f"{path}: no calibration record (missing runs: {data.get('missing')}, errors: {data.get('errors')})")
    return R.CalibrationRecord(**data["record"])


# ----------------------------------------------------------------------------------------------
# physical-offset fallback (A5-F), separate from the stages above; see REFERENCE.md "Physical-offset fallback"
#   stage-physical -> ccx phys_a_offset_law -> physical-joint-decks -> ccx phys_j_* (6) -> read-physical
#   -> native-decks -> ccx a5_native_n* -> read-native
# ----------------------------------------------------------------------------------------------
def _read_law(run_dir, spec: R.StiffenedSpec) -> dict:
    dat = Path(run_dir) / f"{R.CAL_OFFSET_LAW}.dat"
    return R.check_offset_law(spec, R.parse_dat(dat.read_text(encoding="utf-8")))


def physical_joint_decks(spec: R.StiffenedSpec, law_run_dir, out_dir) -> dict:
    law = _read_law(law_run_dir, spec)
    try:
        manifest = R.write_joint_decks(spec, out_dir, law)
    except ValueError as error:  # keep the law evidence next to the refusal
        print("REFUSED:", error)
        print(json.dumps(law, indent=2, default=str))
        return {"ok": False, "law": law}
    _dump(Path(out_dir) / "law.json", law)
    return {"ok": True, "law": law, "decks": list(manifest["decks"])}


def read_physical(law_run_dir, joint_run_dir, spec: R.StiffenedSpec, out_dir) -> dict:
    out = R.require_output_dir(out_dir)
    law = _read_law(law_run_dir, spec)
    joints, evidence, errors = {}, {}, {}
    for mode in R.PHYS_MODES:
        for axis in ("x", "y"):
            key, name = f"{mode}_{axis}", R.joint_name(mode, axis)
            frd, dat = Path(joint_run_dir) / f"{name}.frd", Path(joint_run_dir) / f"{name}.dat"
            try:
                blocks = R.parse_dat(dat.read_text(encoding="utf-8")) if dat.is_file() else None
                evidence[key] = R.check_joint(spec, frd.read_text(encoding="utf-8", errors="replace"), axis, mode, blocks)
                joints[key] = evidence[key]["effective"]
            except (OSError, KeyError, ValueError) as error:  # missing file / probe nodes not found: evidence, joint stays False
                errors[key] = f"{type(error).__name__}: {error}"
                joints[key] = False
    record = R.PhysicalRecord(bool(law["effective"]), int(law["sign_x"]), int(law["sign_y"]), joints, source=str(joint_run_dir))
    result = {"law": law, "joints": evidence, "errors": errors, "record": dataclasses.asdict(record)}
    _dump(out / "physical.json", result)
    return result


def load_physical(path) -> R.PhysicalRecord:
    data = json.loads(Path(path).read_text(encoding="utf-8"))["record"]
    return R.PhysicalRecord(**data)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name, *args):
        p = sub.add_parser(name)
        for a in args:
            p.add_argument(f"--{a}", required=True)
        return p

    add("stage-a5", "out")
    add("read-calibration", "run-dir", "spec", "out")
    add("a5-decks", "spec", "calibration", "out")
    p = add("read-a5", "run-dir", "spec", "out")
    p.add_argument("--levels", type=int, nargs="+", default=list(R.DEFAULT_LEVELS))
    add("stage-physical", "spec", "out")
    add("physical-joint-decks", "spec", "law-run-dir", "out")
    add("read-physical", "spec", "law-run-dir", "joint-run-dir", "out")
    p = add("native-decks", "spec", "calibration", "physical", "out")
    p.add_argument("--levels", type=int, nargs="+", default=list(R.NATIVE_LEVELS))
    p = add("read-native", "run-dir", "spec", "out")
    p.add_argument("--levels", type=int, nargs="+", default=list(R.NATIVE_LEVELS))
    args = ap.parse_args(argv)
    cmd = args.command
    if cmd == "stage-physical":
        print(json.dumps(R.write_offset_law_deck(load_spec(args.spec), args.out)["decks"], indent=2))
        return 0
    if cmd == "physical-joint-decks":
        return 0 if physical_joint_decks(load_spec(args.spec), args.law_run_dir, args.out)["ok"] else 2
    if cmd == "read-physical":
        result = read_physical(args.law_run_dir, args.joint_run_dir, load_spec(args.spec), args.out)
        print(json.dumps({"law_effective": result["law"]["effective"], "joints": result["record"]["joints"], "errors": result["errors"]}, indent=2))
        return 0 if result["law"]["effective"] and all(result["record"]["joints"].values()) and len(result["record"]["joints"]) == 6 else 2
    if cmd == "native-decks":
        try:
            print(json.dumps(list(R.write_native_decks(load_spec(args.spec), args.out, args.levels, calibration=load_calibration(args.calibration),
                                                       physical=load_physical(args.physical))["decks"]), indent=2))
        except ValueError as error:
            print("REFUSED:", error)
            return 2
        return 0
    if cmd == "read-native":
        out = R.require_output_dir(args.out)
        report = R.read_native(args.run_dir, args.levels, load_spec(args.spec))
        _dump(out / "a5_native_report.json", report)
        print(json.dumps(report.get("convergence", report), indent=2, default=str))
        return 0
    if cmd == "stage-a5":
        return 0 if stage_a5(args.out)["ok"] else 2
    if cmd == "read-calibration":
        result = read_calibration(args.run_dir, load_spec(args.spec), args.out)
        print(json.dumps({k: result.get(k) for k in ("missing", "errors", "usable", "refusal", "record")}, indent=2, default=str))
        return 0 if result.get("usable") else 2
    if cmd == "a5-decks":
        print(json.dumps(R.write_a5_decks(load_spec(args.spec), args.out, calibration=load_calibration(args.calibration))["decks"], indent=2))
    elif cmd == "read-a5":
        out = R.require_output_dir(args.out)
        report = R.read_a5(args.run_dir, args.levels, load_spec(args.spec))
        if BUDGET_JSON.is_file() and report["levels"]:
            report["original_a5_comparison"] = R.compare_with_original_a5_run(report["levels"], json.loads(BUDGET_JSON.read_text(encoding="utf-8")))
        _dump(out / "a5_reference_report.json", report)
        print(json.dumps(report.get("convergence", report), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
