"""Plain-data transport for completed results that refuse to pickle.

Why this exists
---------------
A worker process returns its completed result through a pipe, which means
pickling it.  A linear static result (``LinearSolution``) carries the built
solver model, and a model built with the qualified S3 V2D shell formulation
holds *live* element objects whose pickle support is deliberately refused
(``python_pickle_restart`` is blocked pending a successor gate).  The solve is
complete; only the transfer to the application fails.

The refusing element family instead supports stateless plain-data
serialization (``to_dict`` / ``from_dict``, with the solver's own identity and
schema validation inside ``from_dict``).  This module uses exactly those
supported APIs to move a completed *linear* result across the process
boundary: the worker encodes the result into plain data, the parent decodes it
after the worker tree is confirmed retired.

Codec v4: an identity-indexed object graph
------------------------------------------
Every distinct result wrapper, ``BuiltModel``, solver ``FEModel`` and
``FEMesh`` is encoded exactly once in the envelope's ``objects`` table and
referenced elsewhere as ``{"ref": index}``; a batch of N cases therefore
carries one model, not N+1 copies each holding all N load cases.  Ordinary
field values travel *raw* inside the envelope (``{"value": ...}``) so that the
single pickle of the envelope has one memo: a project, history, load-case
list or metadata object shared by several records stays shared after decode,
and ``shape.built.load_case`` stays the very object held in
``fe_model.load_cases``.  Elements are memoized by object identity across the
whole graph (never by element id): one element object held by several meshes
is encoded and rebuilt once (one ``from_dict``) and subscribes to every
reconstructed mesh epoch, while distinct objects with equal ids stay distinct.
Genuinely distinct models stay distinct.

Internal contract: the raw ordinary values (for example the project's
``mappingproxy`` views) require the private transport pickler of
:mod:`anyfem.job_process` (``dumps``/``loads``/``dumps_spooled``).  Standard
``pickle.dumps(envelope)`` compatibility is NOT part of any public result,
project or artifact API and may change with this explicitly versioned
internal codec.  Decoding
rebuilds each distinct object once through its public constructor (so a
reconstructed mesh owns exactly one fresh mutation epoch) and runs the same
validation as before.  Other envelope versions are refused explicitly.

What it deliberately does not do
--------------------------------
* It does not weaken, bypass or re-implement the solver's pickle refusal; the
  live elements are never pickled and no reducer or monkeypatch is installed.
* It does not rebuild physics: nodes, materials, boundary conditions,
  constraint equations, load cases and the DOF manager travel verbatim; only
  the refusing elements are re-constructed by the solver's own ``from_dict``,
  which validates the formulation fingerprints itself.
* It does not carry nonlinear accepted state or history; those results are
  refused with a structured error instead of being silently stripped.  The
  supported scope is the exact linear static classes (``LinearSolution`` and
  ``LinearBatchSolution``).
* It does not transport the solver mesh's private qualified-direct-state
  token; the decoded mesh is rebuilt through the public ``FEMesh``
  constructor, which owns a fresh mutation epoch.

Nothing is dropped silently.  Every dataclass field of a transferred object is
carried (or the transfer fails), the field set is verified on decode, exact
classes are enforced on both sides, and element/node identities must match
their map keys.  The two dynamic attributes the application attaches to a
completed result in the worker (``_requested_global_stress`` and
``_requested_patch_stress``) are carried in a dedicated record on both
``LinearSolution`` and ``LinearBatchSolution``; any other dynamic attribute
fails closed rather than being silently dropped.
"""

from __future__ import annotations

import dataclasses
import importlib
import pickle
from typing import Any, Callable

from .job_process import _TransportPickler

__all__ = [
    "ResultTransportError",
    "encode_result",
    "encode_result_stream",
    "decode_result",
]

CODEC_ID = "anyfem.result_transport"
CODEC_VERSION = 4

_ELEMENT_PICKLE = "pickle"
_ELEMENT_DICT = "element_dict"

_STRESS_NONE = "none"
_STRESS_CARRIED = "carried"

#: Dynamic attributes ``application/workflow.py`` attaches to a completed
#: result in the worker and ``io/result_artifact.py`` reads back by name.
_APPLICATION_EXTRAS = ("_requested_global_stress", "_requested_patch_stress")

#: Sentinel for "no stress cache travelled"; distinct from a carried None.
_NO_CACHE = object()


class ResultTransportError(RuntimeError):
    """A completed result cannot travel through the plain-data transport.

    The message names the concrete boundary (unsupported result type, a field
    with no supported plain-data route, or a decode verification failure) so
    the structured job failure stays actionable.
    """


class _NullSink:
    """Write sink that discards bytes: pickling probes need no storage."""

    def write(self, data) -> int:
        # Protocol 5 may hand over a pickle.PickleBuffer, which has no len().
        return memoryview(data).nbytes


def _probe(value: Any) -> None:
    """Pickle ``value`` into nothing (separate memo); raises if it refuses."""

    _TransportPickler(_NullSink(), protocol=pickle.HIGHEST_PROTOCOL).dump(value)


def _probe_field(owner: str, name: str, value: Any) -> None:
    try:
        _probe(value)
    except Exception as error:
        raise ResultTransportError(
            f"{owner} field {name!r} cannot be transferred through the "
            f"plain-data transport: {type(error).__name__}: {error}"
        ) from error


def _class_location(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _is_solver_mesh(value: Any) -> bool:
    from anysolver.fe_core import FEMesh

    return type(value) is FEMesh


def _extras_classes() -> tuple:
    from .post.results import LinearBatchSolution, LinearSolution

    return (LinearSolution, LinearBatchSolution)


# ----------------------------------------------------------------------
# worker side: encode
# ----------------------------------------------------------------------
def encode_result(result: Any, *, verify: bool = True) -> dict[str, Any]:
    """Encode a completed result into an identity-indexed plain-data envelope.

    Raises :class:`ResultTransportError` for anything outside the exact
    supported linear static classes, or when a required piece has no supported
    plain-data route.  ``verify`` probes every ordinary value for pickle
    support so the failing field is named at once; the worker's streaming
    route passes ``verify=False`` and diagnoses only if the single envelope
    pickle fails.
    """

    from .post.results import LinearBatchSolution, LinearSolution

    supported = (LinearSolution, LinearBatchSolution)
    if type(result) in supported:
        kind = "LinearSolution" if type(result) is LinearSolution else "LinearBatchSolution"
        encoder = _Encoder(verify)
        root = encoder.object_ref(result, "the completed result")
        return {
            "codec": CODEC_ID,
            "version": CODEC_VERSION,
            "kind": kind,
            "root": root["ref"],
            "objects": encoder.objects,
        }
    if isinstance(result, supported):
        raise ResultTransportError(
            f"a completed {_class_location(type(result))} is a subclass of the "
            "supported linear static result family; its extra fields cannot be "
            "carried without verification, so the transfer refuses it rather "
            "than silently reducing it to its base class"
        )
    raise ResultTransportError(
        f"a completed {type(result).__name__} has no supported plain-data "
        "transfer; the supported scope is the exact linear static family "
        "(LinearSolution, LinearBatchSolution). Nonlinear accepted state and "
        "history are not carried by the stateless element serialization and "
        "are refused rather than stripped"
    )


def encode_result_stream(result: Any, serialize: Callable[[dict[str, Any]], Any]) -> Any:
    """Encode ``result`` and hand the envelope to ``serialize`` (a spooling dump).

    A serialization failure that is not a transport/spool error (those are
    ``OSError`` subclasses and propagate untouched: size cap, cancellation and
    disk failures are not codec refusals) is diagnosed field by field so the
    structured failure names the offending piece.
    """

    envelope = encode_result(result, verify=False)
    try:
        return serialize(envelope)
    except OSError:
        raise
    except Exception as error:
        _diagnose(envelope)
        raise ResultTransportError(
            f"the encoded result could not be serialized: {type(error).__name__}: {error}"
        ) from error


def _diagnose(envelope: dict[str, Any]) -> None:
    """Re-probe every ordinary value, raising the first named refusal."""

    for record in envelope["objects"]:
        owner = record["class"].rsplit(".", 1)[-1]
        for name, encoded in record["values"].items():
            if isinstance(encoded, dict) and "value" in encoded:
                _probe_field(owner, name, encoded["value"])
        for name, value in record.get("application_extras", {}).items():
            _probe_field(owner, name, value)


class _Encoder:
    """Identity-indexed object table builder."""

    def __init__(self, verify: bool) -> None:
        self.verify = verify
        self.objects: list[dict[str, Any] | None] = []
        self._index: dict[int, int] = {}
        self._keep: list[Any] = []  # ids stay unique while encoding
        #: id(element) -> (element, record); the element keeps its id alive.
        self._element_records: dict[int, tuple[Any, dict[str, Any]]] = {}

    def object_ref(self, obj: Any, owner: str) -> dict[str, int]:
        key = id(obj)
        found = self._index.get(key)
        if found is None:
            found = len(self.objects)
            self.objects.append(None)  # reserved before children: stable indices
            self._index[key] = found
            self._keep.append(obj)
            self.objects[found] = self._dataclass(obj, owner)
        return {"ref": found}

    def _value(self, owner: str, name: str, value: Any) -> dict[str, Any]:
        if self.verify:
            _probe_field(owner, name, value)
        return {"value": value}

    def _dataclass(self, obj: Any, owner: str) -> dict[str, Any]:
        """Encode every dataclass field of ``obj``; nothing is dropped silently."""

        cls = type(obj)
        values: dict[str, Any] = {}
        for field in dataclasses.fields(cls):
            name = field.name
            value = getattr(obj, name)
            if name == "built":
                values[name] = self.object_ref(_require_built(value), "the built model")
            elif name == "fe_model":
                values[name] = self.object_ref(_require_fe_model(value), "the FE model")
            elif name == "mesh" and _is_solver_mesh(value):
                # FEModel.mesh is the solver FEMesh; BuiltModel.mesh is the
                # application mesh, which travels as an ordinary value.
                values[name] = self.object_ref(_require_mesh(value), "the FE mesh")
            elif name == "shapes":
                values[name] = {
                    "list": [self.object_ref(_require_shape(shape), "a batch shape")
                             for shape in value]
                }
            elif name == "elements":
                values[name] = self._elements(value)
            elif name == "_stress":
                values[name] = self._stress(obj)
            else:
                values[name] = self._value(owner, name, value)
        record = {
            "class": _class_location(cls),
            "fields": [field.name for field in dataclasses.fields(cls)],
            "values": values,
        }
        if cls in _extras_classes():
            record["application_extras"] = self._extras(obj, owner)
        return record

    def _stress(self, solution: Any) -> dict[str, Any]:
        """The recovered-stress cache: carried exactly, or the transfer fails."""

        stress = getattr(solution, "_stress", None)
        if stress is None:
            return {"state": _STRESS_NONE}
        if self.verify:
            _probe_field("the completed result", "_stress", stress)
        return {"state": _STRESS_CARRIED, "value": stress}

    def _extras(self, solution: Any, owner: str) -> dict[str, Any]:
        """Carry the application's two known requested-stress caches, or fail.

        Any other dynamic attribute fails closed: the transport cannot verify
        undocumented state, so it refuses rather than silently drop it.
        """

        field_names = {field.name for field in dataclasses.fields(type(solution))}
        instance_attrs = set(vars(solution))
        unexpected = sorted(instance_attrs - field_names - set(_APPLICATION_EXTRAS))
        if unexpected:
            raise ResultTransportError(
                f"{owner} carries unsupported dynamic attribute(s) {unexpected}; "
                "the transfer refuses rather than silently dropping state it "
                "cannot verify"
            )
        encoded: dict[str, Any] = {}
        for name in _APPLICATION_EXTRAS:
            if name in instance_attrs:
                value = getattr(solution, name)
                if self.verify:
                    _probe_field(owner, name, value)
                encoded[name] = value
        return encoded

    def _elements(self, elements: Any) -> dict[int, dict[str, Any]]:
        """The elements, each through its own supported route.

        An element that pickles travels as itself (inside the envelope's single
        memo).  An element that refuses pickle travels through its own
        ``to_dict`` with its class location recorded for dispatch; the
        solver's ``from_dict`` re-validates the identity fingerprints on
        decode.  An element that offers neither route fails the transfer.
        """

        encoded: dict[int, dict[str, Any]] = {}
        for key, element in elements.items():
            element_id = int(key)
            # Memo by object identity (never by element id): the same element
            # object held by several meshes shares ONE record dict, which the
            # envelope's single pickle memo keeps shared, so the decoder can
            # rebuild it once.  Equal ids of distinct objects stay distinct.
            shared = self._element_records.get(id(element))
            if shared is not None:
                encoded[element_id] = shared[1]
                continue
            try:
                _probe(element)
            except Exception:  # noqa: BLE001 - the refusal is the supported signal
                to_dict = getattr(element, "to_dict", None)
                from_dict = getattr(type(element), "from_dict", None)
                if not callable(to_dict) or not callable(from_dict):
                    raise ResultTransportError(
                        f"element {element_id} ({type(element).__name__}) refuses "
                        "pickle and offers no supported to_dict/from_dict "
                        "plain-data API, so the completed result cannot be "
                        "transferred"
                    ) from None
                encoded[element_id] = {
                    "codec": _ELEMENT_DICT,
                    "module": type(element).__module__,
                    "qualname": type(element).__qualname__,
                    "payload": to_dict(),
                }
            else:
                encoded[element_id] = {"codec": _ELEMENT_PICKLE, "value": element}
            self._element_records[id(element)] = (element, encoded[element_id])
        return encoded


def _require_shape(shape: Any) -> Any:
    from .post.results import LinearSolution

    if type(shape) is not LinearSolution:
        raise ResultTransportError(
            f"a linear batch contains a shape of type {type(shape).__name__}, "
            "which is outside the exact LinearSolution family; the transfer "
            "refuses it rather than transferring it with unverified fields"
        )
    return shape


def _require_built(built: Any) -> Any:
    from .solve.build import BuiltModel

    if type(built) is not BuiltModel:
        raise ResultTransportError(
            f"the completed result carries a built model of type "
            f"{_class_location(type(built))}; only the exact BuiltModel "
            "dataclass is supported, and anything else is refused rather "
            "than transferred with unverified fields"
        )
    return built


def _require_fe_model(fe_model: Any) -> Any:
    from anysolver.fe_core import FEModel

    if type(fe_model) is not FEModel:
        raise ResultTransportError(
            f"the completed result carries an FE model of type "
            f"{_class_location(type(fe_model))}; only the exact solver FEModel "
            "is supported, and anything else is refused rather than "
            "transferred with unverified fields"
        )
    return fe_model


def _require_mesh(mesh: Any) -> Any:
    from anysolver.fe_core import FEMesh

    if type(mesh) is not FEMesh:
        raise ResultTransportError(
            f"the completed result carries an FE mesh of type "
            f"{_class_location(type(mesh))}; only the exact solver FEMesh is "
            "supported, and anything else is refused rather than transferred "
            "with unverified fields"
        )
    return mesh


# ----------------------------------------------------------------------
# application side: decode
# ----------------------------------------------------------------------
def decode_result(envelope: dict[str, Any]) -> Any:
    """Rebuild a completed result from its plain-data envelope.

    Called in the waiting job thread after the worker tree is confirmed
    retired and before the result is returned/attached to the job record.
    Every verification below fails closed: a result that cannot be proven
    identical in structure is a transfer failure, never a silently different
    model.
    """

    if not isinstance(envelope, dict):
        raise ResultTransportError("the encoded result is not a mapping")
    if envelope.get("codec") != CODEC_ID or envelope.get("version") != CODEC_VERSION:
        raise ResultTransportError(
            f"unsupported result transport codec {envelope.get('codec')!r} "
            f"(version {envelope.get('version')!r}; this build reads version "
            f"{CODEC_VERSION})"
        )
    objects = envelope.get("objects")
    if not isinstance(objects, list):
        raise ResultTransportError("the encoded result has no object table")
    kind = envelope.get("kind")
    if kind == "LinearSolution":
        from .post.results import LinearSolution as cls
    elif kind == "LinearBatchSolution":
        from .post.results import LinearBatchSolution as cls
    else:
        raise ResultTransportError(f"unknown encoded result kind {kind!r}")
    return _Decoder(objects).get({"ref": envelope.get("root")}, cls, "the completed result")


class _Decoder:
    """Rebuilds each distinct object of the table exactly once."""

    def __init__(self, objects: list) -> None:
        self.objects = objects
        self._done: dict[int, tuple[Any, type]] = {}
        self._active: set[int] = set()
        #: id(record dict) -> (record, element): shared records rebuild once.
        self._rebuilt: dict[int, tuple[Any, Any]] = {}

    def elements(self, encoded: Any) -> dict[int, Any]:
        return _decode_elements(encoded, self._rebuilt)

    def get(self, encoded: Any, cls: type, owner: str) -> Any:
        index = encoded.get("ref") if isinstance(encoded, dict) else None
        if type(index) is not int or not 0 <= index < len(self.objects):
            raise ResultTransportError(f"{owner} has no valid object reference")
        found = self._done.get(index)
        if found is not None:
            if found[1] is not cls:
                raise ResultTransportError(
                    f"{owner} references an object already decoded as "
                    f"{_class_location(found[1])}, not {_class_location(cls)}"
                )
            return found[0]
        if index in self._active:
            raise ResultTransportError(f"{owner} references itself through a cycle")
        self._active.add(index)
        try:
            obj = self._build(self.objects[index], cls, owner)
        finally:
            self._active.discard(index)
        self._done[index] = (obj, cls)
        return obj

    def is_mesh(self, encoded: Any) -> bool:
        from anysolver.fe_core import FEMesh

        index = encoded.get("ref") if isinstance(encoded, dict) else None
        if type(index) is not int or not 0 <= index < len(self.objects):
            return False
        record = self.objects[index]
        return isinstance(record, dict) and record.get("class") == _class_location(FEMesh)

    def _build(self, record: Any, cls: type, owner: str) -> Any:
        """Rebuild ``cls`` from its encoded record, verifying the field set.

        The encoded field names must exactly match the current class's fields,
        so codec/class drift (or a tampered envelope) fails closed instead of
        guessing.  Init fields go through the supported constructor; init=False
        fields are verified against the encoded value, so derived identity (the
        shell formulation policy) must survive identically or the transfer fails.
        """

        from anysolver.fe_core import FEMesh, FEModel
        from .post.results import LinearSolution
        from .solve.build import BuiltModel

        if not isinstance(record, dict):
            raise ResultTransportError(f"{owner} envelope is not a mapping")
        if record.get("class") != _class_location(cls):
            raise ResultTransportError(
                f"{owner} was encoded as {record.get('class')!r}, not the current "
                f"{_class_location(cls)!r}"
            )
        names = [field.name for field in dataclasses.fields(cls)]
        if record.get("fields") != names:
            raise ResultTransportError(
                f"{owner} was encoded with the field set {record.get('fields')!r}, "
                f"which does not match the current fields {names!r}; the transfer "
                "refuses rather than guess which fields changed"
            )
        values = record.get("values")
        if not isinstance(values, dict) or set(values) != set(names):
            raise ResultTransportError(
                f"{owner} envelope values do not cover its fields exactly"
            )
        kwargs: dict[str, Any] = {}
        for field in dataclasses.fields(cls):
            if not field.init:
                continue
            name = field.name
            encoded = values[name]
            if name == "built":
                kwargs[name] = self.get(encoded, BuiltModel, "the built model")
            elif name == "fe_model":
                kwargs[name] = self.get(encoded, FEModel, "the FE model")
            elif name == "mesh" and self.is_mesh(encoded):
                kwargs[name] = self.get(encoded, FEMesh, "the FE mesh")
            elif name == "shapes":
                kwargs[name] = _decode_shapes(self, encoded)
            elif name == "elements":
                kwargs[name] = self.elements(encoded)
            elif name == "_stress":
                stress = _decode_stress_cache(encoded)
                if stress is not _NO_CACHE:
                    kwargs[name] = stress
            else:
                kwargs[name] = _plain(encoded, owner, name)
        obj = cls(**kwargs)
        for field in dataclasses.fields(cls):
            if field.init:
                continue
            expected = _plain(values[field.name], owner, field.name)
            if getattr(obj, field.name) != expected:
                raise ResultTransportError(
                    f"{owner} field {field.name!r} did not survive the transfer "
                    "identically; the result is refused rather than transferred "
                    "changed"
                )
        if cls in _extras_classes():
            _decode_application_extras(record, obj, owner)
        elif "application_extras" in record:
            raise ResultTransportError(
                f"{owner} envelope carries application extras, which only the "
                "exact LinearSolution/LinearBatchSolution records may hold"
            )
        if cls is FEModel:
            _verify_fe_model(obj)
        return obj


def _plain(encoded: Any, owner: str, name: str) -> Any:
    if not isinstance(encoded, dict) or "value" not in encoded:
        raise ResultTransportError(f"{owner} field {name!r} holds no transported value")
    return encoded["value"]


def _decode_shapes(decoder: _Decoder, encoded: Any) -> list:
    from .post.results import LinearSolution

    items = encoded.get("list") if isinstance(encoded, dict) else None
    if not isinstance(items, list):
        raise ResultTransportError("the batch shapes envelope is not a list")
    return [decoder.get(item, LinearSolution, "a batch shape") for item in items]


def _decode_application_extras(record: dict[str, Any], solution: Any, owner: str) -> None:
    """Restore the carried requested-stress caches, presence exactly as encoded.

    A result record must carry the application-extras record (an absent record
    means codec drift, and the transfer fails closed).  Only the two known
    names are accepted; each present name is restored by ``setattr`` and an
    absent name stays absent, so presence versus absence survives exactly.
    """

    encoded = record.get("application_extras")
    if not isinstance(encoded, dict):
        raise ResultTransportError(
            f"{owner} envelope has no application-extras record; the transfer "
            "refuses rather than guess which requested stress caches travelled"
        )
    unexpected = sorted(set(encoded) - set(_APPLICATION_EXTRAS))
    if unexpected:
        raise ResultTransportError(
            f"{owner} envelope carries unknown application extras {unexpected}"
        )
    for name in _APPLICATION_EXTRAS:
        if name in encoded:
            setattr(solution, name, encoded[name])


def _decode_stress_cache(encoded: Any) -> Any:
    if not isinstance(encoded, dict):
        raise ResultTransportError("the stress cache envelope is not a mapping")
    state = encoded.get("state")
    if state == _STRESS_NONE:
        return _NO_CACHE
    if state == _STRESS_CARRIED:
        if "value" not in encoded:
            raise ResultTransportError(
                "the stress cache says it was carried but holds no payload"
            )
        return encoded["value"]
    raise ResultTransportError(f"unknown stress cache state {state!r}")


def _decode_elements(encoded: Any, rebuilt: dict[int, tuple[Any, Any]]) -> dict[int, Any]:
    elements: dict[int, Any] = {}
    for raw_id, record in encoded.items():
        element_id = int(raw_id)
        if not isinstance(record, dict):
            raise ResultTransportError(
                f"element {element_id} has no transport record"
            )
        shared = rebuilt.get(id(record))
        codec = record.get("codec")
        if shared is not None:
            element = shared[1]  # one object per shared record, one from_dict
        elif codec == _ELEMENT_PICKLE:
            if "value" not in record:
                raise ResultTransportError(
                    f"element {element_id} travelled as itself but holds no value"
                )
            element = record["value"]
        elif codec == _ELEMENT_DICT:
            element = _decode_dict_element(element_id, record)
        else:
            raise ResultTransportError(
                f"element {element_id} has unknown transport codec {codec!r}"
            )
        if shared is None:
            rebuilt[id(record)] = (record, element)  # record kept alive: id stays unique
        identity = getattr(element, "element_id", None)
        if identity is None:
            identity = getattr(element, "id", None)
        if identity is None or int(identity) != element_id:
            raise ResultTransportError(
                f"element stored under id {element_id} carries identity "
                f"{identity!r}; the transfer refuses a model whose element "
                "ids do not match their map keys"
            )
        elements[element_id] = element
    return elements


def _decode_dict_element(element_id: int, record: dict[str, Any]) -> Any:
    """One refusing element, rebuilt by its own exact class ``from_dict``.

    The class is located by the recorded module/qualname and the payload is
    handed to the solver's own ``from_dict``, which validates the exact
    class, the schema keys and every identity fingerprint.  The decoded
    element must be exactly the advertised class and must re-encode to the
    identical payload, which proves the configuration survived the round trip
    exactly.
    """

    try:
        module = importlib.import_module(str(record["module"]))
        cls = module
        for part in str(record["qualname"]).split("."):
            cls = getattr(cls, part)
    except (ImportError, AttributeError, KeyError) as error:
        raise ResultTransportError(
            f"element {element_id}'s class {record.get('qualname')!r} from "
            f"module {record.get('module')!r} cannot be loaded: {error}"
        ) from error
    from_dict = getattr(cls, "from_dict", None)
    if not callable(from_dict):
        raise ResultTransportError(
            f"element {element_id}'s class {record.get('qualname')!r} has no "
            "supported from_dict plain-data API"
        )
    try:
        element = from_dict(record["payload"])
    except Exception as error:
        raise ResultTransportError(
            f"element {element_id} refused its encoded payload: "
            f"{type(error).__name__}: {error}"
        ) from error
    if type(element) is not cls:
        raise ResultTransportError(
            f"element {element_id} decoded to {_class_location(type(element))}, "
            f"not the advertised exact class {_class_location(cls)}"
        )
    to_dict = getattr(element, "to_dict", None)
    if not callable(to_dict) or to_dict() != record["payload"]:
        raise ResultTransportError(
            f"element {element_id} did not survive the plain-data round trip "
            "identically; the result is refused rather than transferred changed"
        )
    return element


def _verify_fe_model(fe_model: Any) -> None:
    """Fail closed unless the decoded model is structurally the encoded one.

    The DOF manager and nodes travelled verbatim, so any mismatch here means
    the envelope was damaged in transit; that is a transfer failure, never a
    quietly remapped model (the displacement vector is indexed by these DOFs).
    """

    mesh = fe_model.mesh
    manager = mesh.dof_manager
    seen_dofs: set[int] = set()
    for node_id, node in mesh.nodes.items():
        if int(getattr(node, "id", -1)) != int(node_id):
            raise ResultTransportError(
                f"node stored under id {node_id} carries id "
                f"{getattr(node, 'id', None)!r}; the transfer refuses a model "
                "whose node ids do not match their map keys"
            )
        dofs = list(node.dofs)
        if dofs != list(manager.get_node_dofs(int(node_id))):
            raise ResultTransportError(
                f"node {node_id}'s DOF mapping did not survive the transfer"
            )
        if len(dofs) != len(set(dofs)) or any(dof in seen_dofs for dof in dofs):
            raise ResultTransportError(
                f"node {node_id} carries a duplicated or shared DOF"
            )
        seen_dofs.update(dofs)
    if seen_dofs != set(range(manager.total_dofs)):
        raise ResultTransportError(
            "the decoded DOF mapping does not cover the model's DOFs exactly"
        )
    for element_id, element in mesh.elements.items():
        node_ids = getattr(element, "node_ids", ())
        missing = [int(n) for n in node_ids if int(n) not in mesh.nodes]
        if missing:
            raise ResultTransportError(
                f"element {element_id} references missing nodes {missing}"
            )
        material_name = getattr(element, "material_name", None)
        if material_name is not None and material_name not in fe_model.materials:
            raise ResultTransportError(
                f"element {element_id} references missing material "
                f"{material_name!r}"
            )
