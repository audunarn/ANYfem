"""PROFILE (not an acceptance test): where does the native inclined Prepare spend its time?

Needs the harness native flags and ``--reused-equilibrium-record`` (the retained actual pose of the 500 kg corner-cargo case; reuse is
labelled in the report, no fresh equilibrium solve is claimed).  Emits flushed stage lines, a cProfile cumulative table, the faulthandler
stacks (every 20 s) and a metrics json into ``$ANYFEM_WAVE_SURFACE_EVIDENCE`` (outside the source tree).  Missing metrics are labelled.
"""
import cProfile
import faulthandler
import io
import json
import os
import pstats
import sys
import time
from pathlib import Path

import numpy as np
import pytest

EVIDENCE = os.environ.get('ANYFEM_WAVE_SURFACE_EVIDENCE')
pytestmark = pytest.mark.skipif(not (os.environ.get('ANYFEM_FLOATING_REUSE_EQUILIBRIUM') and EVIDENCE and
                                     os.environ.get('ANYFEM_FLOATING_EQUILIBRIUM_PYTHON')),
                                reason='profile needs the reused equilibrium record + evidence directory + native runtime')
sys.path.insert(0, str(Path(__file__).parent))
from test_floating_pipeline import box_project, meshed, RHO, G, DEPTH  # noqa: E402
from anyfem.model import Mass  # noqa: E402
from anyfem.application import floating_prepare as fp, floating_apply as fa  # noqa: E402
from anyfem.application.floating_mass import dry_mass_properties  # noqa: E402
from anyfem.solve.build import build_fe_model  # noqa: E402


def test_profile_native_inclined_prepare(tmp_path):
    out = Path(EVIDENCE); out.mkdir(parents=True, exist_ok=True)
    stacks = open(out / 'profile-stacks.txt', 'w')
    faulthandler.dump_traceback_later(20, repeat=True, file=stacks)
    stages = open(out / 'profile-stages.txt', 'w', buffering=1)
    times = {}

    def log(text):
        stages.write(f'{time.perf_counter() - t0:9.3f}s  {text}\n'); stages.flush(); os.fsync(stages.fileno())

    restore = []

    def timed(module, name):
        original = getattr(module, name)
        restore.append((module, name, original))

        def wrapper(*a, **k):
            log(f'BEGIN {name}')
            start = time.perf_counter()
            try:
                return original(*a, **k)
            finally:
                times[name] = times.get(name, 0.0) + time.perf_counter() - start
                log(f'END   {name} ({times[name]:.3f}s cumulative)')
        setattr(module, name, wrapper)

    t0 = time.perf_counter()
    try:
        project, faces = box_project(with_deck=True)
        top_corner = next(v for v in project.geometry.vertices if np.allclose(project.geometry.vertex_position(v), [0., -2., 2.]))
        project.add_mass(Mass(project.point(top_corner), 500.0, 'explicit rigid attached test cargo'))
        mesh = meshed(project, 0.8)
        log('mesh done')
        settings = fp.FloatingSettings(
            components=(fp.FloatingComponent(2.4, .005, 1.1, .35),), times=(0., .4, .8), water_depth=DEPTH, rho=RHO, gravity=G,
            panel_size=float(os.environ.get('ANYFEM_PROFILE_PANEL', '0.7')), allow_native_solve=True, allow_development=True,
            owner_wheel=os.environ.get('ANYFEM_FLOATING_OWNER_WHEEL', ''),
            equilibrium_python=os.environ['ANYFEM_FLOATING_EQUILIBRIUM_PYTHON'],
            stability_source=os.environ.get('ANYFEM_FLOATING_STABILITY_SOURCE', ''), envelope_extra_face_ids=(faces['deck'],))
        built = build_fe_model(project, mesh, require_loads=False, require_supports=False)
        props = dry_mass_properties(built.fe_model)
        log(f'mass/FE built: {props.mass_matrix.shape} dofs, nnz {props.mass_matrix.nnz}, nodes {len(props.node_ids)}')
        pose = json.loads(Path(os.environ['ANYFEM_FLOATING_REUSE_EQUILIBRIUM']).read_text())
        for name in ('collect_snapshots', '_waterline_eta', '_panel_wrench_body', '_vertical_clearance'):
            timed(fp, name)
        for name in ('prepare_target_cells', 'prepare_source', 'build_overlap_map', 'map_pressure_nodal', 'wet_region_nodal'):
            timed(fp.fsrc, name)
        from anyhydro import floating_radiation_fields as ff
        for name in ('solve_family_snapshot', 'evaluate_floating_fields', '_potentials', '_body_for_family'):
            timed(ff, name)
        captured = []
        original_elevations = ff.evaluate_floating_elevations

        def capture(snap, pts, nrm, **kw):
            captured.append((snap, np.array(pts), np.array(nrm)))
            return original_elevations(snap, pts, nrm, **kw)
        ff.evaluate_floating_elevations = capture
        restore.append((ff, 'evaluate_floating_elevations', original_elevations))
        profiler = cProfile.Profile()
        log('PROFILED prepare_from_pose start')
        profiler.enable()
        try:
            prepared = fp.prepare_from_pose(project, mesh, settings, pose, cache_dir=tmp_path / 'cache', built=built, props=props)
        finally:
            profiler.disable()
            text = io.StringIO()
            pstats.Stats(profiler, stream=text).sort_stats('cumulative').print_stats(40)
            (out / 'profile-cprofile.txt').write_text(text.getvalue())
            log('PROFILED prepare_from_pose end')
        ff.evaluate_floating_elevations = original_elevations
        # elevation-only recovery is BIT-IDENTICAL to the elevations of the full-field recovery on the actual retained native family
        # (waterline points, fluid-side offset normals, world datum/heading in the snapshot) and runs no BEM
        import capytaine as cpt
        real_solve = cpt.BEMSolver.solve
        cpt.BEMSolver.solve = lambda *a, **k: pytest.fail('BEM during elevation recovery')
        try:
            assert captured, 'the production waterline path did not use the elevation-only recovery'
            start = time.perf_counter()
            for snap, pts, nrm in captured:
                only = ff.evaluate_floating_elevations(snap, pts, nrm)
                t_only = time.perf_counter() - start
                full = ff.evaluate_floating_fields(snap, pts, nrm)
                t_full = time.perf_counter() - start - t_only
                np.testing.assert_array_equal(only.eta_incident_diffraction, full.eta_incident_diffraction)
                np.testing.assert_array_equal(only.eta_radiation, full.eta_radiation)
                times['eta_only_seconds'], times['eta_full_fields_seconds'] = t_only, t_full
        finally:
            cpt.BEMSolver.solve = real_solve
        log('eta bit-identical check done')
        record = fa.save_prepared(prepared, tmp_path / 'record.npz')
        source = prepared.source
        report = dict(
            equilibrium='REUSED retained pose (no fresh solve): ' + os.environ['ANYFEM_FLOATING_REUSE_EQUILIBRIUM'],
            source_panels_before_conforming=source.provenance.get('panels_before_conforming'), source_panels_after=len(source.triangles),
            source_provenance=source.provenance, fe_dofs=int(props.mass_matrix.shape[0]), fe_nodes=len(props.node_ids),
            payload_bytes=record.stat().st_size, stage_seconds=times, total_seconds=time.perf_counter() - t0,
            threads={k: os.environ.get(k, 'MISSING (not set in this process)') for k in
                     ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMBA_NUM_THREADS')},
            peak_rss_bytes='MISSING (not measured in this test; harness runtime.json holds the tree RSS)',
            gates=dict(source_closure=prepared.diagnostics['source_closure'], mapping=prepared.diagnostics['mapping_error'],
                       clearance=prepared.diagnostics['clearance_m']))
        (out / 'profile-metrics.json').write_text(json.dumps(report, indent=2, default=str))
        log('metrics written')
    finally:
        for module, name, original in reversed(restore):      # the profile test must not contaminate later tests in this process
            setattr(module, name, original)
        faulthandler.cancel_dump_traceback_later()
        stacks.flush(); stages.flush()
        stacks.close(); stages.close()
