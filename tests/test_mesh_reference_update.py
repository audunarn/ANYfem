from types import SimpleNamespace

import numpy as np
import pytest
from anygeometry.entities import EntityRef

from anyfem.mesh_reference_update import stage_mesh_references
from anyfem.model.attributes import LoadCase, Mass
from anyfem.model.project import Project
from anyfem.model.records import OutputRequest
from anyfem.model.regions import ElementFaceRef, ManualRegion, MeshEntityRef, Region, RegionRef
from anyfem.model.wind_turbine import WindTurbineLoad
from anyfem.selection import MeshEntityRef as SelRef


def mesh(nodes, quads=None, beams=None, moved=()):
    coords = {n: np.array([float(n), 0.0, 0.0]) for n in nodes}
    for n in moved:
        coords[n] = coords[n] + 0.5
    return SimpleNamespace(nodes=coords, quads=dict(quads or {}), tris={}, beams=dict(beams or {}), couplings={})


OLD = mesh(range(1, 9), quads={1: (1, 2, 3, 4), 2: (3, 4, 5, 6)}, beams={3: (7, 8)})
# element 2 retired, element 5 new; node 6 moved; beam 3 kept
NEW = mesh(range(1, 11), quads={1: (1, 2, 3, 4), 5: (4, 3, 9, 10)}, beams={3: (7, 8)}, moved=(6,))
RETAINED_N = {1, 2, 3, 4, 7, 8}
RETAINED_E = {1, 3}


def stage(project, **kw):
    args = dict(parent_id="m1", child_id="m2", old_mesh=OLD, new_mesh=NEW,
                retained_node_ids=RETAINED_N, retained_element_ids=RETAINED_E)
    args.update(kw)
    return stage_mesh_references(project, **args)


def project_with(*anchors, mesh_id="m1"):
    project = Project()
    region = Region("r", "mesh", "element", ManualRegion(anchors), mesh_id=mesh_id)
    project.regions.add(region)
    case = project.load_cases.setdefault("c", LoadCase("c"))
    case.add_pressure(EntityRef("face", 1), 2.0, region=RegionRef(region.id))
    return project, region


def test_retained_pressure_face_advances_and_undo_restores_original():
    project, region = project_with(ElementFaceRef("m1", 1, 0), MeshEntityRef("m1", "element", 3))
    staged = stage(project)
    assert staged.diagnostics == ()
    staged.apply(project)
    new = project.regions[region.id]
    assert new is not region and new.mesh_id == "m2"
    assert new.definition.anchors == (ElementFaceRef("m2", 1, 0), MeshEntityRef("m2", "element", 3))
    assert region.mesh_id == "m1" and region.definition.anchors[0].mesh_id == "m1"
    staged.undo(project)
    assert project.regions[region.id] is region
    staged.apply(project)  # redo
    assert project.regions[region.id].mesh_id == "m2"


def test_retired_element_and_moved_node_block_with_actionable_diagnostics():
    project, region = project_with(MeshEntityRef("m1", "element", 2), MeshEntityRef("m1", "node", 6))
    staged = stage(project)
    text = " ".join(staged.diagnostics)
    assert "element 2" in text and "retired" in text and "repair" in text
    assert "node 6" in text
    with pytest.raises(ValueError):
        staged.apply(project)
    assert project.regions[region.id] is region


def test_changed_connectivity_or_family_is_not_retained():
    other = mesh(range(1, 9), quads={1: (1, 2, 4, 3), 2: (3, 4, 5, 6)}, beams={3: (7, 8)})
    project, _ = project_with(MeshEntityRef("m1", "element", 1))
    assert any("connectivity" in d for d in stage(project, new_mesh=other).diagnostics)


def test_foreign_region_blocks_only_when_referenced():
    project, region = project_with(MeshEntityRef("old", "node", 1), mesh_id="old")
    assert any("foreign mesh" in d for d in stage(project).diagnostics)
    unused = Project()
    unused.regions.add(Region("u", "mesh", "node", ManualRegion((MeshEntityRef("old", "node", 1),)), mesh_id="old"))
    assert stage(unused).diagnostics == ()


def test_output_request_region_is_checked():
    project = Project()
    region = Region("o", "mesh", "node", ManualRegion((MeshEntityRef("m1", "node", 5),)), mesh_id="m1")
    project.regions.add(region)
    project.output_requests["o"] = OutputRequest(("displacement",), RegionRef(region.id), "node")
    assert any("node 5" in d for d in stage(project).diagnostics)


def test_explicit_repair_preserves_kind_and_validates_child():
    project, region = project_with(MeshEntityRef("m1", "element", 2))
    ok = stage(project, repairs={SelRef("element", 2): SelRef("element", 5)})
    assert ok.diagnostics == ()
    ok.apply(project)
    assert project.regions[region.id].definition.anchors == (MeshEntityRef("m2", "element", 5),)
    bad_kind = stage(project, repairs={SelRef("element", 2): SelRef("node", 9)})
    assert any("must stay" in d for d in bad_kind.diagnostics)
    project2, _ = project_with(MeshEntityRef("m1", "element", 2))
    missing = stage(project2, repairs={SelRef("element", 2): SelRef("element", 99)})
    assert any("does not exist" in d for d in missing.diagnostics)
    typo = stage(project2, repairs={SelRef("element", 2): SelRef("element", 5), SelRef("node", 42): SelRef("node", 1)})
    assert any("matches no reference" in d for d in typo.diagnostics)


def test_face_repair_validates_local_face():
    project, _ = project_with(ElementFaceRef("m1", 2, 0))
    good = stage(project, repairs={SelRef("element_face", (2, 0)): SelRef("element_face", (5, 0))})
    assert good.diagnostics == ()
    bad = stage(project, repairs={SelRef("element_face", (2, 0)): SelRef("element_face", (5, 4))})
    assert any("not a face" in d for d in bad.diagnostics)


def test_direct_mesh_reference_and_selection():
    project = Project()
    project.masses.append(Mass(ref=SelRef("node", 2), value=1.0))
    project.masses.append(Mass(ref=SelRef("node", 5), value=1.0))
    staged = stage(project, selection=(SelRef("node", 1), SelRef("node", 6), EntityRef("face", 1)))
    assert any("node 5" in d for d in staged.diagnostics)
    assert staged.selection_after == (SelRef("node", 1, "m2"), EntityRef("face", 1))
    repaired = stage(project, repairs={SelRef("node", 5): SelRef("node", 9)})
    original = project.masses[1]
    repaired.apply(project)
    assert project.masses[1].ref == SelRef("node", 9, "m2") and project.masses[0].ref == SelRef("node", 2, "m2")
    repaired.undo(project)
    assert project.masses[1] is original


def test_direct_numbered_reference_round_trips_and_foreign_scope_blocks():
    from anyfem.io.project_file import project_from_dict, project_to_dict

    project = Project()
    project.masses.append(Mass(ref=SelRef("node", 2, "m1"), value=1.0))
    saved = project_from_dict(project_to_dict(project))
    assert saved.masses[0].ref == SelRef("node", 2, "m1")
    staged = stage(saved, repairs={SelRef("node", 2, "m1"): SelRef("node", 9, "m2")})
    assert staged.ok
    staged.apply(saved)
    assert saved.masses[0].ref == SelRef("node", 9, "m2")
    assert any("foreign mesh m2" in problem for problem in stage(saved).diagnostics)


def turbine(hub, element=None):
    return WindTurbineLoad.create("t", thrust_speeds=(0.0, 10.0), thrust_values=(0.0, 1.0), hub_body=(0, 0, 0),
                                  hub_node_id=hub, wind_mode="steady", reference_mean_world=(10.0, 0, 0),
                                  attachment_element_id=element)


def test_wind_turbine_hub_and_attachment_follow_retained_identity_only():
    project = Project()
    project.wind_turbine_loads["a"] = turbine(7, 3)
    assert stage(project).diagnostics == ()
    retired_hub = Project()
    retired_hub.wind_turbine_loads["a"] = turbine(6)
    assert any("hub node 6" in d for d in stage(retired_hub).diagnostics)
    retired_attachment = Project()
    retired_attachment.wind_turbine_loads["a"] = turbine(3, 2)
    assert any("attachment element 2" in d for d in stage(retired_attachment).diagnostics)
    repaired = stage(retired_hub, repairs={("wind_turbine", retired_hub.wind_turbine_loads["a"].id,
                                            "hub_node_id"): 9})
    assert repaired.diagnostics == ()
    original = retired_hub.wind_turbine_loads["a"]
    repaired.apply(retired_hub)
    assert retired_hub.wind_turbine_loads["a"].hub_node_id == 9 and original.hub_node_id == 6
    repaired.undo(retired_hub)
    assert retired_hub.wind_turbine_loads["a"] is original


def test_mesh_ids_are_never_recycled_and_stale_parent_blocked():
    project, region = project_with(MeshEntityRef("m1", "node", 1))
    assert any("never recycled" in d for d in stage(project, child_id="m1").diagnostics)
    project.mesh_records["m2"] = object()
    assert any("already published" in d for d in stage(project).diagnostics)
    del project.mesh_records["m2"]
    project.mesh_numbering = type(project.mesh_numbering)(active_mesh_id="other")
    assert any("stale" in d for d in stage(project).diagnostics)
    NEW._anyfem_mesh_record_id = "zzz"
    try:
        assert any("stale mesh binding" in d for d in stage(project).diagnostics)
    finally:
        del NEW._anyfem_mesh_record_id


def test_geometry_only_project_is_a_noop():
    project = Project()
    staged = stage(project)
    assert staged.diagnostics == () and not staged.changed
    staged.apply(project)
    staged.undo(project)


def test_apply_refuses_when_project_changed_after_staging():
    project, region = project_with(MeshEntityRef("m1", "node", 1))
    staged = stage(project)
    project.regions.replace(Region(region.name, "mesh", "element", region.definition, id=region.id, mesh_id="m1"))
    with pytest.raises(ValueError):
        staged.apply(project)
