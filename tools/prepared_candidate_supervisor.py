"""Preparation-only control slice for the prepared candidate supervisor.

This module owns only the prospective parent/child control contract for the
two prepared candidate cases. Execution is disabled: ``CONTROL_REVIEWED`` is
false and every public entry point checks that gate FIRST, before any OS or
filesystem dispatch. The intended real path (sequential root then child, one
exact unique named Windows Job Object per case, a 180 s wall deadline and a
512 MiB aggregate committed-memory limit EACH, stop on first failure, no
retry) is implemented for separately reviewed enablement only and never runs
while the gate is false.

Reviewed controls are reused, never re-implemented: ``OwnedWindowsJob`` is
imported from the hash-checked Job helper, and ``OwnedDeadlineBackstop`` and
``_cleanup_owned`` plus ``_suspended_thread_id`` and ``_wait_job_empty`` are
AST-extracted from the hash-checked portable control sources checked into
``tools/prepared_candidate_controls`` (exact reviewed source segments whose
recorded origin hashes are kept as provenance) into namespaces that contain
only the required standard library globals. The original driver is never
imported as a module and its reviewed flags or runtime acceptance are not
inherited.

Source/runtime reconciliation: the parent freezes the exact manifest bytes,
hash-checks the manifest and EVERY manifest fingerprint under the already
armed per-case timer/common started+180 s deadline BEFORE creating the Job or
child, and hands the frozen manifest hash to the child in the guard
handshake. The child repeats the exact named Job guard first, then re-verifies
the manifest bytes (refusing if they changed between parent and child) and
all fingerprints BEFORE any engineering import path is added. The pinned
harness loader must be fingerprint-covered and the actual interpreter path
must equal the manifest runtime. Missing or bad fingerprints fail closed.
``started`` is computed FIRST and the deadline timer is armed BEFORE runtime
preflight, so all control bootstrap loading is charged against the deadline;
verification is deadline-checked between steps, not preempted - there is NO
instantaneous scheduling guarantee.

The parent runs ``python -I -S`` (no ``.pth`` startup, no ``PYTHONPATH``
reliance) and installs the manifest import paths explicitly, AFTER the
fingerprint checks, for the supervisor-only psutil dependency (suspended
thread lookup); ``sys.path`` is restored afterwards and no engineering module
is imported by adding those paths. The child adds the manifest import paths
explicitly only after the guard has verified the exact named Job membership,
the queried limit flags (breakaway allowances refused) and memory. The
pinned local harness ``compose_case`` remains hard disabled; this slice
fabricates no permission, duplicates no owner receipt and records engineering
outputs only prospectively, after ``compose_case`` returns, as scalar JSON
without serializing live owner proof. A one-shot exclusive sequence claim is
written before any case control work and replay is refused. Exit 0 alone is
never sufficient: the parent requires and validates the child result packet
(exact case/job/nonce/common deadline plus expected source and manifest
identities and the scalar evidence) before a case may complete unadjudicated.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import ctypes
import functools
import hashlib
import importlib.util
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
from uuid import UUID
from dataclasses import fields, is_dataclass
from ctypes import wintypes
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_SOURCE_DIR = ROOT / "tools" / "prepared_candidate_controls"
JOB_HELPER_PATH = CONTROL_SOURCE_DIR / "_owned_windows_job_proposal.py"
JOB_HELPER_SHA256 = (
    "0d1b924b807970607eced85bd5b434f56c5c5afbd0903c3c08d2cea3359a4574"
)
REVIEWED_DRIVER_PATH = CONTROL_SOURCE_DIR / "reviewed_geometry_driver_extract.py"
REVIEWED_DRIVER_SHA256 = (
    "caba14f01fb92a1fc995a683b77ea2570f8315e0424324408552767358c9452d"
)
REVIEWED_HELPERS_PATH = CONTROL_SOURCE_DIR / "reviewed_supervisor_helpers_extract.py"
REVIEWED_HELPERS_SHA256 = (
    "7b42c53e9d96551e874cc15a26e5127b43d27db4b7c80956a4424072f1939bb4"
)
# Provenance of the portable control sources: the exact original reviewed
# sources (gitignored evidence checkouts) the minimal extracts were taken
# from. The originals are never imported or executed by this supervisor.
REVIEWED_DRIVER_ORIGIN_PATH = ("reports/prepared-mesh-preassembly/"
                               "current-2cbb161/control-source/"
                               "reviewed_geometry_driver.py")
REVIEWED_DRIVER_ORIGIN_SHA256 = (
    "54149b83d7232e33a796a8ae19c18b201debc2ef7b32519be29ec17853387b40"
)
REVIEWED_HELPERS_ORIGIN_PATH = ("reports/prepared-mesh-preassembly/"
                                "current-2cbb161/control-source/"
                                "reviewed_supervisor_helpers.py")
REVIEWED_HELPERS_ORIGIN_SHA256 = (
    "73f1940eacb0794e21f7ba806112d34647ea6e33a3393ae69ba9093054d80000"
)

CONTROL_REVIEWED = False  # Execution gate; enablement requires separate review.
WALL_SECONDS = 180.0
JOB_COMMIT_MIB = 512
JOB_COMMIT_BYTES = JOB_COMMIT_MIB * 1024 ** 2
REQUIRED_LIMIT_FLAGS = 0x00000200 | 0x00002000  # JOB_MEMORY | KILL_ON_JOB_CLOSE
# Breakaway allowances would let a descendant escape the owned limits.
BREAKAWAY_LIMIT_FLAGS = 0x00000800 | 0x00001000  # BREAKAWAY_OK | SILENT_BREAKAWAY_OK
CASES = ("root", "child")
CHILD_MARKER_ENV = "ANYFEM_PREPARED_CANDIDATE_CHILD"
NONCE_ENV = "ANYFEM_PREPARED_CANDIDATE_NONCE"
JOB_NAME_PREFIX = "ANYfem-prepared-candidate-"
REQUIRED_MANIFEST_FIELDS = ("cases", "import_paths", "harness_path", "runtime",
                            "source_fingerprints", "output_dir")
REQUIRED_EVIDENCE_FIELDS = (
    "project_document_id", "geometry_model_id", "geometry_revision",
    "load_case_id", "load_case_name", "pressure", "element_ids",
    "pressure_binding", "reference_ledger", "occurrence_ledger",
    "t3_cell_count", "field_inventory", "production_record",
    "manifest_checksums", "flags",
)


class ControlNotReviewed(RuntimeError):
    """Execution control is not independently reviewed; dispatch is refused."""


class GuardRefused(RuntimeError):
    """The owned child guard rejected the handshake or the queried Job facts."""


class SourceVerificationRefused(RuntimeError):
    """A manifest/fingerprint/interpreter identity check failed closed."""


class SequenceReplayRefused(RuntimeError):
    """The one-shot sequence output was already claimed; replay is refused."""


class ChildResultIncomplete(RuntimeError):
    """The child result packet is missing, foreign or incomplete."""


def _require_control_reviewed() -> None:
    if not CONTROL_REVIEWED:
        raise ControlNotReviewed(
            "prepared candidate execution control is not reviewed; "
            "CONTROL_REVIEWED is false and launch/child dispatch is refused")


def _limit_flags_acceptable(flags) -> bool:
    """Exact required limits present and no breakaway allowance configured."""
    return type(flags) is int and flags == REQUIRED_LIMIT_FLAGS


def _extract_reviewed_functions(path, expected_sha256, names, namespace_globals):
    """Hash-check a reviewed source, then exec ONLY the named function defs.

    The exec namespace contains only the required standard library globals,
    so the extracted definitions cannot reach any other name from the
    original module. The original driver/main is never imported and none of
    its module-level flags or runtime acceptance is inherited.
    """
    data = Path(path).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RuntimeError(f"reviewed control source changed: {Path(path).name}")
    tree = ast.parse(data.decode("utf-8"))
    future = [node for node in tree.body
              if isinstance(node, ast.ImportFrom) and node.module == "__future__"]
    selected = [node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name in names]
    if len(selected) != len(names) or {node.name for node in selected} != set(names):
        raise RuntimeError(
            f"reviewed control source lacks required definitions: {Path(path).name}")
    namespace = dict(namespace_globals)
    exec(compile(ast.Module(body=[*future, *selected], type_ignores=[]),
                 str(path), "exec"), namespace)
    return namespace


@functools.lru_cache(maxsize=1)
def _reviewed_job_module():
    """Load the hash-checked reviewed Windows Job helper module."""
    if hashlib.sha256(JOB_HELPER_PATH.read_bytes()).hexdigest() != JOB_HELPER_SHA256:
        raise RuntimeError("reviewed Windows Job helper changed")
    spec = importlib.util.spec_from_file_location(
        "_reviewed_owned_windows_job", JOB_HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.lru_cache(maxsize=1)
def _reviewed_backstop_namespace():
    """Exact OwnedDeadlineBackstop and _cleanup_owned from the portable extract
    of the reviewed driver; the original driver module is never imported."""
    return _extract_reviewed_functions(
        REVIEWED_DRIVER_PATH, REVIEWED_DRIVER_SHA256,
        ("OwnedDeadlineBackstop", "_cleanup_owned"),
        {"time": time, "threading": threading})


@functools.lru_cache(maxsize=1)
def _reviewed_helper_namespace():
    """Exact _suspended_thread_id and _wait_job_empty from the portable
    extract of the reviewed helpers; the original module is never imported."""
    return _extract_reviewed_functions(
        REVIEWED_HELPERS_PATH, REVIEWED_HELPERS_SHA256,
        ("_suspended_thread_id", "_wait_job_empty"),
        {"time": time})


def _named_job_membership_check(job, child):
    """Query the EXACT named Job; report observed membership, not intent."""
    job_module = _reviewed_job_module()
    kernel = job.kernel
    handle = kernel.OpenJobObjectW(job_module.JOB_OBJECT_QUERY, False, job.name)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenJobObjectW")
    try:
        inside = wintypes.BOOL()
        if not kernel.IsProcessInJob(int(child._handle), handle,
                                     ctypes.byref(inside)):
            raise OSError(ctypes.get_last_error(), "IsProcessInJob")
        return {"member": bool(inside.value), "job_name": job.name}
    finally:
        if not kernel.CloseHandle(handle):
            raise OSError(ctypes.get_last_error(), "CloseHandle")


def load_manifest(path):
    """Validate the exact parent-supplied manifest; fail closed on any gap."""
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a JSON object")
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:
        raise ValueError(f"manifest is missing required fields: {missing}")
    if tuple(manifest["cases"]) != CASES:
        raise ValueError(f"manifest cases must be exactly {list(CASES)}")
    if (not isinstance(manifest["import_paths"], list)
            or not manifest["import_paths"]
            or not all(isinstance(item, str) and item
                       for item in manifest["import_paths"])):
        raise ValueError("manifest import_paths must be a non-empty list of strings")
    if not isinstance(manifest["harness_path"], str) or not manifest["harness_path"]:
        raise ValueError("manifest harness_path must be a non-empty string")
    runtime = manifest["runtime"]
    if (not isinstance(runtime, dict)
            or not isinstance(runtime.get("interpreter"), str)
            or not runtime["interpreter"]):
        raise ValueError("manifest runtime.interpreter must be a non-empty string")
    fingerprints = manifest["source_fingerprints"]
    if (not isinstance(fingerprints, dict) or not fingerprints
            or not all(isinstance(key, str) and isinstance(value, str)
                       for key, value in fingerprints.items())):
        raise ValueError("manifest source_fingerprints must be a non-empty "
                         "string-to-string mapping")
    if not isinstance(manifest["output_dir"], str) or not manifest["output_dir"]:
        raise ValueError("manifest output_dir must be a non-empty string")
    return manifest


def _freeze_manifest_sha256(manifest_path):
    """Parent freezes the exact manifest bytes before any case control work."""
    return hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()


def _source_fingerprints_identity(manifest):
    """Stable identity of the exact fingerprint map (sha256, entry count)."""
    fingerprints = manifest["source_fingerprints"]
    encoded = json.dumps(fingerprints, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(fingerprints)


def _verify_source_fingerprints(manifest, *, manifest_path, clock, deadline,
                                 expected_manifest_sha256=None, interpreter=None):
    """Fail-closed hash check of the exact manifest bytes and every fingerprint.

    The deadline is checked before and during verification: this runs under
    the already armed per-case timer, so bootstrap loading is charged and no
    instantaneous scheduling guarantee is made or needed.
    """
    def check_deadline():
        if clock() >= deadline:
            raise TimeoutError(
                "source fingerprint verification exceeded the case deadline")

    check_deadline()
    manifest_sha256 = hashlib.sha256(
        Path(manifest_path).read_bytes()).hexdigest()
    if (expected_manifest_sha256 is not None
            and manifest_sha256 != expected_manifest_sha256):
        raise SourceVerificationRefused(
            "manifest bytes changed between the parent freeze and this check")
    fingerprints = manifest["source_fingerprints"]
    harness_key = os.path.normcase(
        str(Path(manifest["harness_path"]).resolve()))
    pinned = {os.path.normcase(str(Path(key).resolve()))
              for key in fingerprints}
    if harness_key not in pinned:
        raise SourceVerificationRefused(
            "pinned harness loader is not covered by the manifest fingerprints")
    if interpreter is not None:
        actual = os.path.normcase(str(Path(interpreter).resolve()))
        expected = os.path.normcase(
            str(Path(manifest["runtime"]["interpreter"]).resolve()))
        if actual != expected:
            raise SourceVerificationRefused(
                "actual interpreter path differs from the manifest runtime")
    for key, expected_hash in fingerprints.items():
        check_deadline()
        if (not isinstance(key, str) or not key
                or not isinstance(expected_hash, str)
                or len(expected_hash) != 64):
            raise SourceVerificationRefused(
                f"malformed manifest fingerprint entry: {key!r}")
        try:
            data = Path(key).read_bytes()
        except OSError as error:
            raise SourceVerificationRefused(
                f"missing fingerprinted source: {key}") from error
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise SourceVerificationRefused(
                f"fingerprinted source changed: {key}")
    fingerprints_sha256, count = _source_fingerprints_identity(manifest)
    return {"manifest_sha256": manifest_sha256,
            "source_fingerprints_sha256": fingerprints_sha256,
            "source_fingerprint_count": count}


@contextlib.contextmanager
def _manifest_dependency_scope(manifest):
    """Install explicit manifest import paths for supervisor dependencies only.

    Adding these paths must not import engineering modules; the caller imports
    only the supervisor dependency (psutil) inside the scope. sys.path is
    restored afterwards, so no .pth/PYTHONPATH bootstrap reliance remains.
    """
    previous = sys.path[:]
    sys.path[:0] = [str(path) for path in manifest["import_paths"]]
    try:
        yield
    finally:
        sys.path[:] = previous


def _preload_supervisor_dependency(manifest):
    """Import psutil from explicit manifest paths; restore sys.path after.

    The reviewed ``_suspended_thread_id`` helper imports psutil at call time;
    preloading it here from the explicitly installed manifest paths keeps the
    isolated ``python -I -S`` parent startup free of any path bootstrap while
    no engineering module is ever imported by this supervisor.
    """
    with _manifest_dependency_scope(manifest):
        import psutil
    return psutil.__version__


def control_contract(manifest_path):
    """Preparation-only contract record; performs no execution dispatch."""
    manifest = load_manifest(manifest_path)
    # Hash-check every reused reviewed control before reporting the contract.
    _reviewed_job_module()
    _reviewed_backstop_namespace()
    _reviewed_helper_namespace()
    return {
        "status": "PREPARED_UNEXECUTED",
        "control_reviewed": CONTROL_REVIEWED,
        "gate": "CONTROL_REVIEWED must be independently reviewed to true "
                "before any launch or child dispatch; both refuse first",
        "limits": {
            "wall_seconds_total": WALL_SECONDS,
            "job_memory_limit_mib": JOB_COMMIT_MIB,
            "job_memory_limit_bytes": JOB_COMMIT_BYTES,
            "required_limit_flags": REQUIRED_LIMIT_FLAGS,
            "refused_limit_flags": BREAKAWAY_LIMIT_FLAGS,
            "limit_scope": "one exact unique named Job per case; each case "
                           "gets its own started+180 s deadline",
        },
        "sequence": {
            "cases": list(CASES),
            "order": "sequential root then child",
            "failure_policy": "stop on first failure; no retry",
        },
        "commands": {
            "launch": [manifest["runtime"]["interpreter"], "-I", "-S",
                       str(Path(__file__).resolve()), "launch",
                       "--manifest", str(manifest_path)],
            "child": [manifest["runtime"]["interpreter"], "-I", "-S",
                      str(Path(__file__).resolve()), "child", "--case", "<case>",
                      "--manifest", str(manifest_path)],
            "isolation": "python -I -S for parent and child; no PYTHONPATH and "
                         "no .pth startup; manifest import paths are installed "
                         "explicitly only after the fingerprint checks (parent: "
                         "supervisor psutil dependency) or the child guard",
        },
        "handshake_fields": ["nonce", "case", "job_name", "started", "deadline",
                             "limits", "manifest_sha256", "guard_armed"],
        "source_verification": {
            "enforced": True,
            "parent": "manifest bytes frozen and every manifest fingerprint "
                      "hash-checked under the armed per-case timer BEFORE "
                      "creating the Job or child",
            "child": "repeated after the exact named Job guard and BEFORE any "
                     "engineering import path is added; the child refuses if "
                     "the manifest bytes changed between parent and child",
            "harness_and_interpreter": "the pinned harness loader must be "
                                       "fingerprint-covered and the actual "
                                       "interpreter path must equal the "
                                       "manifest runtime",
            "scheduling": "started is computed first and the deadline timer "
                          "is armed before runtime preflight; verification "
                          "is deadline-checked, not preempted; no "
                          "instantaneous scheduling guarantee",
            "fail_closed": "missing or mismatched fingerprints refuse the case",
        },
        "sequence_claim": {
            "one_shot": True,
            "mechanism": "exclusive create of sequence-claim.json before any "
                         "case control work; replay is refused",
        },
        "child_result": {
            "required_for_completion": "exit 0 alone is insufficient; the "
                                       "parent validates the child packet "
                                       "identities and scalar evidence before "
                                       "completed-unadjudicated",
        },
        "cleanup": "terminate only the owned named Job and its child; bounded "
                   "reap; zero active-process verification; deadline timer "
                   "quiesced before the Job handle closes; every cleanup "
                   "action attempted even after errors",
        "reviewed_control_sources": {
            "_owned_windows_job_proposal.py": JOB_HELPER_SHA256,
            "reviewed_geometry_driver_extract.py": REVIEWED_DRIVER_SHA256,
            "reviewed_supervisor_helpers_extract.py": REVIEWED_HELPERS_SHA256,
        },
        "reviewed_control_source_origins": {
            "reviewed_geometry_driver.py": REVIEWED_DRIVER_ORIGIN_SHA256,
            "reviewed_supervisor_helpers.py": REVIEWED_HELPERS_ORIGIN_SHA256,
            "note": "portable extracts of the exact reviewed segments; the "
                    "gitignored original evidence checkouts are never "
                    "imported or executed",
        },
        "manifest": {
            "cases": list(manifest["cases"]),
            "import_paths": list(manifest["import_paths"]),
            "harness_path": manifest["harness_path"],
            "runtime": manifest["runtime"],
            "source_fingerprints": manifest["source_fingerprints"],
            "output_dir": manifest["output_dir"],
        },
        "execution_enabled": False,
        "mesh_executed": False,
        "preassembly_executed": False,
        "solver_executed": False,
        "runtime_probe": False,
        "notes": "compose_case in the pinned local harness remains hard "
                 "disabled; this contract grants no execution authority and "
                 "makes no runtime-probe claim",
    }


def _child_guard(case, nonce, *, input_stream, job_query, clock):
    """Verify the parent handshake and the EXACT named Job before any import."""
    if not isinstance(nonce, str) or len(nonce) != 64:
        raise GuardRefused("child has no owned-guard nonce")
    line = input_stream.readline()
    try:
        handshake = json.loads(line)
    except (TypeError, ValueError) as error:
        raise GuardRefused("child has no valid guard handshake") from error
    if not isinstance(handshake, dict):
        raise GuardRefused("child has no valid guard handshake")
    expected_name = f"{JOB_NAME_PREFIX}{case}-{nonce}"
    started = handshake.get("started")
    deadline = handshake.get("deadline")
    limits = handshake.get("limits")
    flags = limits.get("configured_limit_flags") if isinstance(limits, dict) else None
    memory = (limits.get("configured_job_memory_limit_bytes")
              if isinstance(limits, dict) else None)
    manifest_sha256 = handshake.get("manifest_sha256")
    if (handshake.get("nonce") != nonce
            or handshake.get("case") != case
            or handshake.get("job_name") != expected_name
            or handshake.get("guard_armed") is not True
            or not isinstance(manifest_sha256, str)
            or len(manifest_sha256) != 64
            or type(flags) is not int
            or not _limit_flags_acceptable(flags)
            or type(memory) is not int
            or memory != JOB_COMMIT_BYTES
            or type(started) not in (int, float)
            or not math.isfinite(started)
            or type(deadline) not in (int, float)
            or not math.isfinite(deadline)
            or deadline != started + WALL_SECONDS
            or clock() >= deadline):
        raise GuardRefused(
            "child guard handshake is malformed, foreign, wrongly limited or expired")
    facts = job_query(expected_name)
    if (not isinstance(facts, dict)
            or facts.get("member") is not True
            or not _limit_flags_acceptable(facts.get("configured_limit_flags"))
            or type(facts.get("configured_job_memory_limit_bytes")) is not int
            or facts["configured_job_memory_limit_bytes"] != JOB_COMMIT_BYTES):
        raise GuardRefused(
            "child is outside the exact owned Job or its queried limits differ")
    return {
        "guard_armed": True,
        "case": case,
        "nonce": nonce,
        "job_name": expected_name,
        "started": started,
        "deadline": deadline,
        "manifest_sha256": manifest_sha256,
        "job_membership_verified": True,
        "observed_limit_flags": facts["configured_limit_flags"],
        "observed_job_memory_limit_bytes": facts["configured_job_memory_limit_bytes"],
    }


def _load_pinned_harness(manifest):
    """Load the pinned local harness module; never fabricate its permission."""
    path = Path(manifest["harness_path"])
    spec = importlib.util.spec_from_file_location("prepared_candidate_integration",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _scalar(value):
    """Coerce evidence to scalar JSON; live objects are never serialized."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if is_dataclass(value) and {field.name for field in fields(value)} == {"kind", "id"}:
        return {"kind": _scalar(value.kind), "id": _scalar(value.id)}
    if isinstance(value, (tuple, list)):
        return [_scalar(item) for item in value]
    return None


def _evidence_field(owner, name, gaps, path):
    """Read one scalar attribute; non-scalar values are refused, not serialized."""
    value = getattr(owner, name, None) if owner is not None else None
    scalar = _scalar(value)
    if value is not None and scalar is None:
        gaps.append(path)
    return scalar


def _identity(value):
    """String identity of an ID-like value without serializing live objects."""
    return None if value is None else str(value)


def _extract_case_evidence(case, result):
    """Capture the prospective compose_case tuple as scalar JSON evidence.

    Reads only scalar attributes of the existing (inputs, pressure, artifact,
    binding) tuple: Project/model/case/record IDs, source/current namespaces,
    pressure/support/reference cell/node scopes, the occurrence/orientation
    ledger, the 26 T3/physical-normal/field inventory, the existing owner
    production record and manifest checksums, and the preassembly/solver/
    publication flags. Live owner proof objects are never serialized and no
    permission is minted; absent or non-scalar fields are recorded as gaps.
    """
    if not isinstance(result, (tuple, list)) or len(result) != 4:
        raise ValueError("compose_case result is not the expected "
                         "(inputs, pressure, artifact, binding) tuple")
    inputs, pressure, artifact, binding = result
    if not isinstance(inputs, dict):
        raise ValueError("compose_case inputs is not a mapping")
    gaps: list = []
    project = inputs.get("project")
    geometry = getattr(project, "geometry", None)
    manifest = inputs.get("manifest")
    source_binding = getattr(artifact, "source_binding", None)
    production = getattr(artifact, "production_record", None)
    pressure_ref = getattr(pressure, "ref", None)
    references = getattr(source_binding, "references", None) or ()
    occurrences = getattr(source_binding, "occurrences", None) or ()
    associations = getattr(source_binding, "associations", None) or ()
    source_mesh = getattr(source_binding, "mesh", None)
    tris = getattr(source_mesh, "tris", None)
    if tris is None or not hasattr(tris, "__len__"):
        gaps.append("source_binding.mesh.tris")
        t3_cell_count = None
    else:
        t3_cell_count = len(tris)
    production_summary = {}
    if isinstance(production, dict):
        production_summary = {
            "schema": production.get("schema"),
            "status": production.get("status"),
            "contract_id": production.get("contract_id"),
            "formulation_id": production.get("formulation_id"),
            "quality_contract_id": production.get("quality_contract_id"),
            "repair_contract_id": production.get("repair_contract_id"),
            "element_count": len(production.get("element_ids") or ()),
            "nodal_normal_count": len(production.get("nodal_normals") or {}),
            "element_owner_normal_count": len(
                production.get("element_owner_normals") or {}),
        }
        try:
            production_summary["checksum_sha256"] = hashlib.sha256(json.dumps(
                production, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            gaps.append("artifact.production_record.checksum_sha256")
    else:
        gaps.append("artifact.production_record")
    evidence = {
        "case": case,
        "project_document_id": _identity(_evidence_field(
            project, "document_id", gaps, "project.document_id")),
        "geometry_model_id": _identity(_evidence_field(
            geometry, "model_id", gaps, "geometry.model_id")),
        "geometry_revision": _evidence_field(
            geometry, "revision", gaps, "geometry.revision"),
        "load_case_id": _identity(_evidence_field(
            artifact, "load_case_id", gaps, "artifact.load_case_id")),
        "load_case_name": _evidence_field(
            artifact, "load_case_name", gaps, "artifact.load_case_name"),
        "pressure": {
            "id": _identity(_evidence_field(
                pressure, "id", gaps, "pressure.id")),
            "ref_kind": _evidence_field(
                pressure_ref, "kind", gaps, "pressure.ref.kind"),
            "ref_id": _identity(_evidence_field(
                pressure_ref, "id", gaps, "pressure.ref.id")),
            "value": _evidence_field(
                pressure, "value", gaps, "pressure.value"),
        },
        "element_ids": _evidence_field(
            artifact, "element_ids", gaps, "artifact.element_ids") or [],
        "association_namespace": (inputs.get("association_namespace")
                                  or "prepared_current"),
        "association_namespaces": sorted({
            _identity(_evidence_field(row, "namespace", gaps,
                                      "association.namespace"))
            for row in associations}),
        "pressure_binding": {
            "key": _evidence_field(binding, "key", gaps, "binding.key"),
            "category": _evidence_field(
                binding, "category", gaps, "binding.category"),
            "source_targets": _evidence_field(
                binding, "source_targets", gaps, "binding.source_targets"),
            "current_targets": _evidence_field(
                binding, "current_targets", gaps, "binding.current_targets"),
            "cell_ids": _evidence_field(
                binding, "cell_ids", gaps, "binding.cell_ids"),
            "node_ids": _evidence_field(
                binding, "node_ids", gaps, "binding.node_ids"),
        },
        "reference_ledger": [
            {"key": _evidence_field(row, "key", gaps, "reference.key"),
             "category": _evidence_field(row, "category", gaps, "reference.category"),
             "source_targets": _evidence_field(
                 row, "source_targets", gaps, "reference.source_targets"),
             "current_targets": _evidence_field(
                 row, "current_targets", gaps, "reference.current_targets"),
             "cell_ids": _evidence_field(row, "cell_ids", gaps, "reference.cell_ids"),
             "node_ids": _evidence_field(row, "node_ids", gaps, "reference.node_ids")}
            for row in references],
        "occurrence_ledger": [
            {"source_sheet_id": _evidence_field(
                 row, "source_sheet_id", gaps, "occurrence.source_sheet_id"),
             "source_face_use_id": _evidence_field(
                 row, "source_face_use_id", gaps, "occurrence.source_face_use_id"),
             "source_face_id": _evidence_field(
                 row, "source_face_id", gaps, "occurrence.source_face_id"),
             "current_face_use_ids": _evidence_field(
                 row, "current_face_use_ids", gaps,
                 "occurrence.current_face_use_ids"),
             "cell_ids": _evidence_field(
                 row, "cell_ids", gaps, "occurrence.cell_ids"),
             "source_orientation": _evidence_field(
                 row, "source_orientation", gaps, "occurrence.source_orientation"),
             "current_orientations": _evidence_field(
                 row, "current_orientations", gaps,
                 "occurrence.current_orientations")}
            for row in occurrences],
        "t3_cell_count": t3_cell_count,
        "field_inventory": _evidence_field(
            artifact, "field_inventory", gaps, "artifact.field_inventory"),
        "production_record": production_summary,
        "manifest_checksums": {
            "schema_version": _evidence_field(
                manifest, "schema_version", gaps, "manifest.schema_version"),
            "document_id": _identity(_evidence_field(
                manifest, "document_id", gaps, "manifest.document_id")),
            "manifest_checksum": _identity(_evidence_field(
                manifest, "manifest_checksum", gaps, "manifest.manifest_checksum")),
            "project_checksum": _identity(_evidence_field(
                manifest, "project_checksum", gaps, "manifest.project_checksum")),
        },
        "flags": {
            "preassembly_checked": _evidence_field(
                artifact, "preassembly_checked", gaps,
                "artifact.preassembly_checked"),
            "solver_executed": _evidence_field(
                artifact, "solver_executed", gaps, "artifact.solver_executed"),
            "publication_qualified": _evidence_field(
                artifact, "publication_qualified", gaps,
                "artifact.publication_qualified"),
            "source_reference_transfer_qualified": _evidence_field(
                source_binding, "source_reference_transfer_qualified", gaps,
                "source_binding.source_reference_transfer_qualified"),
            "solver_admitted": _evidence_field(
                source_binding, "solver_admitted", gaps,
                "source_binding.solver_admitted"),
        },
        "evidence_gaps": gaps,
    }
    return evidence


def _child_dispatch(manifest, case, guard, *, harness_loader=None, recorder=None):
    """Engineering imports and dispatch happen only here, after the guard."""
    if (not isinstance(guard, dict) or guard.get("guard_armed") is not True
            or guard.get("job_membership_verified") is not True):
        raise GuardRefused("child dispatch requires a verified owned guard")
    sys.path[:0] = [str(path) for path in manifest["import_paths"]]
    loader = harness_loader if harness_loader is not None else _load_pinned_harness
    write = recorder if recorder is not None else _make_child_recorder(manifest)
    record = {
        "status": "CHILD_CASE_COMPLETED_UNADJUDICATED",
        "case": case,
        "guard": guard,
        "control_reviewed": CONTROL_REVIEWED,
        "runtime_probe": False,
    }
    try:
        harness = loader(manifest)
        # The fixed trusted wheel/archive/FEM/fixture baseline is still checked
        # by the EXISTING harness verification before any engineering work;
        # no receipt is fabricated here.
        verification = getattr(harness, "verify_artifacts", None)
        if not callable(verification):
            raise RuntimeError(
                "pinned harness lacks the existing artifact verification")
        verification()
        result = harness.compose_case(case)  # Hard disabled until reviewed.
        record["evidence"] = _extract_case_evidence(case, result)
    except BaseException as error:
        # Preserve the full failure; the write-once packet keeps the error.
        record["status"] = "CHILD_CASE_FAILED"
        record["error"] = {"type": type(error).__name__, "message": str(error)}
        write(record, None)
        raise
    write(record, result)  # Prospective only: reached when compose_case returns.
    return record


def _make_child_recorder(manifest):
    directory = Path(manifest["output_dir"])

    def write(record, result):
        directory.mkdir(parents=True, exist_ok=True)
        guard = record.get("guard") if isinstance(record.get("guard"), dict) else {}
        verification = (guard.get("source_verification")
                       if isinstance(guard.get("source_verification"), dict) else {})
        packet = {
            "status": record["status"],
            "case": record["case"],
            "nonce": guard.get("nonce"),
            "job_name": guard.get("job_name"),
            "started": guard.get("started"),
            "deadline": guard.get("deadline"),
            "manifest_sha256": guard.get("manifest_sha256"),
            "source_fingerprints_sha256": verification.get(
                "source_fingerprints_sha256"),
            "source_fingerprint_count": verification.get(
                "source_fingerprint_count"),
            "guard": guard,
            "control_reviewed": record.get("control_reviewed"),
            "runtime_probe": False,
            "evidence": record.get("evidence"),
            "error": record.get("error"),
            "recorded_prospectively": True,
        }
        with (directory / f"{record['case']}-child-result.json").open(
                "x", encoding="utf-8") as stream:
            json.dump(packet, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    return write


def _validate_child_result(packet, *, case, job_name, nonce, started, deadline,
                           manifest_sha256, fingerprints_sha256,
                           fingerprint_count):
    """Require the expected child result; exit 0 alone is never sufficient."""

    def refuse(detail):
        raise ChildResultIncomplete(detail)

    if not isinstance(packet, dict):
        refuse("child result packet is absent or not a record")
    if packet.get("status") != "CHILD_CASE_COMPLETED_UNADJUDICATED":
        refuse(f"child result status is {packet.get('status')!r}")
    if packet.get("case") != case:
        refuse("child result case differs from the supervised case")
    if packet.get("job_name") != job_name:
        refuse("child result Job name differs from the exact owned Job")
    if packet.get("nonce") != nonce:
        refuse("child result nonce differs from the fresh case nonce")
    if (packet.get("started") != started or packet.get("deadline") != deadline):
        refuse("child result does not carry the finite common case deadline")
    if packet.get("manifest_sha256") != manifest_sha256:
        refuse("child result manifest identity differs from the frozen bytes")
    if (packet.get("source_fingerprints_sha256") != fingerprints_sha256
            or packet.get("source_fingerprint_count") != fingerprint_count):
        refuse("child result source fingerprint identity differs")
    guard = packet.get("guard")
    if (not isinstance(guard, dict) or guard.get("guard_armed") is not True
            or guard.get("job_membership_verified") is not True):
        refuse("child result guard facts are absent")
    evidence = packet.get("evidence")
    if not isinstance(evidence, dict):
        refuse("child result carries no engineering evidence")
    for field in REQUIRED_EVIDENCE_FIELDS:
        if evidence.get(field) is None:
            refuse(f"child result evidence lacks {field}")
    if not evidence.get("element_ids"):
        refuse("child result evidence has no element IDs")
    pressure = evidence.get("pressure")
    if (not isinstance(pressure, dict) or pressure.get("id") is None
            or pressure.get("value") is None):
        refuse("child result evidence has no pressure identity")
    binding = evidence.get("pressure_binding")
    if (not isinstance(binding, dict) or not binding.get("key")
            or not binding.get("cell_ids")):
        refuse("child result evidence has no pressure binding scope")
    production = evidence.get("production_record")
    if (not isinstance(production, dict)
            or production.get("checksum_sha256") is None):
        refuse("child result evidence has no production record checksum")
    checksums = evidence.get("manifest_checksums")
    if (not isinstance(checksums, dict)
            or checksums.get("manifest_checksum") is None):
        refuse("child result evidence has no manifest checksum")
    flags = evidence.get("flags")
    if not isinstance(flags, dict):
        refuse("child result evidence has no flags")
    if (flags.get("solver_executed") is not False
            or flags.get("publication_qualified") is not False):
        refuse("child result claims solver or publication authority")
    if flags.get("preassembly_checked") is not True:
        refuse("child result lacks the preassembly check flag")
    return packet


def _make_child_result_reader(output_dir):
    directory = Path(output_dir)

    def reader(case):
        try:
            text = (directory / f"{case}-child-result.json").read_text(
                encoding="utf-8")
        except FileNotFoundError:
            return None
        return json.loads(text)
    return reader


def _make_log_stream_factory(output_dir):
    directory = Path(output_dir)

    @contextlib.contextmanager
    def factory(case):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{case}-stdout.log").open("x", encoding="utf-8") as stdout, \
             (directory / f"{case}-stderr.log").open("x", encoding="utf-8") as stderr:
            yield stdout, stderr
    return factory


def _make_case_record_writer(output_dir):
    directory = Path(output_dir)

    def writer(case, record):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{case}-record.json").open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    return writer


def _make_sequence_record_writer(output_dir):
    directory = Path(output_dir)

    def writer(record):
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "sequence-record.json").open("x", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    return writer


def _make_sequence_claim_writer(output_dir):
    """One-shot exclusive sequence output claim; write-once, replay refused."""
    directory = Path(output_dir)

    def claim(manifest_sha256):
        record = {
            "status": "SEQUENCE_CLAIMED",
            "manifest_sha256": manifest_sha256,
            "cases": list(CASES),
            "policy": "sequential root then child; stop on first failure; "
                      "no retry",
            "one_shot": True,
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / "sequence-claim.json").open(
                    "x", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, sort_keys=True,
                          allow_nan=False)
                stream.write("\n")
        except FileExistsError as error:
            raise SequenceReplayRefused(
                "sequence output was already claimed; replay is refused"
            ) from error
        return record
    return claim


def _run_case_core(case, manifest, *, manifest_path, clock, timer_factory,
                   job_factory, launcher, membership_check, thread_id_lookup,
                   wait_empty, log_stream_factory, nonce_factory,
                   case_record_writer, manifest_sha256,
                   fingerprints_sha256, fingerprint_count,
                   fingerprint_check, child_result_reader):
    """One owned case; every control is injectable for pure process-free tests.

    ``started`` is computed FIRST and the deadline timer is armed BEFORE the
    runtime preflight (fingerprint verification, Job setup, child creation),
    so per-case backstop loading is charged against the common started+180
    deadline. Synchronous steps are deadline-checked between calls, not
    preempted; no instantaneous scheduling guarantee is made.
    """
    started = clock()
    deadline = started + WALL_SECONDS  # All phases share this one deadline.
    backstop = _reviewed_backstop_namespace()
    nonce = nonce_factory()
    job_name = f"{JOB_NAME_PREFIX}{case}-{nonce}"
    guard = backstop["OwnedDeadlineBackstop"](deadline, clock=clock,
                                              timer_factory=timer_factory)
    job = None
    child = None
    reason = None
    interrupt = None
    setup_snapshot = None
    membership = None
    observed = None
    verification = None
    packet = None
    packet_problem = None
    peak_snapshot = None
    final_snapshot = None
    guard_snapshot = None
    cleanup_errors: list = []
    cleanup_started = None
    command = None
    try:
        guard.arm()  # Independent timer BEFORE preflight, Job setup, child creation.
        # Hash-check the exact manifest and ALL file fingerprints BEFORE
        # creating the Job or the child; the frozen parent hash must still match.
        verification = fingerprint_check(
            manifest, manifest_path=manifest_path, clock=clock,
            deadline=deadline, expected_manifest_sha256=manifest_sha256,
            interpreter=sys.executable)
        job = job_factory(JOB_COMMIT_BYTES, name=job_name)
        guard.register_job(job)
        setup_snapshot = job.snapshot()
        if (not _limit_flags_acceptable(
                setup_snapshot.get("configured_limit_flags"))
                or setup_snapshot.get("configured_job_memory_limit_bytes")
                != JOB_COMMIT_BYTES):
            raise RuntimeError("OS Job Object did not confirm the requested limits")
        guard.require_live()
        env = os.environ.copy()
        env[CHILD_MARKER_ENV] = case
        env[NONCE_ENV] = nonce
        env.pop("PYTHONPATH", None)  # Child imports rely only on manifest paths.
        for name, value in manifest["runtime"].get("thread_environment", {}).items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        command = [manifest["runtime"]["interpreter"], "-I", "-S",
                   str(Path(__file__).resolve()), "child", "--case", case,
                   "--manifest", str(manifest_path)]
        flags = (_reviewed_job_module().CREATE_SUSPENDED
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with log_stream_factory(case) as streams:
            stdout, stderr = streams
            child = launcher(command, stdout=stdout, stderr=stderr,
                             stdin=subprocess.PIPE, env=env, creationflags=flags)
            guard.register_child(child)
            thread_id = thread_id_lookup(child.pid)
            guard.require_live()
            job.assign_suspended_only(child)  # Assignment only; resume is separate.
            membership = membership_check(job, child)
            if (not isinstance(membership, dict)
                    or membership.get("member") is not True):
                raise RuntimeError("owned child is not a member of the exact named Job")
            observed = job.snapshot()
            if (not _limit_flags_acceptable(
                    observed["configured_limit_flags"])
                    or observed["configured_job_memory_limit_bytes"]
                    != JOB_COMMIT_BYTES):
                raise RuntimeError("assigned Job limits differ from the contract")
            guard.mark_assigned()
            guard.require_live()
            job.resume_suspended(child, thread_id)
            guard.require_live()
            handshake = {
                "nonce": nonce,
                "case": case,
                "job_name": job_name,
                "started": started,
                "deadline": deadline,
                "limits": {
                    "configured_limit_flags": observed["configured_limit_flags"],
                    "configured_job_memory_limit_bytes":
                        observed["configured_job_memory_limit_bytes"],
                },
                "manifest_sha256": manifest_sha256,
                "guard_armed": guard.snapshot()["armed"],
            }
            child.stdin.write((json.dumps(handshake, sort_keys=True) + "\n").encode())
            child.stdin.flush()
            child.stdin.close()
            guard.require_live()
            child.wait(timeout=max(0.0, deadline - clock()))
    except subprocess.TimeoutExpired:
        reason = "INCOMPLETE_WALL_TIMEOUT"
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            interrupt = error
        reason = ("INCOMPLETE_PREFLIGHT_OR_SETUP_TIMEOUT"
                  if isinstance(error, TimeoutError)
                  else f"FAILED_SUPERVISOR:{type(error).__name__}:{error}")
    finally:
        cleanup_started = clock()
        peak_snapshot, final_snapshot, cleanup_errors, cleanup_interrupt = (
            backstop["_cleanup_owned"](job, child, wait_empty, guard=guard))
        if interrupt is None:
            interrupt = cleanup_interrupt
        guard_snapshot = guard.snapshot()
        cleanup_errors = [*cleanup_errors, *guard_snapshot["errors"]]
    # The child result packet is required: exit 0 alone is never sufficient.
    if child is not None:
        try:
            packet = child_result_reader(case)
        except BaseException as error:  # noqa: BLE001 - recorded, not raised
            packet_problem = f"{type(error).__name__}: {error}"
        else:
            try:
                _validate_child_result(
                    packet, case=case, job_name=job_name, nonce=nonce,
                    started=started, deadline=deadline,
                    manifest_sha256=manifest_sha256,
                    fingerprints_sha256=fingerprints_sha256,
                    fingerprint_count=fingerprint_count)
            except ChildResultIncomplete as error:
                packet_problem = str(error)
    cleanup_seconds = clock() - cleanup_started
    if reason is None:
        if cleanup_errors:
            reason = "INCOMPLETE_CLEANUP"
        elif guard_snapshot is None or not guard_snapshot["armed"]:
            reason = "INCOMPLETE_UNARMED_DEADLINE"
        elif guard_snapshot["job_quiesced"] is not True:
            reason = "INCOMPLETE_UNQUIESCED_DEADLINE_CALLBACK"
        elif guard_snapshot["expired"] or clock() > deadline:
            reason = "INCOMPLETE_WALL_TIMEOUT"
        elif final_snapshot is None or final_snapshot.get("active_processes") != 0:
            reason = "INCOMPLETE_OWNED_PROCESS_CLEANUP"
        elif setup_snapshot is None or membership is None or observed is None:
            reason = "INCOMPLETE_MISSING_CONTROL_FACTS"
        elif child is None:
            reason = "INCOMPLETE_CHILD_NOT_STARTED"
        elif child.returncode != 0:
            reason = "FAILED_CHILD"
        elif packet_problem is not None:
            reason = "INCOMPLETE_CHILD_RESULT_PACKET"
    record = {
        "case": case,
        "status": reason or "CASE_COMPLETED_UNADJUDICATED",
        "job_name": job_name,
        "started": started,
        "deadline": deadline,
        "wall_seconds_total": WALL_SECONDS,
        "job_memory_limit_bytes": JOB_COMMIT_BYTES,
        "manifest_sha256": manifest_sha256,
        "source_verification": verification,
        "child_exit_code": None if child is None else child.returncode,
        "child_result_status": (packet.get("status")
                                if isinstance(packet, dict) else None),
        "child_result_complete": isinstance(packet, dict)
                                 and packet_problem is None,
        "child_result_problem": packet_problem,
        "command": command,
        "queried_job_setup": setup_snapshot,
        "queried_membership": membership,
        "queried_job_assigned": observed,
        "deadline_backstop": guard_snapshot,
        "queried_before_termination": peak_snapshot,
        "queried_after_cleanup": final_snapshot,
        "owned_process_count_zero": bool(
            (job is None and child is None)
            or (final_snapshot is not None
                and final_snapshot.get("active_processes") == 0)),
        "owned_cleanup_basis": ("no Job or child created" if job is None and child is None
                                else "queried owned Job after cleanup"),
        "cleanup_seconds": cleanup_seconds,
        "cleanup_errors": list(cleanup_errors),
        "runtime_probe": False,
        "resource_note": "configured JobMemoryLimit is an aggregate "
                         "committed-allocation limit; queried peaks are "
                         "reported separately; no runtime-probe claim",
    }
    case_record_writer(case, record)
    if interrupt is not None:
        raise interrupt.with_traceback(interrupt.__traceback__)
    return record


def _launch_sequence_core(manifest, *, manifest_path, clock, timer_factory,
                          job_factory, launcher, membership_check,
                          thread_id_lookup, wait_empty, log_stream_factory,
                          nonce_factory, case_record_writer,
                          sequence_record_writer, sequence_claim=None,
                          fingerprint_check=None, child_result_reader=None):
    """Sequential root then child; stop on first failure; no retry.

    The parent freezes the exact manifest bytes and claims the one-shot
    sequence output BEFORE any case control work; replay is refused.
    """
    sequence_started = clock()
    manifest_sha256 = _freeze_manifest_sha256(manifest_path)
    fingerprints_sha256, fingerprint_count = _source_fingerprints_identity(manifest)
    claim = (sequence_claim if sequence_claim is not None
             else _make_sequence_claim_writer(manifest["output_dir"]))
    claim_record = claim(manifest_sha256)
    if not isinstance(claim_record, dict):
        raise RuntimeError("sequence claim must be a record")
    case_records = []
    status = "SEQUENCE_COMPLETED_UNADJUDICATED"
    for case in manifest["cases"]:
        record = _run_case_core(
            case, manifest, manifest_path=manifest_path, clock=clock,
            timer_factory=timer_factory, job_factory=job_factory,
            launcher=launcher, membership_check=membership_check,
            thread_id_lookup=thread_id_lookup, wait_empty=wait_empty,
            log_stream_factory=log_stream_factory, nonce_factory=nonce_factory,
            case_record_writer=case_record_writer, manifest_sha256=manifest_sha256,
            fingerprints_sha256=fingerprints_sha256,
            fingerprint_count=fingerprint_count,
            fingerprint_check=(fingerprint_check
                               if fingerprint_check is not None
                               else _verify_source_fingerprints),
            child_result_reader=(child_result_reader
                                 if child_result_reader is not None
                                 else _make_child_result_reader(
                                     manifest["output_dir"])))
        case_records.append(record)
        if record["status"] != "CASE_COMPLETED_UNADJUDICATED":
            status = f"SEQUENCE_STOPPED_FIRST_FAILURE:{case}:{record['status']}"
            break
    sequence = {
        "status": status,
        "control_reviewed": CONTROL_REVIEWED,
        "sequence_policy": "sequential root then child; stop on first failure; "
                           "no retry",
        "limits": {
            "wall_seconds_total": WALL_SECONDS,
            "job_memory_limit_bytes": JOB_COMMIT_BYTES,
            "required_limit_flags": REQUIRED_LIMIT_FLAGS,
            "refused_limit_flags": BREAKAWAY_LIMIT_FLAGS,
        },
        "sequence_claim": claim_record,
        "manifest_sha256": manifest_sha256,
        "source_fingerprints_sha256": fingerprints_sha256,
        "source_fingerprint_count": fingerprint_count,
        "cases_started": [record["case"] for record in case_records],
        "case_records": case_records,
        "manifest": {
            "path": str(manifest_path),
            "cases": list(manifest["cases"]),
            "runtime": manifest["runtime"],
            "source_fingerprints": manifest["source_fingerprints"],
            "import_paths": list(manifest["import_paths"]),
            "harness_path": manifest["harness_path"],
            "output_dir": manifest["output_dir"],
        },
        "elapsed_seconds": clock() - sequence_started,
        "runtime_probe": False,
    }
    sequence_record_writer(sequence)
    return sequence


def launch_sequence(manifest_path):
    """Public parent entry; ALWAYS refuses first while CONTROL_REVIEWED is false.

    Intended real path after separately reviewed enablement: sequential root
    then child, one exact unique named Job per case, 180 s / 512 MiB each,
    stop on first failure, no retry. The parent itself runs isolated
    (``python -I -S``): after the fingerprint checks it installs the manifest
    import paths explicitly for the supervisor-only psutil dependency and
    restores sys.path afterwards; no engineering module is imported.
    """
    _require_control_reviewed()
    manifest = load_manifest(manifest_path)
    job_module = _reviewed_job_module()
    helpers = _reviewed_helper_namespace()
    manifest_path = Path(manifest_path)
    def controlled_thread_lookup(pid):
        # Called only after per-case fingerprint verification under its armed
        # common deadline. No unarmed preflight or inherited .pth bootstrap.
        with _manifest_dependency_scope(manifest):
            return helpers["_suspended_thread_id"](pid)
    return _launch_sequence_core(
        manifest, manifest_path=manifest_path,
        clock=time.monotonic, timer_factory=threading.Timer,
        job_factory=job_module.OwnedWindowsJob, launcher=subprocess.Popen,
        membership_check=_named_job_membership_check,
        thread_id_lookup=controlled_thread_lookup,
        wait_empty=helpers["_wait_job_empty"],
        log_stream_factory=_make_log_stream_factory(manifest["output_dir"]),
        nonce_factory=lambda: secrets.token_hex(32),
        case_record_writer=_make_case_record_writer(manifest["output_dir"]),
        sequence_record_writer=_make_sequence_record_writer(manifest["output_dir"]),
        sequence_claim=_make_sequence_claim_writer(manifest["output_dir"]),
        fingerprint_check=_verify_source_fingerprints,
        child_result_reader=_make_child_result_reader(manifest["output_dir"]))


def child_entry(manifest_path, case):
    """Public child entry; ALWAYS refuses first while CONTROL_REVIEWED is false."""
    _require_control_reviewed()
    manifest = load_manifest(manifest_path)
    return _child_entry_core(manifest, case, manifest_path=Path(manifest_path))


def _child_entry_core(manifest, case, *, manifest_path, input_stream=None,
                      job_query=None, clock=None, harness_loader=None,
                      recorder=None, fingerprint_check=None):
    if case not in CASES:
        raise ValueError(f"unsupported prospective case: {case}")
    marker = os.environ.get(CHILD_MARKER_ENV)
    nonce = os.environ.get(NONCE_ENV)
    if marker != case:
        raise GuardRefused("child has no owned-run marker for its case")
    if not nonce or len(nonce) != 64:
        raise GuardRefused("child has no owned-guard nonce")
    guard = _child_guard(
        case, nonce,
        input_stream=input_stream if input_stream is not None else sys.stdin,
        job_query=(job_query if job_query is not None
                   else _reviewed_job_module().current_process_job_facts),
        clock=clock if clock is not None else time.monotonic)
    # Repeat the fingerprint check AFTER the exact named Job guard and BEFORE
    # any engineering import path is added; refuse if the manifest bytes
    # changed between the parent freeze and this child.
    verification = (fingerprint_check if fingerprint_check is not None
                    else _verify_source_fingerprints)(
                        manifest, manifest_path=manifest_path,
                        clock=clock if clock is not None else time.monotonic,
                        deadline=guard["deadline"],
                        expected_manifest_sha256=guard["manifest_sha256"],
                        interpreter=sys.executable)
    guard = {**guard, "source_verification": verification}
    return _child_dispatch(manifest, case, guard,
                           harness_loader=harness_loader, recorder=recorder)


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("contract", "launch", "child"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--case", choices=CASES)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.action == "contract":
        print(json.dumps(control_contract(args.manifest), indent=2, sort_keys=True))
        return None
    if args.action == "launch":
        record = launch_sequence(args.manifest)
        print(json.dumps(record, indent=2, sort_keys=True))
        return record
    if args.case is None:
        parser.error("child requires --case")
    record = child_entry(args.manifest, args.case)
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


if __name__ == "__main__":
    main()
