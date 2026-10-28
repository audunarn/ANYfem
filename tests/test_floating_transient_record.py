"""Immutable transient records: status vocabulary, hashed safe save/load, header-first refusals, input roundtrip."""
import json

import numpy as np
import pytest

from anyfem.application import floating_transient_record as rec


def test_applied_transient_references_are_protected_from_older_readers(tmp_path, monkeypatch):
    from anyfem import Project
    from anyfem.model.project import FloatingTransientResultRef
    from anyfem.io import project_file as io
    project = Project()
    assert io.project_to_dict(project) == io._base_project_to_dict(project)
    project.floating_transient_results["wind"] = FloatingTransientResultRef(
        name="wind", result_path="result.npz", result_hash="a" * 64,
        status="complete", hydrodynamic_verification="accepted",
        frame_indices=(0,), frame_times=(0.0,))
    path = tmp_path / "wind.anyfem"
    io.save_project(project, path)
    assert io.load_project(path).floating_transient_results["wind"].result_hash == "a" * 64
    data = io.project_to_dict(project)
    assert data["anyfem"]["format"] == 17
    monkeypatch.setattr(io, "MAX_READ_FORMAT", 16)
    with pytest.raises(io.ProjectFileError, match="upgrade ANYfem"):
        io._base_project_from_dict(data)


def result(status="complete", verification="accepted", **over):
    n = 5
    args = dict(status=status, message="m", hydrodynamic_verification=verification, unpassed_gates=(),
                times=np.arange(n) * 0.1, displacements=np.zeros((n, 6)), velocities=np.zeros((n, 6)),
                accelerations=np.ones((n, 6)), thrust=np.zeros(n), relative_axial_speed=np.zeros(n),
                frame_indices=np.array([0, 2, 4]), frame_loads={"total": np.ones((3, 2, 6)), "inertia": np.zeros((3, 2, 6))},
                frame_displacements=np.zeros((3, 4)), frame_correction_acceleration=np.zeros((3, 6)),
                diagnostics={"closure": [{"passed": True, "worst": float("inf")}]}, identities={"model": "bar"})
    args.update(over)
    return rec.FloatingTransientResult(**args)


def test_status_vocabulary_cannot_relabel_unverified_hydrodynamics():
    assert result().accepted_structural_output
    with pytest.raises(rec.FloatingTransientRecordError):
        result(status="complete", verification="unverified")
    with pytest.raises(rec.FloatingTransientRecordError):
        result(status="completed_unverified_hydrodynamics", verification="accepted")
    unv = result(status="completed_unverified_hydrodynamics", verification="unverified", unpassed_gates=("passivity",))
    assert not unv.accepted_structural_output and unv.qualification_status == "unqualified"
    assert unv.diagnostics["closure"][0]["worst"] == "inf"                 # non-finite values are explicit strings
    with pytest.raises(ValueError):
        unv.times[0] = 1.0


def test_result_roundtrip_hash_and_tamper_refusal(tmp_path):
    r = result()
    path = str(tmp_path / "r.npz")
    digest = r.save(path)
    back = rec.load_floating_transient_result(path, expected_hash=digest)
    assert back.content_hash() == digest and np.array_equal(back.frame_loads["total"], r.frame_loads["total"])
    with pytest.raises(rec.FloatingTransientRecordError, match="stale"):
        rec.load_floating_transient_result(path, expected_hash="0" * 64)
    with np.load(path, allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    data["thrust"] = data["thrust"] + 1.0
    p2 = str(tmp_path / "t.npz"); np.savez(p2, **data)
    with pytest.raises(rec.FloatingTransientRecordError, match="hash mismatch"):
        rec.load_floating_transient_result(p2)
    data2 = {k: v for k, v in data.items()}
    data2["thrust"] = np.zeros(5); data2["extra"] = np.zeros(10)
    p3 = str(tmp_path / "x.npz"); np.savez(p3, **data2)
    with pytest.raises(rec.FloatingTransientRecordError, match="unexpected"):
        rec.load_floating_transient_result(p3)
    header = json.loads(bytes(data["header"].tobytes()).decode())
    header["meta"]["message"] = "relabelled"                              # any forged metadata breaks the hash
    data3 = dict(data); data3["thrust"] = np.zeros(5)
    data3["header"] = np.frombuffer(json.dumps(header).encode(), dtype=np.uint8)
    p4 = str(tmp_path / "h.npz"); np.savez(p4, **data3)
    with pytest.raises(rec.FloatingTransientRecordError, match="hash mismatch"):
        rec.load_floating_transient_result(p4)
    cut = tmp_path / "c.npz"; cut.write_bytes((tmp_path / "r.npz").read_bytes()[:500])
    with pytest.raises(rec.FloatingTransientRecordError):
        rec.load_floating_transient_result(str(cut))


def test_unverified_status_survives_roundtrip_unchanged(tmp_path):
    r = result(status="completed_unverified_hydrodynamics", verification="unverified", unpassed_gates=("passivity",))
    path = str(tmp_path / "u.npz")
    r.save(path)
    back = rec.load_floating_transient_result(path)
    assert back.status == "completed_unverified_hydrodynamics" and back.unpassed_gates == ("passivity",)
    assert not back.accepted_structural_output
