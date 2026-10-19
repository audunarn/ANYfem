"""Hash normalization preserves persisted identities and never edits the codec output."""
from copy import deepcopy

from anyfem.document import _document_payload, _model_payload, canonical_hash


def hash_fixture():
    geometry = {
        "schema": "anygeometry", "model_id": "model", "revision": 8,
        "id_state": {"vertex": 4}, "checksum": "old",
        "replacement_history": [[1, 2]], "structural_replacement_history": [],
        "vertices": [{"id": 1, "position": [0.0, 1.0, 2.0]}],
    }
    geometry["features"] = {
        "next_id": 3, "baseline": deepcopy(geometry),
        "records": [{"id": 2, "kind": "geometry.point", "name": "Point",
                     "parameters": {"x": 1.0}, "outputs": {"point": ["vertex", 1]},
                     "state": "ok", "diagnostic": None, "materialization_checksum": "materialized"}],
    }
    return {
        "anyfem": {"schema": "anyfem.project", "revision": "r", "artifact_root": "old"},
        "geometry": geometry, "name": "example", "jobs": [{"name": "run"}],
        "materials": [{"id": "mat", "name": "steel", "E": 210e9}],
        "plate_sections": [{"id": "plate", "name": "thin", "thickness": 0.01}],
        "beam_sections": [{"id": "beam", "name": "small", "area": 0.02}],
        "assignments": {"sections": [{"id": "assignment", "name": "assignment",
                                       "legacy_singleton": True, "section": "plate", "region": "region"}]},
        "face_sections": {"1": "thin"}, "edge_sections": {},
        "ownership": {"sheet_joins": [{"id": "join", "name": "join", "sheets": [1, 2]}]},
        "regions": [{"id": "region", "name": "deck", "hidden": True, "references": [1]}],
        "coordinate_systems": [{"id": "frame", "name": "local", "origin": [1, 2, 3]}],
        "supports": [{"id": "support", "name": "fixed", "region": "region"}],
        "masses": [{"id": "mass", "name": "weight", "mass": 5.0}],
        "wet_surfaces": [{"id": "wet", "name": "hull", "region": "region"}],
        "connections": [{"id": "connection", "name": "joint", "stiffness": 100.0}],
        "imperfections": [{"id": "shape", "name": "bow", "amplitude": 0.001}],
        "meshing": {"refinements": [{"name": "fine", "size": 0.2}]},
    }


def test_hash_payloads_match_preoptimization_contract_and_preserve_codec_data():
    document = hash_fixture()
    original = deepcopy(document)
    # Frozen against the pre-optimization implementation, including nested
    # baseline normalization and all label/identity exclusions.
    assert canonical_hash(_document_payload(document)) == DOCUMENT_HASH
    assert canonical_hash(_model_payload(document)) == MODEL_HASH
    assert document == original


def test_changed_mechanics_and_feature_intent_keep_distinct_hash_effects():
    document = hash_fixture()
    original_document = canonical_hash(_document_payload(document))
    original_model = canonical_hash(_model_payload(document))
    document["geometry"]["features"]["records"][0]["parameters"]["x"] = 9.0
    assert canonical_hash(_document_payload(document)) != original_document
    assert canonical_hash(_model_payload(document)) == original_model
    document["materials"][0]["E"] = 180e9
    assert canonical_hash(_model_payload(document)) != original_model


DOCUMENT_HASH = "sha256:ce0c2129917854e4b25c5e0875f31adfd7a80474c8555da65451b84293b08ab5"
MODEL_HASH = "sha256:56a4470ffce4bf15decc711730be20e54b941bcfcf3b6994cb32620b218a25f7"
