"""Headless experimental v6 BL5 material arc Project/restart consumer.

Requires the public native-material BL5 arc solver candidate. This example is
source-only until its own consumer runtime/artifact checks are authorized.
"""
from dataclasses import fields, is_dataclass, replace
from contextlib import ExitStack
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import numpy as np
import anyfem
import anysolver
from anysolver import stiffened_shell as ss
from anysolver.control import CancellationToken, SolveCancelled
from anyfem.solve.stiffened_shell import save_case, load_case, SCHEMA_BL5_NATIVE_MIXED_J2_V6
from anyfem.solve.stiffened_shell_project import bl5_panel_from_analysis
from anyfem.model.records import AnalysisDefinition
from anyfem.model.project import Project
from anyfem.io.project_file import save_project, load_project

_BASE = runpy.run_path(str(Path(__file__).with_name(
    'stiffened_shell_native_fibre_bl5_mixed_j2_consumer.py')))
CONTROL, TARGETS, OPTIONS = (_BASE[name] for name in ('CONTROL', 'TARGETS', 'OPTIONS'))
PROFILE = dict(shell='mixed', family='biaxial', beams='mixed', basis='continuous_p1_coarse')


def panel():
    return _BASE['panel'](**PROFILE)


def _json(value):
    if is_dataclass(value):
        return {field.name: _json(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, np.ndarray): return value.tolist()
    if isinstance(value, np.generic): return value.item()
    if isinstance(value, dict): return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return [_json(item) for item in value]
    return value


def _exact(left, right):
    """Compare every public record field; status is handled explicitly by callers."""
    if isinstance(left, np.ndarray):
        if not isinstance(right, np.ndarray) or not np.array_equal(left, right):
            raise RuntimeError('public result array changed across restore/continue')
    elif is_dataclass(left):
        if type(left) is not type(right): raise RuntimeError('public result record type changed')
        for field in fields(left): _exact(getattr(left, field.name), getattr(right, field.name))
    elif isinstance(left, (tuple, list)):
        if type(left) is not type(right) or len(left) != len(right):
            raise RuntimeError('public result membership/order changed')
        for a, b in zip(left, right, strict=True): _exact(a, b)
    elif isinstance(left, dict):
        if left.keys() != right.keys(): raise RuntimeError('public result fields changed')
        for name in left: _exact(left[name], right[name])
    elif left != right: raise RuntimeError('public result scalar changed')


class _CancelAtCommit(CancellationToken):
    """Public token safe-point hook, bounded to the solver DEV commit stage."""
    def raise_if_cancelled(self, stage=''):
        if stage == 'stiffened-shell.arc.commit':
            self.cancel('consumer cancels before atomic arc commit')
        super().raise_if_cancelled(stage)


def _cancelled(call):
    try: call()
    except SolveCancelled as error: return dict(reason=error.reason, stage=error.stage)
    raise RuntimeError('requested arc cancellation did not raise SolveCancelled')


def _refused(call):
    try: call()
    except ValueError as error: return str(error)
    raise RuntimeError('invalid public request was not refused')


def _require_empty(owner):
    try: owner.recover()
    except RuntimeError: return
    raise RuntimeError('failed restore published a recovery on a fresh public owner')


def _physical_histories(case, result):
    identifiers = tuple(beam.instantiate()[0].element_id for beam in case.beams)
    physical = {record.element_id for record in result.beam_fibre_material}
    generalized = {record.element_id for record in result.beam_generalized_material}
    if not physical or physical & generalized:
        raise RuntimeError('ambiguous actual physical/generalized recovery membership')
    if len(identifiers) != len(result.beam_station_accumulated):
        raise RuntimeError('public station history order disagrees with definition')
    members = [dict(element_id=identifier, station_accumulated=list(values))
            for identifier, values in zip(identifiers, result.beam_station_accumulated, strict=True)
            if identifier in physical]
    if {row['element_id'] for row in members} != physical:
        raise RuntimeError('named physical recovery membership does not match definition history')
    return members


def workflow(output):
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=False)
    original = panel()
    standalone = load_case(save_case(original, directory/'original-case.anyfem-ge.json'))
    project = Project('experimental native material BL5 arc', mesh_only=True)
    record = AnalysisDefinition.experimental_bl5_panel('mixed P1 native material arc', standalone)
    project.add_analysis(record)
    reopened = load_project(save_project(project, directory/'original-project.anyfem'))
    record = reopened.analyses[record.id]
    restored = bl5_panel_from_analysis(record)
    _exact(original.to_dict(), standalone.to_dict())
    _exact(original.to_dict(), restored.to_dict())
    if restored.to_dict()['schema'] != SCHEMA_BL5_NATIVE_MIXED_J2_V6:
        raise RuntimeError('native material v6 input schema changed')
    if tuple(beam.raw for beam in original.beams) != tuple(beam.raw for beam in restored.beams):
        raise RuntimeError('native beam definition bytes changed during persistence')
    with ExitStack() as cleanup:
        owner = record.create_bl5_panel_analysis(); cleanup.callback(owner.close)
        fresh = record.create_bl5_panel_analysis(); cleanup.callback(fresh.close)
        wrong = replace(restored, shell=replace(restored.shell,
            thickness_m=restored.shell.thickness_m*1.01)).create_analysis()
        cleanup.callback(wrong.close)
        preflight = owner.preflight()
        if owner.capabilities.get('arc_length') != 'experimental_native_fibre_bl5_material_tangent_plane_v1':
            raise RuntimeError('public solver lacks the native BL5 material arc DEV route')
        force = owner.solve(restored.load_input, (.001,), **OPTIONS)
        if force.status != 'completed': raise RuntimeError('ordinary force seed failed')
        loaded = owner.solve(restored.load_input, TARGETS[:2], displacement_control=CONTROL, **OPTIONS)
        if loaded.status != 'completed': raise RuntimeError('fixed translation material seed failed')
        physical = _physical_histories(restored, loaded)
        fibre_alpha = max(value for row in physical for value in row['station_accumulated'])
        s3_alpha = max(float(value) for row in loaded.shell_s3_material for value in row.alpha)
        if fibre_alpha <= 0. or s3_alpha <= 0.:
            raise RuntimeError('matched mixed/biaxial/P1 loaded fixture did not yield its physical fibres and S3')
        (directory/'loaded-seed.bin').write_bytes(owner.checkpoint())
        loaded_recovery = owner.recover()
        nodes = preflight['arc_metric_node_order']
        metric = ss.PhysicalChordMetric(np.full(len(nodes), 1./len(nodes)), 1.)
        # Explicit caller choice, not inferred mass or a mesh-independent rule.
        control = ss.ArcLengthControl('beam', 105, 0, metric, 1e-6)
        before = owner.checkpoint()
        token = CancellationToken(); token.cancel('consumer before first arc step')
        before_step = _cancelled(lambda: owner.continue_arc_length(restored.load_input,
            control, cancellation_token=token))
        if owner.checkpoint() != before: raise RuntimeError('pre-cancel changed checkpoint')
        _exact(loaded_recovery, owner.recover())
        before_commit = _cancelled(lambda: owner.continue_arc_length(restored.load_input,
            control, cancellation_token=_CancelAtCommit()))
        if before_commit['stage'] != 'stiffened-shell.arc.commit' or owner.checkpoint() != before:
            raise RuntimeError('commit-stage cancellation changed accepted state')
        _exact(loaded_recovery, owner.recover())
        observed = []; token = CancellationToken()
        def stop_after_prefix(accepted):
            if accepted.status != 'accepted_prefix': raise RuntimeError('observer received wrong status')
            observed.append(accepted)
            token.cancel('consumer preserves one accepted arc prefix')
        after_prefix = _cancelled(lambda: owner.continue_arc_length(restored.load_input,
            control, steps=2, cancellation_token=token, on_accepted=stop_after_prefix))
        if len(observed) != 1: raise RuntimeError('prefix cancellation accepted the wrong count')
        prefix = owner.recover(); _exact(observed[0], prefix)
        raw = owner.checkpoint(); (directory/'accepted-arc-prefix.bin').write_bytes(raw)
        corrupted = raw[:-1]+bytes((raw[-1]^1,))
        corrupt_error = _refused(lambda: fresh.restore(corrupted))
        _require_empty(fresh)
        restored_prefix = fresh.restore(raw)
        _exact(prefix, restored_prefix)
        if fresh.checkpoint() != raw: raise RuntimeError('fresh restoration changed prefix bytes')
        wrong_error = _refused(lambda: wrong.restore(raw))
        _require_empty(wrong)
        changed_control = _refused(lambda: owner.continue_arc_length(restored.load_input,
            replace(control, radius=2*control.radius)))
        if owner.checkpoint() != raw: raise RuntimeError('refused controller changed accepted prefix')
        _exact(prefix, owner.recover())
        direct = owner.continue_arc_length(restored.load_input, control)
        resumed = fresh.continue_arc_length(restored.load_input, control)
        if direct.status != 'completed' or resumed.status != 'completed':
            raise RuntimeError('fresh public owner did not complete arc continuation')
        _exact(direct, resumed)
        _exact(owner.recover(), fresh.recover())
        if owner.checkpoint() != fresh.checkpoint(): raise RuntimeError('continued checkpoints differ')
        (directory/'continued-arc.bin').write_bytes(fresh.checkpoint())
        result = dict(status=resumed.status, restored_status=restored_prefix.status,
            production_qualified=False, schema=restored.to_dict()['schema'], profile=dict(PROFILE),
            policy=restored.bl5_material_policy, model_identity=preflight['identity'],
            project_analysis_id=record.id, analysis_type=record.type,
            formulation=record.beam_formulation, solver_route='public create_bl5_panel_analysis -> continue_arc_length',
            provenance=dict(anyfem_module=anyfem.__file__, anysolver_module=anysolver.__file__),
            full_frame_metric=True, metric_weights=metric.weights.tolist(), metric_length=metric.length,
            cancellation=dict(before_step=before_step, before_commit=before_commit, after_prefix=after_prefix),
            refusals=dict(corrupted_checkpoint=corrupt_error, wrong_model=wrong_error,
                          changed_controller=changed_control),
            input_roundtrip_equal=True, native_definition_bytes_equal=True,
            loaded_generation=loaded.generation, prefix_generation=prefix.generation,
            generation=resumed.generation, history=_json(resumed.history),
            physical_fibre_alpha_max=fibre_alpha, s3_alpha_max=s3_alpha,
            physical_fibre_members=physical,
            loaded_material=_json(dict(shell_q4=loaded.shell_q4_material, shell_s3=loaded.shell_s3_material,
                physical_fibres=loaded.beam_fibre_material, generalized=loaded.beam_generalized_material)),
            prefix=_json(prefix), continued=_json(resumed),
            fresh_restore_bytes_equal=True, continued_bytes_equal=True,
            checkpoint_sha256=hashlib.sha256(fresh.checkpoint()).hexdigest())
        (directory/'workflow.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = workflow(args.output)
    print(json.dumps({name: result[name] for name in ('status', 'schema', 'policy',
        'loaded_generation', 'prefix_generation', 'generation', 'physical_fibre_alpha_max',
        's3_alpha_max', 'production_qualified')}, indent=2))
