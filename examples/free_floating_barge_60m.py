"""Set up the 60 m drawing barge for free-floating irregular-wave preparation.

The example has three explicit levels; save and open never start BEM:

* default: an openable closed free-free project with ballast explicitly *pending*;
  no mesh is generated, and this is not the final ballasted project.
* ``--model``: mesh, exact ballast assignment for the 1 m target draft, verification
  on the saved project; no BEM.
* ``--prepare``: the same, then the production floating Prepare job (equilibrium in the
  configured isolated runtime, native BEM, all gates), offline revalidation and
  ``ApplyFloatingLoad``.

Any refusal is recorded as such; nothing is substituted and no solved claim is made.

Run with an environment containing the ANY owner packages::

    python examples/free_floating_barge_60m.py OUTPUT_DIR [--hs 1 --tp 6 ...]
    python examples/free_floating_barge_60m.py OUTPUT_DIR --model
    python examples/free_floating_barge_60m.py OUTPUT_DIR --prepare --allow-development \
        --equilibrium-python PY --owner-wheel WHEEL [--stability-source SRC]

See ``free_floating_barge_60m.md`` for the defaults, outputs and limitations.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from anygeometry.entities import OrientedEdge
from anygeometry.surfaces import Plane

from anyfem import commands
from anyfem.application.examples import drawing_barge_60m
from anyfem.application.irregular_wave_input import IrregularWaveInput
from anyfem.native_meshing import NativeMeshSettings

# The drawing hull (application.examples.drawing_barge_60m); its geometry is checked, not assumed.
HULL_LENGTH, HULL_BREADTH, HULL_DEPTH = 60., 5., 2.
HULL_VOLUME = HULL_LENGTH * HULL_BREADTH * HULL_DEPTH
HULL_PART, HULL_SECTION = "Barge midship section", "Hull 6 mm"
TOLERANCE = 1.e-9
PROJECT_NAME = "Drawing barge 60 m — free floating, irregular sea along +X"
TRANSOMS = ("Transom X = 0 m", "Transom X = 60 m")
WET_FACES = ("Bottom", "Port side", "Starboard side", *TRANSOMS)
# User-approved modelled ballast: the 1 m mean draft is a target; the owner solves the real pose.
WATER_DENSITY, TARGET_DRAFT = 1025., 1.
TARGET_DISPLACEMENT = WATER_DENSITY * HULL_LENGTH * HULL_BREADTH * TARGET_DRAFT
MASS_TOLERANCE = 1.e-9
BALLAST_NAME = "Example ballast (1 m target draft)"
# Use the drawing generator's settings; the two coarser linear candidates refused admission.
MESH_SIZE, ELEMENT_ORDER = .5, "quadratic"
FILES = {"base_project": "free_floating_barge_60m_unballasted.anyfem",
         "model_project": "free_floating_barge_60m_ballasted.anyfem",
         "manifest": "model_manifest.json", "request": "wave_request.json", "status": "status.json"}
PREPARE_FILES = {"loaded_project": "free_floating_barge_60m_floating.anyfem",
                 "record": "free_floating_barge_60m_floating_record.npz"}
OUTPUTS = (*FILES.values(), *PREPARE_FILES.values())
CACHE_DIRECTORY = "floating-cache"
SERIES_NAME = "Floating irregular sea"
# What ApplyFloatingLoad creates for the series; checked after Apply, never edited here.
FREE_FREE_BATCH = {"constraint_mode": "nullspace", "inertia_relief": True,
                   "allow_unbalanced_free_free": False}
# User values: Hs and Tp.  The rest are illustrative defaults, not environmental facts.
SEA_DEFAULTS = dict(significant_height=1., peak_period=6., gamma=3.3, seed=0, heading=0.,
                    duration=120., dt=.1, depth=100., component_count=32)
HEADING_CONVENTION = ("heading is the wave propagation direction: 0 degrees means waves travel towards "
                      "+X and reach the X = 0 m transom first; it is not a naval direction-from")
LIMITATIONS = (
    "The transoms at X = 0 m and X = 60 m are admitted model additions: unstiffened 6 mm closing plates "
    "on the existing hull corner edges. They are not tied to the centreline bulkhead end along its height.",
    "The ballast is a fixed attached mass shared equally over the bottom plate nodes (not per area); "
    "there is no sloshing. The 1 m draft is its target; only the owner equilibrium gives the real pose.",
    "Actual FE target size and element order are recorded in mesh_settings and the mesh manifest; "
    "all plates and stiffeners remain. The default is the drawing generator's 0.5 m quadratic mesh.",
    "The native BEM source is limited to 600 panels; a refused or insufficient source is reported, "
    "never coarsened silently.",
    "No irregular-frequency lid is qualified; responses near irregular frequencies are not qualified.",
    "Hs = 1 m and Tp = 6 s are user values; JONSWAP gamma, seed, heading, duration, time step, depth "
    "and component count are illustrative defaults.",
)


def locate_hull_faces(project):
    """Bottom, side and deck plates by exact geometry; anything ambiguous refuses.

    A face matches when all its corners lie on the plane and its corners span the full
    hull panel, so bulkheads, the centreline bulkhead and end-house walls never match.
    """
    geometry = project.geometry
    panels = {
        "Bottom": (2, 0., (0, HULL_LENGTH), (1, -HULL_BREADTH / 2., HULL_BREADTH / 2.)),
        "Port side": (1, -HULL_BREADTH / 2., (0, HULL_LENGTH), (2, 0., HULL_DEPTH)),
        "Starboard side": (1, HULL_BREADTH / 2., (0, HULL_LENGTH), (2, 0., HULL_DEPTH)),
        "Deck": (2, HULL_DEPTH, (0, HULL_LENGTH), (1, -HULL_BREADTH / 2., HULL_BREADTH / 2.)),
    }
    found = {name: [] for name in panels}
    for identifier in geometry.faces:
        corners = [geometry.vertex_position(v) for v in geometry.face_corner_vertices(identifier)]
        for name, (axis, value, (_, length), (other, low, high)) in panels.items():
            if not all(abs(c[axis] - value) <= TOLERANCE for c in corners):
                continue
            span = (min(c[0] for c in corners), max(c[0] for c in corners),
                    min(c[other] for c in corners), max(c[other] for c in corners))
            if all(abs(a - b) <= TOLERANCE for a, b in zip(span, (0., length, low, high))):
                found[name].append(identifier)
    if any(len(ids) != 1 for ids in found.values()):
        raise ValueError(f"the drawing hull plates are not unique: {found}")
    faces = {name: ids[0] for name, ids in found.items()}
    missing = [name for name, identifier in faces.items() if identifier not in project.face_sections]
    if missing:
        raise ValueError(f"hull faces without a plate section: {missing}")
    return faces


def _end_edge(geometry, face_id, x):
    """The one boundary edge of a hull plate lying in the end plane X = ``x``."""
    edges = []
    for item in geometry.faces[face_id].loop:
        edge = geometry.edges[item.edge]
        if all(abs(geometry.vertex_position(v)[0] - x) <= TOLERANCE for v in (edge.start, edge.end)):
            edges.append(item.edge)
    if len(edges) != 1:
        raise ValueError(f"hull face {face_id} has no unique end edge at X = {x:g} m")
    return edges[0]


def _closing_loop(geometry, edge_ids, start):
    """Oriented loop over existing edges, and its corners, walked from vertex ``start``."""
    loop, corners, at = [], [], start
    for identifier in edge_ids:
        edge = geometry.edges[identifier]
        if at not in (edge.start, edge.end):
            raise ValueError(f"hull end edge {identifier} does not continue the transom loop")
        corners.append(at)
        loop.append(OrientedEdge(identifier, edge.start == at))
        at = edge.end if edge.start == at else edge.start
    if at != start:
        raise ValueError("the hull end edges do not close a transom loop")
    return tuple(loop), corners


def add_closing_transoms(project, hull):
    """One plate per hull end on the four existing corner edges: no new vertex, edge or duplicate plating."""
    geometry = project.geometry
    if project.face_sections[hull["Bottom"]] != HULL_SECTION:
        raise ValueError(f"the bottom plate is not {HULL_SECTION!r}")
    parts = [identifier for identifier, part in geometry.parts.items() if part.name == HULL_PART]
    if len(parts) != 1:
        raise ValueError(f"expected one hull part {HULL_PART!r}, found {parts}")
    transoms = {}
    # Both loops wind so that the plate normal points out of the hull, like the other hull plates.
    for name, x, order in ((TRANSOMS[0], 0., ("Port side", "Deck", "Starboard side", "Bottom")),
                           (TRANSOMS[1], HULL_LENGTH, ("Bottom", "Starboard side", "Deck", "Port side"))):
        edges = [_end_edge(geometry, hull[plate], x) for plate in order]
        ends = [{geometry.edges[e].start, geometry.edges[e].end} for e in edges]
        start = ends[0] & ends[-1]
        if len(start) != 1:
            raise ValueError(f"the hull end edges at X = {x:g} m share no unique corner")
        loop, corners = _closing_loop(geometry, edges, start.pop())
        positions = [geometry.vertex_position(v) for v in corners]
        with geometry.transaction():
            transoms[name] = geometry.add_face_from_loop(loop, (0, 1, 2, 3), surface=Plane(
                positions[0], positions[1] - positions[0], positions[3] - positions[0]))
            geometry.add_sheet((transoms[name],), part_id=parts[0], name=name)
        project.assign_plate(transoms[name], HULL_SECTION)
    return transoms


def build_free_floating_project(mesh_size=MESH_SIZE, element_order=ELEMENT_ORDER):
    """The drawing barge closed by two transoms, with every demonstration constraint/load removed."""
    project = drawing_barge_60m()
    stack = commands.CommandStack(project)
    if project.combinations:
        raise ValueError("unexpected load combinations in the drawing barge")
    for item in (*project.supports, *project.connections):
        stack.run(commands.DeleteAttribute(item.id))
    for name in [name for name, case in project.load_cases.items() if case.parent is None]:
        stack.run(commands.DeleteLoadCase(name))
    for identifier in tuple(project.analyses):
        stack.run(commands.DeleteProjectRecord("analysis", identifier))
    if project.wet_surfaces:
        raise ValueError("unexpected wet surfaces in the drawing barge")
    faces = locate_hull_faces(project)
    faces.update(add_closing_transoms(project, faces))
    centre = (HULL_LENGTH / 2., 0., HULL_DEPTH / 2.)
    for name in WET_FACES:
        # The fluid is outside the closed hull volume around the centre; decided per element.
        stack.run(commands.AddWetSurface(project.face(faces[name]), name=f"{name} (wet exterior)",
                                         fluid_side="away_from_point", interior_point=centre))
    # Same backend and parameters as the drawing barge; only the size and the order differ.
    stored = project.native_mesh_settings.to_dict()
    project.set_native_mesh_settings(NativeMeshSettings.create(
        mesh_size, element_order=element_order, backend=stored["backend"], parameters=stored["parameters"]))
    project.target_size = float(mesh_size)
    project.name = PROJECT_NAME
    return project, faces


def closed_envelope(project, faces):
    """The geometry-owner closed displacement envelope; a refusal propagates."""
    from anyfem.application.floating_equilibrium_process import envelope_document
    document = envelope_document(project, [faces["Deck"]])
    if abs(document["volume"] - HULL_VOLUME) > TOLERANCE * HULL_VOLUME:
        raise ValueError(f"the hull envelope volume is {document['volume']} m3, not {HULL_VOLUME:g} m3")
    return dict(volume_m3=document["volume"], identity=document["identity"], face_ids=document["face_ids"])


def sea_input(**overrides):
    """Owner-validated irregular sea; invalid values raise ``ValueError``."""
    values = {**SEA_DEFAULTS, **overrides}
    unknown = set(values) - set(SEA_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown sea settings {sorted(unknown)}")
    if not all(math.isfinite(float(values[k])) for k in values if k not in ("seed", "component_count")):
        raise ValueError("sea settings must be finite")
    if values["dt"] > values["duration"]:
        raise ValueError("the time step must not exceed the duration")
    return IrregularWaveInput(name="Irregular sea along +X", wave_type="irregular",
                              include_hydrostatic=False, follow_surface=False, **values)


def authoritative_mass(project, mesh):
    """Mass, CG and inertia of the assembled FE model: the same public owner call Prepare uses."""
    from anyfem.application.floating_mass import dry_mass_properties
    from anyfem.solve.build import build_fe_model
    built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
    return dry_mass_properties(built.fe_model)


def add_ballast(project, faces, properties, target=TARGET_DISPLACEMENT):
    """Add the mass missing to ``target`` on the bottom plate; ``properties()`` is rebuilt to verify it.

    Material density and mass matrices are never rescaled: the ballast is one ordinary positive
    ``total_distributed`` Mass of the project.
    """
    structure = properties()
    ballast = float(target) - float(structure.mass)
    if not math.isfinite(ballast) or ballast <= 0.:
        raise ValueError(f"the assembled mass {structure.mass} kg leaves no positive ballast for "
                         f"the {target:g} kg target")
    commands.CommandStack(project).run(commands.AddMass(
        project.face(faces["Bottom"]), value=ballast, name=BALLAST_NAME,
        distribution_policy="total_distributed"))
    loaded = properties()
    if abs(float(loaded.mass) - target) > MASS_TOLERANCE * target:
        raise ValueError(f"the rebuilt mass {loaded.mass} kg misses the {target:g} kg target")
    return dict(state="assigned", name=BALLAST_NAME, face=faces["Bottom"], distribution="total_distributed",
                target_draft_m=TARGET_DRAFT, water_density_kg_m3=WATER_DENSITY, target_displacement_kg=target,
                ballast_mass_kg=ballast, structural_mass_kg=float(structure.mass),
                structural_cg_m=[float(x) for x in structure.cg], total_mass_kg=float(loaded.mass),
                cg_m=[float(x) for x in loaded.cg],
                inertia_about_cg_kg_m2=np.asarray(loaded.inertia_cg, dtype=float).tolist())


def mesh_identity(mesh):
    from anyfem.document import canonical_hash
    from anymesher.serialize import mesh_to_dict
    return canonical_hash(mesh_to_dict(mesh))


def mesh_summary(mesh, requested_order):
    """What the mesher really produced; a different element order than requested refuses."""
    sizes = {len(nodes) for nodes in (*mesh.quads.values(), *mesh.tris.values())}
    effective = "linear" if sizes <= {3, 4} else "quadratic" if sizes <= {6, 8} else "mixed"
    if not sizes or effective != requested_order:
        raise ValueError(f"requested {requested_order} shells, the mesh has {effective} node counts {sorted(sizes)}")
    return dict(effective_element_order=effective, nodes=len(mesh.nodes), quads=len(mesh.quads),
                tris=len(mesh.tris), beams=len(mesh.beams), identity=mesh_identity(mesh))


def floating_options(faces, *, panel_size=0., max_panels=600, equilibrium_python="",
                     owner_wheel="", stability_source="", allow_development=False,
                     allow_native_solve=False, equilibrium_timeout_s=600.):
    """FloatingSettings keywords: the deck closes the envelope but is not wet."""
    return dict(envelope_extra_face_ids=[faces["Deck"]], panel_size=float(panel_size),
                max_panels=int(max_panels), equilibrium_python=str(equilibrium_python),
                owner_wheel=str(owner_wheel), stability_source=str(stability_source),
                allow_development=bool(allow_development), allow_native_solve=bool(allow_native_solve),
                equilibrium_timeout_s=float(equilibrium_timeout_s))


def wave_request(project, faces, wave, floating, *, project_file=FILES["base_project"], ballast=None):
    """The exact, reproducible request (no runtime paths); realization identity via the owner."""
    from anyfem.application.floating_job import floating_settings_from_wave
    shareable = {k: v for k, v in floating.items()
                 if k in ("envelope_extra_face_ids", "panel_size", "max_panels")}
    settings = floating_settings_from_wave(asdict(wave), **{**shareable, "envelope_extra_face_ids":
                                                            tuple(shareable["envelope_extra_face_ids"])})
    return {
        "kind": "anyfem_free_floating_wave_request_v2",
        "project": project_file,
        "wave": asdict(wave),
        "floating": shareable,
        "user_values": ["significant_height", "peak_period"],
        "illustrative_defaults": sorted(k for k in SEA_DEFAULTS
                                        if k not in ("significant_height", "peak_period")),
        "heading_convention": HEADING_CONVENTION,
        "stationary_fe_mesh": True,
        "realization_digest": settings.realization_digest(),
        "components": [asdict(c) for c in settings.components],
        "sample_count": len(settings.times),
        "wet_faces": {name: faces[name] for name in WET_FACES},
        "closing_faces": {"Deck": faces["Deck"]},
        "wet_fluid_side": "away_from_point",
        "interior_point": [HULL_LENGTH / 2., 0., HULL_DEPTH / 2.],
        "envelope": closed_envelope(project, faces),
        "ballast": ballast or dict(state="pending", target_draft_m=TARGET_DRAFT,
                                   water_density_kg_m3=WATER_DENSITY, target_displacement_kg=TARGET_DISPLACEMENT),
        "mesh_settings": project.native_mesh_settings.to_dict(),
        "free_free_batch": FREE_FREE_BATCH,
        "limitations": list(LIMITATIONS),
    }


def _json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _write_status(path, status):
    """Atomic state change of this run's own status."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_json_text(status), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _refuse_existing(directory, overwrite, names=OUTPUTS):
    existing = [str(directory / name) for name in names if (directory / name).exists()]
    if existing and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing files {existing}; "
                              "choose a new output directory or pass --overwrite")


def _supersede(directory):
    """``--overwrite`` moves earlier outputs aside: a prior refusal stays available as evidence.

    The status goes first, so a failed move never leaves it describing outputs that have left.
    """
    existing = [directory / name for name in OUTPUTS if (directory / name).exists()]
    if existing:
        target = next(directory / f"superseded-{index:03d}" for index in itertools.count(1)
                      if not (directory / f"superseded-{index:03d}").exists())
        target.mkdir()
        for path in sorted(existing, key=lambda path: path.name != FILES["status"]):
            os.replace(path, target / path.name)


def _exclusive_replace(source, destination):
    """POSIX ``rename`` replaces silently, so the absent name is claimed exclusively first."""
    with open(destination, "xb"):
        pass
    try:
        os.replace(source, destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


# Windows ``rename`` is itself an atomic no-clobber move and refuses an existing destination.
_move_no_clobber = os.rename if os.name == "nt" else _exclusive_replace


def _publish(directory, write, *, overwrite=False):
    """Stage everything, then move the files in order (status last) without replacing anything.

    The status is the commit marker.  A failed move removes the files this call placed, so
    no partial set stays behind; if that also fails, the error names what is left.
    """
    directory.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".example-publication-", dir=directory))
    try:
        names = write(staging)
        if overwrite:
            _supersede(directory)
        _refuse_existing(directory, False, names)
        published = []
        try:
            for name in names:
                _move_no_clobber(staging / name, directory / name)
                published.append(name)
        except BaseException as error:
            remaining = []
            for name in published:
                try:
                    (directory / name).unlink(missing_ok=True)
                except OSError:
                    remaining.append(name)
            if remaining:
                raise OSError(f"publication failed ({error}) and left incomplete outputs {remaining}") from error
            raise
    finally:
        shutil.rmtree(staging)


def write_base(output_directory, *, overwrite=False, wave=None, panel_size=0.,
               mesh_size=MESH_SIZE, element_order=ELEMENT_ORDER):
    """Write the closed project with ballast pending, the wave request and a status; nothing is meshed."""
    from anyfem.io.project_file import save_project
    directory = Path(output_directory).resolve()
    _refuse_existing(directory, overwrite)
    project, faces = build_free_floating_project(mesh_size, element_order)
    wave = wave or sea_input()
    request = wave_request(project, faces, wave, floating_options(faces, panel_size=panel_size))
    status = dict(state="pending_ballast", ballast_assigned=False, floating_load_applied=False, solved=False,
                  project=FILES["base_project"], realization_digest=request["realization_digest"],
                  message="This is not the ballasted project: the ballast is pending and no floating load is "
                          "applied. Run with --model or --prepare.",
                  limitations=list(LIMITATIONS))

    def write(staging):
        save_project(project, staging / FILES["base_project"])
        (staging / FILES["request"]).write_text(_json_text(request), encoding="utf-8", newline="\n")
        (staging / FILES["status"]).write_text(_json_text(status), encoding="utf-8", newline="\n")
        return FILES["base_project"], FILES["request"], FILES["status"]

    _publish(directory, write, overwrite=overwrite)
    return project, faces, request, {key: directory / FILES[key] for key in ("base_project", "request", "status")}


def _deadline_check(deadline, deadline_s):
    def check(_where=""):
        if time.monotonic() > deadline:
            raise TimeoutError(f"the {deadline_s:g} s deadline has passed")
    return check


def write_model(output_directory, *, overwrite=False, wave=None, panel_size=0., mesh_size=MESH_SIZE,
                element_order=ELEMENT_ORDER, deadline_s=600., deadline=None, properties=authoritative_mass,
                mesher=None):
    """Mesh, assign the exact ballast and publish the verified project; never starts BEM.

    Returns a dict with ``status``; ``project``, ``faces``, ``mesh``, ``request`` and ``manifest``
    are present only when ``status["state"]`` is ``model_ready``.
    """
    from anyfem.io.project_file import load_project, save_project
    directory = Path(output_directory).resolve()
    _refuse_existing(directory, overwrite)
    wave = wave or sea_input()
    deadline = time.monotonic() + float(deadline_s) if deadline is None else deadline
    mesher = mesher or (lambda project, check: project.generate_mesh(cancellation_check=check))
    stage = "geometry"
    try:
        project, faces = build_free_floating_project(mesh_size, element_order)
        envelope = closed_envelope(project, faces)
        stage = "mesh"
        mesh = mesher(project, _deadline_check(deadline, deadline_s))
        summary = mesh_summary(mesh, element_order)
        stage = "ballast"
        ballast = add_ballast(project, faces, lambda: properties(project, mesh))
        request = wave_request(project, faces, wave, floating_options(faces, panel_size=panel_size),
                               project_file=FILES["model_project"], ballast=ballast)
        manifest = dict(kind="anyfem_free_floating_model_v1", project=FILES["model_project"], envelope=envelope,
                        ballast=ballast, mesh=summary, mesh_settings=project.native_mesh_settings.to_dict(),
                        realization_digest=request["realization_digest"], limitations=list(LIMITATIONS))
        status = dict(state="model_ready", ballast_assigned=True, floating_load_applied=False, solved=False,
                      project=FILES["model_project"], manifest=FILES["manifest"],
                      realization_digest=request["realization_digest"],
                      message="Ballasted model saved; no floating preparation has run and the 1 m draft is "
                              "only a target until the owner equilibrium is solved.",
                      limitations=list(LIMITATIONS))

        def write(staging):
            nonlocal stage
            stage = "publish"
            target = staging / FILES["model_project"]
            save_project(project, target)
            reopened = properties(load_project(target), mesh)
            if abs(float(reopened.mass) - TARGET_DISPLACEMENT) > MASS_TOLERANCE * TARGET_DISPLACEMENT:
                raise ValueError(f"the saved project reopens with mass {reopened.mass} kg")
            manifest["reopened_total_mass_kg"] = float(reopened.mass)
            for key, value in (("manifest", manifest), ("request", request), ("status", status)):
                (staging / FILES[key]).write_text(_json_text(value), encoding="utf-8", newline="\n")
            return FILES["model_project"], FILES["manifest"], FILES["request"], FILES["status"]

        _publish(directory, write, overwrite=overwrite)
    except Exception as error:
        status = dict(state="model_refused", stage=stage, error=f"{type(error).__name__}: {error}",
                      ballast_assigned=False, floating_load_applied=False, solved=False,
                      message="The ballasted model was not published; any output named in the error is "
                              "incomplete and nothing was substituted.",
                      limitations=list(LIMITATIONS))

        def write_refusal(staging):
            (staging / FILES["status"]).write_text(_json_text(status), encoding="utf-8", newline="\n")
            return FILES["status"],

        _publish(directory, write_refusal, overwrite=overwrite)
        return dict(status=status)
    return dict(status=status, project=project, faces=faces, mesh=mesh, request=request, manifest=manifest)


def _runtime_problems(runtime):
    """The production equilibrium runtime requirements, reported before an expensive mesh."""
    problems = []
    if not runtime["equilibrium_python"] or not Path(runtime["equilibrium_python"]).is_file():
        problems.append("no valid isolated equilibrium Python (--equilibrium-python)")
    if not runtime["owner_wheel"] or not Path(runtime["owner_wheel"]).is_file():
        problems.append("no valid pinned ANYgeometry owner wheel (--owner-wheel)")
    if runtime["allow_development"] is not True:
        problems.append("engineering/development equilibrium needs --allow-development")
    return problems


def _run_worker(function, kwargs, deadline, label):
    """Contained worker process with a hard deadline (tree terminated on expiry)."""
    from anyfem.job_process import run_in_worker_process
    from anysolver import CancellationToken
    token, trees = CancellationToken(), []

    def expire():
        token.cancel()
        for tree in trees:
            tree.terminate()

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(f"{label}: deadline already passed")
    timer = threading.Timer(remaining, expire)
    timer.start()
    try:
        return run_in_worker_process(function=function, kwargs=kwargs, snapshot=None, project_override={},
                                     progress=lambda value: print(f"{label}: {value}", flush=True),
                                     token=token, containment=True,
                                     on_tree=lambda process, tree: trees.append(tree))
    finally:
        timer.cancel()


def mean_pose_report(pose):
    """Actual heel, trim and keel draft from an owner-accepted pose (world = Q body + origin)."""
    rotation = np.asarray(pose["rotation_body_to_world"], dtype=float)
    keel = {"X0": (0., 0., 0.), "X30": (HULL_LENGTH / 2., 0., 0.), "X60": (HULL_LENGTH, 0., 0.)}
    drafts = {name: -float(rotation[2] @ np.array(point) + pose["origin_z_m"]) for name, point in keel.items()}
    return dict(keel_draft_m=drafts, target_draft_m=TARGET_DRAFT,
                roll_about_x_deg=math.degrees(math.atan2(rotation[2, 1], rotation[2, 2])),
                pitch_about_y_deg=math.degrees(math.asin(max(-1., min(1., -rotation[2, 0])))))


def prepare(output_directory, *, overwrite=False, wave=None, panel_size=0., mesh_size=MESH_SIZE,
            element_order=ELEMENT_ORDER, runtime=None, deadline_s=600.):
    """Explicit production route; returns the final status (``floating_load_applied`` or a refusal).

    ``runtime`` holds ``equilibrium_python``, ``owner_wheel``, ``stability_source`` and
    ``allow_development``; they are passed unchanged to the production floating settings.
    A runtime problem refuses before any mesh.  A later refusal keeps the ballasted model.
    """
    from anyfem.application.floating_apply import ApplyFloatingLoad, floating_history_of
    from anyfem.application.floating_job import (owned_publication_workspace, prepare_floating_job,
                                                 reopen_floating_job)
    from anyfem.document import canonical_hash
    from anyfem.io.project_file import project_from_dict, project_to_dict, save_project
    from anymesher.serialize import mesh_to_dict
    directory = Path(output_directory).resolve()
    _refuse_existing(directory, overwrite)
    runtime = {**dict(equilibrium_python="", owner_wheel="", stability_source="", allow_development=False),
               **(runtime or {})}
    status_path = directory / FILES["status"]
    problems = _runtime_problems(runtime)
    if problems:
        status = dict(state="prepare_refused", stage="runtime", ballast_assigned=False,
                      floating_load_applied=False, solved=False,
                      error="floating preparation cannot start: " + "; ".join(problems),
                      message="Refused before any mesh; use --model for the ballasted project without BEM.",
                      limitations=list(LIMITATIONS))

        def write_refusal(staging):
            (staging / FILES["status"]).write_text(_json_text(status), encoding="utf-8", newline="\n")
            return FILES["status"],

        _publish(directory, write_refusal, overwrite=overwrite)
        return status
    deadline = time.monotonic() + float(deadline_s)
    wave = wave or sea_input()
    model = write_model(directory, overwrite=overwrite, wave=wave, panel_size=panel_size, mesh_size=mesh_size,
                        element_order=element_order, deadline_s=deadline_s, deadline=deadline)
    status = dict(model["status"])
    if status["state"] != "model_ready":
        return status
    project, faces, mesh = model["project"], model["faces"], model["mesh"]
    floating = floating_options(faces, panel_size=panel_size, allow_native_solve=True, **runtime)
    record = directory / PREPARE_FILES["record"]
    status.update(state="prepare_refused", realization_digest=model["request"]["realization_digest"])
    stage = "prepare"
    try:
        document = project_to_dict(project)
        settings = dict(wave=asdict(wave), floating=floating)
        kwargs = dict(document=document, mesh_data=mesh_to_dict(mesh), settings=settings,
                      cache_directory=str(directory / CACHE_DIRECTORY), record_path=str(record))
        with owned_publication_workspace(record) as workspace:
            summary = _run_worker(prepare_floating_job, {**kwargs, "publication_directory": str(workspace)},
                                  deadline, "prepare")
        status["mean_pose"] = mean_pose_report(summary["pose"])
        stage = "reopen"
        reopen = {k: v for k, v in kwargs.items() if k != "cache_directory"}
        with owned_publication_workspace(record) as workspace:
            again = _run_worker(reopen_floating_job, {**reopen, "publication_directory": str(workspace)},
                                deadline, "reopen")
        if again["array_sha256"] != summary["array_sha256"]:
            raise RuntimeError("the floating record changed during offline revalidation")
        stage = "apply"
        times = list(summary["preview_times"])
        parent = commands.CommandStack(project).run(ApplyFloatingLoad(dict(
            name=SERIES_NAME, record_path=str(record), array_sha256=summary["array_sha256"],
            selected_times=times, window=[times[0], times[-1]],
            input_document_hash=canonical_hash(document), replace=False)))
        analysis = next(a for a in project.analyses.values() if a.target_id == parent.name)
        if any(analysis.settings.get(k) != v for k, v in FREE_FREE_BATCH.items()):
            raise RuntimeError(f"unexpected free-free batch settings {analysis.settings}")
        stage = "save"
        reopened = project_from_dict(json.loads(json.dumps(project_to_dict(project))))
        if floating_history_of(reopened.load_cases[SERIES_NAME]) is None:
            raise RuntimeError("the saved project does not retain the floating payload")

        def write(staging):
            save_project(project, staging / PREPARE_FILES["loaded_project"])
            return PREPARE_FILES["loaded_project"],

        _publish(directory, write)
        status.update(state="floating_load_applied", floating_load_applied=True, analysis=analysis.name,
                      series=SERIES_NAME, steps=len(times), record=PREPARE_FILES["record"],
                      project=PREPARE_FILES["loaded_project"], summary=summary,
                      message="Floating load applied and saved; the batch has NOT been solved.")
    except BaseException as error:
        # A record published before a later refusal is an unapplied production record, never a load.
        status.update(stage=stage, error=f"{type(error).__name__}: {error}", floating_load_applied=False,
                      unapplied_record_present=record.exists(),
                      message="Floating preparation refused; the ballasted model is kept and no floating "
                              "load, synthetic field or result was produced.")
        if not isinstance(error, Exception):
            _write_status(status_path, status)
            raise
    _write_status(status_path, status)
    return status


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("output_directory")
    parser.add_argument("--hs", type=float, default=SEA_DEFAULTS["significant_height"], help="Hs [m]")
    parser.add_argument("--tp", type=float, default=SEA_DEFAULTS["peak_period"], help="Tp [s]")
    parser.add_argument("--gamma", type=float, default=SEA_DEFAULTS["gamma"])
    parser.add_argument("--seed", type=int, default=SEA_DEFAULTS["seed"])
    parser.add_argument("--heading", type=float, default=SEA_DEFAULTS["heading"],
                        help="wave propagation direction [degrees]; 0 = towards +X")
    parser.add_argument("--duration", type=float, default=SEA_DEFAULTS["duration"], help="[s]")
    parser.add_argument("--dt", type=float, default=SEA_DEFAULTS["dt"], help="[s]")
    parser.add_argument("--depth", type=float, default=SEA_DEFAULTS["depth"], help="[m]")
    parser.add_argument("--components", type=int, default=SEA_DEFAULTS["component_count"])
    parser.add_argument("--panel-size", type=float, default=0., help="BEM source size; 0 = automatic")
    parser.add_argument("--mesh-size", type=float, default=MESH_SIZE, help="illustrative FE target size [m]")
    parser.add_argument("--element-order", choices=("linear", "quadratic"), default=ELEMENT_ORDER)
    parser.add_argument("--overwrite", action="store_true",
                        help="move existing outputs to a superseded-NNN folder and write new ones")
    level = parser.add_mutually_exclusive_group()
    level.add_argument("--model", action="store_true", help="mesh, assign the exact ballast and save; no BEM")
    level.add_argument("--prepare", action="store_true",
                       help="--model, then the production floating Prepare (native BEM) and Apply")
    parser.add_argument("--equilibrium-python", default=os.environ.get("ANYFEM_FLOATING_EQUILIBRIUM_PYTHON", ""))
    parser.add_argument("--owner-wheel", default=os.environ.get("ANYFEM_FLOATING_OWNER_WHEEL", ""))
    parser.add_argument("--stability-source", default=os.environ.get("ANYFEM_FLOATING_STABILITY_SOURCE", ""))
    parser.add_argument("--allow-development", action="store_true",
                        help="explicit opt-in to the engineering/development equilibrium")
    parser.add_argument("--deadline", type=float, default=600., help="mesh + prepare wall-time limit [s]")
    return parser.parse_args(argv)


def sea_from_arguments(arguments):
    return sea_input(significant_height=arguments.hs, peak_period=arguments.tp, gamma=arguments.gamma,
                     seed=arguments.seed, heading=arguments.heading, duration=arguments.duration,
                     dt=arguments.dt, depth=arguments.depth, component_count=arguments.components)


def main(argv=None):
    arguments = parse_arguments(argv)
    try:
        wave = sea_from_arguments(arguments)
    except ValueError as error:
        print(f"Invalid sea settings: {error}", file=sys.stderr)
        return 2
    common = dict(overwrite=arguments.overwrite, wave=wave, panel_size=arguments.panel_size,
                  mesh_size=arguments.mesh_size, element_order=arguments.element_order)
    try:
        if arguments.prepare:
            status = prepare(arguments.output_directory, deadline_s=arguments.deadline, **common,
                             runtime=dict(equilibrium_python=arguments.equilibrium_python,
                                          owner_wheel=arguments.owner_wheel,
                                          stability_source=arguments.stability_source,
                                          allow_development=arguments.allow_development))
            success = status["floating_load_applied"]
        elif arguments.model:
            status = write_model(arguments.output_directory, deadline_s=arguments.deadline, **common)["status"]
            success = status["state"] == "model_ready"
        else:
            _, _, request, paths = write_base(arguments.output_directory, **common)
            print(f"Wrote {paths['base_project']} (ballast pending; "
                  f"realization {request['realization_digest'][:12]})")
            return 0
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 3
    print(json.dumps({k: status[k] for k in ("state", "message") if k in status}, indent=2))
    if "error" in status:
        print(status["error"], file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
