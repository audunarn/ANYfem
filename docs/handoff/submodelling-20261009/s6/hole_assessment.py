"""A3/A4 hole assessment stages. Each solve command operates on one registered case."""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
from hashlib import sha256
from itertools import product
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

import numpy as np

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests"))
from submodel_a8_helpers import (
    DIAMETER, HOWLAND_KT, NOMINAL_STRESS, RADIUS, THICKNESS, WIDTH,
    configure_cut_mesh, holed_strip, mesh_settings, stress_factor,
)
from test_submodel_regional_integration import solve_parent
from anyfem.io.artifacts import ArtifactStore
from anyfem.io.project_file import load_project
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import ProjectError
from anyfem.model.submodels import SourceReference
from anyfem.submodel_cut import cut_submodel
from anyfem.submodel_jobs import add_submodel, discard_staged_run, job_source, publish_submodel_run, stage_submodel_run, submodel_snapshot
from anygeometry.curves import Arc
from anygeometry.errors import GeometryError
from anygeometry.operations import split_face_between
from anygeometry.volume_cut import BoxVolume
from anymesher.quality import verify_mesh_quality
from anymesher.errors import MeshError

PLACEMENTS = {f"x{x:g}-y{y:g}": (x, y) for x, y in product((1.5, 2., 3.), (1.25, 1.75, 2.25))}
RECOMMENDED = "x2-y1.75"


def write(path, data):
    def primitive(value):
        if isinstance(value, (np.ndarray, np.generic)):
            return value.tolist()
        raise TypeError(f"unsupported report value {type(value).__name__}")

    path.write_text(json.dumps(data, indent=2, allow_nan=False, default=primitive), encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def run_inputs():
    import anyfem.submodel_jobs
    import anygeometry.volume_cut
    import anymesher.structured
    import anysolver.results
    import submodel_a8_helpers
    import test_submodel_a8_regional
    import test_submodel_regional_integration

    paths = [Path(__file__), *(Path(module.__file__) for module in (
        anyfem.submodel_jobs, anygeometry.volume_cut, anymesher.structured, anysolver.results,
        submodel_a8_helpers, test_submodel_a8_regional, test_submodel_regional_integration))]
    return {"python": sys.executable, "python_version": sys.version,
            "files": {str(p.resolve()): digest(p) for p in paths}}


def inventory():
    return {
        "placements_in_diameters": PLACEMENTS,
        "recommended_case": RECOMMENDED,
        "physics": {"diameter_m": DIAMETER, "width_m": WIDTH, "standard_half_length_m": 1.,
                    "thickness_m": THICKNESS, "gross_remote_stress_Pa": NOMINAL_STRESS,
                    "axial_line_load_N_per_m": NOMINAL_STRESS * THICKNESS},
        "sensors_m": [[0., RADIUS, 0.], [0., -RADIUS, 0.]],
        "recovery": "At each sensor divide mean top/bottom nodally recovered global xx by the gross remote stress.",
        "reference": {"gross_kt": HOWLAND_KT, "citation": "Howland (1930), printed p. 74 Table XII, d/W = 0.2",
                      "doi": "10.1098/rsta.1930.0002",
                      "uncertainty": "The author allows second-decimal error. A continuum bound needs independent assessment."},
        "execution_unit": {"parent": "1 published mesh and 1 numbered-job solve",
                           "placement": "1 box cut, 1 child mesh, 1 transfer and solve, 1 publication",
                           "placement --mesh-only": "1 box cut and 1 mesh, no solves",
                           "summarize": "reads the nine case results, no solves"},
        "proposed_inputs_for_freeze": {
            "reference_parent_n": [8, 16, 32], "half_length_m": 1., "long_end_half_length_m": 1.5,
            "end_parent_n": 32, "nine_placement_parent_n": 16,
            "nine_child_circle_and_radial_divisions": 32, "nine_child_target_m": .00875,
            "recommended_child_discretization_levels_with_parent_16": [[8, .035], [16, .0175], [32, .00875]],
            "recommended_transfer_parent_n_with_child_32": [8, 16, 32],
            "parent_count": 4, "nine_children_count": 9,
            "additional_recommended_child_levels": 2, "additional_recommended_transfer_parents": 2,
            "unique_solves": 17, "separate_a8_comparison": "3 solves per invocation of compare_holed_strip",
            "preconditions": "Fixed inputs need reviewed preflight and oracle uncertainty before an independently coordinated formal run."
        },
        "development_evidence": "regional-shell/recovery-cyclic-01, recovery-a8-sequence and a4-development-*",
        "inputs": run_inputs(),
    }


def manifest(root):
    """Emit literal one-shot stage commands. The assessment coordinator owns their execution."""
    root = root.resolve()
    stages = []

    def stage(name, count, args, *dependencies):
        stages.append({"id": name, "solves": count, "requires": dependencies,
                       "argv": [sys.executable, str(Path(__file__).resolve()), *map(str, args)]})

    parents = [root / f"parent-{n}" for n in (8, 16, 32)]
    end = root / "parent-32-long"
    for n, path in zip((8, 16, 32, 32), [*parents, end]):
        stage(path.name, 1, ["parent", "--divisions", n, "--half-length", 1.5 if path == end else 1.,
                             "--job-timeout", 300, "--out", path])
    def placement_stage(case, parent_dir, n, target):
        size = .035 * 8 / n
        stage(str(target.relative_to(root)), 1,
              ["placement", "--parent", parent_dir, "--case", case, "--divisions", n, "--radial-divisions", n,
               "--child-size", size, "--partition", "--out", target], parent_dir.name)
    placements = root / "placements"
    for case in PLACEMENTS:
        placement_stage(case, parents[1], 32, placements / case)
    child_levels = [root / "recommended-child-8", root / "recommended-child-16", placements / RECOMMENDED]
    for n, target in zip((8, 16), child_levels[:2]):
        placement_stage(RECOMMENDED, parents[1], n, target)
    transfer_levels = [root / "recommended-source-8", placements / RECOMMENDED, root / "recommended-source-32"]
    for parent_dir, target in zip(parents[::2], transfer_levels[::2]):
        placement_stage(RECOMMENDED, parent_dir, 32, target)
    for n in (4, 8, 16):
        stage(f"a8-{n}", 3, ["comparator", "--parent-divisions", n, "--out", root / f"a8-{n}"])
    stage("summarize", 0, ["summarize", "--placements", placements, "--references",
                          *(p/"result.json" for p in parents), "--end-reference", end/"result.json",
                          "--child-levels", *(p/"result.json" for p in child_levels),
                          "--transfer-levels", *(p/"result.json" for p in transfer_levels),
                          "--out", root/"summary"], *(s["id"] for s in stages))
    return {"mode": "proposed, execution requires an independently frozen assessment", "root": str(root),
            "stages": stages, "solves": sum(s["solves"] for s in stages), "ceiling": 32,
            "stop_rule": "Adjudicate the reference and end uncertainty before child solves. Preserve every attempted refusal; no adaptive changes.",
            "inputs": inventory()}


def resolution(project, mesh):
    ring = [(edge, mesh.nodes_of_edge[edge]) for edge, item in project.geometry.edges.items()
            if type(item.curve) is Arc]
    sizes = [float(np.linalg.norm(np.asarray(mesh.nodes[b]) - mesh.nodes[a])) for _, ids in ring
             for a, b in zip(ids, ids[1:])]
    if len(ring) != 4 or not sizes:
        raise ValueError("all four retained hole arcs must be represented")
    rim = {n for _, ids in ring for n in ids}
    neighbours = defaultdict(set)
    cells = defaultdict(set)
    for nodes in mesh.quads.values():
        for a, b in zip(nodes, nodes[1:] + nodes[:1]):
            neighbours[a].add(b)
            neighbours[b].add(a)
            cells[frozenset((a, b))].update(nodes)
    rows = []
    for sign in (1, -1):
        sensor, = [n for n in rim if np.linalg.norm(np.asarray(mesh.nodes[n]) - (0., sign*RADIUS, 0.)) < 1.e-9]
        first, = neighbours[sensor] - rim
        row = [sensor, first]
        # A composite outer side can shift a mapped spoke away from the sensor's x=0 line.
        # At an interior Q4 vertex, follow the only edge outside the two preceding cells.
        while True:
            previous, current = row[-2:]
            onward = neighbours[current] - cells[frozenset((previous, current))]
            if not onward:
                break
            node, = onward
            if node in row:
                raise ValueError("cyclic radial grid line at hole sensor")
            row.append(node)
        rows.append(row)
    quality = verify_mesh_quality(mesh)
    return {"nodes": len(mesh.nodes), "shells": len(mesh.shells), "arc_divisions": {e: len(ids)-1 for e, ids in ring},
            "hole_chord_range_m": [min(sizes), max(sizes)],
            "radial_rows": [len(row)-1 for row in rows],
            "first_radial_nodes_m": [list(mesh.nodes[row[1]]) for row in rows],
            "first_radial_steps_m": [float(np.linalg.norm(np.asarray(mesh.nodes[row[1]])-mesh.nodes[row[0]])) for row in rows],
            "max_aspect_ratio": quality.max_aspect_ratio, "min_scaled_jacobian": quality.min_scaled_jacobian}


def parent(args, report):
    project, arcs, spokes = holed_strip(half_length=args.half_length)
    analysis = project.add_analysis(AnalysisDefinition(name="Membrane tension", target_id="default",
                                                       settings={"load_case":"default"}))
    path = args.out / "parent.anyfem"
    workflow = project.mesh_session(path)
    try:
        report["stage"] = "parent_mesh"
        mesh = workflow.generate_mesh(mesh_settings(arcs, spokes, args.divisions, size=WIDTH/args.divisions))
        workflow.save()
        report["mesh"] = resolution(project, mesh)
        report["stage"] = "parent_solve"
        store = ArtifactStore(path)
        job = solve_parent(workflow, analysis, store, timeout=args.job_timeout)
        snap = workflow.session.snapshot()
        source = job_source(snap.thaw(), SourceReference(job.id, "default"), store, document=snap.document,
                            model_hash=snap.revision.model_hash)
        report["kt"] = stress_factor(source.solution)
        report["dofs"] = len(source.solution.displacements)
        workflow.save()
        report.update(parent_id=project.document_id, job_id=job.id, mesh_id=workflow.mesh_record_id,
                      mesh_hash=job.mesh_hash, model_hash=job.model_hash, project_sha256=digest(path),
                      divisions=args.divisions, half_length_m=args.half_length, status="measured")
    finally:
        workflow.close()


def partition_sectors(definition):
    """Separate each rectangular extension from its circular sector through the two existing spoke ends."""
    child = definition.child
    geometry = child.geometry
    original_arcs = {e for e, edge in geometry.edges.items() if type(edge.curve) is Arc}
    original_cut = {side: tuple(edges) for side, edges in definition.cut_faces.items()}
    replacements = {}
    for face_id, face in list(geometry.faces.items()):
        if len(face.loop) != 6:
            continue
        k, = [i for i, u in enumerate(face.loop) if u.edge in original_arcs]
        arc = geometry.edges[face.loop[k].edge]
        ends = []
        for i in (k - 1, k + 1):
            spoke = geometry.edges[face.loop[i % 6].edge]
            outer, = {spoke.start, spoke.end} - {arc.start, arc.end}
            ends.append(outer)
        sources = definition.source_faces.pop(face_id)
        section = child.face_sections[face_id]
        divider, pair = split_face_between(geometry, face_id, *ends)
        if any(len(geometry.faces[f].loop) != 4 for f in pair):
            raise ValueError("partition must make one four-edge circular sector and one rectangle")
        for f in pair:
            definition.source_faces[f] = sources
        replacements[face_id] = {"faces": pair, "source_faces": sources, "divider": divider, "section": section}
    # Face assignments resolve through the owner's replacement history. Retarget the membrane supports explicitly.
    old_supports, child.supports = list(child.supports), []
    for item in old_supports:
        if item.ref.kind == "face" and item.ref.id in replacements:
            for f in replacements[item.ref.id]["faces"]:
                child.add_support(replace(item, ref=child.face(f), region=None, id=str(uuid4())))
        else:
            child.add_support(item)
    child.resolve_section_assignments(strict=True)
    if any(child.face_sections.get(f) != replacement["section"] for replacement in replacements.values()
           for f in replacement["faces"]):
        raise ValueError("sector partition lost a source section")
    if set(definition.source_faces) != set(geometry.faces) or set(child.face_sections) != set(geometry.faces):
        raise ValueError("sector partition lost its material or source face")
    membrane = {"uz": 0., "rx": 0., "ry": 0.}
    if (len(child.supports) != len(geometry.faces)
            or {item.ref.id for item in child.supports} != set(geometry.faces)
            or any(item.ref.kind != "face" or item.region != child.singleton_region(item.ref) or
                   dict(item.constraints) != membrane for item in child.supports)):
        raise ValueError("each partitioned face must retain exactly its original membrane support")
    uses = [u.edge for face in geometry.faces.values() for u in face.loop]
    if (set(original_arcs) != {e for e, edge in geometry.edges.items() if type(edge.curve) is Arc}
            or any(uses.count(e) != 1 for edges in original_cut.values() for e in edges)
            or any(uses.count(r["divider"]) != 2 for r in replacements.values())):
        raise ValueError("sector partition changed the hole or the artificial boundary")
    definition.validate()
    return replacements


def placement(args, report):
    from anyfem.io.project_file import save_project
    from anyfem.document import DocumentSession

    original = args.parent / "parent.anyfem"
    provenance = read(args.parent / "result.json")
    if provenance["status"] != "measured" or digest(original) != provenance["project_sha256"]:
        raise ValueError("the saved parent does not match its measured input")
    # Each case has an independent project file/artifact store; the parent is immutable.
    project = load_project(original)
    path = args.out / "case.anyfem"
    store, parent_store = ArtifactStore(path), ArtifactStore(original)
    for artifact in project.artifacts.values():
        store.copy_from(parent_store, artifact)
    save_project(project, path)
    session = DocumentSession(project, path=path)
    x, y = PLACEMENTS[args.case]
    half = (x*DIAMETER, y*DIAMETER, .05)
    box = BoxVolume(tuple(-v for v in half), half)
    report.update(case=args.case, half_spans_m=half[:2], parent_project_sha256=provenance["project_sha256"],
                  parent_mesh_hash=provenance["mesh_hash"], parent_job=provenance["job_id"],
                  parent_divisions=provenance["divisions"], half_length_m=provenance["half_length_m"],
                  requested_arc_divisions=args.divisions, requested_radial_divisions=args.radial_divisions,
                  requested_target_m=args.child_size)
    report["stage"] = "box_cut"
    cut = cut_submodel(session.snapshot(), box, source=SourceReference(provenance["job_id"], "default"),
                       policy="drive_all", target_size=args.child_size)
    child = cut.definition.child
    report["clipped_face_edge_counts"] = {f:len(face.loop) for f,face in child.geometry.faces.items()}
    report["partitioned"] = args.partition
    if args.partition:
        report["stage"] = "partition_clipped_sectors"
        report["replacements"] = partition_sectors(cut.definition)
    report["stage"] = "mapped_sides"
    configure_cut_mesh(child, divisions=args.divisions, size=args.child_size, radial_divisions=args.radial_divisions,
                       core_half=min(half[:2]) if args.partition else None)
    with session.transaction("Register A4 cut", solver_affecting=False):
        add_submodel(project, cut.definition)
    save_project(project, path)
    if args.mesh_only:
        report["stage"] = "child_mesh"
        mesh = child.generate_mesh(args.child_size)
        report.update(status="mesh_only", mesh=resolution(child, mesh))
        return
    report["stage"] = "child_mesh_transfer_solve"
    staged = stage_submodel_run(submodel_snapshot(session), cut.definition.id, store)
    try:
        solution = staged.result.solution
        report["stage"] = "physical_probe"
        report["kt"] = stress_factor(solution)
        report["mesh"] = resolution(child, solution.built.mesh)
        report["dofs"] = len(solution.displacements)
        report["diagnostics"] = {k:staged.result.diagnostics[k] for k in
                                 ("source_eta_scaled", "source_kappa_scaled", "trace_residual_ratio", "free_body")}
        publish_submodel_run(session, staged)
        save_project(project, path)
        report.update(status="measured", run_id=staged.run.id)
    finally:
        if not staged.published:
            discard_staged_run(staged)


def refinement_estimate(records, key):
    n = [r[key] for r in records]
    if (len(n) != 3 or any(r["status"] != "measured" for r in records)
            or n[1] != 2*n[0] or n[2] != 2*n[1]):
        raise ValueError("order requires three measured, doubled resolution levels")
    orders, estimates = [], []
    for a, b, c in zip(*(r["kt"] for r in records)):
        ratio = (a-b)/(b-c) if b != c else 0.
        orders.append(math.log2(ratio) if ratio > 1. else None)
        estimates.append(abs(c-b)/(ratio-1) if ratio > 1. else None)
    return {"levels": n, "kt_by_level": [r["kt"] for r in records], "observed_orders_by_sensor": orders,
            "estimated_finest_error_kt_by_sensor": estimates,
            "interpretation": "Single-term Richardson estimate conditional on monotone asymptotic convergence."}


def summarize(args):
    result = {case:read(args.placements/case/"result.json") for case in PLACEMENTS}
    if any(r.get("case") != case for case,r in result.items()):
        raise ValueError("placement reports do not match the nine required case IDs")
    if len({(v.get("parent_project_sha256"),v.get("requested_arc_divisions"),v.get("requested_radial_divisions"),
             v.get("requested_target_m"), v.get("partitioned"))
            for v in result.values()}) != 1:
        raise ValueError("placements do not share one frozen parent and child discretization")
    output = {"recommended_case":RECOMMENDED, "reference_gross_kt":HOWLAND_KT, "placements":result,
              "measured_placements": sum(v["status"] == "measured" for v in result.values()),
              "refused_placements": [k for k,v in result.items() if v["status"] == "refused"],
              "relative_error_by_sensor":{k:[q/HOWLAND_KT-1 for q in v["kt"]] for k,v in result.items()
                                          if v["status"]=="measured"}}
    levels = sorted([read(p) for p in args.references], key=lambda v:v["divisions"])
    if levels:
        if len({r["half_length_m"] for r in levels}) != 1:
            raise ValueError("reference parents must have the same half length")
        output["reference_discretization"] = refinement_estimate(levels, "divisions")
        output["reference_levels"] = levels
        output["difference_from_finest_parent_kt_by_sensor"] = {
            k: [q-ref for q,ref in zip(v["kt"], levels[-1]["kt"])] for k,v in result.items()
            if v["status"] == "measured"}
    if args.end_reference:
        end=read(args.end_reference)
        if not levels or end["status"]!="measured" or end["divisions"]!=levels[-1]["divisions"] or end["half_length_m"]<=levels[-1]["half_length_m"]:
            raise ValueError("end comparison needs the same finest resolution and a longer strip")
        output["end_length_comparison"]={"half_length_m":end["half_length_m"],
            "absolute_kt_difference_by_sensor":[abs(a-b) for a,b in zip(end["kt"],levels[-1]["kt"])],
            "interpretation": "Observed sensitivity to moving the end, subject to the reference discretization error."}
    for paths, key, label in ((args.child_levels, "requested_arc_divisions", "child_discretization"),
                              (args.transfer_levels, "parent_divisions", "coarse_source_transfer")):
        if not paths:
            continue
        group = sorted([read(p) for p in paths], key=lambda r: r[key])
        if any(r.get("case") != RECOMMENDED for r in group):
            raise ValueError("sensitivity terms require the frozen recommended rectangle")
        fixed = (("parent_mesh_hash", "parent_project_sha256") if label == "child_discretization" else
                 ("requested_arc_divisions", "requested_radial_divisions", "requested_target_m"))
        if len({tuple(r.get(k) for k in (*fixed, "half_length_m", "partitioned")) for r in group}) != 1:
            raise ValueError(f"mixed inputs in {label} comparison")
        output[label] = refinement_estimate(group, key)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="stage",required=True)
    commands.add_parser("inventory")
    man = commands.add_parser("manifest")
    man.add_argument("--root", type=Path, required=True, help="Fresh future stage directory")
    p=commands.add_parser("parent")
    p.add_argument("--half-length",type=float,default=1.)
    p.add_argument("--divisions",type=int,required=True)
    p.add_argument("--job-timeout",type=float,required=True)
    c=commands.add_parser("placement")
    c.add_argument("--case",choices=PLACEMENTS,required=True)
    c.add_argument("--parent",type=Path,required=True)
    c.add_argument("--divisions",type=int,required=True)
    c.add_argument("--radial-divisions",type=int,help="Fixed radial rows per clipped sector in all nine placements")
    c.add_argument("--child-size",type=float,required=True)
    c.add_argument("--mesh-only",action="store_true")
    c.add_argument("--partition", action="store_true", help="Split six-edge sectors at the existing outer spoke endpoints")
    a8 = commands.add_parser("comparator")
    a8.add_argument("--parent-divisions", type=int, required=True, choices=(4, 8, 16))
    s=commands.add_parser("summarize")
    s.add_argument("--placements",type=Path,required=True)
    s.add_argument("--references",type=Path,nargs="*",default=[])
    s.add_argument("--end-reference",type=Path)
    s.add_argument("--child-levels", type=Path, nargs=3)
    s.add_argument("--transfer-levels", type=Path, nargs=3)
    for cmd in (p,c,s,a8):
        cmd.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if args.stage=="inventory":
        print(json.dumps(inventory(), indent=2, allow_nan=False))
        return 0
    if args.stage=="manifest":
        print(json.dumps(manifest(args.root), indent=2, allow_nan=False))
        return 0
    args.out.mkdir(parents=True,exist_ok=False)
    report={"stage":args.stage,"status":"not_started","inputs":run_inputs()}
    start=perf_counter()
    try:
        if args.stage=="summarize":
            report.update(summarize(args),status="summarized")
        elif args.stage=="parent":
            if args.divisions < 2 or args.divisions%2 or args.job_timeout<=0:
                raise ValueError("parent divisions must be positive and even and timeout positive")
            parent(args,report)
        elif args.stage == "comparator":
            from test_submodel_a8_regional import compare_holed_strip
            def record(name, result):
                report[name] = json.loads(result)
            compare_holed_strip(args.out, record, coarse_divisions=args.parent_divisions)
            report["status"] = "measured"
        else:
            if (args.divisions < 2 or args.divisions % 2 or args.child_size <= 0
                    or (args.radial_divisions is not None and args.radial_divisions < 1)):
                raise ValueError("placements require even positive arc counts, positive radial counts and target size")
            placement(args,report)
    except Exception as error:
        refused = isinstance(error, (ProjectError, GeometryError, MeshError)) or (
            report["stage"] == "mapped_sides" and isinstance(error, ValueError))
        report.update(status="refused" if refused else "failed",error_type=type(error).__module__+"."+type(error).__name__,
                      code=getattr(error,"code",type(error).__name__),message=str(error))
        raise
    finally:
        report["wall_seconds"]=perf_counter()-start
        write(args.out/"result.json",report)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
