"""Input-only consumer for explicitly selected finite distributing materials.

Constitutive mechanics, solver state and checkpoints remain owned by ANYsolver.
The persisted record is experimental input, not an accepted-state checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
import json

from anymaterial import DNVC208MaterialCurve
from anysolver import b3_ge, stiffened_shell


SCHEMA_FINITE_DISTRIBUTING_MATERIAL = (
    "anyfem.experimental-ge-finite-distributing-material-panel-v1")
FINITE_DISTRIBUTING_CYCLIC_ADMISSION = (
    "fixed_signed_zero_nodal_translation_force_history_only")


@dataclass(frozen=True)
class ExperimentalGEFiniteDistributingMaterialPanel:
    """Flat full-map Q4/native B3 input with an explicit material policy.

    Loads are dead nodal translation forces. The selected public policy owns
    monotone or cyclic force targets; input construction assembles no solver.
    """

    shell: stiffened_shell.ShellPanel
    beams: tuple[b3_ge.BeamDefinition, ...]
    points: tuple[stiffened_shell.PointConnection, ...]
    fixed: tuple[stiffened_shell.FixedCoordinate, ...]
    actions: tuple[stiffened_shell.NodalAction, ...] = ()
    finite_distributing_footprints: tuple = ()
    q4_pullback_policy: str = field(default=stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1)
    finite_distributing_material_policy: str = field(kw_only=True)

    @staticmethod
    def _public_contract():
        # Import the consumer framing lazily: its facade exports this class only
        # after defining the existing elastic input consumers.
        from .stiffened_shell import ExperimentalGEFiniteDistributingPanel

        ExperimentalGEFiniteDistributingPanel._public_contract()
        names = ("GE_FINITE_DISTRIBUTING_GENERALIZED_MATERIAL_V1",
                 "GE_FINITE_DISTRIBUTING_COUPLED_J2_MATERIAL_V1")
        policies = tuple(getattr(stiffened_shell, name, None) for name in names)
        if policies != tuple(name + "_DEV" for name in names):
            raise ValueError("installed solver lacks the exact finite distributing material policies")
        cyclic_names = ("GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1",
                        "GE_FINITE_DISTRIBUTING_COUPLED_J2_CYCLIC_MATERIAL_V1")
        policies += tuple(
            name + "_DEV" if getattr(stiffened_shell, name, None) == name + "_DEV" else None
            for name in cyclic_names)
        if not callable(getattr(stiffened_shell, "GEReferenceDistributingFootprintSelection", None)):
            raise ValueError("installed solver lacks finite distributing recovery selection")
        return policies

    @staticmethod
    def _require_policy(policy, policies):
        cyclic_names = ("GE_FINITE_DISTRIBUTING_GENERALIZED_CYCLIC_MATERIAL_V1_DEV",
                        "GE_FINITE_DISTRIBUTING_COUPLED_J2_CYCLIC_MATERIAL_V1_DEV")
        if type(policy) is str and policy in cyclic_names and policy not in policies:
            raise ValueError("installed solver lacks the selected cyclic distributing material policy")
        if type(policy) is not str or policy not in policies:
            raise ValueError("explicit finite distributing material policy required")
        if (policy in policies[2:] and
                "cyclic_reference_actions" not in stiffened_shell.LinearDefinition.__dataclass_fields__):
            raise ValueError("installed solver lacks cyclic reference action definition field")

    def __post_init__(self):
        from .stiffened_shell import (
            ExperimentalGEFiniteDistributingPanel, SCHEMA_Q4_PULLBACK,
            _parse_point_components, _point_components_payload,
        )

        policies = self._public_contract()
        self._require_policy(self.finite_distributing_material_policy, policies)
        coupled = self.finite_distributing_material_policy in (policies[1], policies[3])
        cyclic = self.finite_distributing_material_policy in (policies[2], policies[3])
        if cyclic and not self.actions:
            raise ValueError("cyclic distributing material requires explicit reference actions")
        if (type(self.shell) is not stiffened_shell.ShellPanel or
                (coupled and type(self.shell.j2_curve) is not DNVC208MaterialCurve) or
                (not coupled and self.shell.j2_curve is not None) or
                type(self.q4_pullback_policy) is not str or
                self.q4_pullback_policy != stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1):
            raise ValueError("finite distributing material policy requires matching canonical shell material and full-map Q4")
        # Explicit elastic geometry surrogate is used only for shared framing.
        # This also validates typed tuples and translation-only load admission.
        elastic = ExperimentalGEFiniteDistributingPanel(
            replace(self.shell, j2_curve=None), self.beams, self.points,
            self.fixed, self.actions, self.finite_distributing_footprints,
            self.q4_pullback_policy)
        # Canonical as_dict plus the existing parser owns the curve, nested shell
        # arrays and native BeamDefinition bytes. No element/owner is assembled.
        try:
            data = json.loads(json.dumps(_point_components_payload(
                replace(elastic.shell, j2_curve=self.shell.j2_curve),
                elastic.beams, elastic.points, elastic.fixed, elastic.actions,
                elastic.q4_pullback_policy), allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise ValueError("finite strictly framed material distributing inputs required") from error
        data["schema"] = SCHEMA_Q4_PULLBACK
        owned = _parse_point_components(data, self.q4_pullback_policy)
        object.__setattr__(self, "shell", owned[0])
        for name in ("beams", "points", "fixed", "actions",
                     "finite_distributing_footprints", "q4_pullback_policy"):
            object.__setattr__(self, name, getattr(elastic, name))

    @property
    def status(self):
        from .stiffened_shell import STATUS
        return STATUS

    @property
    def load_input(self):
        return self.actions

    @property
    def recovery_selection(self):
        self._public_contract()
        return stiffened_shell.GEReferenceDistributingFootprintSelection(
            tuple(group.footprint.footprint_id for group in self.finite_distributing_footprints))

    @classmethod
    def from_definition(cls, definition, actions=()):
        from .stiffened_shell import ExperimentalGEFiniteDistributingPanel

        policies = cls._public_contract()
        expected = {"profile", "shell", "beams", "points", "fixed", "lines",
                    "q4_pullback_policy", "footprints", "modal_boundary_policy",
                    "footprint_chart_policy", "bl5_material_policy",
                    "shell_reference_policy", "retained_scale_policy",
                    "distributing_footprints", "finite_distributing_footprints",
                    "finite_distributing_material_policy"}
        if "cyclic_reference_actions" in stiffened_shell.LinearDefinition.__dataclass_fields__:
            expected.add("cyclic_reference_actions")
        if (type(definition) is not stiffened_shell.LinearDefinition or
                {item.name for item in fields(definition)} != expected or
                type(definition.shell) is not stiffened_shell.ShellPanel):
            raise ValueError("exact supported finite distributing material definition required")
        cls._require_policy(definition.finite_distributing_material_policy, policies)
        cyclic = definition.finite_distributing_material_policy in policies[2:]
        reference_actions = getattr(definition, "cyclic_reference_actions", ())
        if type(reference_actions) is not tuple or (not cyclic and reference_actions):
            raise ValueError("cyclic reference actions must match the owned material actions exactly")
        # Existing export admission rejects every unsupported policy/attachment;
        # only the explicitly selected material fields use the elastic surrogate.
        elastic = ExperimentalGEFiniteDistributingPanel.from_definition(replace(
            definition, shell=replace(definition.shell, j2_curve=None),
            finite_distributing_material_policy="elastic",
            **({"cyclic_reference_actions": ()} if cyclic else {})), actions)
        if cyclic:
            # Both sides pass the same strict input framing before comparison.
            # NodalAction equality on caller-owned NumPy wrench arrays is ambiguous.
            reference = replace(elastic, actions=reference_actions)
            if reference.actions != elastic.actions:
                raise ValueError("cyclic reference actions must match the owned material actions exactly")
        return cls(definition.shell, definition.beams, definition.points,
                   definition.fixed, elastic.actions, definition.finite_distributing_footprints,
                   definition.q4_pullback_policy,
                   finite_distributing_material_policy=definition.finite_distributing_material_policy)

    def definition(self):
        from .stiffened_shell import ExperimentalGEFiniteDistributingPanel

        elastic = ExperimentalGEFiniteDistributingPanel(
            replace(self.shell, j2_curve=None), self.beams, self.points,
            self.fixed, self.actions, self.finite_distributing_footprints,
            self.q4_pullback_policy)
        policies = self._public_contract()
        cyclic = self.finite_distributing_material_policy in policies[2:]
        return replace(elastic.definition(), shell=self.shell,
                       finite_distributing_material_policy=self.finite_distributing_material_policy,
                       **({"cyclic_reference_actions": self.actions} if cyclic else {}))

    def create_analysis(self):
        from .stiffened_shell import FINITE_DISTRIBUTING_LAW

        policies = self._public_contract()
        cyclic = self.finite_distributing_material_policy in policies[2:]
        checkpoint_kind = {
            policies[0]: "finite_distributing_material",
            policies[1]: "finite_distributing_coupled",
            policies[2]: "finite_distributing_cyclic_material",
            policies[3]: "finite_distributing_cyclic_coupled",
        }[self.finite_distributing_material_policy]
        owner = stiffened_shell.create_analysis(definition=self.definition())
        try:
            caps, preflight = owner.capabilities, owner.preflight()
            if (type(caps) is not dict or caps.get("status") != self.status or
                    caps.get("profile") != stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1 or
                    caps.get("distributing_footprint_law") != FINITE_DISTRIBUTING_LAW or
                    caps.get("material_policy") != self.finite_distributing_material_policy or
                    caps.get("analysis") != ("nonlinear_force",) or
                    caps.get("loads") != ("nodal dead translation forces",) or
                    caps.get("checkpoints") != (checkpoint_kind,) or
                    caps.get("fixed_step_max_backtracks") != 16 or
                    (cyclic and (caps.get("increment_control") != ("fixed",) or
                        caps.get("zero_load_normalization") != "immutable unit-factor reference pattern" or
                        caps.get("adaptive_max_backtracks", "missing") is not None)) or
                    not callable(getattr(owner, "recover", None)) or
                    type(preflight) is not dict or
                    type(preflight.get("identity")) is not str or
                    len(preflight["identity"]) != 64 or
                    preflight.get("constraints") != 6 * (
                        len(self.points) + len(self.finite_distributing_footprints))):
                raise ValueError("public owner lacks the exact experimental finite distributing material admission")
            return owner
        except BaseException:
            owner.close()
            raise

    def to_dict(self):
        from .stiffened_shell import ExperimentalGEFiniteDistributingPanel, _point_components_payload

        elastic = ExperimentalGEFiniteDistributingPanel(
            replace(self.shell, j2_curve=None), self.beams, self.points,
            self.fixed, self.actions, self.finite_distributing_footprints,
            self.q4_pullback_policy)
        record = elastic.to_dict()
        record["shell"] = _point_components_payload(
            self.shell, self.beams, self.points, self.fixed, self.actions,
            self.q4_pullback_policy)["shell"]
        record.update(schema=SCHEMA_FINITE_DISTRIBUTING_MATERIAL,
                      finite_distributing_material_policy=self.finite_distributing_material_policy)
        if self.finite_distributing_material_policy in self._public_contract()[2:]:
            record["admission"] = FINITE_DISTRIBUTING_CYCLIC_ADMISSION
        return record

    @classmethod
    def from_dict(cls, value):
        from .stiffened_shell import (
            ExperimentalGEFiniteDistributingPanel, FINITE_DISTRIBUTING_ADMISSION,
            FINITE_DISTRIBUTING_LAW, SCHEMA_FINITE_DISTRIBUTING,
            SCHEMA_Q4_PULLBACK, STATUS, _exact_fields, _parse_point_components,
        )

        data = _exact_fields(value, {"schema", "status", "profile", "shell", "beams",
            "points", "fixed", "actions", "q4_pullback_policy", "law", "admission",
            "finite_distributing_footprints", "finite_distributing_material_policy"},
            "finite-distributing material GE panel")
        required = {"schema": SCHEMA_FINITE_DISTRIBUTING_MATERIAL, "status": STATUS,
                    "profile": stiffened_shell.B3_GE_E4_PL_STIFFENED_SHELL_V1,
                    "law": FINITE_DISTRIBUTING_LAW,
                    "q4_pullback_policy": stiffened_shell.Q4_ALGORITHMIC_PULLBACK_V1}
        if any(type(data[name]) is not str or data[name] != expected
               for name, expected in required.items()):
            raise ValueError("explicit finite distributing material schema/profile/law/admission required")
        policies = cls._public_contract()
        policy = data["finite_distributing_material_policy"]
        cls._require_policy(policy, policies)
        expected_admission = (FINITE_DISTRIBUTING_CYCLIC_ADMISSION
            if policy in policies[2:] else FINITE_DISTRIBUTING_ADMISSION)
        if type(data["admission"]) is not str or data["admission"] != expected_admission:
            raise ValueError("finite distributing material policy and admission must match")
        owned = _parse_point_components(dict(data, schema=SCHEMA_Q4_PULLBACK),
                                        data["q4_pullback_policy"])
        elastic_data = {name: item for name, item in data.items()
                        if name != "finite_distributing_material_policy"}
        elastic_data.update(schema=SCHEMA_FINITE_DISTRIBUTING,
                            shell=dict(data["shell"], j2_curve=None),
                            admission=FINITE_DISTRIBUTING_ADMISSION)
        elastic = ExperimentalGEFiniteDistributingPanel.from_dict(elastic_data)
        return cls(owned[0], elastic.beams, elastic.points, elastic.fixed,
                   elastic.actions, elastic.finite_distributing_footprints,
                   elastic.q4_pullback_policy,
                   finite_distributing_material_policy=policy)


__all__ = ["ExperimentalGEFiniteDistributingMaterialPanel",
           "SCHEMA_FINITE_DISTRIBUTING_MATERIAL", "FINITE_DISTRIBUTING_CYCLIC_ADMISSION"]
