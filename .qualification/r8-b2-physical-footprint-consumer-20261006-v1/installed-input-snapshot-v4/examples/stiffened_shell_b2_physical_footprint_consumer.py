"""Experimental physical B2 five-row footprint case/Project/static/buckling inputs."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import numpy as np
from scipy import sparse
from anysolver import stiffened_shell as api
from anyfem.solve.stiffened_shell_b2 import ExperimentalB2PhysicalRigidFootprintPanel, save_case, load_case
from anyfem.solve.stiffened_shell_project import point_panel_from_analysis, solve_point_panel
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import save_project, load_project

DOFS = (12, 13, 36, 41)
SELECTION = api.B2PhysicalRigidFootprintSelection(('physical-left',))
RECOVERY = {'beam-right': (0., .5, 1.)}


def definition():
    """Explicit development fixture from the public solver physical-footprint example."""
    nodes = {1:(0.,0.,0.), 2:(1.,0.,0.), 3:(2.,0.,0.),
             4:(0.,1.,0.), 5:(1.,1.,0.), 6:(2.,1.,0.)}
    shells = tuple(api.Q4PanelShell(f'shell-{i}', ids, 1000., .3, .15)
        for i, ids in enumerate(((1,2,5,4), (2,3,6,5))))
    section = api.LinearSection.isotropic(E=2000., nu=.25, area=.04,
        Iy=.04*.2**2/12., Iz=.04*.2**2/12., J=2*.04*.2**2/12., ky=.8, kz=.8)
    members = tuple(api.PhysicalB2Member(name, 'explicit-square-'+name, (10+i,11+i),
        ((.5+i,.5,0.), (1.5+i,.5,0.)), (0.,1.,0.), 'square', section)
        for i, name in enumerate(('beam','beam-right')))
    group = api.B2PhysicalRigidFootprint('physical-left', (1,2,5,4), 10,
        (0.,0.,1.), ((1.,0.), (0.,1.), (0.,0.)), ((1,2,5,4),))
    free = [i for i in range(54) if i not in range(36,42)]
    return api.B2PhysicalRigidFootprintDefinition(api.B2_E4_PL_FAST_LINEAR_V1,
        nodes, shells, members, (group,), sparse.eye(54, format='csr')[:,free], np.zeros(54))


def preload():
    load = np.zeros(54); load[[12,30,48]] = -1.
    return load


def traction_sources():
    """Explicit square-section traction input; evaluation remains solver-owned."""
    a = .1/np.sqrt(3.); yz = np.array(((a,a),(a,-a),(-a,a),(-a,-a)))
    area = .04; moment = .04*.2**2/12.; polar = 2*moment
    mapping = np.zeros((4,3,6))
    for i, (y,z) in enumerate(yz):
        mapping[i,0,(0,4,5)] = (1/area, z/moment, -y/moment)
        mapping[i,1,(1,3)] = (1/area, -z/polar)
        mapping[i,2,(2,3)] = (1/area, y/polar)
    return tuple(api.B2SectionTractionQuadrature(name, 'explicit-square-gauss-'+name,
        yz, np.full(4,.01), mapping) for name in ('beam','beam-right'))


def panel(count=1):
    if type(count) is not int or count not in (1,10,100):
        raise ValueError('explicit compatible development case count 1, 10 or 100 required')
    return ExperimentalB2PhysicalRigidFootprintPanel.from_definition(definition(),
        tuple((f'case-{i}', (i+1)*preload()) for i in range(count)), selected_dofs=DOFS,
        selected_footprints=SELECTION, recover_members=RECOVERY)


def workflow(directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    original = panel()
    standalone = load_case(save_case(original, directory/'physical.anyfem-b2.json'))
    project = Project('physical B2 footprint', mesh_only=True)
    record = AnalysisDefinition.experimental_point_panel('physical footprint', standalone)
    project.add_analysis(record)
    reopened = load_project(save_project(project, directory/'physical.anyfem'))
    record = reopened.analyses[record.id]; restored = point_panel_from_analysis(record)
    if restored.payload != original.payload:
        raise RuntimeError('physical-footprint case/Project inputs changed')
    static = restored.solve_linear('case-0')
    project_batch = solve_point_panel(record)
    direct_owner = api.create_analysis(definition=definition())
    try:
        reference = direct_owner.solve_linear_many((('case-0',preload()),),
            selected_dofs=DOFS, selected_footprints=SELECTION, recover_members=RECOVERY)
    finally:
        direct_owner.close()
    if reference.status != 'completed' or project_batch.status != 'completed':
        raise RuntimeError('public original-definition/Project mapping check failed')
    actual = project_batch.cases[0]; expected = reference.cases[0]
    np.testing.assert_array_equal(actual.selected_displacements, expected.selected_displacements)
    np.testing.assert_array_equal(actual.selected_affine_reactions, expected.selected_affine_reactions)
    if tuple(name for name,values in actual.beam_resultants_local) != tuple(RECOVERY):
        raise RuntimeError('requested member recovery identity changed')
    for (name,values), (expected_name,expected_values) in zip(actual.beam_resultants_local,
                                                           expected.beam_resultants_local):
        if name != expected_name or len(values) != len(RECOVERY[name]):
            raise RuntimeError('requested member/station recovery changed')
        np.testing.assert_array_equal(values, expected_values)
    np.testing.assert_equal(tuple(asdict(row) for row in actual.footprint_wrenches),
                            tuple(asdict(row) for row in expected.footprint_wrenches))
    original_descriptors = [json.loads(json.dumps(group.descriptor()))
                            for group in definition().physical_footprints]
    if restored.to_dict()['physical_footprints'] != original_descriptors:
        raise RuntimeError('complete public physical descriptor/frame changed')
    summaries = []
    for count in (1,10,100):
        batch = project_batch if count == 1 else panel(count).solve_linear_many()
        if batch.status != 'completed':
            raise RuntimeError(batch.failure_reason)
        summaries.append(dict(case_count=len(batch.cases), status=batch.status,
            factorization_count=batch.factorization_count, solve_count=batch.solve_count,
            maximum_rhs_chunk=batch.maximum_rhs_chunk, selected_dofs=list(batch.selected_dofs),
            footprint_law_id=batch.footprint_law_id,
            selected_footprint_counts=[len(case.footprint_wrenches) for case in batch.cases]))
    spectral = restored.buckling(api.B2PhysicalFootprintBucklingRequest(preload(), traction_sources()))
    if type(spectral) is not api.B2PhysicalFootprintBucklingResult:
        raise RuntimeError('physical-footprint buckling result type changed')
    result = dict(status=static.status, project_status=project_batch.status,
        schema=restored.schema, physical_descriptor=restored.to_dict()['physical_footprints'][0],
        input_bytes_equal=True, batches=summaries, buckling_status=spectral.status,
        selected_mapping_matches_original_definition=True,
        footprint_wrenches_match_original_definition=True,
        recovered_footprint_ids=[row.footprint_id for row in actual.footprint_wrenches],
        selected_displacements=list(actual.selected_displacements),
        selected_affine_reactions=list(actual.selected_affine_reactions),
        requested_member_stations={name:list(stations) for name,stations in RECOVERY.items()},
        beam_resultants_local=[[name,[list(row) for row in values]]
                               for name,values in actual.beam_resultants_local],
        buckling_policy=spectral.policy, buckling_failure_reason=spectral.failure_reason,
        load_factors=list(spectral.load_factors), production_qualified=False)
    (directory/'workflow.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', required=True)
    args = parser.parse_args(); print(json.dumps(workflow(args.output), indent=2))
