"""Input-only experimental ANYfem consumers for physical B2/Q4 attachments.

This is a separate, bounded file format. Reopening it recreates solver inputs;
it does not restore solver state or turn an experimental profile into a
qualified ANYfem project.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from scipy import sparse

from anysolver import stiffened_shell


SCHEMA = "anyfem.experimental-b2-q4-point-panel-v1"
SCHEMA_MIXED = "anyfem.experimental-b2-mixed-point-panel-v1"
SCHEMA_MIXED_REFERENCE_MODAL = "anyfem.experimental-b2-mixed-reference-modal-panel-v1"
SCHEMA_MIXED_FREE_REFERENCE_MODAL = "anyfem.experimental-b2-mixed-free-reference-modal-panel-v1"
_MIXED_MODAL_SCHEMAS = (SCHEMA_MIXED_REFERENCE_MODAL, SCHEMA_MIXED_FREE_REFERENCE_MODAL)
SCHEMA_MASS = "anyfem.experimental-b2-q4-point-panel-mass-v2"
SCHEMA_NODAL_FOOTPRINT = "anyfem.experimental-b2-nodal-rigid-footprint-panel-v1"
SCHEMA_REFERENCE_MODAL = "anyfem.experimental-b2-nodal-footprint-reference-modal-panel-v1"
SCHEMA_BODY_ACCELERATION = "anyfem.experimental-b2-q4-body-acceleration-panel-v1"
SCHEMA_MIXED_BODY_ACCELERATION = "anyfem.experimental-b2-mixed-body-acceleration-panel-v1"
SCHEMA_FOOTPRINT_BODY_ACCELERATION = "anyfem.experimental-b2-nodal-footprint-body-acceleration-panel-v1"
SCHEMA_LOADED_POINT = "anyfem.experimental-b2-q4-loaded-point-panel-v1"
SCHEMA_LOADED_POINT_BODY = "anyfem.experimental-b2-q4-loaded-point-body-panel-v1"
_LOADED_POINT_SCHEMAS = (SCHEMA_LOADED_POINT, SCHEMA_LOADED_POINT_BODY)
LOADED_POINT_POLICY = "B2_FIXED_END_LOADED_POINT_TRACE_V1_DEV"
_BODY_SCHEMAS = (SCHEMA_BODY_ACCELERATION, SCHEMA_MIXED_BODY_ACCELERATION,
                 SCHEMA_FOOTPRINT_BODY_ACCELERATION)
STATUS = "EXPERIMENTAL_NOT_QUALIFIED"
SUFFIX = ".anyfem-b2.json"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_COORDINATES = 500_000
MAX_NONZEROS = 1_000_000


def _exact(value, fields, label):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError(f"strict {label} record required")
    return value


def _integer(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} integer required")
    return value


def _number(value, label):
    if type(value) not in (int, float):
        raise ValueError(f"finite {label} required")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"finite {label} required")
    return float(value)


def _vector(value, size, label, *, integer=False):
    if type(value) is not list or len(value) != size:
        raise ValueError(f"{label} requires {size} values")
    return tuple(_integer(v, label) if integer else _number(v, label)
                 for v in value)


def _identifier(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(f"nonempty {label} required")
    return value


def _json_load(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate experimental B2 field: {key}")
            result[key] = value
        return result

    def nonfinite(_value):
        raise ValueError("nonfinite experimental B2 JSON value")

    try:
        return json.loads(payload.decode("ascii"), object_pairs_hook=unique,
                          parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid experimental B2 panel JSON") from exc


def _decode(record):
    loaded = type(record) is dict and record.get("schema") in _LOADED_POINT_SCHEMAS
    body = type(record) is dict and record.get("schema") in _BODY_SCHEMAS + (SCHEMA_LOADED_POINT_BODY,)
    free_modal = type(record) is dict and record.get("schema") == SCHEMA_MIXED_FREE_REFERENCE_MODAL
    mixed_modal = type(record) is dict and record.get("schema") in _MIXED_MODAL_SCHEMAS
    mixed = type(record) is dict and record.get("schema") in (SCHEMA_MIXED, SCHEMA_MIXED_BODY_ACCELERATION) + _MIXED_MODAL_SCHEMAS
    modal = type(record) is dict and record.get("schema") in (SCHEMA_REFERENCE_MODAL,) + _MIXED_MODAL_SCHEMAS
    nodal = type(record) is dict and record.get("schema") in (SCHEMA_NODAL_FOOTPRINT, SCHEMA_REFERENCE_MODAL, SCHEMA_FOOTPRINT_BODY_ACCELERATION)
    record = _exact(record, ("schema", "status", "profile", "shell_nodes",
                             "shells", "members", "points", "affine") +
                                  (("request", "modal_boundary_policy") if modal else
                                  ("cases", "selection")) + (("footprints",) if nodal else ()) +
                                  (("inertia_policy",) if mixed_modal else ()) + (("point_trace_policy",) if loaded else ()),
                    "experimental B2 panel")
    if (record["schema"] not in (SCHEMA, SCHEMA_MIXED, SCHEMA_MASS, SCHEMA_NODAL_FOOTPRINT, SCHEMA_REFERENCE_MODAL) + _MIXED_MODAL_SCHEMAS + _BODY_SCHEMAS + _LOADED_POINT_SCHEMAS or record["status"] != STATUS or
            record["profile"] != stiffened_shell.B2_E4_PL_FAST_LINEAR_V1):
        raise ValueError("experimental B2 panel schema/status/profile required")
    if loaded and record["point_trace_policy"] != LOADED_POINT_POLICY:
        raise ValueError("explicit loaded-point trace policy required")
    with_mass = body or loaded or record["schema"] in (SCHEMA_MASS, SCHEMA_REFERENCE_MODAL) + _MIXED_MODAL_SCHEMAS
    mixed_definition, s3_type = _mixed_api() if mixed else (None, None)
    if mixed_modal:
        mixed_definition, _, _, policy = _mixed_modal_api()
        if type(record["inertia_policy"]) is not str or record["inertia_policy"] != policy:
            raise ValueError("explicit native S3 mixed reference inertia policy required")
    if free_modal:
        free_policy, _ = _mixed_free_modal_api()
        if type(record["modal_boundary_policy"]) is not str or record["modal_boundary_policy"] != free_policy:
            raise ValueError("explicit public B2 free-free modal-only boundary policy required")
    node_rows = record["shell_nodes"]
    if type(node_rows) is not list or not node_rows:
        raise ValueError("experimental B2 shell nodes required")
    shell_nodes = {}
    for entry in node_rows:
        if type(entry) is not list or len(entry) != 2:
            raise ValueError("shell node ID and coordinate required")
        node_id = _integer(entry[0], "shell node ID", minimum=1)
        xyz = _vector(entry[1], 3, "shell coordinate")
        if node_id in shell_nodes:
            raise ValueError("unique shell node IDs required")
        shell_nodes[node_id] = xyz

    if type(record["shells"]) is not list or not record["shells"]:
        raise ValueError("experimental B2 Q4 shells required")
    shells = []
    for item in record["shells"]:
        fields = ("shell_id", "node_ids", "E", "nu", "thickness_m",
                  "reference_normal")
        row = _exact(item, fields + (("density_kg_m3",) if with_mass else ()) +
                     (("formulation",) if mixed else ()), "mixed shell" if mixed else "Q4 shell")
        formulation = row["formulation"] if mixed else "e4-pl"
        if formulation not in ("e4-pl", "e4-pl-s3-v2d"):
            raise ValueError("explicit mixed shell formulation e4-pl or e4-pl-s3-v2d required")
        count = 3 if formulation == "e4-pl-s3-v2d" else 4
        node_ids = _vector(row["node_ids"], count, "mixed node IDs" if mixed else "Q4 node IDs", integer=True)
        normal = _vector(row["reference_normal"], 3, "mixed normal" if mixed else "Q4 normal")
        if mixed and (len(set(node_ids)) != count or any(node not in shell_nodes for node in node_ids)
                      or not any(value != 0. for value in normal)):
            raise ValueError("distinct known mixed shell nodes and nonzero authoritative normal required")
        density = row.get("density_kg_m3")
        shell_type = s3_type if count == 3 else stiffened_shell.Q4PanelShell
        shells.append(shell_type(
            _identifier(row["shell_id"], "shell ID"),
            node_ids,
            _number(row["E"], "Q4 modulus"),
            _number(row["nu"], "Q4 Poisson ratio"),
            _number(row["thickness_m"], "Q4 thickness"),
            normal,
            density_kg_m3=(None if density is None else
                          _number(density, "Q4 density"))))

    if type(record["members"]) is not list or not record["members"]:
        raise ValueError("experimental physical B2 members required")
    members = []
    for item in record["members"]:
        fields = ("member_id", "source_id", "node_ids",
                            "reference_points_m", "local_y_global",
                            "section_id", "section", "profile_id",
                            "schema_id", "unit_system")
        row = _exact(item, fields + (("mass",) if with_mass else ()),
                     "physical B2 member")
        mass = None
        if with_mass and row["mass"] is not None:
            mass_row = _exact(row["mass"],
                              ("source_id", "origin_local_m", "matrix_per_m"),
                              "physical B2 mass")
            matrix = mass_row["matrix_per_m"]
            if type(matrix) is not list or len(matrix) != 6:
                raise ValueError("six-row physical B2 mass required")
            mass = stiffened_shell.MassMetadata(
                _identifier(mass_row["source_id"], "mass source ID"),
                _vector(mass_row["origin_local_m"], 3, "mass origin"),
                tuple(_vector(line, 6, "mass row") for line in matrix))
        section_row = _exact(row["section"],
                             ("operator_id", "kind", "stiffness",
                              "scalar_parameters"), "physical B2 section")
        stiffness = section_row["stiffness"]
        if type(stiffness) is not list or len(stiffness) != 6:
            raise ValueError("six-row physical B2 section required")
        scalar = section_row["scalar_parameters"]
        if scalar is not None:
            scalar = _vector(scalar, 8, "scalar section parameters")
        section = stiffened_shell.LinearSection(
            tuple(_vector(line, 6, "section row") for line in stiffness),
            _identifier(section_row["kind"], "section kind"),
            _identifier(section_row["operator_id"], "section operator ID"),
            scalar)
        points = row["reference_points_m"]
        if type(points) is not list or len(points) != 2:
            raise ValueError("two physical B2 reference positions required")
        members.append(stiffened_shell.PhysicalB2Member(
            _identifier(row["member_id"], "member ID"),
            _identifier(row["source_id"], "member source ID"),
            _vector(row["node_ids"], 2, "beam node IDs", integer=True),
            tuple(_vector(point, 3, "beam reference position")
                  for point in points),
            _vector(row["local_y_global"], 3, "beam local y"),
            _identifier(row["section_id"], "section ID"), section, mass=mass,
            profile_id=_identifier(row["profile_id"], "member profile"),
            schema_id=_identifier(row["schema_id"], "member schema"),
            unit_system=_identifier(row["unit_system"], "unit system")))

    if type(record["points"]) is not list or (not nodal and not record["points"]):
        raise ValueError("experimental B2 point attachments required")
    attachments = []
    for item in record["points"]:
        row = _exact(item, ("point_id", "shell_id", "member_id",
                            "station", "offset_local_m"), "B2 point")
        attachments.append(stiffened_shell.Q4B2Point(
            _identifier(row["point_id"], "point ID"),
            _identifier(row["shell_id"], "point shell ID"),
            _identifier(row["member_id"], "point member ID"),
            _number(row["station"], "point station"),
            _vector(row["offset_local_m"], 3, "point offset")))

    footprints = _decode_footprints(record["footprints"]) if nodal else ()

    affine = _exact(record["affine"],
                    ("shape", "indptr", "indices", "data", "offset"),
                    "B2 affine map")
    rows, columns = _vector(affine["shape"], 2, "affine shape", integer=True)
    if (not 0 < rows <= MAX_COORDINATES or not 0 < columns <= rows or
            type(affine["indptr"]) is not list or
            len(affine["indptr"]) != rows+1 or
            type(affine["indices"]) is not list or
            type(affine["data"]) is not list or
            len(affine["indices"]) != len(affine["data"]) or
            len(affine["data"]) > MAX_NONZEROS):
        raise ValueError("bounded B2 affine CSR map required")
    expected_coordinates = 6*(len(shell_nodes)+len({node_id for member in members
                                                   for node_id in member.node_ids}))
    if rows != expected_coordinates:
        raise ValueError("B2 affine shape does not match model nodes")
    indptr = np.array([_integer(v, "CSR pointer") for v in affine["indptr"]],
                      dtype=np.int64)
    indices = np.array([_integer(v, "CSR index") for v in affine["indices"]],
                       dtype=np.int64)
    data = np.array([_number(v, "CSR coefficient") for v in affine["data"]])
    if (indptr[0] != 0 or indptr[-1] != len(data) or
            np.any(np.diff(indptr) < 0) or np.any(indices >= columns)):
        raise ValueError("valid B2 affine CSR map required")
    T = sparse.csr_matrix((data, indices, indptr), shape=(rows, columns))
    offset = np.asarray(_vector(affine["offset"], rows, "affine offset"))
    definition_type = mixed_definition if mixed else stiffened_shell.B2Q4PanelDefinition
    definition = definition_type(
        record["profile"], shell_nodes, tuple(shells), tuple(members),
        tuple(attachments), T, offset, **({"footprints": footprints} if nodal else {}),
        **({"inertia_policy": record["inertia_policy"]} if mixed_modal else {}),
        **({"boundary_policy": record["modal_boundary_policy"]} if free_modal else {}),
        **({"point_trace_policy": record["point_trace_policy"]} if loaded else {}))

    if body:
        if any(shell.density_kg_m3 is None or shell.density_kg_m3 <= 0.
               for shell in shells) or any(member.mass is None for member in members):
            raise ValueError("body acceleration requires positive shell density and explicit physical member mass")
        for entries, name in ((shells, "shell_id"), (members, "member_id"), (attachments, "point_id")):
            ids = [getattr(entry, name) for entry in entries]
            if len(set(ids)) != len(ids):
                raise ValueError(f"unique body-acceleration {name} values required")
        hosts = {shell.shell_id for shell in shells}, {member.member_id for member in members}
        if any(point.shell_id not in hosts[0] or point.member_id not in hosts[1] for point in attachments):
            raise ValueError("known body-acceleration point host IDs required")

    if modal:
        if not free_modal and record["modal_boundary_policy"] != "supported":
            raise ValueError("B2 mixed reference modes require supported boundaries; free-free unsupported" if mixed_modal else
                             "B2 footprint reference modes require supported boundaries; free-free unsupported")
        _validate_modal_definition(definition, mixed=mixed_modal, free=free_modal)
        if mixed_modal:
            for entries, name in ((shells, "shell_id"), (members, "member_id"), (attachments, "point_id")):
                ids = [getattr(entry, name) for entry in entries]
                if len(set(ids)) != len(ids):
                    raise ValueError(f"unique mixed reference-modal {name} values required")
            hosts = {shell.shell_id for shell in shells}, {member.member_id for member in members}
            if any(point.shell_id not in hosts[0] or point.member_id not in hosts[1] for point in attachments):
                raise ValueError("known mixed reference-modal point host IDs required")
        request_type = getattr(stiffened_shell, "B2ReferenceModesRequest", None)
        if request_type is None:
            raise ValueError("installed solver lacks public typed B2 reference-mode controls")
        controls = _exact(record["request"], ("count", "shift_hz", "max_iterations"), "B2 modal request")
        return definition, request_type(**controls)

    if type(record["cases"]) is not list or not record["cases"]:
        raise ValueError("ordered B2 panel cases required")
    cases = []
    for item in record["cases"]:
        field = "acceleration_m_s2" if body else "load"
        row = _exact(item, ("id", field), "B2 panel case")
        cases.append((_identifier(row["id"], "case ID"),
                      (_vector(row[field], 3, "body acceleration") if body else
                       np.asarray(_vector(row[field], rows, "panel load")))))
    if len({name for name, _ in cases}) != len(cases):
        raise ValueError("unique B2 panel case IDs required")
    selection = _exact(record["selection"],
                       ("dofs", "points", "members") + (("footprints",) if nodal else ()),
                       "B2 selection")
    if nodal:
        _decode_footprint_selection(selection["footprints"], footprints)
    dofs = selection["dofs"]
    point_ids = selection["points"]
    recovery = selection["members"]
    if (type(dofs) is not list or not dofs or
            any(type(index) is not int or not 0 <= index < rows
                for index in dofs) or len(set(dofs)) != len(dofs) or
            type(point_ids) is not list or
            any(type(value) is not str for value in point_ids) or
            len(set(point_ids)) != len(point_ids) or
            type(recovery) is not dict):
        raise ValueError("selected B2 coordinates, points and members required")
    available_points = {point.point_id for point in attachments}
    available_members = {member.member_id for member in members}
    if (any(value not in available_points for value in point_ids) or
            any(value not in available_members for value in recovery)):
        raise ValueError("selected B2 identity is not in model")
    stations = {}
    for member_id, values in recovery.items():
        if type(values) is not list or not values:
            raise ValueError("selected B2 stations required")
        parsed = tuple(_number(value, "recovery station") for value in values)
        if any(not 0. <= value <= 1. for value in parsed):
            raise ValueError("normalized B2 recovery stations required")
        stations[member_id] = parsed
    return definition, tuple(cases), tuple(dofs), tuple(point_ids), stations


def _mixed_api():
    definition = getattr(stiffened_shell, "B2MixedPanelDefinition", None)
    shell = getattr(stiffened_shell, "S3V2DPanelShell", None)
    if definition is None or shell is None:
        raise ValueError("installed solver lacks explicit public B2 mixed-panel inputs")
    return definition, shell


def _mixed_modal_api():
    names = ("B2MixedReferenceModalDefinition", "B2ReferenceModesRequest",
             "ExperimentalB2MixedReferenceModalCapabilities",
             "S3_V2D_TRANSLATIONAL_LUMPED_SECTION_AREAL_MASS_V1")
    exports = tuple(getattr(stiffened_shell, name, None) for name in names)
    if any(value is None for value in exports):
        raise ValueError("installed solver lacks public experimental B2 mixed reference-modal exports")
    return exports


def _mixed_free_modal_api():
    names = ("B2_FREE_FREE_MODAL_ONLY_V1", "B2FreeReferenceModesResult")
    exports = tuple(getattr(stiffened_shell, name, None) for name in names)
    if any(value is None for value in exports) or type(exports[0]) is not str:
        raise ValueError("installed solver lacks public experimental B2 free reference-modal exports")
    return exports


def _footprint_api():
    names = ("B2NodalRigidFootprint", "B2NodalRigidFootprintSelection",
             "ExperimentalB2Q4FootprintCapabilities", "ExperimentalB2Q4FootprintBatchResult",
             "B2NodalRigidFootprintWrench", "B2NodalFootprintNodeAction",
             "ExperimentalB2Q4FootprintSelectedCase")
    exports = tuple(getattr(stiffened_shell, name, None) for name in names)
    if any(value is None for value in exports):
        raise ValueError("installed solver lacks public experimental B2 nodal-footprint exports")
    return exports[:4]


def _decode_footprints(rows):
    group_type, *_ = _footprint_api()
    if type(rows) is not list or not rows:
        raise ValueError("explicit nonempty B2 nodal-footprint list required")
    groups = []
    for item in rows:
        row = _exact(item, ("law", "footprint_id", "shell_nodes", "beam_node",
            "patch_cells", "semantics", "configuration", "normal_spin", "production_qualified"),
            "B2 nodal footprint")
        if type(row["shell_nodes"]) is not list or type(row["patch_cells"]) is not list:
            raise ValueError("explicit B2 footprint node and Q4 cell lists required")
        cells = tuple(_vector(cell, 4, "B2 footprint cell", integer=True)
                      for cell in row["patch_cells"])
        nodes = tuple(_integer(node, "B2 footprint shell node", minimum=1)
                      for node in row["shell_nodes"])
        group = group_type(_identifier(row["footprint_id"], "B2 footprint ID"), nodes,
            _integer(row["beam_node"], "physical B2 endpoint", minimum=1), cells)
        expected = json.loads(json.dumps(group.descriptor(), allow_nan=False))
        if row != expected or type(row["production_qualified"]) is not bool:
            raise ValueError("matching public B2 reference-linear nodal-footprint descriptor required")
        groups.append(group)
    if len({g.footprint_id for g in groups}) != len(groups):
        raise ValueError("distinct B2 nodal-footprint IDs required")
    return tuple(groups)


def _decode_footprint_selection(values, groups):
    _, selection_type, *_ = _footprint_api()
    if type(values) is not list:
        raise ValueError("explicit selected B2 footprint ID list required")
    if not values:
        return None
    made = selection_type(tuple(values))
    available = {g.footprint_id for g in groups}
    if any(name not in available for name in made.footprint_ids):
        raise ValueError("selected B2 footprint ID is not declared")
    return made


def _validate_modal_definition(definition, *, mixed=False, free=False):
    """Validate input restrictions; physical and constraint admission stays owned by the solver."""
    if (any(shell.density_kg_m3 is None or shell.density_kg_m3 <= 0.
            for shell in definition.shells) or
            any(member.mass is None for member in definition.members)):
        raise ValueError("B2 mixed reference modes require explicit positive shell density and beam physical mass" if mixed else
                         "B2 footprint reference modes require explicit positive shell density and beam physical mass")
    if np.any(np.asarray(definition.prescribed_offset) != 0.):
        raise ValueError("virgin B2 mixed reference modes require zero prescribed offset" if mixed else
                         "virgin B2 footprint reference modes require zero prescribed offset")
    selector = definition.transformation.tocsr(copy=True)
    selector.sum_duplicates()
    selector.eliminate_zeros()
    if free:
        selector.sort_indices()
        size = selector.shape[0]
        if (selector.shape != (size, size) or
                not np.array_equal(selector.indptr, np.arange(size+1)) or
                not np.array_equal(selector.indices, np.arange(size)) or
                not np.array_equal(selector.data, np.ones(size))):
            raise ValueError("free B2 mixed reference modes require identity coordinates without supports or MPCs")
        return
    if (selector.shape[1] >= selector.shape[0] or
            np.any(np.diff(selector.tocsc().indptr) != 1) or
            np.any(np.diff(selector.indptr) > 1)):
        raise ValueError("supported B2 mixed reference modes require coordinate supports without MPCs" if mixed else
                         "supported B2 footprint reference modes require coordinate supports without MPCs")


@dataclass(frozen=True)
class ExperimentalB2PointPanel:
    """Immutable canonical input record; construction never solves the model."""

    payload: bytes

    def __post_init__(self):
        if type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES:
            raise ValueError("bounded experimental B2 panel bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") not in (SCHEMA, SCHEMA_MASS):
            raise ValueError("explicit historical B2 point-panel schema required")
        _decode(record)
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("ascii") + b"\n"
        object.__setattr__(self, "payload", canonical)

    @property
    def status(self):
        return STATUS

    @property
    def schema(self):
        return _json_load(self.payload)["schema"]

    @classmethod
    def from_definition(cls, definition, cases, *, selected_dofs,
                        selected_points=(), recover_members=None, schema=SCHEMA):
        if getattr(definition, "footprints", ()):
            raise ValueError("B2 nodal footprints require the explicit footprint consumer schema")
        return cls._from_definition(definition, cases, selected_dofs=selected_dofs,
            selected_points=selected_points, recover_members=recover_members, schema=schema)

    @classmethod
    def _from_definition(cls, definition, cases, *, selected_dofs,
                         selected_points=(), recover_members=None, schema=SCHEMA,
                         nodal=False, selected_footprints=None, request=None):
        loaded = schema in _LOADED_POINT_SCHEMAS
        body = schema in _BODY_SCHEMAS + (SCHEMA_LOADED_POINT_BODY,)
        if loaded:
            if (cls is not ExperimentalB2LoadedPointPanel or
                    type(definition) is not stiffened_shell.B2Q4PanelDefinition or
                    getattr(definition, "point_trace_policy", None) != LOADED_POINT_POLICY or
                    getattr(definition, "footprints", ())):
                raise ValueError("explicit Q4 loaded-point policy without footprints required")
        elif getattr(definition, "point_trace_policy", None) is not None:
            raise ValueError("loaded-point policy requires its explicit consumer schema")
        free_modal = schema == SCHEMA_MIXED_FREE_REFERENCE_MODAL
        mixed_modal = schema in _MIXED_MODAL_SCHEMAS
        modal = schema in (SCHEMA_REFERENCE_MODAL,) + _MIXED_MODAL_SCHEMAS
        mixed = schema in (SCHEMA_MIXED, SCHEMA_MIXED_BODY_ACCELERATION) + _MIXED_MODAL_SCHEMAS
        with_mass = body or loaded or schema in (SCHEMA_MASS, SCHEMA_REFERENCE_MODAL) + _MIXED_MODAL_SCHEMAS
        if schema not in ((SCHEMA_NODAL_FOOTPRINT, SCHEMA_REFERENCE_MODAL, SCHEMA_FOOTPRINT_BODY_ACCELERATION) if nodal else (SCHEMA, SCHEMA_MASS, SCHEMA_MIXED, SCHEMA_BODY_ACCELERATION, SCHEMA_MIXED_BODY_ACCELERATION) + _MIXED_MODAL_SCHEMAS + _LOADED_POINT_SCHEMAS):
            raise ValueError("experimental B2 panel schema required")
        definition_type, s3_type = (_mixed_api() if mixed else
                                    (stiffened_shell.B2Q4PanelDefinition, None))
        if mixed_modal:
            definition_type, _, _, policy = _mixed_modal_api()
            if (type(definition) is not definition_type or
                    type(definition.inertia_policy) is not str or definition.inertia_policy != policy):
                raise ValueError("explicit typed native S3 mixed reference-modal definition required")
            boundary = getattr(definition, "boundary_policy", None)
            expected_boundary = _mixed_free_modal_api()[0] if free_modal else None
            if boundary != expected_boundary or (boundary is not None and type(boundary) is not str):
                raise ValueError("mixed reference boundary policy requires its explicit consumer schema")
        consumer_type = (ExperimentalB2BodyAccelerationPanel if body else
                         ExperimentalB2MixedReferenceModalPanel if mixed_modal else ExperimentalB2MixedPointPanel)
        if mixed and (cls is not consumer_type or
                      type(definition) is not definition_type or
                      getattr(definition, "footprints", ()) or
                      any(type(shell) not in (stiffened_shell.Q4PanelShell, s3_type)
                          or type(shell.node_ids) is not tuple
                          or any(type(node) is not int for node in shell.node_ids)
                          for shell in definition.shells) or
                      any(type(point) is not stiffened_shell.Q4B2Point for point in definition.points) or
                      any(type(node) is not int or node <= 0 for node in definition.shell_nodes_m)):
            raise ValueError("explicit typed mixed B2 point-panel inputs without footprints required")
        if (not with_mass and
                type(definition) is definition_type and
                any(getattr(shell, "density_kg_m3", None) is not None
                    for shell in definition.shells)):
            raise ValueError("shell density is unsupported by experimental B2 input persistence")
        if (type(definition) is not definition_type or
                definition.profile != stiffened_shell.B2_E4_PL_FAST_LINEAR_V1 or
                any(type(member) is not stiffened_shell.PhysicalB2Member or
                    (not with_mass and member.mass is not None) or
                    member.geometric is not None
                    for member in definition.members)):
            raise ValueError("elastic physical B2 mixed-point-panel inputs required" if mixed
                             else "elastic physical B2/Q4 point-panel inputs required")
        T = definition.transformation.tocsr(copy=True)
        T.sum_duplicates()
        T.sort_indices()
        record = {
            "schema": schema, "status": STATUS, "profile": definition.profile,
            "shell_nodes": [[int(key), list(value)] for key, value in
                            sorted(definition.shell_nodes_m.items())],
            "shells": [{"shell_id": shell.shell_id,
                        "node_ids": list(shell.node_ids), "E": shell.E,
                        "nu": shell.nu, "thickness_m": shell.thickness_m,
                        "reference_normal": list(shell.reference_normal)}
                       for shell in definition.shells],
            "members": [{"member_id": member.member_id,
                         "source_id": member.source_id,
                         "node_ids": list(member.node_ids),
                         "reference_points_m": [list(v) for v in
                                                member.reference_points_m],
                         "local_y_global": list(member.local_y_global),
                         "section_id": member.section_id,
                         "section": {"operator_id": member.section.operator_id,
                                     "kind": member.section.kind,
                                     "stiffness": [list(v) for v in
                                                   member.section.stiffness],
                                     "scalar_parameters": (
                                         None if member.section.scalar_parameters is None
                                         else list(member.section.scalar_parameters))},
                         "profile_id": member.profile_id,
                         "schema_id": member.schema_id,
                         "unit_system": member.unit_system}
                        for member in definition.members],
            "points": [{"point_id": point.point_id,
                        "shell_id": point.shell_id,
                        "member_id": point.member_id,
                        "station": point.station,
                        "offset_local_m": list(point.offset_local_m)}
                       for point in definition.points],
            "affine": {"shape": list(T.shape),
                       "indptr": T.indptr.tolist(),
                       "indices": T.indices.tolist(),
                       "data": T.data.tolist(),
                       "offset": np.asarray(
                           definition.prescribed_offset).tolist()},
            "cases": [{"id": name, "acceleration_m_s2" if body else "load":
                       np.asarray(load, dtype=object if body else None).tolist()}
                      for name, load in cases],
            "selection": {"dofs": list(selected_dofs),
                          "points": list(selected_points),
                          "members": {} if recover_members is None else
                          {name: list(values) for name, values in
                           recover_members.items()}},
        }
        if loaded:
            record["point_trace_policy"] = LOADED_POINT_POLICY
        if mixed:
            for row, shell in zip(record["shells"], definition.shells):
                row["formulation"] = "e4-pl-s3-v2d" if type(shell) is s3_type else "e4-pl"
        if mixed_modal:
            record["inertia_policy"] = definition.inertia_policy
        if with_mass:
            for row, shell in zip(record["shells"], definition.shells):
                row["density_kg_m3"] = shell.density_kg_m3
            for row, member in zip(record["members"], definition.members):
                row["mass"] = (None if member.mass is None else {
                    "source_id": member.mass.source_id,
                    "origin_local_m": list(member.mass.origin_local_m),
                    "matrix_per_m": [list(v) for v in member.mass.matrix_per_m],
                })
        if nodal:
            group_type, selection_type, *_ = _footprint_api()
            groups = getattr(definition, "footprints", ())
            if not groups or any(type(g) is not group_type for g in groups):
                raise ValueError("explicit nonempty public B2 nodal footprints required")
            if selected_footprints is not None and type(selected_footprints) is not selection_type:
                raise ValueError("typed public B2 footprint recovery selection required")
            record["footprints"] = [group.descriptor() for group in groups]
            record["selection"]["footprints"] = (list(selected_footprints.footprint_ids)
                if selected_footprints is not None else [])
        if modal:
            request_type = getattr(stiffened_shell, "B2ReferenceModesRequest", None)
            if request_type is None or type(request) is not request_type:
                raise ValueError("public typed B2 reference-mode controls required")
            record.pop("cases")
            record.pop("selection")
            record.update(request=asdict(request), modal_boundary_policy=(
                _mixed_free_modal_api()[0] if free_modal else "supported"))
        return cls(json.dumps(record, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("ascii"))

    def create_analysis(self):
        definition, *_ = _decode(_json_load(self.payload))
        return stiffened_shell.create_analysis(definition=definition)

    def solve_linear_many(self, *, cancellation_token=None, on_case=None):
        definition, cases, dofs, points, recovery = _decode(
            _json_load(self.payload))
        owner = stiffened_shell.create_analysis(definition=definition)
        try:
            return owner.solve_linear_many(
                cases, selected_dofs=dofs, selected_points=points,
                recover_members=recovery,
                cancellation_token=cancellation_token, on_case=on_case)
        finally:
            owner.close()

    def modes(self, request: stiffened_shell.B2ReferenceModesRequest, *,
              cancellation=None):
        """Delegate reference modes; preserve admission and numerical status."""
        owner = self.create_analysis()
        try:
            return owner.modes(request, cancellation=cancellation)
        finally:
            owner.close()


@dataclass(frozen=True)
class ExperimentalB2MixedPointPanel(ExperimentalB2PointPanel):
    """Explicit static Q4/native S3 V2D inputs; no inertia or solver-state persistence."""

    def __post_init__(self):
        if type(self) is not ExperimentalB2MixedPointPanel or type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES:
            raise ValueError("exact bounded experimental B2 mixed-panel bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") != SCHEMA_MIXED:
            raise ValueError("explicit B2 mixed-point-panel schema required")
        _decode(record)
        object.__setattr__(self, "payload", json.dumps(record, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("ascii")+b"\n")

    @classmethod
    def from_definition(cls, definition, cases, *, selected_dofs,
                        selected_points=(), recover_members=None, schema=SCHEMA_MIXED):
        if schema != SCHEMA_MIXED:
            raise ValueError("explicit B2 mixed-point-panel schema required")
        return cls._from_definition(definition, cases, selected_dofs=selected_dofs,
            selected_points=selected_points, recover_members=recover_members, schema=schema)

    def to_dict(self):
        return _json_load(self.payload)

    @classmethod
    def from_dict(cls, record):
        return cls(json.dumps(record, allow_nan=False).encode("ascii"))

    def to_definition(self):
        """Reconstruct detached public solver inputs without assembling an owner."""
        return _decode(_json_load(self.payload))[0]

    def modes(self, request=None, *, cancellation=None):
        raise ValueError("mixed-panel consumer supports static batches only; mass and modes deferred")


@dataclass(frozen=True)
class ExperimentalB2NodalRigidFootprintPanel(ExperimentalB2PointPanel):
    """Explicit static B2 selected-node inputs; no mass, finite state or weld claim.

    Reopen validates owned input syntax without assembling an owner. Membership,
    overlap and supported row rank are checked by the public solver on creation.
    """

    def __post_init__(self):
        if type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES:
            raise ValueError("bounded experimental B2 footprint bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") != SCHEMA_NODAL_FOOTPRINT:
            raise ValueError("explicit experimental B2 nodal-footprint schema required")
        _decode(record)
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("ascii") + b"\n"
        object.__setattr__(self, "payload", canonical)

    @classmethod
    def from_definition(cls, definition, cases, *, selected_dofs,
                        selected_points=(), recover_members=None,
                        selected_footprints=None, schema=SCHEMA_NODAL_FOOTPRINT):
        return cls._from_definition(definition, cases, selected_dofs=selected_dofs,
            selected_points=selected_points, recover_members=recover_members,
            schema=schema, nodal=True, selected_footprints=selected_footprints)

    def create_analysis(self):
        definition, *_ = _decode(_json_load(self.payload))
        _, _, capability_type, _ = _footprint_api()
        owner = stiffened_shell.create_analysis(definition=definition)
        try:
            cap = getattr(owner, "capabilities", None)
            if (type(cap) is not capability_type or cap.profile_id != definition.profile or
                    cap.status != "experimental" or cap.production_qualified is not False or
                    cap.footprint_law_id != definition.footprints[0].descriptor()["law"] or
                    cap.footprints != definition.footprints or
                    set(cap.operations) != {"solve_linear", "solve_linear_many", "close"}):
                raise ValueError("installed solver lacks the matching static B2 footprint capability")
            return owner
        except BaseException:
            owner.close()
            raise

    def solve_linear_many(self, *, cancellation_token=None, on_case=None):
        record = _json_load(self.payload)
        definition, cases, dofs, points, recovery = _decode(record)
        selection = _decode_footprint_selection(record["selection"]["footprints"], definition.footprints)
        owner = self.create_analysis()
        try:
            return owner.solve_linear_many(cases, selected_dofs=dofs,
                selected_points=points, selected_footprints=selection,
                recover_members=recovery, cancellation_token=cancellation_token,
                on_case=on_case)
        finally:
            owner.close()

    def modes(self, request, *, cancellation=None):
        raise ValueError("B2 nodal-footprint consumer is static only; modes and prestress unsupported")


@dataclass(frozen=True)
class ExperimentalB2ReferenceModalPanel(ExperimentalB2PointPanel):
    """Virgin supported footprint mode inputs with explicit physical inertia.

    The reference-linear footprint locks normal spin and remains mechanically
    unqualified. Persistence never assembles or factors an owner. Free-free,
    prestress, MPC and nonzero prescribed offsets are unsupported.
    """

    def __post_init__(self):
        if type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES:
            raise ValueError("bounded experimental B2 reference-modal bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") != SCHEMA_REFERENCE_MODAL:
            raise ValueError("explicit B2 footprint reference-modal schema required")
        _decode(record)
        object.__setattr__(self, "payload", json.dumps(record, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n")

    @classmethod
    def from_definition(cls, definition, request):
        return cls._from_definition(definition, (), selected_dofs=(),
            schema=SCHEMA_REFERENCE_MODAL, nodal=True, request=request)

    @classmethod
    def from_dict(cls, record):
        return cls(json.dumps(record, allow_nan=False).encode("ascii"))

    def to_dict(self):
        return _json_load(self.payload)

    def definition(self):
        return _decode(_json_load(self.payload))[0]

    @property
    def request(self):
        return _decode(_json_load(self.payload))[1]

    def create_analysis(self):
        definition = self.definition()
        _, _, capability_type, _ = _footprint_api()
        owner = stiffened_shell.create_analysis(definition=definition)
        try:
            cap = getattr(owner, "capabilities", None)
            if (type(cap) is not capability_type or cap.profile_id != definition.profile or
                    cap.status != "experimental" or cap.production_qualified is not False or
                    cap.footprint_law_id != definition.footprints[0].descriptor()["law"] or
                    cap.footprints != definition.footprints or "modes" not in cap.operations or
                    not callable(getattr(owner, "modes", None))):
                raise ValueError("installed solver does not admit supported B2 footprint reference modes")
            return owner
        except BaseException:
            owner.close()
            raise

    def modes(self, *, cancellation_token=None):
        owner = self.create_analysis()
        try:
            return owner.modes(self.request, cancellation=cancellation_token)
        finally:
            owner.close()

    def solve_linear_many(self, *, cancellation_token=None, on_case=None):
        raise ValueError("B2 reference-modal consumer supports modes only")


@dataclass(frozen=True)
class ExperimentalB2MixedReferenceModalPanel(ExperimentalB2PointPanel):
    """Virgin mixed reference-mode inputs; persistence is not a checkpoint.

    Native S3 facets omit all rotary inertia. Shared assembled nodes may also
    receive Q4 director inertia; omission is a facet policy, not a node claim.
    """

    def __post_init__(self):
        if type(self) is not ExperimentalB2MixedReferenceModalPanel or type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES:
            raise ValueError("exact bounded experimental B2 mixed reference-modal bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") not in _MIXED_MODAL_SCHEMAS:
            raise ValueError("explicit B2 mixed reference-modal schema required")
        _decode(record)
        object.__setattr__(self, "payload", json.dumps(record, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("ascii")+b"\n")

    @classmethod
    def from_definition(cls, definition, request):
        boundary = getattr(definition, "boundary_policy", None)
        schema = SCHEMA_MIXED_REFERENCE_MODAL
        if boundary is not None:
            if type(boundary) is not str or boundary != _mixed_free_modal_api()[0]:
                raise ValueError("explicit public B2 free-free modal-only boundary policy required")
            schema = SCHEMA_MIXED_FREE_REFERENCE_MODAL
        return cls._from_definition(definition, (), selected_dofs=(),
            schema=schema, request=request)

    @classmethod
    def from_dict(cls, record):
        return cls(json.dumps(record, allow_nan=False).encode("ascii"))

    def to_dict(self):
        return _json_load(self.payload)

    def definition(self):
        return _decode(_json_load(self.payload))[0]

    @property
    def request(self):
        return _decode(_json_load(self.payload))[1]

    def create_analysis(self):
        definition = self.definition()
        _, _, capability_type, policy = _mixed_modal_api()
        free = self.schema == SCHEMA_MIXED_FREE_REFERENCE_MODAL
        boundary = _mixed_free_modal_api()[0] if free else None
        owner = stiffened_shell.create_analysis(definition=definition)
        try:
            cap = getattr(owner, "capabilities", None)
            q4_policy = getattr(stiffened_shell, "Q4_DIRECTOR_MASS_POLICY", None)
            if q4_policy is None and any(type(shell) is stiffened_shell.Q4PanelShell for shell in definition.shells):
                raise ValueError("installed solver lacks public Q4 director mass-policy identity")
            expected_policies = tuple((shell.shell_id, policy if type(shell) is stiffened_shell.S3V2DPanelShell else q4_policy)
                                      for shell in definition.shells)
            if (type(cap) is not capability_type or cap.profile_id != definition.profile or
                    cap.status != "experimental" or cap.production_qualified is not False or
                    cap.inertia_policy != definition.inertia_policy or cap.shell_mass_policies != expected_policies or
                    cap.s3_rotary_inertia != capability_type().s3_rotary_inertia or
                    cap.beam_mass_policy != capability_type().beam_mass_policy or
                    cap.footprints != () or cap.footprint_law_id is not None or
                    getattr(cap, "boundary_policy", None) != boundary or
                    (free and (cap.operations != ("modes", "close") or
                               not callable(getattr(owner, "close", None)))) or
                    "modes" not in cap.operations or not callable(getattr(owner, "modes", None))):
                raise ValueError("installed solver does not admit the declared mixed reference modes/inertia policies")
            return owner
        except BaseException:
            closer = getattr(owner, "close", None)
            if callable(closer):
                closer()
            raise

    def modes(self, *, cancellation_token=None):
        owner = self.create_analysis()
        try:
            result = owner.modes(self.request, cancellation=cancellation_token)
            if self.schema == SCHEMA_MIXED_FREE_REFERENCE_MODAL:
                policy, result_type = _mixed_free_modal_api()
                flexible_type = getattr(stiffened_shell, "B2ReferenceModesResult", None)
                if (type(result) is not result_type or result.policy != policy or
                        result.production_qualified is not False or
                        type(result.flexible) is not flexible_type or
                        result.flexible.production_qualified is not False):
                    raise ValueError("installed solver returned an incompatible public B2 free reference-mode result")
            return result
        finally:
            owner.close()

    def solve_linear_many(self, *, cancellation_token=None, on_case=None):
        raise ValueError("B2 mixed reference-modal consumer supports modes only")


@dataclass(frozen=True)
class ExperimentalB2BodyAccelerationPanel(ExperimentalB2PointPanel):
    """Reference-linear uniform global acceleration inputs in m/s2.

    Physical shell density and member mass are explicit inputs. Expansion and
    model binding belong to the fresh public solver owner. Typed body loads keep
    equilibrated sided section fields separate from nodal constitutive recovery
    and energy. Loaded interior pose, finite gravity and qualification are not
    provided by this experimental consumer.
    """

    def __post_init__(self):
        if (type(self) is not ExperimentalB2BodyAccelerationPanel or
                type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES):
            raise ValueError("exact bounded experimental B2 body-acceleration bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") not in _BODY_SCHEMAS:
            raise ValueError("explicit B2 body-acceleration schema required")
        _decode(record)
        object.__setattr__(self, "payload", json.dumps(record, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("ascii")+b"\n")

    @classmethod
    def from_definition(cls, definition, cases, *, selected_dofs,
                        selected_points=(), recover_members=None,
                        selected_footprints=None):
        mixed_type = getattr(stiffened_shell, "B2MixedPanelDefinition", None)
        nodal = bool(getattr(definition, "footprints", ()))
        mixed = mixed_type is not None and type(definition) is mixed_type
        if type(definition) is not stiffened_shell.B2Q4PanelDefinition and not mixed:
            raise ValueError("explicit public static B2 point or Q4 footprint definition required")
        if any(type(node) is not int or node <= 0 for node in definition.shell_nodes_m):
            raise ValueError("positive integer body-acceleration shell node IDs required")
        if selected_footprints is not None and not nodal:
            raise ValueError("selected footprints require a Q4 nodal-footprint definition")
        schema = (SCHEMA_FOOTPRINT_BODY_ACCELERATION if nodal else
                  SCHEMA_MIXED_BODY_ACCELERATION if mixed else SCHEMA_BODY_ACCELERATION)
        return cls._from_definition(definition, cases, selected_dofs=selected_dofs,
            selected_points=selected_points, recover_members=recover_members,
            schema=schema, nodal=nodal, selected_footprints=selected_footprints)

    @classmethod
    def from_dict(cls, record):
        return cls(json.dumps(record, allow_nan=False).encode("ascii"))

    def to_dict(self):
        return _json_load(self.payload)

    def to_definition(self):
        """Reconstruct detached public inputs without assembly or solve."""
        return _decode(self.to_dict())[0]

    @property
    def acceleration_cases(self):
        return _decode(self.to_dict())[1]

    def create_analysis(self):
        owner = stiffened_shell.create_analysis(definition=self.to_definition())
        try:
            required_exports = ("B2ReferenceBodyLoad", "B2LoadedMemberSections",
                "B2BodyLoadedPanelResult", "B2BodyLoadedFootprintResult",
                "B2BodyLoadedPanelSelectedCase", "B2BodyLoadedFootprintSelectedCase")
            missing = tuple(name for name in required_exports
                            if not isinstance(getattr(stiffened_shell, name, None), type))
            if missing:
                raise ValueError("installed solver lacks public typed B2 body-load recovery exports: "
                                 + ", ".join(missing))
            cap = getattr(owner, "capabilities", None)
            if (not callable(getattr(owner, "body_acceleration_load", None)) or
                    cap is None or cap.status != "experimental" or
                    cap.production_qualified is not False or
                    "body_acceleration_load" not in cap.operations):
                raise ValueError("installed solver lacks public reference-linear B2 body-load capability")
            return owner
        except BaseException:
            owner.close()
            raise

    def solve_linear_many(self, *, cancellation_token=None, on_case=None):
        record = self.to_dict()
        definition, cases, dofs, points, recovery = _decode(record)
        owner = self.create_analysis()
        try:
            loads = tuple((name, owner.body_acceleration_load(acceleration))
                          for name, acceleration in cases)
            options = {}
            if definition.footprints:
                options["selected_footprints"] = _decode_footprint_selection(
                    record["selection"]["footprints"], definition.footprints)
            return owner.solve_linear_many(loads, selected_dofs=dofs,
                selected_points=points, recover_members=recovery,
                cancellation_token=cancellation_token, on_case=on_case, **options)
        finally:
            owner.close()

    def modes(self, request=None, *, cancellation=None, cancellation_token=None):
        raise ValueError("body-acceleration consumer supports reference-linear batches only; modes and finite gravity unsupported")


@dataclass(frozen=True)
class ExperimentalB2LoadedPointPanel(ExperimentalB2BodyAccelerationPanel):
    """Detached experimental Q4 loaded-point inputs, not a qualified project.

    The raw schema preserves nodal load vectors. Only the body schema expands
    declared accelerations to fresh owner-bound distributed loads.
    """

    def __post_init__(self):
        if (type(self) is not ExperimentalB2LoadedPointPanel or
                type(self.payload) is not bytes or len(self.payload) > MAX_FILE_BYTES):
            raise ValueError("exact bounded experimental B2 loaded-point bytes required")
        record = _json_load(self.payload)
        if type(record) is not dict or record.get("schema") not in _LOADED_POINT_SCHEMAS:
            raise ValueError("explicit B2 loaded-point schema required")
        _decode(record)
        object.__setattr__(self, "payload", json.dumps(record, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("ascii")+b"\n")

    @classmethod
    def from_definition(cls, definition, cases, *, selected_dofs,
                        selected_points=(), recover_members=None, body=False):
        if type(body) is not bool:
            raise ValueError("explicit boolean body-load selection required")
        return cls._from_definition(definition, cases, selected_dofs=selected_dofs,
            selected_points=selected_points, recover_members=recover_members,
            schema=SCHEMA_LOADED_POINT_BODY if body else SCHEMA_LOADED_POINT)

    @property
    def acceleration_cases(self):
        if self.schema != SCHEMA_LOADED_POINT_BODY:
            raise ValueError("raw nodal cases are not acceleration cases")
        return _decode(self.to_dict())[1]

    def create_analysis(self):
        owner = stiffened_shell.create_analysis(definition=self.to_definition())
        try:
            cap = getattr(owner, "capabilities", None)
            if (cap is None or cap.status != "experimental" or
                    cap.production_qualified is not False or
                    getattr(cap, "point_trace_policy", None) != LOADED_POINT_POLICY or
                    "solve_linear_many" not in cap.operations or
                    not callable(getattr(owner, "solve_linear_many", None))):
                raise ValueError("installed solver lacks explicit experimental B2 loaded-point capability")
            if (self.schema == SCHEMA_LOADED_POINT_BODY and
                    ("body_acceleration_load" not in cap.operations or
                     not callable(getattr(owner, "body_acceleration_load", None)))):
                raise ValueError("installed solver lacks B2 loaded-point body-load capability")
            return owner
        except BaseException:
            owner.close()
            raise

    def solve_linear_many(self, *, cancellation_token=None, on_case=None):
        _, cases, dofs, points, recovery = _decode(self.to_dict())
        owner = self.create_analysis()
        try:
            if self.schema == SCHEMA_LOADED_POINT_BODY:
                cases = tuple((name, owner.body_acceleration_load(acceleration))
                              for name, acceleration in cases)
            return owner.solve_linear_many(cases, selected_dofs=dofs,
                selected_points=points, recover_members=recovery,
                cancellation_token=cancellation_token, on_case=on_case)
        finally:
            owner.close()

    def modes(self, *args, **kwargs):
        raise ValueError("loaded-point consumer supports reference-linear batches only")


def save_case(case: ExperimentalB2PointPanel, path: str | Path) -> Path:
    if type(case) not in (ExperimentalB2PointPanel, ExperimentalB2MixedPointPanel, ExperimentalB2NodalRigidFootprintPanel, ExperimentalB2ReferenceModalPanel, ExperimentalB2MixedReferenceModalPanel, ExperimentalB2BodyAccelerationPanel, ExperimentalB2LoadedPointPanel):
        raise ValueError("experimental B2 panel case required")
    destination = Path(path)
    if not destination.name.endswith(SUFFIX):
        raise ValueError(f"experimental B2 panel filename must end with {SUFFIX}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                         prefix=f".{destination.name}.",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(case.payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def load_case(path: str | Path) -> ExperimentalB2PointPanel:
    source = Path(path)
    if not source.name.endswith(SUFFIX):
        raise ValueError(f"experimental B2 panel filename must end with {SUFFIX}")
    with source.open("rb") as stream:
        payload = stream.read(MAX_FILE_BYTES + 1)
    record = _json_load(payload)
    schema = record.get("schema") if type(record) is dict else None
    consumer = (ExperimentalB2LoadedPointPanel if schema in _LOADED_POINT_SCHEMAS else
                ExperimentalB2BodyAccelerationPanel if schema in _BODY_SCHEMAS else
                ExperimentalB2ReferenceModalPanel if schema == SCHEMA_REFERENCE_MODAL else
                ExperimentalB2MixedReferenceModalPanel if schema in _MIXED_MODAL_SCHEMAS else
                ExperimentalB2MixedPointPanel if schema == SCHEMA_MIXED else
                ExperimentalB2NodalRigidFootprintPanel if schema == SCHEMA_NODAL_FOOTPRINT else ExperimentalB2PointPanel)
    return consumer(payload)


__all__ = ["ExperimentalB2PointPanel", "SCHEMA", "SCHEMA_MASS", "STATUS", "SUFFIX",
           "ExperimentalB2MixedPointPanel", "SCHEMA_MIXED",
           "ExperimentalB2MixedReferenceModalPanel", "SCHEMA_MIXED_REFERENCE_MODAL",
           "SCHEMA_MIXED_FREE_REFERENCE_MODAL",
           "ExperimentalB2NodalRigidFootprintPanel", "SCHEMA_NODAL_FOOTPRINT",
           "ExperimentalB2ReferenceModalPanel", "SCHEMA_REFERENCE_MODAL",
           "ExperimentalB2BodyAccelerationPanel", "SCHEMA_BODY_ACCELERATION",
           "SCHEMA_MIXED_BODY_ACCELERATION", "SCHEMA_FOOTPRINT_BODY_ACCELERATION",
           "ExperimentalB2LoadedPointPanel", "SCHEMA_LOADED_POINT", "SCHEMA_LOADED_POINT_BODY",
           "save_case", "load_case"]
