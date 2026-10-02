"""Headless, explicitly experimental GE point-panel consumer.

This record stores inputs only.  Solver state, results, and qualification are
not inferred from reopening it.  Ordinary ANYfem projects are unaffected.
The Q4 algorithmic pullback is a separate experimental v3 opt-in; historical
v1 and J2 v2 records retain their original schema and solver selection.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field, replace
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
SCHEMA_STAGED = "anyfem.experimental-ge-point-panel-staged-v4"
SCHEMA_BODY_LOAD = "anyfem.experimental-ge-point-panel-body-load-v1"
SCHEMA_BL5 = "anyfem.experimental-ge-bl5-panel-v1"
SCHEMA_NODAL_FOOTPRINT = "anyfem.experimental-ge-nodal-rigid-footprint-panel-v1"
SCHEMA_NODAL_FOOTPRINT_CHART_V2 = "anyfem.experimental-ge-nodal-rigid-footprint-chart-panel-v2"
LEGACY_FOOTPRINT_CHART_POLICY = "GE_NODAL_RIGID_FOOTPRINT_UNREBASED_ADMISSION_V1"
SCHEMA_REFERENCE_MODAL = "anyfem.experimental-ge-reference-modal-panel-v1"
STATUS = "EXPERIMENTAL_NOT_QUALIFIED"
SUFFIX = ".anyfem-ge.json"
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_BEAMS = 16
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

    if type(data["beams"]) is not list or not 1 <= len(data["beams"]) <= MAX_BEAMS:
        raise ValueError("bounded experimental GE beam list required")
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
        for name, expected, minimum in (
            ("beams", b3_ge.BeamDefinition, 1),
            ("points", stiffened_shell.PointConnection, 1),
            ("fixed", stiffened_shell.FixedCoordinate, 1),
            ("actions", stiffened_shell.NodalAction, 0),
        ):
            rows = getattr(self, name)
            if type(rows) is not tuple or len(rows) < minimum or any(type(v) is not expected for v in rows):
                raise ValueError(f"explicit experimental GE {name} required")
        if len(self.beams) > MAX_BEAMS:
            raise ValueError("experimental GE beam count exceeds file limit")
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
        return stiffened_shell.LinearDefinition(
            *args, q4_pullback_policy=self.q4_pullback_policy)

    def create_analysis(self):
        """Construct the fresh experimental owner through its public facade."""
        if self.body_load is not None:
            body_type = getattr(stiffened_shell, "GEBodyLoad", None)
            if body_type is None or type(self.body_load) is not body_type:
                raise ValueError("installed solver requires public experimental GE body load")
        owner = stiffened_shell.create_analysis(definition=self.definition())
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
        if self.body_load is not None:
            return self.body_load
        return self.actions if self.load_programme is None else self.load_programme

    def to_dict(self) -> dict:
        if self.body_load is not None:
            return dict(schema=SCHEMA_BODY_LOAD, status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, body_load=None).to_dict(),
                        body_load=self.body_load.to_payload())
        if self.load_programme is not None:
            return dict(schema=SCHEMA_STAGED, status=STATUS,
                        profile=stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                        panel=replace(self, load_programme=None).to_dict(),
                        load_programme=self.load_programme.to_payload())
        return _point_components_payload(self.shell, self.beams, self.points,
                                         self.fixed, self.actions, self.q4_pullback_policy)

    @classmethod
    def from_dict(cls, value: object) -> ExperimentalGEPointPanel:
        if type(value) is not dict:
            raise ValueError("strict experimental GE panel record required")
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
        selected_q4 = value.get("schema") == SCHEMA_Q4_PULLBACK
        fields = {"schema", "status", "profile", "shell", "beams",
                  "points", "fixed", "actions"}
        data = _exact_fields(value, fields | ({"q4_pullback_policy"} if selected_q4 else set()),
                             "experimental GE panel")
        if (data["schema"] not in (SCHEMA, SCHEMA_J2, SCHEMA_Q4_PULLBACK) or
                data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1):
            raise ValueError("experimental GE panel schema, status, and profile required")
        q4_policy = (data["q4_pullback_policy"] if selected_q4 else
                     HISTORICAL_Q4_PULLBACK_POLICY)
        if (selected_q4 and
                q4_policy != getattr(stiffened_shell, "Q4_ALGORITHMIC_PULLBACK_V1", None)):
            raise ValueError("unsupported experimental Q4 pullback policy")

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
                definition.lines or definition.footprints):
            raise ValueError("exact GE six-pose point modal definition required; lines and footprints unsupported")
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
                self.panel.load_programme is not None or self.panel.body_load is not None):
            raise ValueError("nodal footprint requires an explicit unstaged GE point panel without body load")
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
    """Separate elastic Q4/generalized-B3 BL5 input record, never accepted state.

    Public solver definitions validate the line controls and native sections.
    Geometry, constraint admission and elastic finite-response limits remain
    with the solver owner when constructing or running an analysis.
    """

    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    lines: tuple[stiffened_shell.ExperimentalBL5Line, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    actions: tuple[stiffened_shell.NodalAction, ...] = ()

    def __post_init__(self) -> None:
        if (type(self.shell) is not stiffened_shell.ShellPanel or
                not self.shell.q4_elements or self.shell.s3_elements or
                self.shell.j2_curve is not None):
            raise ValueError("experimental BL5 requires a pure Q4 elastic shell")
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
        if len(self.beams) > MAX_BEAMS:
            raise ValueError("experimental GE beam count exceeds file limit")
        for beam in self.beams:
            element, _ = beam.instantiate()
            if type(element.section) is not b3_ge.EllipsoidalGeneralizedSection:
                raise ValueError("experimental BL5 requires native generalized B3 sections")

    @property
    def status(self) -> str:
        return STATUS

    def definition(self) -> stiffened_shell.LinearDefinition:
        return stiffened_shell.LinearDefinition(
            stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
            self.shell, self.beams, (), self.fixed, self.lines)

    def create_analysis(self):
        """Construct a fresh experimental owner through its public facade."""
        return stiffened_shell.create_analysis(definition=self.definition())

    def to_dict(self) -> dict:
        return {
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

    @classmethod
    def from_dict(cls, value: object) -> ExperimentalGEBL5Panel:
        data = _exact_fields(value, {"schema", "status", "profile", "shell",
                                     "beams", "lines", "fixed", "actions"},
                             "experimental BL5 panel")
        if (data["schema"] != SCHEMA_BL5 or data["status"] != STATUS or
                data["profile"] != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1):
            raise ValueError("experimental BL5 panel schema, status, and profile required")
        shell_data = _exact_fields(data["shell"],
            {"node_ids", "coordinates_m", "q4_elements", "s3_elements",
             "thickness_m", "young_pa", "poisson", "density_kg_m3"}, "BL5 shell")
        shell = stiffened_shell.ShellPanel(
            _ids(shell_data["node_ids"], "shell nodes"),
            _rows(shell_data["coordinates_m"], 3, _finite_number, "shell coordinates"),
            _rows(shell_data["q4_elements"], 4, _positive_int, "Q4 elements"),
            _finite_number(shell_data["thickness_m"], "shell thickness"),
            _finite_number(shell_data["young_pa"], "shell Young modulus"),
            _finite_number(shell_data["poisson"], "shell Poisson ratio"),
            _finite_number(shell_data["density_kg_m3"], "shell density"),
            _rows(shell_data["s3_elements"], 3, _positive_int, "S3 elements"))
        if type(data["beams"]) is not list or not 1 <= len(data["beams"]) <= MAX_BEAMS:
            raise ValueError("bounded experimental GE beam list required")
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
        return cls(shell, tuple(beams), tuple(lines), fixed, actions)


def save_case(case: ExperimentalGEPointPanel | ExperimentalGEBL5Panel | ExperimentalGENodalRigidFootprintPanel | ExperimentalGEReferenceModalPanel, path: str | Path) -> Path:
    """Atomically save a bounded input record, never solver state or results."""
    if type(case) not in (ExperimentalGEPointPanel, ExperimentalGEBL5Panel, ExperimentalGENodalRigidFootprintPanel, ExperimentalGEReferenceModalPanel):
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


def load_case(path: str | Path) -> ExperimentalGEPointPanel | ExperimentalGEBL5Panel | ExperimentalGENodalRigidFootprintPanel | ExperimentalGEReferenceModalPanel:
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
    if type(data) is dict and data.get("schema") == SCHEMA_BL5:
        return ExperimentalGEBL5Panel.from_dict(data)
    if type(data) is dict and data.get("schema") in (SCHEMA_NODAL_FOOTPRINT, SCHEMA_NODAL_FOOTPRINT_CHART_V2):
        return ExperimentalGENodalRigidFootprintPanel.from_dict(data)
    if type(data) is dict and data.get("schema") == SCHEMA_REFERENCE_MODAL:
        return ExperimentalGEReferenceModalPanel.from_dict(data)
    return ExperimentalGEPointPanel.from_dict(data)


__all__ = ["ExperimentalGEPointPanel", "ExperimentalGEBL5Panel", "ExperimentalGENodalRigidFootprintPanel",
           "ExperimentalGEReferenceModalPanel", "SCHEMA_REFERENCE_MODAL",
           "SCHEMA_NODAL_FOOTPRINT", "SCHEMA_NODAL_FOOTPRINT_CHART_V2", "SCHEMA", "SCHEMA_J2",
           "SCHEMA_Q4_PULLBACK", "SCHEMA_BL5", "STATUS", "SUFFIX", "save_case", "load_case"]
