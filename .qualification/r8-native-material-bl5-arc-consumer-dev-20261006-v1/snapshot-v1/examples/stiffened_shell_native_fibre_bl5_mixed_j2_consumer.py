"""Experimental ANYfem native BL5 mixed J2 Project and restart workflow."""
from dataclasses import asdict, replace
import argparse
import json
from pathlib import Path
import runpy
import numpy as np
from anymaterial import DNVC208MaterialCurve
from anysolver import stiffened_shell as ss
from anyfem.solve.stiffened_shell import save_case, load_case
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import save_project, load_project

_elastic_panel = runpy.run_path(str(Path(__file__).with_name(
    'stiffened_shell_native_fibre_bl5_consumer.py')))['panel']
CONTROL = ss.DisplacementControl('beam', 105, 0)
TARGETS = (1e-6, 1e-5, 5e-6)
OPTIONS = dict(max_iterations=24, max_backtracks=12)


def panel(shell='mixed', family='biaxial', beams='mixed', basis='p0'):
    if shell not in ('s3', 'mixed'):
        raise ValueError('native mixed J2 requires all-S3 or mixed Q4/S3')
    model = _elastic_panel(family, beams, basis, yield_stress=1e5, shell=shell)
    curve = DNVC208MaterialCurve(1e6, 1.05e6, 1.1e6, .005, .01, 4e6, .2)
    return replace(model, shell=replace(model.shell, j2_curve=curve),
        actions=(ss.NodalAction('beam', 105, (500., 0., 0., 0., 0., 0.)),),
        bl5_material_policy=ss.BL5_NATIVE_FIBRE_MIXED_SHELL_MATERIAL_V1)


def _history_records(records):
    """Retain public recovery field names and actual material membership."""
    def compatible(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, dict):
            return {key: compatible(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [compatible(item) for item in value]
        return value
    return [compatible(asdict(record)) for record in records]


def workflow(directory, shell='mixed', family='biaxial', beams='mixed', basis='p0'):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    original = panel(shell, family, beams, basis)
    standalone = load_case(save_case(original, directory/'panel.anyfem-ge.json'))
    project = Project('native mixed J2 BL5', mesh_only=True)
    record = AnalysisDefinition.experimental_bl5_panel('native material line', standalone)
    project.add_analysis(record)
    reopened = load_project(save_project(project, directory/'panel.anyfem'))
    record = reopened.analyses[record.id]
    restored = bl5_panel_from_analysis(record)
    if restored.to_dict() != original.to_dict():
        raise RuntimeError('consumer inputs changed during persistence')
    owner = record.create_bl5_panel_analysis(); fresh = restored.create_analysis()
    try:
        preflight = owner.preflight()
        seed = owner.solve(restored.load_input, (.001,), **OPTIONS)
        if seed.status != 'completed':
            raise RuntimeError('ordinary native force seed failed')
        fresh.restore(owner.checkpoint())
        if fresh.checkpoint() != owner.checkpoint():
            raise RuntimeError('force-seed restore changed bytes')
        fresh.close(); fresh = restored.create_analysis()
        loaded = owner.solve(restored.load_input, TARGETS[:2], displacement_control=CONTROL, **OPTIONS)
        if loaded.status != 'completed':
            raise RuntimeError('fixed translation loading failed')
        checkpoint = directory/'accepted.bin'; checkpoint.write_bytes(owner.checkpoint())
        fresh.restore(checkpoint.read_bytes())
        if fresh.checkpoint() != owner.checkpoint():
            raise RuntimeError('fresh material restore changed bytes')
        direct = owner.solve(restored.load_input, TARGETS[2:], displacement_control=CONTROL, **OPTIONS)
        continued = fresh.solve(restored.load_input, TARGETS[2:], displacement_control=CONTROL, **OPTIONS)
        if direct.status != 'completed' or continued.status != 'completed':
            raise RuntimeError('fixed translation unloading failed')
        if fresh.checkpoint() != owner.checkpoint():
            raise RuntimeError('material continuation changed bytes')
        recovered = fresh.recover()
        member_ids = tuple(beam.instantiate()[0].element_id for beam in restored.beams)
        physical_ids = {item.element_id for item in loaded.beam_fibre_material}
        if len(member_ids) != len(loaded.beam_station_accumulated):
            raise RuntimeError('public station history membership mismatch')
        physical_members = [dict(element_id=identifier, station_accumulated=list(values))
            for identifier, values in zip(member_ids, loaded.beam_station_accumulated)
            if identifier in physical_ids]
        if {item['element_id'] for item in physical_members} != physical_ids:
            raise RuntimeError('physical fibre history membership mismatch')
        result = dict(status=continued.status, production_qualified=False,
            schema=restored.to_dict()['schema'], policy=restored.bl5_material_policy,
            model_identity=preflight['identity'], shell=shell, generation=continued.generation,
            history_generation=loaded.generation, target=continued.target,
            free_equilibrium_ratio=continued.free_equilibrium_ratio,
            constraint_max=continued.constraint_max,
            shell_s3_material=_history_records(loaded.shell_s3_material),
            beam_fibre_material=_history_records(loaded.beam_fibre_material),
            beam_generalized_material=_history_records(loaded.beam_generalized_material),
            shell_layer_alpha=[list(values) for values in loaded.shell_layer_alpha],
            physical_fibre_members=physical_members,
            physical_fibre_alpha_max=max(value for item in physical_members
                                         for value in item['station_accumulated']),
            input_bytes_equal=tuple(b.raw for b in original.beams)==tuple(b.raw for b in restored.beams),
            force_seed_restore_bytes_equal=True, fresh_restore_bytes_equal=True,
            continued_bytes_equal=True, recovered_generation=recovered.generation)
        (directory/'workflow.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
        return result
    finally:
        owner.close(); fresh.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--shell', choices=('s3', 'mixed'), default='mixed')
    parser.add_argument('--family', choices=('axial', 'biaxial'), default='biaxial')
    parser.add_argument('--beams', choices=('fibre', 'mixed'), default='mixed')
    parser.add_argument('--basis', choices=('p0', 'continuous_p1_coarse'), default='p0')
    args = parser.parse_args()
    result = workflow(args.output, args.shell, args.family, args.beams, args.basis)
    print(json.dumps({name: result[name] for name in ('status', 'schema', 'policy', 'target',
        'generation', 'physical_fibre_alpha_max', 'production_qualified')}, indent=2))
