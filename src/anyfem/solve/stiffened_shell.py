"""Headless, explicitly experimental GE point-panel consumer.

This record stores inputs only.  Solver state, results, and qualification are
not inferred from reopening it.  Ordinary ANYfem projects are unaffected.
The Q4 algorithmic pullback is a separate experimental v3 opt-in; historical
v1 and J2 v2 records retain their original schema and solver selection.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
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
SCHEMA_BL5 = "anyfem.experimental-ge-bl5-panel-v1"
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


@dataclass(frozen=True)
class ExperimentalGEPointPanel:
    """One GE/Q4-S3 point-panel definition and admitted nodal actions."""

    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    points: tuple[stiffened_shell.PointConnection, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    actions: tuple[stiffened_shell.NodalAction, ...] = ()
    q4_pullback_policy: str = HISTORICAL_Q4_PULLBACK_POLICY

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
        return stiffened_shell.create_analysis(definition=self.definition())

    def to_dict(self) -> dict:
        shell_j2 = self.shell.j2_curve
        selected_q4 = self.q4_pullback_policy != HISTORICAL_Q4_PULLBACK_POLICY
        record = {
            "schema": (SCHEMA_Q4_PULLBACK if selected_q4 else
                       SCHEMA_J2 if shell_j2 is not None else SCHEMA),
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
                {"raw_base64": base64.b64encode(beam.raw).decode("ascii"), "sha256": beam.sha256}
                for beam in self.beams
            ],
            "points": [asdict(point) for point in self.points],
            "fixed": [asdict(item) for item in self.fixed],
            "actions": [dict(domain=item.domain, node=item.node,
                             wrench=list(item.wrench)) for item in self.actions],
        }
        if selected_q4:
            record["q4_pullback_policy"] = self.q4_pullback_policy
            record["shell"]["j2_curve"] = (
                None if shell_j2 is None else shell_j2.as_dict())
        elif shell_j2 is not None:
            record["shell"]["j2_curve"] = shell_j2.as_dict()
        return record

    @classmethod
    def from_dict(cls, value: object) -> ExperimentalGEPointPanel:
        if type(value) is not dict:
            raise ValueError("strict experimental GE panel record required")
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
        return cls(shell, tuple(beams), points, fixed, actions, q4_policy)


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


def save_case(case: ExperimentalGEPointPanel | ExperimentalGEBL5Panel, path: str | Path) -> Path:
    """Atomically save a bounded input record, never solver state or results."""
    if type(case) not in (ExperimentalGEPointPanel, ExperimentalGEBL5Panel):
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


def load_case(path: str | Path) -> ExperimentalGEPointPanel | ExperimentalGEBL5Panel:
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
    return ExperimentalGEPointPanel.from_dict(data)


__all__ = ["ExperimentalGEPointPanel", "ExperimentalGEBL5Panel", "SCHEMA", "SCHEMA_J2",
           "SCHEMA_Q4_PULLBACK", "SCHEMA_BL5", "STATUS", "SUFFIX", "save_case", "load_case"]
