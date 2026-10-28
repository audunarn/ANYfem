"""Saved, reusable wind turbine load definition: thrust curve, hub, rotor and the wind it OWNS.

A ``WindTurbineLoad`` is an immutable, validated project record (``Project.wind_turbine_loads``).  It owns its wind
data: a steady declared mean, the actual imported 3-component samples (never only a mutable file path), or a generated
longitudinal Kaimal realization (ANYloads ``generate_kaimal_realization``) whose continuous coefficients are retained,
so reopening the project or resampling on another integration grid never silently redraws the random stream.

Conventions (unchanged from the floating transient route): ``hub_body`` is the ABSOLUTE body point of the hub (the
body -> CG conversion happens once, where the transient loads are built); the wind is world-frame and absolute-time;
the declared ``reference_mean_world`` is the physical mean of the operating equilibrium (the sample mean is never
subtracted); the thrust abscissa is the rotor-axial relative speed. Optional explicit mass/inertia is structural
model content for every analysis; wind/thrust is consumed only by floating transient runs. No flexible tower feedback,
controller, yaw dynamics or torque is introduced. Hub coordinates may instead be offsets from the attachment node.

Primitives only: this module imports no GUI and no project code.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, fields
from typing import Any, Mapping, Optional
from uuid import uuid4

import numpy as np

__all__ = ["WIND_MODES", "WindTurbineLoad", "WindTurbineLoadError", "realization_record"]

WIND_MODES = ("steady", "import", "kaimal")
_KAIMAL_PARAMETERS = ("turbulence_intensity", "integral_scale", "seed", "timestep", "duration", "max_frequency")
_KAIMAL_COEFFICIENTS = ("generator", "frequencies", "bin_energy", "amplitudes", "phases", "missing_low_variance",
                        "missing_high_variance")


class WindTurbineLoadError(ValueError):
    """A wind turbine load definition cannot be admitted; nothing was repaired."""


def _array(value: Any, name: str, *, ndim: int, shape: Optional[tuple] = None) -> np.ndarray:
    try:
        raw = np.asarray(value)
    except (TypeError, ValueError) as exc:
        raise WindTurbineLoadError(f"{name}: not a numeric array ({exc})") from None
    if raw.dtype.kind in "cbOUS" and raw.size:
        raise WindTurbineLoadError(f"{name}: real numbers are required")
    try:
        arr = np.asarray(raw, dtype=float)
    except (TypeError, ValueError) as exc:
        raise WindTurbineLoadError(f"{name}: not a numeric array ({exc})") from None
    if arr.size == 0 and ndim == 1:
        arr = arr.reshape(0)
    if arr.ndim != ndim or (shape is not None and arr.shape != shape) or not np.all(np.isfinite(arr)):
        raise WindTurbineLoadError(f"{name}: expected finite real values of shape {shape or ('n',) * ndim}")
    return arr


def _vector(value: Any, name: str) -> tuple:
    return tuple(float(x) for x in _array(value, name, ndim=1))


def _vec3(value: Any, name: str) -> tuple:
    return tuple(float(x) for x in _array(value, name, ndim=1, shape=(3,)))


def _scalar(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise WindTurbineLoadError(f"{name}: a number is required")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise WindTurbineLoadError(f"{name}: a number is required") from None
    if not math.isfinite(x):
        raise WindTurbineLoadError(f"{name}: must be finite")
    return x


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise WindTurbineLoadError(f"{name}: an integer is required")
    try:
        i = int(value)
    except (TypeError, ValueError, OverflowError):        # int(inf) overflows, int(nan) is a ValueError
        raise WindTurbineLoadError(f"{name}: a finite integer is required") from None
    if i != value:
        raise WindTurbineLoadError(f"{name}: an integer is required")
    return i


def _rows3(value: Any, name: str) -> tuple:
    """Detached tuple of 3-tuples; an empty sequence stays the empty tuple."""
    if value is None or (hasattr(value, "__len__") and len(value) == 0):
        return ()
    rows = _array(value, name, ndim=2)
    if rows.shape[1] != 3:
        raise WindTurbineLoadError(f"{name}: expected rows of three finite values")
    return tuple(tuple(float(x) for x in row) for row in rows)


def realization_record(real) -> dict:
    """Exact retained realization (coefficients + omitted-variance accounting) for provenance with a job input."""
    return dict(kind='kaimal', generator='anyloads.generate_kaimal_realization', mean_speed=real.mean_speed,
                turbulence_intensity=real.turbulence_intensity, integral_scale=real.integral_scale,
                direction=np.asarray(real.direction).tolist(), seed=real.seed, period=real.period,
                max_frequency=real.max_frequency, frequencies=np.asarray(real.frequencies).tolist(),
                bin_energy=np.asarray(real.bin_energy).tolist(), amplitudes=np.asarray(real.amplitudes).tolist(),
                phases=np.asarray(real.phases).tolist(), missing_low_variance=real.missing_low_variance,
                missing_high_variance=real.missing_high_variance, target_variance=real.target_variance,
                represented_variance=real.represented_variance)


@dataclass(frozen=True)
class WindTurbineLoad:
    """One named turbine load definition; see the module docstring for conventions.

    Rotor: either ``rotor_yaw_deg`` + ``rotor_tilt_deg`` (ANYloads ``TurbineLoadApplicator.from_yaw_tilt``) or an
    explicit unit ``rotor_axis`` (body frame, thrust/downwind direction; never normalised silently) - not both.
    Wind: ``steady`` (declared mean only), ``import`` (owned samples ``import_times`` / ``import_velocity``) or
    ``kaimal`` (generation parameters + the retained coefficients of ONE continuous realization over
    ``[0, duration]``).  Fields of the other modes must stay empty, so a record never holds a competing wind.
    Build new records with :meth:`create`, which generates a Kaimal realization only when its parameters changed.
    """

    name: str
    thrust_speeds: tuple
    thrust_values: tuple
    hub_body: tuple
    hub_node_id: int
    wind_mode: str
    reference_mean_world: tuple
    rotor_yaw_deg: Optional[float] = 0.0
    rotor_tilt_deg: Optional[float] = 0.0
    rotor_axis: Optional[tuple] = None
    import_times: tuple = ()
    import_velocity: tuple = ()
    import_source: str = ""
    turbulence_intensity: float = 0.0
    integral_scale: float = 0.0
    seed: int = 0
    timestep: float = 0.0
    duration: float = 0.0
    max_frequency: float = 0.0
    generator: str = ""
    frequencies: tuple = ()
    bin_energy: tuple = ()
    amplitudes: tuple = ()
    phases: tuple = ()
    missing_low_variance: float = 0.0
    missing_high_variance: float = 0.0
    id: str = field(default_factory=lambda: str(uuid4()))
    attachment_element_id: int | None = None
    hub_reference: str = "absolute"
    mass_mode: str = "already_modelled"
    mass: float = 0.0
    mass_cg_offset: tuple = (0., 0., 0.)
    inertia_cg: tuple = ((0.,0.,0.),(0.,0.,0.),(0.,0.,0.))

    def __post_init__(self) -> None:
        from anyloads.wind_turbine import ThrustCurve, WindError, WindHistory

        def put(key, value):
            object.__setattr__(self, key, value)

        if not isinstance(self.name, str) or not self.name.strip():
            raise WindTurbineLoadError("a wind turbine load needs a non-empty name")
        put("name", self.name.strip())
        if not isinstance(self.id, str) or not self.id:
            raise WindTurbineLoadError("a wind turbine load needs a non-empty id")
        speeds, thrusts = _vector(self.thrust_speeds, "thrust_speeds"), _vector(self.thrust_values, "thrust_values")
        try:
            ThrustCurve(np.array(speeds), np.array(thrusts))
        except WindError as error:
            raise WindTurbineLoadError(f"thrust curve: {error}") from None
        put("thrust_speeds", speeds); put("thrust_values", thrusts)
        put("hub_body", _vec3(self.hub_body, "hub_body"))
        put("hub_node_id", _integer(self.hub_node_id, "hub_node_id"))
        if self.attachment_element_id is not None:
            put("attachment_element_id", _integer(self.attachment_element_id, "attachment_element_id"))
        if self.hub_reference not in ("absolute", "attachment"):
            raise WindTurbineLoadError("hub_reference must be absolute or attachment")
        if self.mass_mode not in ("already_modelled", "additional"):
            raise WindTurbineLoadError("mass_mode must be already_modelled or additional")
        from anysolver.concentrated_mass import ConcentratedMass
        try:
            physical = ConcentratedMass(_scalar(self.mass, "mass"), self.mass_cg_offset, self.inertia_cg)
        except (TypeError, ValueError) as error:
            raise WindTurbineLoadError(str(error)) from None
        put("mass", physical.mass); put("mass_cg_offset", physical.offset); put("inertia_cg", physical.inertia_cg)
        if self.rotor_axis is None:
            if self.rotor_yaw_deg is None or self.rotor_tilt_deg is None:
                raise WindTurbineLoadError("rotor: give yaw and tilt, or an explicit unit rotor axis")
            put("rotor_yaw_deg", _scalar(self.rotor_yaw_deg, "rotor_yaw_deg"))
            put("rotor_tilt_deg", _scalar(self.rotor_tilt_deg, "rotor_tilt_deg"))
        else:
            if self.rotor_yaw_deg is not None or self.rotor_tilt_deg is not None:
                raise WindTurbineLoadError("rotor: give either yaw/tilt or an explicit rotor axis, not both")
            axis = _vec3(self.rotor_axis, "rotor_axis")
            if abs(float(np.linalg.norm(axis)) - 1.0) > 1e-10:
                raise WindTurbineLoadError("rotor_axis: must already be a unit vector within 1e-10; it is never "
                                           "normalised silently")
            put("rotor_axis", axis)
        if self.wind_mode not in WIND_MODES:
            raise WindTurbineLoadError(f"wind_mode {self.wind_mode!r}: expected one of {WIND_MODES}")
        ref = _vec3(self.reference_mean_world, "reference_mean_world")
        put("reference_mean_world", ref)
        if not isinstance(self.import_source, str) or not isinstance(self.generator, str):
            raise WindTurbineLoadError("import_source and generator must be text")
        # every sequence field, active or not, becomes a detached immutable tuple (shape-checked) before the mode
        # checks: no caller-held list can later change the record's physical content
        for key in ("import_times", "frequencies", "bin_energy", "amplitudes", "phases"):
            put(key, _vector(getattr(self, key), key))
        put("import_velocity", _rows3(self.import_velocity, "import_velocity"))
        has_import = bool(len(self.import_times) or len(self.import_velocity) or self.import_source)
        has_kaimal = bool(self.generator or any(len(getattr(self, k)) for k in
                                                ("frequencies", "bin_energy", "amplitudes", "phases"))
                          or self.seed != 0 or any(_scalar(getattr(self, k), k) != 0.0 for k in
                                                   ("turbulence_intensity", "integral_scale", "timestep", "duration",
                                                    "max_frequency", "missing_low_variance",
                                                    "missing_high_variance")))
        if self.wind_mode != "import" and has_import:
            raise WindTurbineLoadError(f"a {self.wind_mode} wind owns no imported samples (one wind per turbine)")
        if self.wind_mode != "kaimal" and has_kaimal:
            raise WindTurbineLoadError(f"a {self.wind_mode} wind owns no Kaimal parameters or coefficients")
        if self.wind_mode == "import":
            times = _array(self.import_times, "import_times", ndim=1)
            velocity = _array(self.import_velocity, "import_velocity", ndim=2, shape=(times.size, 3))
            try:
                WindHistory.from_vectors(times, velocity, reference_mean=np.array(ref), source=self.import_source)
            except WindError as error:
                raise WindTurbineLoadError(f"imported wind: {error}") from None
            put("import_times", tuple(float(x) for x in times))
            put("import_velocity", tuple(tuple(float(x) for x in row) for row in velocity))
        elif self.wind_mode == "kaimal":
            if not float(np.linalg.norm(ref)) > 0.0:
                raise WindTurbineLoadError("a Kaimal wind needs a nonzero declared mean (speed and direction)")
            ti, length = _scalar(self.turbulence_intensity, "turbulence_intensity"), _scalar(self.integral_scale,
                                                                                            "integral_scale")
            seed = _integer(self.seed, "seed")
            dt, duration = _scalar(self.timestep, "timestep"), _scalar(self.duration, "duration")
            fmax = _scalar(self.max_frequency, "max_frequency")
            if ti < 0.0 or length <= 0.0 or seed < 0:
                raise WindTurbineLoadError("Kaimal: TI >= 0, integral scale > 0 and a non-negative seed are required")
            if dt <= 0.0 or duration < dt:
                raise WindTurbineLoadError("Kaimal: timestep > 0 and duration >= timestep are required")
            if fmax <= 0.0 or fmax > 0.5 / dt * (1.0 + 1e-12):
                raise WindTurbineLoadError("Kaimal: 0 < max frequency <= Nyquist frequency 0.5 / timestep")
            if not self.generator:
                raise WindTurbineLoadError("Kaimal: the retained realization must record its generator")
            for key, value in (("turbulence_intensity", ti), ("integral_scale", length), ("seed", seed),
                               ("timestep", dt), ("duration", duration), ("max_frequency", fmax)):
                put(key, value)
            for key in ("frequencies", "bin_energy", "amplitudes", "phases"):
                put(key, _vector(getattr(self, key), key))
            put("missing_low_variance", _scalar(self.missing_low_variance, "missing_low_variance"))
            put("missing_high_variance", _scalar(self.missing_high_variance, "missing_high_variance"))
            try:
                self.kaimal_realization()
            except WindError as error:
                raise WindTurbineLoadError(f"Kaimal realization: {error}") from None

    # ------------------------------------------------------------------ builders
    @classmethod
    def create(cls, name: str, *, thrust_speeds, thrust_values, hub_body, hub_node_id, wind_mode: str,
               reference_mean_world, rotor_yaw_deg=None, rotor_tilt_deg=None, rotor_axis=None, import_times=(),
               import_velocity=(), import_source: str = "", turbulence_intensity=0.0, integral_scale=0.0, seed=0,
               timestep=None, duration=None, max_frequency=None, previous: "WindTurbineLoad | None" = None,
               id: str | None = None, attachment_element_id=None, hub_reference="absolute",
               mass_mode="already_modelled", mass=0., mass_cg_offset=(0.,0.,0.),
               inertia_cg=((0.,0.,0.),)*3) -> "WindTurbineLoad":
        """Validated record from primitives.  A Kaimal wind is generated by ANYloads only when no ``previous`` record
        holds a realization with identical mean and generation parameters; otherwise those coefficients are retained
        exactly (an edit of name, hub, rotor or curve never redraws the realization).  ``max_frequency`` defaults to
        the Nyquist frequency ``0.5 / timestep``."""
        from anyloads.wind_turbine import KAIMAL_GENERATOR_VERSION, WindError, generate_kaimal_realization
        if rotor_axis is not None:
            rotor = dict(rotor_yaw_deg=None, rotor_tilt_deg=None, rotor_axis=tuple(rotor_axis))
        else:
            rotor = dict(rotor_yaw_deg=0.0 if rotor_yaw_deg is None else rotor_yaw_deg,
                         rotor_tilt_deg=0.0 if rotor_tilt_deg is None else rotor_tilt_deg, rotor_axis=None)
        identity = id or (previous.id if previous is not None else str(uuid4()))
        common = dict(name=name, thrust_speeds=tuple(thrust_speeds), thrust_values=tuple(thrust_values),
                      hub_body=tuple(hub_body), hub_node_id=hub_node_id, wind_mode=wind_mode,
                      reference_mean_world=_vec3(reference_mean_world, "reference_mean_world"), id=identity, **rotor)
        common.update(attachment_element_id=attachment_element_id, hub_reference=hub_reference,
                      mass_mode=mass_mode, mass=mass, mass_cg_offset=mass_cg_offset, inertia_cg=inertia_cg)
        if wind_mode == "steady":
            return cls(**common)
        if wind_mode == "import":
            return cls(**common, import_times=_vector(import_times, "import_times"),
                       import_velocity=tuple(tuple(r) for r in _array(import_velocity, "import_velocity", ndim=2)),
                       import_source=str(import_source))
        if wind_mode != "kaimal":
            raise WindTurbineLoadError(f"wind_mode {wind_mode!r}: expected one of {WIND_MODES}")
        dt = _scalar(timestep, "timestep")
        if dt <= 0.0:
            raise WindTurbineLoadError("Kaimal: timestep > 0 is required")
        params = dict(turbulence_intensity=_scalar(turbulence_intensity, "turbulence_intensity"),
                      integral_scale=_scalar(integral_scale, "integral_scale"), seed=_integer(seed, "seed"),
                      timestep=dt, duration=_scalar(duration, "duration"),
                      max_frequency=0.5 / dt if max_frequency is None else _scalar(max_frequency, "max_frequency"))
        if (previous is not None and previous.wind_mode == "kaimal"
                and previous.reference_mean_world == common["reference_mean_world"]
                and all(getattr(previous, k) == v for k, v in params.items())):
            coefficients = {k: getattr(previous, k) for k in _KAIMAL_COEFFICIENTS}     # retained, never redrawn
        else:
            ref = np.array(common["reference_mean_world"])
            speed = float(np.linalg.norm(ref))
            if not speed > 0.0:
                raise WindTurbineLoadError("a Kaimal wind needs a nonzero declared mean (speed and direction)")
            try:
                real = generate_kaimal_realization(mean_speed=speed, direction=ref / speed, period=params["duration"],
                                                   **{k: params[k] for k in ("turbulence_intensity", "integral_scale",
                                                                             "seed", "max_frequency")})
            except WindError as error:
                raise WindTurbineLoadError(f"Kaimal generation: {error}") from None
            coefficients = dict(generator=KAIMAL_GENERATOR_VERSION, frequencies=tuple(real.frequencies.tolist()),
                                bin_energy=tuple(real.bin_energy.tolist()), amplitudes=tuple(real.amplitudes.tolist()),
                                phases=tuple(real.phases.tolist()), missing_low_variance=real.missing_low_variance,
                                missing_high_variance=real.missing_high_variance)
        return cls(**common, **params, **coefficients)

    # ------------------------------------------------------------------ evaluation (ANYloads)
    def attachment_node(self, fe_model):
        node = fe_model.mesh.get_node(self.hub_node_id)
        if node is None or len(node.dofs) != 6:
            raise WindTurbineLoadError("turbine attachment requires an existing six-DOF structural node")
        elements = fe_model.mesh.elements
        if not any(self.hub_node_id in e.node_ids for e in elements.values()):
            raise WindTurbineLoadError("turbine attachment node is not connected to a structural element")
        if self.attachment_element_id is not None:
            element = elements.get(self.attachment_element_id)
            if element is None or self.hub_node_id not in element.node_ids:
                raise WindTurbineLoadError("turbine attachment node does not belong to the selected structural element")
        return node

    def resolved_hub_body(self, fe_model):
        node = self.attachment_node(fe_model)
        return np.asarray(self.hub_body) + (np.asarray(node.coords()) if self.hub_reference == "attachment" else 0.)

    def thrust_curve(self):
        from anyloads.wind_turbine import ThrustCurve
        return ThrustCurve(np.array(self.thrust_speeds), np.array(self.thrust_values))

    def rotor_axis_body(self) -> np.ndarray:
        """Unit rotor axis (body frame): the explicit axis, or ANYloads ``from_yaw_tilt`` (no local re-expression)."""
        if self.rotor_axis is not None:
            return np.array(self.rotor_axis, dtype=float)
        from anyloads.wind_turbine import TurbineLoadApplicator
        applicator = TurbineLoadApplicator.from_yaw_tilt(np.array(self.hub_body), yaw=math.radians(self.rotor_yaw_deg),
                                                         tilt=math.radians(self.rotor_tilt_deg))
        return np.asarray(applicator.rotor_axis_body, dtype=float)

    def kaimal_realization(self):
        """The retained continuous realization rebuilt from its stored coefficients (validated by ANYloads)."""
        if self.wind_mode != "kaimal":
            return None
        from anyloads.wind_turbine import KaimalRealization
        ref = np.array(self.reference_mean_world)
        speed = float(np.linalg.norm(ref))
        return KaimalRealization(speed, self.turbulence_intensity, self.integral_scale, ref / speed, self.seed,
                                 self.duration, self.max_frequency, np.array(self.frequencies),
                                 np.array(self.bin_energy), np.array(self.amplitudes), np.array(self.phases),
                                 self.missing_low_variance, self.missing_high_variance)

    def wind_history(self, times):
        """World wind on an integration grid: ``(times, velocity, reference_mean, provenance, realization)``.

        steady -> the declared mean on the grid; import -> the OWNED samples, which must cover the whole grid (no
        extrapolation; the transient interpolates them); kaimal -> the SAME retained realization sampled on the grid
        (ANYloads refuses aliasing and any time outside ``[0, duration]``; it is never looped or regenerated)."""
        from anyloads.wind_turbine import WindError, WindHistory
        t = _array(times, "integration times", ndim=1)
        if t.size < 2 or np.any(np.diff(t) <= 0.0):
            raise WindTurbineLoadError(f"wind turbine {self.name!r}: the integration grid needs >= 2 strictly "
                                       "increasing times")
        ref = np.array(self.reference_mean_world)
        turbine = dict(id=self.id, name=self.name, content_hash=self.content_hash())
        try:
            if self.wind_mode == "steady":
                return (t.copy(), np.tile(ref, (t.size, 1)), ref,
                        dict(kind="steady", mean_world=ref.tolist(), turbine=turbine), None)
            if self.wind_mode == "import":
                hist = WindHistory.from_vectors(np.array(self.import_times), np.array(self.import_velocity),
                                                reference_mean=ref, source=self.import_source or "imported")
                if hist.times[0] > t[0] or hist.times[-1] < t[-1]:
                    raise WindTurbineLoadError(
                        f"wind turbine {self.name!r}: imported wind [{hist.times[0]!r}, {hist.times[-1]!r}] s does not "
                        f"cover the integration grid [{t[0]!r}, {t[-1]!r}] s (no extrapolation)")
                return (hist.times, hist.velocity, ref,
                        dict(kind="import", source=self.import_source, sha256=self.samples_sha256(),
                             n_samples=int(hist.times.size), turbine=turbine), None)
            real = self.kaimal_realization()
            hist = real.to_wind_history(t)
            return hist.times, hist.velocity, ref, dict(realization_record(real), turbine=turbine), real
        except WindError as error:
            raise WindTurbineLoadError(f"wind turbine {self.name!r}: {error}") from None

    # ------------------------------------------------------------------ identity / persistence
    def samples_sha256(self) -> str:
        h = hashlib.sha256(b"anyfem.wind_turbine.import_samples.v1")
        h.update(np.ascontiguousarray(np.array(self.import_times, dtype="<f8")).tobytes())
        h.update(np.ascontiguousarray(np.array(self.import_velocity, dtype="<f8")).tobytes())
        return h.hexdigest()

    def content_hash(self) -> str:
        """Physical content identity (name and id excluded)."""
        data = {k: v for k, v in self.to_dict().items() if k not in ("id", "name")}
        payload = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(b"anyfem.wind_turbine_load.v1" + payload.encode()).hexdigest()

    def to_dict(self) -> dict:
        def plain(value):
            if isinstance(value, tuple):
                return [plain(x) for x in value]
            return value
        return {f.name: plain(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WindTurbineLoad":
        if not isinstance(data, Mapping):
            raise WindTurbineLoadError("a wind turbine load record must be an object")
        allowed = {f.name for f in fields(cls)}
        extra = set(data) - allowed
        if extra:
            raise WindTurbineLoadError(f"unknown wind turbine load fields {sorted(extra)}")
        missing = {"id", "name", "thrust_speeds", "thrust_values", "hub_body", "hub_node_id", "wind_mode",
                   "reference_mean_world"} - set(data)
        if missing:
            raise WindTurbineLoadError(f"missing wind turbine load fields {sorted(missing)}")
        return cls(**dict(data))
