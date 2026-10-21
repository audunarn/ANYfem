"""Homogeneous MPC clamps may be implied; other support equations stay audited."""
from types import SimpleNamespace
from anyfem.model.project import Project
from anyfem.model.attributes import fixed, support
from anyfem.solve.build import _add_supports, _implied_zero_clamp_nodes


def fixture(condition):
    project = Project()
    identifier = project.geometry.add_point(0,0,0)
    ref = project.point(identifier)
    project.add_support(condition(ref))
    mesh = SimpleNamespace(constrained_nodes_on=lambda _: [1,2], couplings={
        1:SimpleNamespace(beam_node=2,plate_nodes=(1,),weights=(1.,))})
    added = []
    model = SimpleNamespace(add_boundary_condition=lambda value: added.append(value))
    return project,mesh,model,added


def test_full_zero_clamp_keeps_master_without_duplicate_slave_equation():
    project,mesh,model,added = fixture(fixed)
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1]


def test_partial_clamp_is_preserved_for_solver_audit():
    project,mesh,model,added = fixture(lambda ref:support(ref,ux=0))
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1,2]


def test_nonzero_clamp_is_preserved_for_solver_audit():
    project,mesh,model,added = fixture(lambda ref:support(ref,ux=.01,uy=0,uz=0,rx=0,ry=0,rz=0))
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1,2]


def test_unfixed_nonzero_weight_master_prevents_omission():
    project,mesh,model,added = fixture(fixed)
    mesh.couplings[1].plate_nodes = (1,3)
    mesh.couplings[1].weights = (.5,.5)
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1,2]


def test_zero_weight_master_does_not_block_implied_clamp():
    project,mesh,model,added = fixture(fixed)
    mesh.couplings[1].plate_nodes = (1,3)
    mesh.couplings[1].weights = (1.,0.)
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1]


def test_roundoff_weight_master_does_not_block_implied_clamp():
    project,mesh,model,added = fixture(fixed)
    mesh.couplings[1].plate_nodes = (1,3)
    mesh.couplings[1].weights = (1.,8.88e-16)
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1]


def test_local_full_zero_clamp_does_not_establish_implication():
    project,mesh,_,_ = fixture(fixed)
    from dataclasses import replace
    project.supports[0] = replace(project.supports[0], coordinate_system_id="local")
    assert _implied_zero_clamp_nodes(project,mesh) == set()


def test_legacy_pair_coupling_also_implies_zero_clamp():
    project,mesh,model,added = fixture(fixed)
    mesh.couplings[1] = (2,1)
    _add_supports(project,mesh,model)
    assert added[0].node_ids == [1]


def test_separate_fully_implied_slave_scope_adds_no_duplicate_bc():
    project,mesh,model,added = fixture(fixed)
    identifier = project.geometry.add_point(1,0,0)
    project.add_support(fixed(project.point(identifier)))
    mesh.constrained_nodes_on = lambda ref:[1] if ref.id == 1 else [2]
    _add_supports(project,mesh,model)
    assert len(added) == 1 and added[0].node_ids == [1]
