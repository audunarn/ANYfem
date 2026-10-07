"""Strict input framing for the existing finite distributing displacement route.

No mechanics or accepted-state codec lives here. Targets and affine amplitudes
retain the public solver's meanings; stored input is never an accepted seed.
"""
import json
import math

from anysolver import stiffened_shell


SCHEMA_FINITE_DISTRIBUTING_DISPLACEMENT = (
    "anyfem.experimental-ge-finite-distributing-displacement-panel-v2")
SCHEMA_FINITE_DISTRIBUTING_MATERIAL_DISPLACEMENT = (
    "anyfem.experimental-ge-finite-distributing-material-displacement-panel-v2")
DISPLACEMENT_POLICY = "B3_GE_DISTRIBUTING_DISPLACEMENT_V1_DEV"
DISPLACEMENT_KIND = "finite_distributing_displacement"
DISPLACEMENT_ADMISSION = "fixed_signed_physical_translation_from_ordinary_force_seed_only"
_REQUEST_FIELDS = {"displacement_control", "displacement_seed_factors",
                   "displacement_policy", "force_seed_admission"}


def own_request(control, factors):
    if type(factors) is not tuple:
        raise ValueError("owned immutable displacement seed factor tuple required")
    if control is None:
        if factors:
            raise ValueError("displacement seed factors require an explicit control")
        return None, ()
    control_type = getattr(stiffened_shell, "DisplacementControl", None)
    if (control_type is None or type(control) is not control_type or
            type(control.domain) is not str or control.domain not in ("shell", "beam") or
            type(control.node) is not int or control.node <= 0 or
            type(control.component) is not int or not 0 <= control.component < 3 or
            control.trial_policy is not None):
        raise ValueError("explicit fixed physical translation DisplacementControl required")
    previous = 0.
    owned = []
    for factor in factors:
        if type(factor) not in (int, float):
            raise ValueError("finite strictly increasing positive ordinary-force seed factors required")
        try:
            number = float(factor)
        except (ValueError, OverflowError) as error:
            raise ValueError("finite ordinary-force seed factors required") from error
        if not math.isfinite(number) or number <= previous:
            raise ValueError("finite strictly increasing positive ordinary-force seed factors required")
        owned.append(number)
        previous = number
    if not owned:
        raise ValueError("explicit nonempty ordinary-force seed factors required")
    return control_type(control.domain, control.node, control.component), tuple(owned)


def validate_request(panel):
    control, factors = own_request(panel.displacement_control,
                                   panel.displacement_seed_factors)
    if control is not None:
        if not panel.actions:
            raise ValueError("displacement request requires an explicit nodal load direction")
        nodes = (set(panel.shell.node_ids) if control.domain == "shell" else
                 {node for beam in panel.beams for node in json.loads(beam.raw)["node_ids"]})
        if control.node not in nodes:
            raise ValueError("unknown displacement control node")
        if any((item.domain, item.node, item.component) ==
               (control.domain, control.node, control.component) for item in panel.fixed):
            raise ValueError("displacement control is restrained")
    object.__setattr__(panel, "displacement_control", control)
    object.__setattr__(panel, "displacement_seed_factors", factors)


def encode_request(record, panel, schema):
    if panel.displacement_control is None:
        return record
    control = panel.displacement_control
    return dict(record, schema=schema, displacement_policy=DISPLACEMENT_POLICY,
                force_seed_admission=record["admission"], admission=DISPLACEMENT_ADMISSION,
                displacement_control=dict(domain=control.domain, node=control.node,
                                          component=control.component),
                displacement_seed_factors=list(panel.displacement_seed_factors))


def decode_request(value, schema, base_schema):
    if type(value) is not dict or value.get("schema") != schema:
        return value, {}
    if (not _REQUEST_FIELDS <= set(value) or
            type(value.get("admission")) is not str or
            value["admission"] != DISPLACEMENT_ADMISSION or
            type(value["force_seed_admission"]) is not str or
            type(value["displacement_policy"]) is not str or
            value["displacement_policy"] != DISPLACEMENT_POLICY or
            type(value["displacement_control"]) is not dict or
            set(value["displacement_control"]) != {"domain", "node", "component"} or
            type(value["displacement_seed_factors"]) is not list):
        raise ValueError("strict experimental finite distributing displacement request required")
    control_type = getattr(stiffened_shell, "DisplacementControl", None)
    if control_type is None:
        raise ValueError("installed solver lacks public DisplacementControl")
    control, factors = own_request(control_type(**value["displacement_control"]),
                                   tuple(value["displacement_seed_factors"]))
    core = {name: item for name, item in value.items() if name not in _REQUEST_FIELDS}
    core["schema"] = base_schema
    core["admission"] = value["force_seed_admission"]
    return core, dict(displacement_control=control, displacement_seed_factors=factors)


def exact_route_capabilities(caps, force_kind, displacement_requested):
    if type(caps) is not dict:
        return False
    modern = (caps.get("analysis") == ("nonlinear_force", "experimental_fixed_displacement") and
              caps.get("checkpoints") == (force_kind, DISPLACEMENT_KIND) and
              caps.get("displacement_checkpoint") == DISPLACEMENT_KIND and
              caps.get("displacement_control") ==
              "fixed signed physical translational targets from accepted fixed-force seed")
    legacy = (caps.get("analysis") == ("nonlinear_force",) and
              caps.get("checkpoints") == (force_kind,))
    return modern or (not displacement_requested and legacy)


def validate_targets(targets):
    if type(targets) is not tuple or not targets:
        raise ValueError("nonempty immutable signed physical translation target tuple required")
    result = []
    for target in targets:
        if type(target) not in (int, float):
            raise ValueError("finite changed signed physical translation targets required")
        try:
            number = float(target)
        except (ValueError, OverflowError) as error:
            raise ValueError("finite signed physical translation targets required") from error
        if not math.isfinite(number) or (result and number == result[-1]):
            raise ValueError("finite changed signed physical translation targets required")
        result.append(number)
    return tuple(result)


def validate_execution_options(on_accepted, max_iterations, max_backtracks):
    if on_accepted is not None and not callable(on_accepted):
        raise ValueError("displacement on_accepted must be callable or None")
    if type(max_iterations) is not int or not 1 <= max_iterations <= 24:
        raise ValueError("displacement max_iterations must be an integer in 1..24")
    if type(max_backtracks) is not int or not 0 <= max_backtracks <= 12:
        raise ValueError("displacement max_backtracks must be an integer in 0..12")
