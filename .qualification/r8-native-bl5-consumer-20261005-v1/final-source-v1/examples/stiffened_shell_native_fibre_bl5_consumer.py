"""Explicit experimental ANYfem elastic native-fibre BL5 consumer."""
from dataclasses import replace
import argparse
import json
import numpy as np
from anysolver import b3_ge, stiffened_shell as ss
from anyfem.solve.stiffened_shell import ExperimentalGEBL5Panel, save_case, load_case
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import save_project, load_project
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis


def panel(family='biaxial', beams='mixed', basis='p0', yield_stress=1e20, shell='q4'):
    if family not in ('axial', 'biaxial') or beams not in ('fibre', 'mixed'):
        raise ValueError('axial/biaxial fibres and fibre/mixed member families required')
    if shell not in ('q4','s3','mixed'): raise ValueError('q4/s3/mixed shells required')
    e, nu = 210e9, .3; g = e/(2*(1+nu))
    stiffness = (e*.01, g*.008, g*.008, g*1.6666666666666667e-5,
                 e*8.333333333333333e-6, e*8.333333333333333e-6)
    generalized = b3_ge.EllipsoidalGeneralizedSection(np.diag(stiffness), np.eye(6), 1e20, 1e9)
    arm = np.sqrt(8.333333333333333e-6/.01)
    fibres = tuple(b3_ge.Fibre(str(i), y*arm if family == 'biaxial' else 0.,
        z*arm if family == 'biaxial' else 0., .0025, e,
        b3_ge.FlowCurve((0.,), (yield_stress,), 1e9))
        for i, (y, z) in enumerate(((-1,-1), (-1,1), (1,-1), (1,1))))
    background = np.array(stiffness); background[0] = 0.
    if family == 'biaxial': background[4:] = 0.
    physical = b3_ge.PhysicalFibreSection(fibres, np.diag(np.sqrt(background)))
    positions = np.array([(x, 0., .0675) for x in (0., .25, .5, .75, 1.)])
    members = tuple(b3_ge.define_beam('b3-ge', i+1, (101+2*i,102+2*i,103+2*i),
        positions[2*i:2*i+3], np.tile(np.eye(3), (3,1,1)),
        generalized if beams == 'mixed' and i == 0 else physical, np.eye(6)) for i in range(2))
    xs = np.linspace(0., 1., 4)
    panel = ss.ShellPanel(tuple(range(1,9)),
        tuple((float(x),0.,0.) for x in xs)+tuple((float(x),.2,0.) for x in xs),
        ((1,2,6,5),(2,3,7,6),(3,4,8,7)), .035, e, nu, 7850.)
    line = ss.ExperimentalBL5Line((1,2,3,4), ((0,0),(0,1),(1,0),(1,1)), basis=basis)
    model = ss.LinearDefinition(ss.B3_GE_E4_PL_STIFFENED_SHELL_V1, panel, members, (),
        tuple(ss.FixedCoordinate('shell',node,axis) for node in (1,5) for axis in range(6)),
        (line,), q4_pullback_policy=ss.Q4_ALGORITHMIC_PULLBACK_V1,
        bl5_material_policy=ss.BL5_ELASTIC_NATIVE_FIBRE_V1)
    if shell != 'q4':
        cells = panel.q4_elements if shell == 's3' else panel.q4_elements[1:]
        panel = replace(panel,q4_elements=() if shell == 's3' else panel.q4_elements[:1],
            s3_elements=tuple(triangle for a,b,c,d in cells for triangle in ((a,b,c),(a,c,d))))
        model = replace(model,shell=panel,
            bl5_material_policy=ss.BL5_ELASTIC_NATIVE_FIBRE_MIXED_SHELL_V1,
            q4_pullback_policy='historical_corotational' if shell == 's3' else ss.Q4_ALGORITHMIC_PULLBACK_V1)
    return ExperimentalGEBL5Panel(model.shell, model.beams, model.lines, model.fixed,
        (ss.NodalAction("beam",105,(-1000.,0.,100.,0.,0.,0.)),),
        q4_pullback_policy=model.q4_pullback_policy,
        bl5_material_policy=model.bl5_material_policy)


def workflow(directory, shell='q4', family='biaxial', beams='mixed', basis='p0'):
    """Save inputs, reopen a Project and replay fresh public solver checkpoints."""
    from pathlib import Path
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    original = panel(family, beams, basis, shell=shell)
    standalone = load_case(save_case(original, directory/'panel.anyfem-ge.json'))
    project = Project('elastic native BL5', mesh_only=True)
    record = AnalysisDefinition.experimental_bl5_panel('native line', standalone)
    project.add_analysis(record)
    reopened = load_project(save_project(project, directory/'panel.anyfem'))
    record = reopened.analyses[record.id]
    restored_panel = bl5_panel_from_analysis(record)
    if restored_panel.to_dict() != original.to_dict():
        raise RuntimeError('consumer inputs changed during persistence')
    owner = record.create_bl5_panel_analysis(); fresh = restored_panel.create_analysis()
    try:
        preflight = owner.preflight()
        prefix = owner.solve(restored_panel.load_input, (.5,), max_iterations=24, max_backtracks=12)
        if prefix.status != 'completed': raise RuntimeError('finite prefix failed')
        checkpoint = directory/'accepted.bin'; checkpoint.write_bytes(owner.checkpoint())
        fresh.restore(checkpoint.read_bytes())
        if fresh.checkpoint() != owner.checkpoint(): raise RuntimeError('fresh restore changed bytes')
        direct = owner.solve(restored_panel.load_input, (1.,), max_iterations=24, max_backtracks=12)
        continued = fresh.solve(restored_panel.load_input, (1.,), max_iterations=24, max_backtracks=12)
        if direct.status != 'completed' or continued.status != 'completed':
            raise RuntimeError('finite continuation failed')
        if fresh.checkpoint() != owner.checkpoint(): raise RuntimeError('continuation changed bytes')
        recovery = fresh.recover()
        return dict(status=continued.status, production_qualified=False,
            schema=restored_panel.to_dict()['schema'], policy=restored_panel.bl5_material_policy,
            shell=shell, model_identity=preflight['identity'], generation=continued.generation,
            free_equilibrium_ratio=continued.free_equilibrium_ratio, constraint_max=continued.constraint_max,
            fibre_stations=len(recovery.beam_fibre_material), line_work_records=len(recovery.beam_shell_line_work),
            input_bytes_equal=tuple(b.raw for b in original.beams)==tuple(b.raw for b in restored_panel.beams),
            fresh_restore_bytes_equal=True, continued_bytes_equal=True)
    finally:
        owner.close(); fresh.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--shell', choices=('q4', 's3', 'mixed'), default='q4')
    parser.add_argument('--family', choices=('axial', 'biaxial'), default='biaxial')
    parser.add_argument('--beams', choices=('fibre', 'mixed'), default='mixed')
    parser.add_argument('--basis', choices=('p0', 'continuous_p1_coarse'), default='p0')
    args = parser.parse_args()
    print(json.dumps(workflow(args.output, args.shell, args.family, args.beams, args.basis), indent=2, allow_nan=False))



