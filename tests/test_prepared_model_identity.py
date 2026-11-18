"""Allowlist certification that two prepared models (serial vs thread-route ON) are equal.

Only the fields derived from the random per-run ``model_id`` may differ; every other difference, and any allowlisted field that
differs in a way a different ``model_id`` cannot explain, is a typed refusal (fail closed).
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prepared_model_identity", ROOT / "tools" / "prepared_model_identity.py")
pmi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pmi)

PLANNING = "anygeometry:intersection_planning"


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def seal(doc: dict) -> dict:
    """Recompute route and model checksums (independent re-implementation of the producer's formulas)."""
    pub = doc["extensions"][PLANNING]["publications"][0]
    pub["route_checksum"] = _sha([pub["content_checksum"], pub["planning_mode"], pub["certificate_version"], pub["r_max"],
                                  pub["seam_policy"], pub["seam_policy_version"]])
    body = {k: v for k, v in doc.items() if k != "checksum"}
    text = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    doc["checksum"] = {"algorithm": "sha256", "value": hashlib.sha256(text.encode()).hexdigest()}
    return doc


def build(tag: str) -> dict:
    """A tiny model whose model_id-derived fields are all a function of ``tag``."""
    return seal({
        "model_id": f"id-{tag}", "revision": 1, "schema": "x", "version": 1,
        "faces": {"1": {"coords": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.5]]}}, "groups": {"g": [1, 2, 3]},
        "extensions": {PLANNING: {"publications": [{
            "planning_mode": "exact", "seam_policy": "ring_split", "seam_policy_version": "seam-ring-split-1",
            "certificate_version": None, "r_max": None, "route_tolerance": None, "source_revision": 1,
            "source_checksum": _sha(["src", tag]), "content_checksum": _sha(["content", tag])}]}},
    })


def pub(doc: dict) -> dict:
    return doc["extensions"][PLANNING]["publications"][0]


def refusal(a, b) -> str:
    with pytest.raises(pmi.IdentityRefusal) as info:
        pmi.certify(a, b)
    return info.value.code


def test_identical_models_certify():
    a = build("a")
    cert = pmi.certify(a, copy.deepcopy(a))
    assert cert["differing_allowlisted_fields"] == []


def test_models_differing_only_in_model_id_derived_fields_certify():
    cert = pmi.certify(build("a"), build("b"))
    assert sorted(cert["differing_allowlisted_fields"]) == sorted(pmi.ALLOWLIST)
    assert len(pmi.ALLOWLIST) == 5


def test_allowlist_is_explicit_paths_not_a_pattern():
    assert all(isinstance(p, tuple) and all(isinstance(s, (str, int)) for s in p) for p in pmi.ALLOWLIST)
    assert ("model_id",) in pmi.ALLOWLIST and ("faces",) not in pmi.ALLOWLIST


def test_mutated_non_allowlisted_field_is_refused():
    a, b = build("a"), build("b")
    b["faces"]["1"]["coords"][1][2] = 0.5000000001
    assert refusal(a, seal(b)) == "non-allowlisted-difference"


def test_missing_and_extra_keys_and_type_changes_are_refused():
    a, b = build("a"), build("b")
    del b["groups"]
    assert refusal(a, seal(b)) == "non-allowlisted-difference"
    b = build("b"); b["extra"] = 1
    assert refusal(a, seal(b)) == "non-allowlisted-difference"
    b = build("b"); b["revision"] = "1"
    assert refusal(a, seal(b)) == "non-allowlisted-difference"
    b = build("b"); b["groups"]["g"] = [1, 2]
    assert refusal(a, seal(b)) == "non-allowlisted-difference"


def test_other_publication_fields_are_not_masked():
    a, b = build("a"), build("b")
    pub(b)["seam_policy"] = "baseline"
    assert refusal(a, seal(b)) == "non-allowlisted-difference"
    b = build("b"); pub(b)["source_revision"] = 2
    assert refusal(a, seal(b)) == "non-allowlisted-difference"


def test_allowlisted_field_differing_with_equal_model_id_is_refused():
    a, b = build("a"), build("a")
    pub(b)["source_checksum"] = _sha("other")
    assert refusal(a, seal(b)) == "allowlist-not-model-id-derived"


def test_model_id_differs_but_a_derived_field_does_not_is_refused():
    a, b = build("a"), build("b")
    pub(b)["source_checksum"] = pub(a)["source_checksum"]
    assert refusal(a, seal(b)) == "allowlist-not-model-id-derived"


def test_stale_model_checksum_is_refused():
    a, b = build("a"), build("b")
    b["checksum"]["value"] = "0" * 64
    assert refusal(a, b) == "model-checksum-invalid"


def test_route_checksum_not_derived_from_its_content_is_refused():
    a, b = build("a"), build("b")
    pub(b)["route_checksum"] = "1" * 64
    body = {k: v for k, v in b.items() if k != "checksum"}
    b["checksum"]["value"] = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    assert refusal(a, b) == "route-checksum-not-derived"


def test_publication_count_other_than_one_is_refused():
    a, b = build("a"), build("b")
    b["extensions"][PLANNING]["publications"].append(copy.deepcopy(pub(b)))
    assert refusal(a, seal(b)) == "unsupported-structure"


def test_non_model_documents_are_refused():
    assert refusal({}, build("a")) == "unsupported-structure"
    assert refusal(build("a"), []) == "unsupported-structure"


SAVED = Path(os.environ.get("ANYFEM_ROUTE_SAVED", "C:/ANYworkspaceWork/pinbump-20261116/measure"))


@pytest.mark.skipif(not (SAVED / "final-on/out/prepared-model.json").exists(), reason="saved tripod outputs not on this machine")
def test_saved_tripod_route_on_equals_serial_modulo_model_id_derived_fields():
    load = lambda n: json.loads((SAVED / n / "out/prepared-model.json").read_text())
    serial, on = load("nc-serial"), load("final-on")
    cert = pmi.certify(serial, on)
    assert sorted(cert["differing_allowlisted_fields"]) == sorted(pmi.ALLOWLIST)
    assert pmi.certify(load("serial"), serial)["differing_allowlisted_fields"]       # serial vs serial differs in exactly those too
    mutated = copy.deepcopy(on)
    mutated["revision"] = mutated["revision"] + 1
    with pytest.raises(pmi.IdentityRefusal) as info:
        pmi.certify(serial, seal_any(mutated))
    assert info.value.code == "non-allowlisted-difference"


def seal_any(doc: dict) -> dict:
    body = {k: v for k, v in doc.items() if k != "checksum"}
    text = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)
    doc["checksum"] = {"algorithm": "sha256", "value": hashlib.sha256(text.encode()).hexdigest()}
    return doc


def test_cli_exit_codes(tmp_path):
    a, b, bad = build("a"), build("b"), build("b")
    bad["revision"] = 2
    paths = {}
    for name, doc in (("a", a), ("b", b), ("bad", seal(bad))):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(doc))
    assert pmi.main([str(paths["a"]), str(paths["b"])]) == 0
    assert pmi.main([str(paths["a"]), str(paths["bad"])]) == 1
    assert pmi.main([str(paths["a"]), str(tmp_path / "missing.json")]) == 2
