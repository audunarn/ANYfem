"""Headless, explicitly experimental GE point-panel consumer.

This record stores inputs only.  Solver state, results, and qualification are
not inferred from reopening it.  Ordinary ANYfem projects are unaffected.
The Q4 algorithmic pullback is a separate experimental v3 opt-in; historical
v1 and J2 v2 records retain their original schema and solver selection.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field, fields as dataclass_fields, replace
import json
import math
import os
from pathlib import Path
import tempfile

from anymaterial import DNVC208MaterialCurve
from anysolver import b3_ge, stiffened_shell


SCHEMA = "anyfem.experimental-ge-point-panel-v1"
SCHEMA_J2 = "anyfem.experimental-ge-point-panel-j2-v2"
SCHEMA_Q4_PULLBACK = "anyfem.experimental-ge-point-panel-q4-pullback-v3"
SCHEMA_CURVED_Q4 = "anyfem.experimental-ge-curved-q4-point-panel-v1"
SCHEMA_CURVED_Q4_MATERIAL = "anyfem.experimental-ge-curved-q4-point-panel-material-v2"
SCHEMA_CURVED_Q4_MATERIAL_NATIVE = "anyfem.experimental-ge-curved-q4-point-panel-material-native-scale-v3"
SCHEMA_STAGED = "anyfem.experimental-ge-point-panel-staged-v4"
SCHEMA_CURVED_Q4_MATERIAL_STAGED = "anyfem.experimental-ge-curved-q4-point-panel-material-staged-v1"
SCHEMA_BODY_LOAD = "anyfem.experimental-ge-point-panel-body-load-v1"
SCHEMA_PRESSURE_LOAD = "anyfem.experimental-ge-point-panel-pressure-load-v1"
SCHEMA_DISTRIBUTED_LOAD = "anyfem.experimental-ge-point-panel-distributed-load-v1"
SCHEMA_DISTRIBUTED_PROGRAMME = "anyfem.experimental-ge-point-panel-distributed-programme-v1"
SCHEMA_BL5 = "anyfem.experimental-ge-bl5-panel-v1"
SCHEMA_BL5_STAGED_V2 = "anyfem.experimental-ge-bl5-panel-staged-v2"
SCHEMA_BL5_MATERIAL_V3 = "anyfem.experimental-ge-bl5-panel-material-v3"
SCHEMA_BL5_COUPLED_MATERIAL_V4 = "anyfem.experimental-ge-bl5-panel-coupled-material-v4"
SCHEMA_BL5_DISTRIBUTED = "anyfem.experimental-ge-bl5-panel-distributed-load-v1"
SCHEMA_BL5_DISTRIBUTED_PROGRAMME = "anyfem.experimental-ge-bl5-panel-distributed-programme-v1"
SCHEMA_NODAL_FOOTPRINT = "anyfem.experimental-ge-nodal-rigid-footprint-panel-v1"
SCHEMA_NODAL_FOOTPRINT_CHART_V2 = "anyfem.experimental-ge-nodal-rigid-footprint-chart-panel-v2"
LEGACY_FOOTPRINT_CHART_POLICY = "GE_NODAL_RIGID_FOOTPRINT_UNREBASED_ADMISSION_V1"
SCHEMA_REFERENCE_MODAL = "anyfem.experimental-ge-reference-modal-panel-v1"
SCHEMA_REFERENCE_DISTRIBUTING = "anyfem.experimental-ge-reference-distributing-panel-v1"
REFERENCE_DISTRIBUTING_LAW = "GE_FLAT_Q4_DISTRIBUTING_DIRECTOR_POLAR_REFERENCE_LINEAR_V1_EXPERIMENTAL"
REFERENCE_DISTRIBUTING_ADMISSION = "reference_linear_nodal_dead_actions_only"
STATUS = "EXPERIMENTAL_NOT_QUALIFIED"
SUFFIX = ".anyfem-ge.json"
MAX_FILE_BYTES = 32 * 1024 * 1024
HISTORICAL_Q4_PULLBACK_POLICY = "historical_corotational"


def _exact_fields(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise ValueError(f"strict {label} record required")
    return value


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _finite_number(value: object, label: str) -> int | float:
    if type(value) not in (int, float):
        raise ValueError(f"{label} must be finite numeric data")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be finite numeric data")
    return value


def _rows(value: object, size: int, item, label: str) -> tuple:
    if type(value) is not list:
        raise ValueError(f"{label} must be a list")
    result = []
    for row in value:
        if type(row) is not list or len(row) != size:
            raise ValueError(f"{label} entries must have {size} values")
        result.append(tuple(item(entry, label) for entry in row))
    return tuple(result)


def _ids(value: object, label: str) -> tuple[int, ...]:
    if type(value) is not list:
        raise ValueError(f"{label} must be a list")
    return tuple(_positive_int(item, label) for item in value)


def _domain(value: object) -> str:
    if value not in ("shell", "beam") or type(value) is not str:
        raise ValueError("shell or beam domain required")
    return value


def _component(value: object) -> int:
    if type(value) is not int or not 0 <= value < 6:
        raise ValueError("physical coordinate component 0..5 required")
    return value


def _wrench(value: object) -> tuple[int | float, ...]:
    if type(value) is not list or len(value) != 6:
        raise ValueError("six-component nodal action required")
    return tuple(_finite_number(v, "action wrench") for v in value)


def _point_components_payload(shell, beams, points, fixed, actions, q4_pullback_policy):
    shell_j2 = shell.j2_curve
    selected_q4 = q4_pullback_policy != HISTORICAL_Q4_PULLBACK_POLICY
    record = {
        "schema": (SCHEMA_Q4_PULLBACK if selected_q4 else
                   SCHEMA_J2 if shell_j2 is not None else SCHEMA),
        "status": STATUS,
        "profile": stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
        "shell": {
            "node_ids": list(shell.node_ids),
            "coordinates_m": [list(row) for row in shell.coordinates_m],
            "q4_elements": [list(row) for row in shell.q4_elements],
            "s3_elements": [list(row) for row in shell.s3_elements],
            "thickness_m": shell.thickness_m,
            "young_pa": shell.young_pa,
            "poisson": shell.poisson,
            "density_kg_m3": shell.density_kg_m3,
        },
        "beams": [
            {"raw_base64": base64.b64encode(beam.raw).decode("ascii"), "sha256": beam.sha256}
            for beam in beams
        ],
        "points": [asdict(point) for point in points],
        "fixed": [asdict(item) for item in fixed],
        "actions": [dict(domain=item.domain, node=item.node,
                         wrench=list(item.wrench)) for item in actions],
    }
    if selected_q4:
        record["q4_pullback_policy"] = q4_pullback_policy
        record["shell"]["j2_curve"] = (
            None if shell_j2 is None else shell_j2.as_dict())
    elif shell_j2 is not None:
        record["shell"]["j2_curve"] = shell_j2.as_dict()
    return record



def _parse_point_components(data, q4_policy):
    shell_fields = {"node_ids", "coordinates_m", "q4_elements", "s3_elements",
                    "thickness_m", "young_pa", "poisson", "density_kg_m3"}
    shell_j2 = data["schema"] in (SCHEMA_J2, SCHEMA_Q4_PULLBACK)
    shell_data = _exact_fields(data["shell"], shell_fields | ({"j2_curve"} if shell_j2 else set()), "GE shell")
    if data["schema"] == SCHEMA_J2 and shell_data["j2_curve"] is None:
        raise ValueError("shell J2 curve required by v2 schema")
    curve = None
    if shell_j2 and shell_data["j2_curve"] is not None:
        curve_data = _exact_fields(shell_data["j2_curve"],
                                   {"sigma_prop", "sigma_yield", "sigma_yield_2",
                                    "eps_p_y1", "eps_p_y2", "K", "n"},
                                   "shell J2 curve")
        curve = DNVC208MaterialCurve(**{
            name: _finite_number(value, "shell J2 curve")
            for name, value in curve_data.items()})
    shell = stiffened_shell.ShellPanel(
        _ids(shell_data["node_ids"], "shell nodes"),
        _rows(shell_data["coordinates_m"], 3, _finite_number, "shell coordinates"),
        _rows(shell_data["q4_elements"], 4, _positive_int, "Q4 elements"),
        _finite_number(shell_data["thickness_m"], "shell thickness"),
        _finite_number(shell_data["young_pa"], "shell Young modulus"),
        _finite_number(shell_data["poisson"], "shell Poisson ratio"),
        _finite_number(shell_data["density_kg_m3"], "shell density"),
        _rows(shell_data["s3_elements"], 3, _positive_int, "S3 elements"),
        curve,
    )

    if type(data["beams"]) is not list or not data["beams"]:
        raise ValueError("nonempty experimental GE beam list required")
    beams = []
    for row in data["beams"]:
        binding = _exact_fields(row, {"raw_base64", "sha256"}, "native beam binding")
        encoded, digest = binding["raw_base64"], binding["sha256"]
        if (type(encoded) is not str or len(encoded) > 2_800_000 or
                type(digest) is not str or len(digest) != 64):
            raise ValueError("bounded native beam binding required")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("invalid native beam encoding") from error
        beams.append(b3_ge.BeamDefinition.from_bytes(raw, expected_sha256=digest))

    if type(data["points"]) is not list or type(data["fixed"]) is not list or type(data["actions"]) is not list:
        raise ValueError("experimental GE point, fixed, and action lists required")
    points = tuple(stiffened_shell.PointConnection(
        _positive_int(row["shell_node"], "point shell node"),
        _positive_int(row["beam_node"], "point beam node"))
        for item in data["points"]
        for row in (_exact_fields(item, {"shell_node", "beam_node"}, "point connection"),))
    fixed = tuple(stiffened_shell.FixedCoordinate(
        _domain(row["domain"]), _positive_int(row["node"], "fixed node"),
        _component(row["component"]))
        for item in data["fixed"]
        for row in (_exact_fields(item, {"domain", "node", "component"}, "fixed coordinate"),))
    actions = tuple(stiffened_shell.NodalAction(
        _domain(row["domain"]), _positive_int(row["node"], "action node"),
        _wrench(row["wrench"]))
        for item in data["actions"]
        for row in (_exact_fields(item, {"domain", "node", "wrench"}, "nodal action"),))
    return shell, tuple(beams), points, fixed, actions, q4_policy



@dataclass(frozen=True)
class ExperimentalGEPointPanel:
    """One GE/Q4-S3 point-panel definition and admitted nodal actions."""

    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    points: tuple[stiffened_shell.PointConnection, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    actions: tuple[stiffened_shell.NodalAction, ...] = ()
    q4_pullback_policy: str = HISTORICAL_Q4_PULLBACK_POLICY
    load_programme: stiffened_shell.StagedDeadLoadProgramme | None = None
    body_load: stiffened_shell.GEBodyLoad | None = field(default=None, kw_only=True)
    distributed_load: stiffened_shell.GEBeamDistributedLoad | None = field(default=None, kw_only=True)
    pressure_load: stiffened_shell.GEShellPressureLoad | None = field(default=None, kw_only=True)
    distributed_programme: stiffened_shell.GEBeamDistributedProgramme | None = field(default=None, kw_only=True)
    shell_reference_policy: str = field(default="flat", kw_only=True)
    retained_scale_policy: str = field(default="profile", kw_only=True)

    def __post_init__(self) -> None:
        if type(self.shell) is not stiffened_shell.ShellPanel:
            raise ValueError("explicit GE shell panel required")
        if (self.shell.j2_curve is not None and
                type(self.shell.j2_curve) is not DNVC208MaterialCurve):
            raise ValueError("explicit isotropic shell J2 curve required")
        if (type(self.q4_pullback_policy) is not str or
                self.q4_pullback_policy not in (
                    HISTORICAL_Q4_PULLBACK_POLICY,
                    getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None))):
            raise ValueError("unsupported experimental Q4 pullback policy")
        if (self.q4_pullback_policy != HISTORICAL_Q4_PULLBACK_POLICY and
                not self.shell.q4_elements):
            raise ValueError("Q4 pullback policy requires at least one Q4 element")
        curved_policy = getattr(stiffened_shell, "CURVED_Q4_POINT_REFERENCE_V1", None)
        curved_material_policy = getattr(stiffened_shell,
                                         "CURVED_Q4_POINT_MATERIAL_REFERENCE_V1", None)
        if (type(self.retained_scale_policy) is not str or
                self.retained_scale_policy not in ("profile", "native_force_moment_roles_v1") or
                (self.retained_scale_policy != "profile" and (
                    self.shell_reference_policy != curved_material_policy or
                    "retained_scale_policy" not in stiffened_shell.LinearDefinition.__dataclass_fields__))):
            raise ValueError("native retained scaling requires the installed curved material point policy")
        if (type(self.shell_reference_policy) is not str or
                self.shell_reference_policy != "flat" and
                self.shell_reference_policy not in (curved_policy, curved_material_policy)):
            raise ValueError("unsupported installed GE shell reference policy")
        if self.shell_reference_policy != "flat":
            if (self.q4_pullback_policy != getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None) or
                    not self.shell.q4_elements or self.shell.s3_elements or
                    (self.shell_reference_policy == curved_policy and
                     self.shell.j2_curve is not None)):
                raise ValueError("curved point consumer requires admitted algorithmic Q4 material policy")
            if (any(value is not None for value in (self.body_load, self.distributed_load,
                    self.pressure_load, self.distributed_programme)) or
                    (self.load_programme is not None and
                     self.shell_reference_policy != curved_material_policy)):
                raise ValueError("curved point consumer admits fixed nodal actions only")
        for name, expected, minimum in (
            ("beams", b3_ge.BeamDefinition, 1),
            ("points", stiffened_shell.PointConnection, 1),
            ("fixed", stiffened_shell.FixedCoordinate, 1),
            ("actions", stiffened_shell.NodalAction, 0),
        ):
            rows = getattr(self, name)
            if type(rows) is not tuple or len(rows) < minimum or any(type(v) is not expected for v in rows):
                raise ValueError(f"explicit experimental GE {name} required")
        if self.shell_reference_policy != "flat" and any(
                any(value != 0. for value in action.wrench[3:]) for action in self.actions):
            raise ValueError("curved point consumer admits translational nodal actions only")
        if self.shell_reference_policy == curved_material_policy:
            for beam in self.beams:
                element, _ = beam.instantiate()
                if type(element.section) is not b3_ge.EllipsoidalGeneralizedSection:
                    raise ValueError("curved material point consumer requires generalized B3 sections")
        if self.distributed_programme is not None:
            programme_type = getattr(stiffened_shell, "GEBeamDistributedProgramme", None)
            if programme_type is None or type(self.distributed_programme) is not programme_type:
                raise ValueError("public experimental GE distributed programme required")
            if (self.actions or self.load_programme is not None or self.body_load is not None or
                    self.distributed_load is not None or self.pressure_load is not None):
                raise ValueError("distributed programme requires empty legacy actions and no other load")
            object.__setattr__(self, "distributed_programme",
                               programme_type.deserialize(self.distributed_programme.serialize()))
        if self.pressure_load is not None:
            pressure_type = getattr(stiffened_shell, "GEShellPressureLoad", None)
            if pressure_type is None or type(self.pressure_load) is not pressure_type:
                raise ValueError("installed solver requires public experimental GE pressure load")
            if (self.actions or self.load_programme is not None or
                    self.body_load is not None or self.distributed_load is not None):
                raise ValueError("GE pressure load requires empty legacy actions and no staged programme or body/distributed load")
            object.__setattr__(self, "pressure_load",
                               pressure_type.deserialize(self.pressure_load.serialize()))
        if self.distributed_load is not None:
            distributed_type = getattr(stiffened_shell, "GEBeamDistributedLoad", None)
            if distributed_type is None or type(self.distributed_load) is not distributed_type:
                raise ValueError("installed solver requires public experimental GE distributed load")
            if self.actions or self.load_programme is not None or self.body_load is not None:
                raise ValueError("GE distributed load requires empty legacy actions and no staged programme or body load")
            object.__setattr__(self, "distributed_load",
                               distributed_type.deserialize(self.distributed_load.serialize()))
        if self.body_load is not None:
            body_type = getattr(stiffened_shell, "GEBodyLoad", None)
            if body_type is None or type(self.body_load) is not body_type:
                raise ValueError("installed solver requires public experimental GE body load")
            if self.actions or self.load_programme is not None:
                raise ValueError("GE body load requires empty legacy actions and no staged programme")
            object.__setattr__(self, "body_load",
                               body_type.deserialize(self.body_load.serialize()))
        if self.load_programme is not None:
            programme_type = getattr(stiffened_shell, "StagedDeadLoadProgramme", None)
            if programme_type is None or type(self.load_programme) is not programme_type:
                raise ValueError("public experimental GE staged load programme required")
            if self.actions:
                raise ValueError("staged GE programme requires empty legacy actions")
            # Public owner framing owns policy, bounds and all future endpoints.
            object.__setattr__(self, "load_programme",
                               programme_type.deserialize(self.load_programme.serialize()))

    @property
    def status(self) -> str:
        return STATUS

    def definition(self) -> stiffened_shell.LinearDefinition:
        args = (
            stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            self.shell, self.beams, self.points, self.fixed,
        )
        if self.q4_pullback_policy == HISTORICAL_Q4_PULLBACK_POLICY:
            return stiffened_shell.LinearDefinition(*args)
        if self.shell_reference_policy != "flat":
            scale = ({} if self.retained_scale_policy == "profile" else
                     {"retained_scale_policy": self.retained_scale_policy})
            return stiffened_shell.LinearDefinition(*args,
                q4_pullback_policy=self.q4_pullback_policy,
                shell_reference_policy=self.shell_reference_policy, **scale)
        return stiffened_shell.LinearDefinition(
            *args, q4_pullback_policy=self.q4_pullback_policy)

    def create_analysis(self):
        """Construct the fresh experimental owner through its public facade."""
        if self.pressure_load is not None:
            pressure_type = getattr(stiffened_shell, "GEShellPressureLoad", None)
            if pressure_type is None or type(self.pressure_load) is not pressure_type:
                raise ValueError("installed solver requires public experimental GE pressure load")
        if self.distributed_load is not None:
            distributed_type = getattr(stiffened_shell, "GEBeamDistributedLoad", None)
            if distributed_type is None or type(self.distributed_load) is not distributed_type:
                raise ValueError("installed solver requires public experimental GE distributed load")
        if self.body_load is not None:
            body_type = getattr(stiffened_shell, "GEBodyLoad", None)
            if body_type is None or type(self.body_load) is not body_type:
                raise ValueError("installed solver requires public experimental GE body load")
        if self.distributed_programme is not None:
            programme_type = getattr(stiffened_shell, "GEBeamDistributedProgramme", None)
            if programme_type is None or type(self.distributed_programme) is not programme_type:
                raise ValueError("public experimental GE distributed programme required")
        owner = stiffened_shell.create_analysis(definition=self.definition())
        if self.distributed_programme is not None:
            try:
                if owner.preflight()["identity"] != self.distributed_programme.model_identity:
                    raise ValueError("distributed programme belongs to a different panel model")
            except BaseException:
                owner.close()
                raise
        if self.pressure_load is not None:
            try:
                if owner.preflight()["identity"] != self.pressure_load.model_identity:
                    raise ValueError("GE pressure load belongs to a different panel model")
            except BaseException:
                owner.close()
                raise
        if self.distributed_load is not None:
            try:
                if owner.preflight()["identity"] != self.distributed_load.model_identity:
                    raise ValueError("GE distributed load belongs to a different panel model")
            except BaseException:
                owner.close()
                raise
        if self.body_load is not None:
            try:
                if owner.preflight()["identity"] != self.body_load.model_identity:
                    raise ValueError("GE body load belongs to a different panel model")
            except BaseException:
                owner.close()
                raise
        if self.load_programme is not None:
            try:
                if owner.preflight()["identity"] != self.load_programme.model_identity:
                    raise ValueError("staged GE programme belongs to a different panel model")
            except BaseException:
                owner.close()
                raise
        return owner

    @property
    def load_input(self):
        """Explicit selected public solve input; legacy actions retain their meaning."""
        if self.distributed_programme is not None:
            return self.distributed_programme
        if self.pressure_load is not None:
            return self.pressure_load
        if self.distributed_load is not None:
            return self.distributed_load
        if self.body_load is not None:
            return self.body_load
        return self.actions if self.load_programme is None else self.load_programme

    def to_dict(self) -> dict:
        if self.distributed_programme is not None:
            return dict(schema=SCHEMA_DISTRIBUTED_PROGRAMME, status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, distributed_programme=None).to_dict(),
                        distributed_programme=self.distributed_programme.to_payload())
        if self.pressure_load is not None:
            return dict(schema=SCHEMA_PRESSURE_LOAD, status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, pressure_load=None).to_dict(),
                        pressure_load=self.pressure_load.to_payload())
        if self.distributed_load is not None:
            return dict(schema=SCHEMA_DISTRIBUTED_LOAD, status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, distributed_load=None).to_dict(),
                        distributed_load=self.distributed_load.to_payload())
        if self.body_load is not None:
            return dict(schema=SCHEMA_BODY_LOAD, status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, body_load=None).to_dict(),
                        body_load=self.body_load.to_payload())
        if self.load_programme is not None:
            return dict(schema=(SCHEMA_CURVED_Q4_MATERIAL_STAGED
                                if self.shell_reference_policy == getattr(
                                    stiffened_shell, "CURVED_Q4_POINT_MATERIAL_REFERENCE_V1", None)
                                else SCHEMA_STAGED), status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, load_programme=None).to_dict(),
                        load_programme=self.load_programme.to_payload())
        record = _point_components_payload(self.shell, self.beams, self.points,
                                          self.fixed, self.actions, self.q4_pullback_policy)
        if self.shell_reference_policy != "flat":
            record.update(schema=(SCHEMA_CURVED_Q4_MATERIAL
                                  if self.shell_reference_policy == getattr(
                                      stiffened_shell, "CURVED_Q4_POINT_MATERIAL_REFERENCE_V1", None)
                                  else SCHEMA_CURVED_Q4),
                           shell_reference_policy=self.shell_reference_policy)
            if self.retained_scale_policy != "profile":
                record.update(schema=SCHEMA_CURVED_Q4_MATERIAL_NATIVE,
                              retained_scale_policy=self.retained_scale_policy)
        return record

    @classmethod
    def from_dict(cls, value: object) -> ExperimentalGEPointPanel:
        if type(value) is not dict:
            raise ValueError("strict experimental GE panel record required")
        if value.get("schema") == SCHEMA_DISTRIBUTED_PROGRAMME:
            data = _exact_fields(value, {"schema", "status", "profile", "panel", "distributed_programme"},
                                 "distributed-programme GE point panel")
            if (data["status"] != STATUS or
                    data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    type(data["panel"]) is not dict or data["panel"].get("schema") not in
                    (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK)):
                raise ValueError("distributed programme requires an explicit legacy point input schema")
            programme_type = getattr(stiffened_shell, "GEBeamDistributedProgramme", None)
            if programme_type is None:
                raise ValueError("installed solver lacks the public experimental GE distributed programme")
            panel = cls.from_dict(data["panel"])
            return replace(panel, distributed_programme=programme_type.from_payload(data["distributed_programme"]))
        if value.get("schema") == SCHEMA_PRESSURE_LOAD:
            data = _exact_fields(value, {"schema", "status", "profile", "panel", "pressure_load"},
                                 "pressure-load GE point panel")
            if (data["status"] != STATUS or
                    data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    type(data["panel"]) is not dict or data["panel"].get("schema") not in
                    (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK)):
                raise ValueError("pressure-load GE point panel requires an explicit legacy point input schema")
            pressure_type = getattr(stiffened_shell, "GEShellPressureLoad", None)
            if pressure_type is None:
                raise ValueError("installed solver lacks the public experimental GE pressure load")
            panel = cls.from_dict(data["panel"])
            return replace(panel, pressure_load=pressure_type.from_payload(data["pressure_load"]))
        if value.get("schema") == SCHEMA_DISTRIBUTED_LOAD:
            data = _exact_fields(value, {"schema", "status", "profile", "panel", "distributed_load"},
                                 "distributed-load GE point panel")
            if (data["status"] != STATUS or
                    data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    type(data["panel"]) is not dict or data["panel"].get("schema") not in
                    (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK)):
                raise ValueError("distributed-load GE point panel requires an explicit legacy point input schema")
            distributed_type = getattr(stiffened_shell, "GEBeamDistributedLoad", None)
            if distributed_type is None:
                raise ValueError("installed solver lacks the public experimental GE distributed load")
            panel = cls.from_dict(data["panel"])
            return replace(panel, distributed_load=distributed_type.from_payload(data["distributed_load"]))
        if value.get("schema") == SCHEMA_BODY_LOAD:
            data = _exact_fields(value, {"schema", "status", "profile", "panel", "body_load"},
                                 "body-load GE point panel")
            if (data["status"] != STATUS or
                    data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    type(data["panel"]) is not dict or data["panel"].get("schema") not in
                    (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK)):
                raise ValueError("body-load GE point panel requires an explicit legacy point input schema")
            body_type = getattr(stiffened_shell, "GEBodyLoad", None)
            if body_type is None:
                raise ValueError("installed solver lacks the public experimental GE body load")
            panel = cls.from_dict(data["panel"])
            return replace(panel, body_load=body_type.from_payload(data["body_load"]))
        if value.get("schema") == SCHEMA_STAGED:
            data = _exact_fields(value, {"schema", "status", "profile", "panel", "load_programme"},
                                 "staged GE point panel")
            if (data["status"] != STATUS or
                    data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    type(data["panel"]) is not dict or data["panel"].get("schema") not in
                    (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK)):
                raise ValueError("staged GE point panel requires an explicit legacy point input schema")
            programme_type = getattr(stiffened_shell, "StagedDeadLoadProgramme", None)
            if programme_type is None:
                raise ValueError("installed solver lacks the public staged GE load programme")
            panel = cls.from_dict(data["panel"])
            programme = programme_type.from_payload(data["load_programme"])
            return replace(panel, load_programme=programme)
        if value.get("schema") == SCHEMA_CURVED_Q4_MATERIAL_STAGED:
            data = _exact_fields(value, {"schema", "status", "profile", "panel", "load_programme"},
                                 "curved material staged GE point panel")
            if (data["status"] != STATUS or
                    data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    type(data["panel"]) is not dict or data["panel"].get("schema") not in
                    (SCHEMA_CURVED_Q4_MATERIAL, SCHEMA_CURVED_Q4_MATERIAL_NATIVE)):
                raise ValueError("curved material staged panel requires its explicit material base record")
            programme_type = getattr(stiffened_shell, "StagedDeadLoadProgramme", None)
            if programme_type is None:
                raise ValueError("installed solver lacks the public staged GE load programme")
            panel = cls.from_dict(data["panel"])
            programme = programme_type.from_payload(data["load_programme"])
            return replace(panel, load_programme=programme)
        native = value.get("schema") == SCHEMA_CURVED_Q4_MATERIAL_NATIVE
        curved = value.get("schema") in (SCHEMA_CURVED_Q4, SCHEMA_CURVED_Q4_MATERIAL,
                                        SCHEMA_CURVED_Q4_MATERIAL_NATIVE)
        selected_q4 = value.get("schema") in (
            SCHEMA_Q4_PULLBACK, SCHEMA_CURVED_Q4, SCHEMA_CURVED_Q4_MATERIAL,
            SCHEMA_CURVED_Q4_MATERIAL_NATIVE)
        fields = {"schema", "status", "profile", "shell", "beams",
                  "points", "fixed", "actions"}
        data = _exact_fields(value, fields | ({"q4_pullback_policy"} if selected_q4 else set()) |
                             ({"shell_reference_policy"} if curved else set()) |
                             ({"retained_scale_policy"} if native else set()),
                             "experimental GE panel")
        if (data["schema"] not in (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK,
                                   SCHEMA_CURVED_Q4, SCHEMA_CURVED_Q4_MATERIAL,
                                   SCHEMA_CURVED_Q4_MATERIAL_NATIVE) or
                data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1):
            raise ValueError("experimental GE panel schema, status, and profile required")
        q4_policy = (data["q4_pullback_policy"] if selected_q4 else
                     HISTORICAL_Q4_PULLBACK_POLICY)
        if (selected_q4 and
                q4_policy != getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None)):
            raise ValueError("unsupported experimental Q4 pullback policy")

        if curved:
            policy_name = ("CURVED_Q4_POINT_MATERIAL_REFERENCE_V1"
                           if data["schema"] in (SCHEMA_CURVED_Q4_MATERIAL,
                                                 SCHEMA_CURVED_Q4_MATERIAL_NATIVE) else
                           "CURVED_Q4_POINT_REFERENCE_V1")
            policy = getattr(stiffened_shell, policy_name, None)
            if policy is None or data["shell_reference_policy"] != policy:
                raise ValueError("curved schema requires installed explicit curved reference policy")
            if native and data["retained_scale_policy"] != "native_force_moment_roles_v1":
                raise ValueError("native curved schema requires explicit native retained scaling")
            # Reuse strict Q4 component framing; the new outer schema carries
            # reference identity, and cannot reinterpret an old flat record.
            return cls(*_parse_point_components(dict(data, schema=SCHEMA_Q4_PULLBACK), q4_policy),
                       shell_reference_policy=policy,
                       retained_scale_policy=data["retained_scale_policy"] if native else "profile")
        return cls(*_parse_point_components(data, q4_policy))


@dataclass(frozen=True)
class ExperimentalGEReferenceModalPanel:
    """Definition-only supported or explicit free-free reference modal input.

    No accepted state, actions, material programme or precomputed identity is
    invented. When model_identity is supplied the public owner checks it at
    creation; absence means no precomputed solver-identity binding.
    """
    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    points: tuple[stiffened_shell.PointConnection, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    request: stiffened_shell.GEReferenceModesRequest
    modal_boundary_policy: str = field(default="supported", kw_only=True)
    q4_pullback_policy: str = field(default=HISTORICAL_Q4_PULLBACK_POLICY, kw_only=True)
    model_identity: str | None = field(default=None, kw_only=True)

    def __post_init__(self):
        request_type = getattr(stiffened_shell, "GEReferenceModesRequest", None)
        if request_type is None or type(self.request) is not request_type:
            raise ValueError("installed solver requires public typed GE reference-mode controls")
        free_policy = getattr(stiffened_shell, "GE_FREE_FREE_MODAL_ONLY_V1", None)
        if (type(self.modal_boundary_policy) is not str or
                self.modal_boundary_policy not in (("supported", free_policy) if free_policy else ("supported",))):
            raise ValueError("explicit supported or installed public free-free modal boundary policy required")
        if (self.model_identity is not None and
                (type(self.model_identity) is not str or len(self.model_identity) != 64 or
                 any(c not in "0123456789abcdef" for c in self.model_identity))):
            raise ValueError("optional model identity must be canonical 64 lowercase hexadecimal characters")
        if type(self.shell) is not stiffened_shell.ShellPanel:
            raise ValueError("explicit modal Q4 shell panel required")
        if (type(self.q4_pullback_policy) is not str or
                self.q4_pullback_policy not in (HISTORICAL_Q4_PULLBACK_POLICY,
                    getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None))):
            raise ValueError("unsupported experimental modal Q4 pullback policy")
        for name, expected, minimum in (("beams", b3_ge.BeamDefinition, 1),
                ("points", stiffened_shell.PointConnection, 1),
                ("fixed", stiffened_shell.FixedCoordinate, 0)):
            rows = getattr(self, name)
            if type(rows) is not tuple or len(rows) < minimum or any(type(v) is not expected for v in rows):
                raise ValueError(f"explicit experimental modal {name} required")
        # Reuse the pure, strict point input framing to own mutable shell lists
        # and native bindings. Empty supports are admitted only in this record.
        data = _point_components_payload(self.shell, self.beams, self.points,
                                         self.fixed, (), self.q4_pullback_policy)
        shell, beams, points, fixed, _, _ = _parse_point_components(data, self.q4_pullback_policy)
        if (not shell.q4_elements or shell.s3_elements or shell.j2_curve is not None or
                shell.density_kg_m3 <= 0. or shell.thickness_m <= 0.):
            raise ValueError("reference modes require pure elastic Q4 with explicit positive density and thickness")
        for beam in beams:
            element, _ = beam.instantiate()
            if type(element.section) is not b3_ge.EllipsoidalGeneralizedSection:
                raise ValueError("reference modes require explicit native generalized beam inertia")
        if (self.modal_boundary_policy == "supported" and not fixed) or (
                self.modal_boundary_policy != "supported" and fixed):
            raise ValueError("supported modes require supports; explicit free-free modal-only input requires empty supports")
        for name, value in (("shell", shell), ("beams", beams), ("points", points), ("fixed", fixed)):
            object.__setattr__(self, name, value)
        if len(json.dumps(self.to_dict(), allow_nan=False).encode("ascii")) > MAX_FILE_BYTES:
            raise ValueError("experimental modal input exceeds size limit")

    @property
    def status(self): return STATUS

    def definition(self):
        kwargs = dict(q4_pullback_policy=self.q4_pullback_policy)
        if self.modal_boundary_policy != "supported":
            kwargs["modal_boundary_policy"] = self.modal_boundary_policy
        return stiffened_shell.LinearDefinition(stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            self.shell, self.beams, self.points, self.fixed, **kwargs)

    @classmethod
    def from_definition(cls, definition, request, *, model_identity=None):
        if (type(definition) is not stiffened_shell.LinearDefinition or
                definition.profile != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                definition.lines or definition.footprints or
                getattr(definition, "distributing_footprints", ()) or
                getattr(definition, "shell_reference_policy", "flat") != "flat"):
            raise ValueError("exact flat GE six-pose point modal definition required; curved references, lines and footprints unsupported")
        return cls(definition.shell, tuple(definition.beams), tuple(definition.points),
            tuple(definition.fixed), request,
            modal_boundary_policy=getattr(definition, "modal_boundary_policy", "supported"),
            q4_pullback_policy=definition.q4_pullback_policy, model_identity=model_identity)

    def to_dict(self):
        record = _point_components_payload(self.shell, self.beams, self.points,
                                          self.fixed, (), self.q4_pullback_policy)
        record.pop("actions")
        record.update(schema=SCHEMA_REFERENCE_MODAL, q4_pullback_policy=self.q4_pullback_policy,
            modal_boundary_policy=self.modal_boundary_policy, model_identity=self.model_identity,
            request=asdict(self.request))
        return record

    @classmethod
    def from_dict(cls, value):
        data = _exact_fields(value, {"schema", "status", "profile", "shell", "beams", "points",
            "fixed", "q4_pullback_policy", "modal_boundary_policy", "model_identity", "request"},
            "experimental GE reference modal panel")
        if (data["schema"] != SCHEMA_REFERENCE_MODAL or data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1):
            raise ValueError("strict experimental GE reference-modal schema/status/profile required")
        request_type = getattr(stiffened_shell, "GEReferenceModesRequest", None)
        if request_type is None:
            raise ValueError("installed solver lacks public GE reference-mode controls")
        request = _exact_fields(data["request"], {"count", "shift_hz", "max_iterations"}, "modal request")
        core = dict(data, schema=(SCHEMA if data["q4_pullback_policy"] == HISTORICAL_Q4_PULLBACK_POLICY
                                 else SCHEMA_Q4_PULLBACK), actions=[])
        shell, beams, points, fixed, _, _ = _parse_point_components(core, data["q4_pullback_policy"])
        return cls(shell, beams, points, fixed, request_type(**request),
            modal_boundary_policy=data["modal_boundary_policy"],
            q4_pullback_policy=data["q4_pullback_policy"], model_identity=data["model_identity"])

    def create_analysis(self):
        owner = stiffened_shell.create_analysis(definition=self.definition())
        try:
            descriptor = getattr(owner, "reference_modal_capabilities", None)
            if (not callable(getattr(owner, "modes", None)) or type(descriptor) is not dict or
                    descriptor.get("operation") != "modes" or not descriptor.get("eligible")):
                raise ValueError("installed public owner does not admit this reference-modal input")
            preflight = owner.preflight()
            if (self.modal_boundary_policy != "supported" and
                    preflight.get("modal_boundary_policy") != self.modal_boundary_policy):
                raise ValueError("installed owner did not preserve explicit free-free modal policy")
            if self.model_identity is not None and preflight["identity"] != self.model_identity:
                raise ValueError("reference-modal input belongs to a different solver model identity")
            return owner
        except BaseException:
            owner.close()
            raise

    def modes(self, *, cancellation_token=None):
        owner = self.create_analysis()
        try: return owner.modes(self.request, cancellation_token=cancellation_token)
        finally: owner.close()


@dataclass(frozen=True)
class ExperimentalGEReferenceDistributingPanel:
    """Owned reference-linear inputs, consuming the public distributing owner.

    This is not a point-panel wrapper: an empty point list is preserved exactly.
    Host geometry, physical admission and all mechanics remain solver-owned.
    Saved input is neither an accepted-state checkpoint nor qualification.
    """
    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    points: tuple[stiffened_shell.PointConnection, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    actions: tuple[stiffened_shell.NodalAction, ...] = ()
    distributing_footprints: tuple = ()
    q4_pullback_policy: str = field(default=stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1)

    @staticmethod
    def _public_contract():
        aliases = tuple(getattr(stiffened_shell, name, None) for name in (
            "GEReferenceDistributingFootprint", "GEReferenceDistributingFootprintSelection",
            "GEReferenceDistributingFootprintWrench"))
        if (any(value is None for value in aliases) or
                getattr(stiffened_shell, "GE_DISTRIBUTING_REFERENCE_LINEAR_V1_EXPERIMENTAL", None) != REFERENCE_DISTRIBUTING_LAW):
            raise ValueError("installed solver lacks the declared public reference-linear distributing contract")
        return aliases

    def __post_init__(self):
        footprint_type, _, _ = self._public_contract()
        if (type(self.shell) is not stiffened_shell.ShellPanel or not self.shell.q4_elements or
                self.shell.s3_elements or self.shell.j2_curve is not None or
                self.q4_pullback_policy != stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1):
            raise ValueError("explicit elastic full-map Q4 reference-distributing shell inputs required")
        for values, expected, label in ((self.beams, b3_ge.BeamDefinition, "native beams"),
                (self.points, stiffened_shell.PointConnection, "points"),
                (self.fixed, stiffened_shell.FixedCoordinate, "supports"),
                (self.actions, stiffened_shell.NodalAction, "nodal dead actions"),
                (self.distributing_footprints, footprint_type, "distributing footprints")):
            if type(values) is not tuple or any(type(v) is not expected for v in values):
                raise ValueError(f"explicit typed tuple of {label} required")
        if not self.beams or not self.fixed or not self.distributing_footprints:
            raise ValueError("nonempty native beams, physical supports and distributing footprints required")
        groups = tuple(footprint_type(g.footprint_id, g.host_element_ids, g.beam_node_id, g.bounds_xy_m)
                       for g in self.distributing_footprints)
        if len({g.footprint_id for g in groups}) != len(groups):
            raise ValueError("duplicate distributing footprint ID")
        # Existing strict framing owns nested arrays/native bytes without assembly.
        try:
            data = json.loads(json.dumps(_point_components_payload(self.shell, self.beams,
                self.points, self.fixed, self.actions, self.q4_pullback_policy), allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise ValueError("finite strictly framed reference-distributing inputs required") from error
        owned = _parse_point_components(data, self.q4_pullback_policy)
        for name, value in zip(("shell", "beams", "points", "fixed", "actions", "q4_pullback_policy"), owned):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "distributing_footprints", groups)

    @property
    def status(self): return STATUS

    @property
    def load_input(self): return self.actions

    @property
    def recovery_selection(self):
        _, selection_type, _ = self._public_contract()
        return selection_type(tuple(group.footprint_id for group in self.distributing_footprints))

    @staticmethod
    def _validate_export(definition):
        expected = {"profile", "shell", "beams", "points", "fixed", "lines", "q4_pullback_policy",
                    "footprints", "modal_boundary_policy", "footprint_chart_policy", "bl5_material_policy",
                    "shell_reference_policy", "retained_scale_policy", "distributing_footprints"}
        if (type(definition) is not stiffened_shell.LinearDefinition or
                {item.name for item in dataclass_fields(definition)} != expected or
                definition.profile != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                definition.lines != () or definition.footprints != () or
                definition.modal_boundary_policy != "supported" or
                definition.footprint_chart_policy != LEGACY_FOOTPRINT_CHART_POLICY or
                definition.bl5_material_policy != "elastic" or definition.shell_reference_policy != "flat" or
                definition.retained_scale_policy != "profile"):
            raise ValueError("exact supported reference-linear distributing definition required; omitted policies/fields are unsupported")

    @classmethod
    def from_definition(cls, definition, actions=()):
        cls._validate_export(definition)
        return cls(definition.shell, definition.beams, definition.points, definition.fixed, actions,
                   definition.distributing_footprints, definition.q4_pullback_policy)

    def definition(self):
        definition = stiffened_shell.LinearDefinition(stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            self.shell, self.beams, self.points, self.fixed, lines=(), q4_pullback_policy=self.q4_pullback_policy,
            footprints=(), modal_boundary_policy="supported", footprint_chart_policy=LEGACY_FOOTPRINT_CHART_POLICY,
            bl5_material_policy="elastic", shell_reference_policy="flat", retained_scale_policy="profile",
            distributing_footprints=self.distributing_footprints)
        self._validate_export(definition)
        return definition

    def create_analysis(self):
        owner = stiffened_shell.create_analysis(definition=self.definition())
        try:
            caps, preflight = owner.capabilities, owner.preflight()
            if (type(caps) is not dict or caps.get("status") != STATUS or
                    caps.get("profile") != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    caps.get("distributing_footprint_law") != REFERENCE_DISTRIBUTING_LAW or
                    caps.get("analysis") != ("reference_linear",) or caps.get("loads") != ("nodal dead actions",) or
                    caps.get("checkpoints") != ("reference_linear",) or
                    caps.get("q4_force_map") != self.q4_pullback_policy or
                    caps.get("distributing_footprint_recovery") !=
                        "physical exerted reference-spatial actions, shell area-centroid and actual beam-node origins" or
                    not callable(getattr(owner, "recover", None)) or type(preflight) is not dict or
                    preflight.get("constraints") != 6*(len(self.points)+len(self.distributing_footprints)) or
                    preflight.get("retained_dofs") != 24*len(self.beams) or
                    type(preflight.get("identity")) is not str or len(preflight["identity"]) != 64):
                raise ValueError("public owner lacks the exact experimental reference-linear distributing/recovery admission")
            _ = self.recovery_selection
        except BaseException:
            owner.close()
            raise
        return owner

    def to_dict(self):
        record = _point_components_payload(self.shell, self.beams, self.points,
                                          self.fixed, self.actions, self.q4_pullback_policy)
        record.update(schema=SCHEMA_REFERENCE_DISTRIBUTING, law=REFERENCE_DISTRIBUTING_LAW,
            admission=REFERENCE_DISTRIBUTING_ADMISSION, distributing_footprints=[dict(
                law=REFERENCE_DISTRIBUTING_LAW, footprint_id=g.footprint_id,
                host_element_ids=list(g.host_element_ids), beam_node_id=g.beam_node_id,
                bounds_xy_m=list(g.bounds_xy_m)) for g in self.distributing_footprints])
        return record

    @classmethod
    def from_dict(cls, value):
        data = _exact_fields(value, {"schema", "status", "profile", "shell", "beams", "points", "fixed",
            "actions", "q4_pullback_policy", "law", "admission", "distributing_footprints"},
            "reference-distributing GE panel")
        if (data["schema"] != SCHEMA_REFERENCE_DISTRIBUTING or data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                data["law"] != REFERENCE_DISTRIBUTING_LAW or data["admission"] != REFERENCE_DISTRIBUTING_ADMISSION or
                data["q4_pullback_policy"] != stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1):
            raise ValueError("explicit reference-linear distributing schema/law/admission required")
        footprint_type, _, _ = cls._public_contract()
        if type(data["distributing_footprints"]) is not list or not data["distributing_footprints"]:
            raise ValueError("explicit nonempty distributing footprint list required")
        groups = []
        for item in data["distributing_footprints"]:
            row = _exact_fields(item, {"law", "footprint_id", "host_element_ids", "beam_node_id", "bounds_xy_m"},
                                "reference distributing footprint")
            if row["law"] != REFERENCE_DISTRIBUTING_LAW or type(row["bounds_xy_m"]) is not list or len(row["bounds_xy_m"]) != 4:
                raise ValueError("exact reference distributing law and four finite rectangle bounds required")
            groups.append(footprint_type(row["footprint_id"], _ids(row["host_element_ids"], "host element IDs"),
                _positive_int(row["beam_node_id"], "beam node ID"),
                tuple(_finite_number(v, "rectangle bounds") for v in row["bounds_xy_m"])))
        core = dict(data, schema=SCHEMA_Q4_PULLBACK)
        owned = _parse_point_components(core, data["q4_pullback_policy"])
        return cls(*owned[:5], tuple(groups), owned[5])


@dataclass(frozen=True)
class ExperimentalGENodalRigidFootprintPanel:
    """Explicit selected-node input, not an area weld or accepted solver state.

    The public owner admits geometry/rank and elastic candidate response. This
    consumer persists exact mesh-native inputs without assembling them. Its
    initial route excludes staged programmes, plastic/fibre sections and lines;
    V1 retains unrebased admission. V2 explicitly selects the public elastic
    increment-origin chart policy; all mechanics remain with the solver owner.
    """

    panel: ExperimentalGEPointPanel
    footprints: tuple[stiffened_shell.NodalRigidFootprint, ...]
    footprint_chart_policy: str = field(default=LEGACY_FOOTPRINT_CHART_POLICY, kw_only=True)

    def __post_init__(self) -> None:
        footprint_type = getattr(stiffened_shell, "NodalRigidFootprint", None)
        if footprint_type is None:
            raise ValueError("installed solver lacks public experimental nodal rigid footprints")
        chart_policy = getattr(stiffened_shell, "GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2", None)
        if (type(self.footprint_chart_policy) is not str or
                self.footprint_chart_policy != LEGACY_FOOTPRINT_CHART_POLICY and
                (chart_policy is None or self.footprint_chart_policy != chart_policy)):
            raise ValueError("explicit installed public nodal footprint chart policy required")
        if (type(self.panel) is not ExperimentalGEPointPanel or
                self.panel.shell_reference_policy != "flat" or
                self.panel.load_programme is not None or self.panel.body_load is not None or
                self.panel.distributed_load is not None or self.panel.pressure_load is not None or
                self.panel.distributed_programme is not None):
            raise ValueError("nodal footprint requires an explicit flat unstaged GE point panel without body load or distributed load or pressure load")
        # Own nested inputs using their existing strict public input framing.
        panel = ExperimentalGEPointPanel.from_dict(self.panel.to_dict())
        if self.footprint_chart_policy != LEGACY_FOOTPRINT_CHART_POLICY:
            # Structural input admission only; physical chart operations and
            # their independent support guard belong to the public owner.
            for node in panel.shell.node_ids:
                rotations = {item.component for item in panel.fixed
                             if item.domain == "shell" and item.node == node and item.component >= 3}
                if rotations and rotations != {3, 4, 5}:
                    raise ValueError("nodal footprint charts require full or free shell rotational triples")
        if panel.shell.j2_curve is not None:
            raise ValueError("nodal footprint requires an elastic shell")
        if (panel.shell.q4_elements and
                panel.q4_pullback_policy != getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None)):
            raise ValueError("nodal footprint requires explicitly selected full-map Q4")
        for beam in panel.beams:
            element, _ = beam.instantiate()
            if type(element.section) is not b3_ge.EllipsoidalGeneralizedSection:
                raise ValueError("nodal footprint requires native generalized sections with elastic response only")
        if (type(self.footprints) is not tuple or not self.footprints or
                any(type(group) is not footprint_type for group in self.footprints)):
            raise ValueError("explicit nonempty public nodal footprint tuple required")
        groups = tuple(footprint_type(group.footprint_id, group.shell_nodes,
                                    group.beam_node, group.patch_cells) for group in self.footprints)
        if len({group.footprint_id for group in groups}) != len(groups):
            raise ValueError("distinct nodal footprint IDs required")
        object.__setattr__(self, "panel", panel)
        object.__setattr__(self, "footprints", groups)

    @property
    def status(self) -> str:
        return STATUS

    @property
    def actions(self):
        return self.panel.actions

    @property
    def recovery_selection(self):
        return stiffened_shell.NodalRigidFootprintSelection(
            tuple(group.footprint_id for group in self.footprints))

    def definition(self):
        owned = type(self).from_dict(self.to_dict())
        if owned.footprint_chart_policy != LEGACY_FOOTPRINT_CHART_POLICY:
            return replace(owned.panel.definition(), footprints=owned.footprints,
                           footprint_chart_policy=owned.footprint_chart_policy)
        return replace(owned.panel.definition(), footprints=owned.footprints)

    def create_analysis(self):
        owner = stiffened_shell.create_analysis(definition=self.definition())
        try:
            policy = owner.preflight().get("footprint_chart_admission_policy")
            if self.footprint_chart_policy == LEGACY_FOOTPRINT_CHART_POLICY:
                if (type(policy) is not dict or policy.get("rebase") != "unsupported" or
                        policy.get("chart_checkpoint_restore") != "unsupported"):
                    raise ValueError("nodal footprint consumer requires the public unrebased admission policy")
            else:
                expected = dict(policy=self.footprint_chart_policy, rebase="experimental_elastic",
                    chart_checkpoint_restore="experimental_elastic",
                    increment_origin="zero_rotation_coordinates_at_accepted_physical_frames",
                    newton_anchor="fixed_for_all_iterations_and_backtracks",
                    rotational_supports="fully_fixed_or_fully_free_triples",
                    production_qualified=False)
                if type(policy) is not dict or policy != expected or policy.get("production_qualified") is not False:
                    raise ValueError("nodal footprint consumer requires the declared public increment-origin chart admission policy")
        except BaseException:
            owner.close()
            raise
        return owner

    def to_dict(self) -> dict:
        record = dict(schema=SCHEMA_NODAL_FOOTPRINT, status=STATUS,
                    profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                    admission="experimental_elastic_unrebased_point_owner",
                    panel=self.panel.to_dict(), footprints=[
                        json.loads(json.dumps(group.descriptor(), allow_nan=False))
                        for group in self.footprints])
        if self.footprint_chart_policy != LEGACY_FOOTPRINT_CHART_POLICY:
            record.update(schema=SCHEMA_NODAL_FOOTPRINT_CHART_V2,
                          admission="experimental_elastic_increment_origin_chart_point_owner",
                          footprint_chart_policy=self.footprint_chart_policy)
        return record

    @classmethod
    def from_dict(cls, value):
        charted = type(value) is dict and value.get("schema") == SCHEMA_NODAL_FOOTPRINT_CHART_V2
        fields = {"schema", "status", "profile", "admission", "panel", "footprints"}
        if charted:
            fields.add("footprint_chart_policy")
        data = _exact_fields(value, fields,
                             "experimental nodal footprint panel")
        schema = SCHEMA_NODAL_FOOTPRINT_CHART_V2 if charted else SCHEMA_NODAL_FOOTPRINT
        admission = ("experimental_elastic_increment_origin_chart_point_owner" if charted
                     else "experimental_elastic_unrebased_point_owner")
        policy = LEGACY_FOOTPRINT_CHART_POLICY
        if charted:
            policy = getattr(stiffened_shell, "GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2", None)
            if policy is None or type(data["footprint_chart_policy"]) is not str or data["footprint_chart_policy"] != policy:
                raise ValueError("explicit installed public nodal footprint chart policy required")
        if (data["schema"] != schema or data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                data["admission"] != admission or
                type(data["panel"]) is not dict or data["panel"].get("schema") not in
                (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK)):
            raise ValueError("explicit experimental elastic nodal footprint schema and admission required")
        footprint_type = getattr(stiffened_shell, "NodalRigidFootprint", None)
        if footprint_type is None:
            raise ValueError("installed solver lacks public experimental nodal rigid footprints")
        if type(data["footprints"]) is not list or not data["footprints"]:
            raise ValueError("explicit nonempty nodal footprint list required")
        groups = []
        for item in data["footprints"]:
            row = _exact_fields(item, {"law", "footprint_id", "shell_nodes", "beam_node", "patch_cells", "semantics"}, "nodal footprint")
            if type(row["patch_cells"]) is not list:
                raise ValueError("explicit nodal footprint patch cell list required")
            cells = []
            for cell in row["patch_cells"]:
                if type(cell) is not list or len(cell) not in (3, 4):
                    raise ValueError("explicit Q4/S3 footprint patch cells required")
                cells.append(tuple(_positive_int(node, "footprint cell node") for node in cell))
            group = footprint_type(row["footprint_id"], _ids(row["shell_nodes"], "footprint nodes"),
                                   _positive_int(row["beam_node"], "footprint beam node"), tuple(cells))
            if json.loads(json.dumps(group.descriptor())) != row:
                raise ValueError("matching public nodal footprint law and selected-node semantics required")
            groups.append(group)
        return cls(ExperimentalGEPointPanel.from_dict(data["panel"]), tuple(groups),
                   footprint_chart_policy=policy)


@dataclass(frozen=True)
class ExperimentalGEBL5Panel:
    """Separate experimental Q4/generalized-B3 BL5 input, never accepted state.

    Public solver definitions validate the line controls and native sections.
    Geometry, constraint admission and finite-response limits remain
    with the solver owner when constructing or running an analysis.
    """

    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    lines: tuple[stiffened_shell.ExperimentalBL5Line, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    actions: tuple[stiffened_shell.NodalAction, ...] = ()
    q4_pullback_policy: str = field(default=HISTORICAL_Q4_PULLBACK_POLICY, kw_only=True)
    load_programme: stiffened_shell.StagedDeadLoadProgramme | None = field(default=None, kw_only=True)
    bl5_material_policy: str = field(default="elastic", kw_only=True)

    distributed_load: stiffened_shell.GEBeamDistributedLoad | None = field(default=None, kw_only=True)
    distributed_programme: stiffened_shell.GEBeamDistributedProgramme | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        coupled = getattr(stiffened_shell, "BL5_COUPLED_J2_MATERIAL_V1", None)
        if (type(self.shell) is not stiffened_shell.ShellPanel or
                not self.shell.q4_elements or self.shell.s3_elements or
                (self.shell.j2_curve is not None and
                 type(self.shell.j2_curve) is not DNVC208MaterialCurve) or
                (self.shell.j2_curve is None) != (self.bl5_material_policy != coupled)):
            raise ValueError("experimental BL5 requires pure Q4 and matching shell material policy")
        for name, expected, minimum in (
            ("beams", b3_ge.BeamDefinition, 1),
            ("lines", stiffened_shell.ExperimentalBL5Line, 1),
            ("fixed", stiffened_shell.FixedCoordinate, 1),
            ("actions", stiffened_shell.NodalAction, 0),
        ):
            rows = getattr(self, name)
            if (type(rows) is not tuple or len(rows) < minimum or
                    any(type(item) is not expected for item in rows)):
                raise ValueError(f"explicit experimental BL5 {name} required")
        selected = getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None)
        if (type(self.q4_pullback_policy) is not str or
                self.q4_pullback_policy not in (HISTORICAL_Q4_PULLBACK_POLICY, selected)):
            raise ValueError("unsupported experimental BL5 Q4 pullback policy")
        material = getattr(stiffened_shell, "BL5_GENERALIZED_MATERIAL_V1", None)
        if (type(self.bl5_material_policy) is not str or
                self.bl5_material_policy not in ("elastic", material, coupled)):
            raise ValueError("unsupported experimental BL5 material policy")
        if self.bl5_material_policy != "elastic" and self.q4_pullback_policy != selected:
            raise ValueError("BL5 material policy requires selected Q4 pullback")
        if self.load_programme is not None:
            programme_type = getattr(stiffened_shell, "StagedDeadLoadProgramme", None)
            if programme_type is None or type(self.load_programme) is not programme_type:
                raise ValueError("public experimental BL5 staged load programme required")
            if self.q4_pullback_policy != selected or self.actions:
                raise ValueError("staged BL5 programme requires selected Q4 pullback and empty actions")
            object.__setattr__(self, "load_programme",
                           programme_type.deserialize(self.load_programme.serialize()))
        for name, typename in (("distributed_load", "GEBeamDistributedLoad"),
                               ("distributed_programme", "GEBeamDistributedProgramme")):
            value = getattr(self, name)
            if value is None:
                continue
            descriptor_type = getattr(stiffened_shell, typename, None)
            if descriptor_type is None or type(value) is not descriptor_type:
                raise ValueError("public BL5 distributed load descriptor required")
            if (self.q4_pullback_policy != selected or self.actions or
                    self.load_programme is not None or
                    (self.distributed_load is not None and self.distributed_programme is not None)):
                raise ValueError("BL5 distributed loading requires objective Q4 and exactly one load input")
            object.__setattr__(self, name, descriptor_type.deserialize(value.serialize()))
        for beam in self.beams:
            element, _ = beam.instantiate()
            if type(element.section) is not b3_ge.EllipsoidalGeneralizedSection:
                raise ValueError("experimental BL5 requires native generalized B3 sections")

    @property
    def status(self) -> str:
        return STATUS

    def definition(self) -> stiffened_shell.LinearDefinition:
        args = (
            stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            self.shell, self.beams, (), self.fixed, self.lines)
        if self.q4_pullback_policy == HISTORICAL_Q4_PULLBACK_POLICY:
            return stiffened_shell.LinearDefinition(*args)
        return stiffened_shell.LinearDefinition(*args,
            q4_pullback_policy=self.q4_pullback_policy,
            **({} if self.bl5_material_policy == "elastic" else
               {"bl5_material_policy": self.bl5_material_policy}))

    def create_analysis(self):
        """Construct a fresh experimental owner through its public facade."""
        owner = stiffened_shell.create_analysis(definition=self.definition())
        descriptor = next((v for v in (self.load_programme, self.distributed_load,
                                       self.distributed_programme) if v is not None), None)
        if descriptor is not None:
            try:
                if owner.preflight()["identity"] != descriptor.model_identity:
                    raise ValueError("BL5 load belongs to a different panel model")
            except BaseException:
                owner.close()
                raise
        return owner

    @property
    def load_input(self):
        """Public finite solve input selected by this experimental record."""
        return next((v for v in (self.distributed_programme, self.distributed_load,
                                self.load_programme) if v is not None), self.actions)

    def to_dict(self) -> dict:
        for name, schema in (("distributed_load", SCHEMA_BL5_DISTRIBUTED),
                             ("distributed_programme", SCHEMA_BL5_DISTRIBUTED_PROGRAMME)):
            value = getattr(self, name)
            if value is not None:
                return dict(schema=schema, status=STATUS,
                            panel=replace(self, **{name: None}).to_dict(),
                            **{name: value.to_payload()})
        record = {
            "schema": SCHEMA_BL5,
            "status": STATUS,
            "profile": stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            "shell": {
                "node_ids": list(self.shell.node_ids),
                "coordinates_m": [list(row) for row in self.shell.coordinates_m],
                "q4_elements": [list(row) for row in self.shell.q4_elements],
                "s3_elements": [list(row) for row in self.shell.s3_elements],
                "thickness_m": self.shell.thickness_m,
                "young_pa": self.shell.young_pa,
                "poisson": self.shell.poisson,
                "density_kg_m3": self.shell.density_kg_m3,
            },
            "beams": [
                {"raw_base64": base64.b64encode(beam.raw).decode("ascii"),
                 "sha256": beam.sha256} for beam in self.beams
            ],
            "lines": [dict(shell_nodes=list(line.shell_nodes),
                           beam_halfcells=[list(pair) for pair in line.beam_halfcells],
                           multiplier_span=line.multiplier_span,
                           expected_beam_arm=list(line.expected_beam_arm),
                           basis=line.basis) for line in self.lines],
            "fixed": [asdict(item) for item in self.fixed],
            "actions": [dict(domain=item.domain, node=item.node,
                             wrench=list(item.wrench)) for item in self.actions],
        }
        if self.bl5_material_policy == getattr(stiffened_shell, "BL5_COUPLED_J2_MATERIAL_V1", None):
            record["shell"]["j2_curve"] = self.shell.j2_curve.as_dict()
            record.update(schema=SCHEMA_BL5_COUPLED_MATERIAL_V4,
                          q4_pullback_policy=self.q4_pullback_policy,
                          load_programme=(None if self.load_programme is None else
                                          self.load_programme.to_payload()),
                          bl5_material_policy=self.bl5_material_policy)
        elif self.bl5_material_policy != "elastic":
            record.update(schema=SCHEMA_BL5_MATERIAL_V3,
                          q4_pullback_policy=self.q4_pullback_policy,
                          load_programme=(None if self.load_programme is None else
                                          self.load_programme.to_payload()),
                          bl5_material_policy=self.bl5_material_policy)
        elif self.q4_pullback_policy != HISTORICAL_Q4_PULLBACK_POLICY:
            record.update(schema=SCHEMA_BL5_STAGED_V2,
                          q4_pullback_policy=self.q4_pullback_policy,
                          load_programme=(None if self.load_programme is None else
                                          self.load_programme.to_payload()))
        return record

    @classmethod
    def from_dict(cls, value: object) -> ExperimentalGEBL5Panel:
        if type(value) is dict and value.get("schema") in (
                SCHEMA_BL5_DISTRIBUTED, SCHEMA_BL5_DISTRIBUTED_PROGRAMME):
            name, typename = (("distributed_load", "GEBeamDistributedLoad")
                if value["schema"] == SCHEMA_BL5_DISTRIBUTED else
                ("distributed_programme", "GEBeamDistributedProgramme"))
            data = _exact_fields(value, {"schema", "status", "panel", name},
                                 "distributed BL5 input")
            if (data["status"] != STATUS or type(data["panel"]) is not dict or
                    data["panel"].get("schema") not in
                    (SCHEMA_BL5_STAGED_V2, SCHEMA_BL5_MATERIAL_V3,
                     SCHEMA_BL5_COUPLED_MATERIAL_V4)):
                raise ValueError("distributed BL5 requires an objective Q4 base record")
            descriptor_type = getattr(stiffened_shell, typename, None)
            if descriptor_type is None:
                raise ValueError("installed solver lacks BL5 distributed load descriptor")
            return replace(cls.from_dict(data["panel"]),
                           **{name: descriptor_type.from_payload(data[name])})
        staged = type(value) is dict and value.get("schema") in (
            SCHEMA_BL5_STAGED_V2, SCHEMA_BL5_MATERIAL_V3,
            SCHEMA_BL5_COUPLED_MATERIAL_V4)
        material = type(value) is dict and value.get("schema") in (
            SCHEMA_BL5_MATERIAL_V3, SCHEMA_BL5_COUPLED_MATERIAL_V4)
        coupled = type(value) is dict and value.get("schema") == SCHEMA_BL5_COUPLED_MATERIAL_V4
        data = _exact_fields(value, {"schema", "status", "profile", "shell",
                                     "beams", "lines", "fixed", "actions"} |
                              ({"q4_pullback_policy", "load_programme"} if staged else set()) |
                              ({"bl5_material_policy"} if material else set()),
                             "experimental BL5 panel")
        if (data["schema"] not in (SCHEMA_BL5, SCHEMA_BL5_STAGED_V2,
                                  SCHEMA_BL5_MATERIAL_V3, SCHEMA_BL5_COUPLED_MATERIAL_V4) or
                data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1):
            raise ValueError("experimental BL5 panel schema, status, and profile required")
        q4_policy = HISTORICAL_Q4_PULLBACK_POLICY
        programme = None
        material_policy = "elastic"
        if staged:
            q4_policy = data["q4_pullback_policy"]
            if q4_policy != getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None):
                raise ValueError("staged BL5 schema requires selected Q4 pullback policy")
            if data["load_programme"] is not None:
                programme_type = getattr(stiffened_shell, "StagedDeadLoadProgramme", None)
                if programme_type is None:
                    raise ValueError("installed solver lacks public BL5 staged load programme")
                programme = programme_type.from_payload(data["load_programme"])
        if material:
            material_policy = data["bl5_material_policy"]
            expected_policy = ("BL5_COUPLED_J2_MATERIAL_V1" if coupled else
                               "BL5_GENERALIZED_MATERIAL_V1")
            if material_policy != getattr(stiffened_shell, expected_policy, None):
                raise ValueError("material BL5 schema requires selected public material policy")
        shell_data = _exact_fields(data["shell"],
            {"node_ids", "coordinates_m", "q4_elements", "s3_elements",
             "thickness_m", "young_pa", "poisson", "density_kg_m3"} |
            ({"j2_curve"} if coupled else set()), "BL5 shell")
        curve = None
        if coupled:
            curve_data = _exact_fields(shell_data["j2_curve"],
                {"sigma_prop", "sigma_yield", "sigma_yield_2", "eps_p_y1",
                 "eps_p_y2", "K", "n"}, "shell J2 curve")
            curve = DNVC208MaterialCurve(**{
                name: _finite_number(item, "shell J2 curve")
                for name, item in curve_data.items()})
        shell = stiffened_shell.ShellPanel(
            _ids(shell_data["node_ids"], "shell nodes"),
            _rows(shell_data["coordinates_m"], 3, _finite_number, "shell coordinates"),
            _rows(shell_data["q4_elements"], 4, _positive_int, "Q4 elements"),
            _finite_number(shell_data["thickness_m"], "shell thickness"),
            _finite_number(shell_data["young_pa"], "shell Young modulus"),
            _finite_number(shell_data["poisson"], "shell Poisson ratio"),
            _finite_number(shell_data["density_kg_m3"], "shell density"),
            _rows(shell_data["s3_elements"], 3, _positive_int, "S3 elements"), curve)
        if type(data["beams"]) is not list or not data["beams"]:
            raise ValueError("nonempty experimental GE beam list required")
        beams = []
        for item in data["beams"]:
            row = _exact_fields(item, {"raw_base64", "sha256"}, "native beam binding")
            encoded, digest = row["raw_base64"], row["sha256"]
            if (type(encoded) is not str or len(encoded) > 2_800_000 or
                    type(digest) is not str or len(digest) != 64):
                raise ValueError("bounded native beam binding required")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, base64.binascii.Error) as error:
                raise ValueError("invalid native beam encoding") from error
            beams.append(b3_ge.BeamDefinition.from_bytes(raw, expected_sha256=digest))
        if any(type(data[name]) is not list for name in ("lines", "fixed", "actions")):
            raise ValueError("experimental BL5 line, fixed, and action lists required")
        lines = []
        for item in data["lines"]:
            row = _exact_fields(item, {"shell_nodes", "beam_halfcells", "multiplier_span",
                                      "expected_beam_arm", "basis"}, "BL5 line")
            pairs = row["beam_halfcells"]
            if (type(pairs) is not list or
                    any(type(pair) is not list or len(pair) != 2 or
                        type(pair[0]) is not int or pair[0] < 0 or
                        type(pair[1]) is not int or pair[1] not in (0, 1)
                        for pair in pairs)):
                raise ValueError("explicit BL5 beam half-cell pairs required")
            arm = row["expected_beam_arm"]
            if type(arm) is not list or len(arm) != 3:
                raise ValueError("three-component BL5 beam arm required")
            lines.append(stiffened_shell.ExperimentalBL5Line(
                _ids(row["shell_nodes"], "BL5 shell nodes"),
                tuple(tuple(pair) for pair in pairs),
                _positive_int(row["multiplier_span"], "BL5 multiplier span"),
                tuple(_finite_number(v, "BL5 beam arm") for v in arm), row["basis"]))
        fixed = tuple(stiffened_shell.FixedCoordinate(
            _domain(row["domain"]), _positive_int(row["node"], "fixed node"),
            _component(row["component"]))
            for item in data["fixed"]
            for row in (_exact_fields(item, {"domain", "node", "component"}, "fixed coordinate"),))
        actions = tuple(stiffened_shell.NodalAction(
            _domain(row["domain"]), _positive_int(row["node"], "action node"),
            _wrench(row["wrench"]))
            for item in data["actions"]
            for row in (_exact_fields(item, {"domain", "node", "wrench"}, "nodal action"),))
        return cls(shell, tuple(beams), tuple(lines), fixed, actions,
                   q4_pullback_policy=q4_policy, load_programme=programme,
                   bl5_material_policy=material_policy)


def save_case(case: ExperimentalGEPointPanel | ExperimentalGEBL5Panel | ExperimentalGENodalRigidFootprintPanel | ExperimentalGEReferenceModalPanel | ExperimentalGEReferenceDistributingPanel, path: str | Path) -> Path:
    """Atomically save a bounded input record, never solver state or results."""
    if type(case) not in (ExperimentalGEPointPanel, ExperimentalGEBL5Panel, ExperimentalGENodalRigidFootprintPanel, ExperimentalGEReferenceModalPanel, ExperimentalGEReferenceDistributingPanel):
        raise ValueError("experimental GE panel case required")
    destination = Path(path)
    if not destination.name.endswith(SUFFIX):
        raise ValueError(f"experimental GE panel filename must end with {SUFFIX}")
    record = case.to_dict()
    type(case).from_dict(record)
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode("ascii")
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError("experimental GE panel file exceeds size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent, prefix=f".{destination.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def load_case(path: str | Path) -> ExperimentalGEPointPanel | ExperimentalGEBL5Panel | ExperimentalGENodalRigidFootprintPanel | ExperimentalGEReferenceModalPanel | ExperimentalGEReferenceDistributingPanel:
    source = Path(path)
    if not source.name.endswith(SUFFIX):
        raise ValueError(f"experimental GE panel filename must end with {SUFFIX}")
    with source.open("rb") as stream:
        payload = stream.read(MAX_FILE_BYTES + 1)
    if len(payload) > MAX_FILE_BYTES:
        raise ValueError("experimental GE panel file exceeds size limit")

    def unique_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate experimental GE field: {key}")
            result[key] = item
        return result

    def reject_nonfinite(_: str):
        raise ValueError("nonfinite JSON value")

    try:
        data = json.loads(payload.decode("ascii"), object_pairs_hook=unique_pairs,
                          parse_constant=reject_nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid experimental GE panel JSON") from error
    if type(data) is dict and data.get("schema") in (SCHEMA_BL5, SCHEMA_BL5_STAGED_V2,
                                                       SCHEMA_BL5_MATERIAL_V3, SCHEMA_BL5_COUPLED_MATERIAL_V4,
                                                       SCHEMA_BL5_DISTRIBUTED, SCHEMA_BL5_DISTRIBUTED_PROGRAMME):
        return ExperimentalGEBL5Panel.from_dict(data)
    if type(data) is dict and data.get("schema") in (SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2):
        return ExperimentalGENodalRigidFootprintPanel.from_dict(data)
    if type(data) is dict and data.get("schema") == SCHEMA_REFERENCE_MODAL:
        return ExperimentalGEReferenceModalPanel.from_dict(data)
    if type(data) is dict and data.get("schema") == SCHEMA_REFERENCE_DISTRIBUTING:
        return ExperimentalGEReferenceDistributingPanel.from_dict(data)
    return ExperimentalGEPointPanel.from_dict(data)


__all__ = ["ExperimentalGEPointPanel", "ExperimentalGEBL5Panel", "ExperimentalGENodalRigidFootprintPanel",
           "ExperimentalGEReferenceDistributingPanel", "SCHEMA_REFERENCE_DISTRIBUTING", "REFERENCE_DISTRIBUTING_LAW",
           "ExperimentalGEReferenceModalPanel", "SCHEMA_REFERENCE_MODAL",
           "SCHEMA_NODAL_FOOTPRINT", "SCHEMA_NODAL_FOOTPRINT_CHART_V2", "SCHEMA", "SCHEMA_J2",
            "SCHEMA_Q4_PULLBACK", "SCHEMA_CURVED_Q4", "SCHEMA_CURVED_Q4_MATERIAL",
            "SCHEMA_CURVED_Q4_MATERIAL_NATIVE",
            "SCHEMA_CURVED_Q4_MATERIAL_STAGED",
            "SCHEMA_BL5", "SCHEMA_BL5_STAGED_V2",
           "SCHEMA_BL5_MATERIAL_V3", "SCHEMA_BL5_COUPLED_MATERIAL_V4",
           "SCHEMA_BL5_DISTRIBUTED", "SCHEMA_BL5_DISTRIBUTED_PROGRAMME",
           "STATUS", "SUFFIX", "save_case", "load_case"]
