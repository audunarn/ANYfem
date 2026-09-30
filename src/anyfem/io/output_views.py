"""Named, immutable views of requested native result quantities.

Native fields stay available for the workbench. Each requested view is a real
subset with independent association/frame metadata, not a filter on live state.
No basis transformation, recovery, or unavailable value is invented here.
"""

from __future__ import annotations

from dataclasses import replace
from urllib.parse import quote
import numpy as np

from ..model.records import OutputRequest


def _matches(key, descriptor, quantity):
    if quantity == key:
        return True, None
    quantity = quantity.replace(":", ".")
    for separator in (".", "_"):
        if quantity.startswith(key + separator):
            component = quantity[len(key) + 1:]
            if component in descriptor.components:
                return True, component
    family, separator, component = quantity.partition(".")
    if not separator:
        return key.startswith(family + "_"), None
    if key == family and component in descriptor.components:
        return True, component
    return key == family + "_" + component, None


def _view(descriptor, values, source_key, scope, request, component, tables):
    if descriptor.location != request.location:
        raise ValueError(f"location {descriptor.location!r} does not match {request.location!r}")
    if request.basis != descriptor.basis:
        raise ValueError(f"basis {request.basis!r} is unavailable (stored {descriptor.basis!r})")
    if request.recovery not in {"native", descriptor.recovery}:
        raise ValueError(f"recovery {request.recovery!r} is unavailable (stored {descriptor.recovery!r})")
    if request.reduction != "none":
        raise ValueError(f"reduction {request.reduction!r} requires an explicit qualified quantity")
    if request.frame_policy == "selected" and not request.frame_indices:
        raise ValueError("selected-frame requests require explicit frame indices; none are recorded")

    data = np.asarray(values)
    frames = tuple(descriptor.frames)
    if not frames or data.shape[0] != len(frames):
        raise ValueError("stored quantity has no unambiguous frame association")
    association = None
    identifiers = None
    missing = []
    if request.location in {"node", "element", "integration_point", "element_face"}:
        association = "node_ids" if request.location == "node" else "element_ids"
        table = tables.get(f"{source_key}_{association}")
        if table is None:
            raise ValueError("stored quantity has no explicit entity association")
        source_association = np.asarray(table, dtype=np.int64)
        if source_association.ndim == 1:
            source_ids = source_association
        elif request.location == "element_face" and source_association.ndim == 2 and source_association.shape[1] == 2:
            source_ids = source_association[:, 0]
        else:
            raise ValueError("stored entity association has an unsupported layout")
        if data.ndim < 2 or data.shape[1] != len(source_ids):
            raise ValueError("stored entity association does not match the field")
        wanted = scope[association]
        missing = sorted(set(wanted).difference(map(int, source_ids)))
        indices = np.flatnonzero(np.isin(source_ids, wanted))
        if request.location == "element_face" and scope.get("element_faces"):
            if source_association.ndim != 2:
                raise ValueError("stored quantity has no explicit element-face association")
            faces = {tuple(item) for item in scope["element_faces"]}
            missing = sorted(faces.difference(map(tuple, source_association)))
            indices = np.asarray([index for index in indices
                                  if tuple(source_association[index]) in faces], dtype=np.intp)
        if not len(indices):
            raise ValueError("requested region has no entities carrying this quantity")
        data = np.take(data, indices, axis=1)
        identifiers = source_association[indices]

    components = descriptor.components
    unit = descriptor.unit
    if component is not None:
        if data.shape[-1] != len(components):
            raise ValueError("stored component association does not match the field")
        index = components.index(component)
        data = data[..., index:index + 1]
        if unit.startswith("mixed:"):
            units = unit.removeprefix("mixed:").split(",")
            if len(units) == len(components):
                unit = units[index]
            elif len(units) == 2:
                unit = units[1 if component in {"rx", "ry", "rz", "mx", "my", "mz"} else 0]
        components = (component,)
    if request.frame_policy in {"first", "last"}:
        index = 0 if request.frame_policy == "first" else len(frames) - 1
        data = data[index:index + 1]
        frames = (frames[index],)
    elif request.frame_policy == "selected":
        if any(index >= len(frames) for index in request.frame_indices):
            raise ValueError(f"requested frame indices {request.frame_indices} exceed stored range 0..{len(frames)-1}")
        data = np.take(data, request.frame_indices, axis=0)
        frames = tuple(frames[index] for index in request.frame_indices)
    elif request.frame_policy == "envelope":
        # Match ANYfem's existing signed maximum-absolute envelope convention.
        indices = np.argmax(np.abs(data), axis=0)
        data = np.take_along_axis(data, indices[np.newaxis, ...], axis=0)
        frames = (0.0,)

    provenance = {
        **descriptor.provenance,
        "output_request": request.to_dict(),
        "source_quantity": source_key,
        "scope": dict(scope),
        "frame_policy": request.frame_policy,
        "missing_requested_entities": missing,
    }
    if request.frame_policy == "envelope":
        provenance["envelope_source_frames"] = list(descriptor.frames)
        provenance["envelope_convention"] = "signed maximum absolute value per entry"
    return replace(
        descriptor, label=f"{request.label}: {descriptor.label}", components=components,
        unit=unit, frames=frames, deformation_required=False, provenance=provenance,
    ), data, association, identifiers


def add_output_request_views(payload, scopes):
    """Materialize only available requested subsets and disclose refusals."""

    fields = dict(payload.fields)
    tables = dict(payload.tables)
    outcomes = []
    for scope in scopes:
        request = OutputRequest.from_dict(scope["request"])
        outcome = {"request_id": request.id, "label": request.label, "fields": [], "diagnostics": []}
        for quantity in request.quantity_keys:
            matched = False
            for key, (descriptor, values) in payload.fields.items():
                matches, component = _matches(key, descriptor, quantity)
                if not matches:
                    continue
                matched = True
                try:
                    view, data, association, identifiers = _view(
                        descriptor, values, key, scope, request, component, payload.tables,
                    )
                except (KeyError, ValueError, IndexError) as error:
                    outcome["diagnostics"].append(f"{quantity} ({key}): {error}")
                    continue
                view_key = "request_" + ".".join(quote(value, safe="") for value in (request.id, quantity, key))
                fields[view_key] = (replace(view, key=view_key), data)
                if association is not None:
                    tables[f"{view_key}_{association}"] = identifiers
                outcome["fields"].append(view_key)
                if view.provenance["missing_requested_entities"]:
                    outcome["diagnostics"].append(
                        f"{quantity} ({key}): missing requested entities "
                        f"{view.provenance['missing_requested_entities']}"
                    )
            if not matched:
                outcome["diagnostics"].append(f"{quantity}: unavailable in the retained solver output")
        outcome["status"] = (
            "partial" if outcome["fields"] and outcome["diagnostics"] else
            "available" if outcome["fields"] else "unavailable"
        )
        outcomes.append(outcome)
    tables["output_request_outcomes"] = outcomes
    return replace(payload, fields=fields, tables=tables,
                   summary={**payload.summary, "field_keys": sorted(fields),
                            "output_request_outcomes": outcomes},
                   provenance={**payload.provenance, "output_request_outcomes": outcomes})
