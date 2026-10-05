"""Isolated current-candidate harness; CLI is inspection/proposal only.

Engineering functions below are prepared for a separately reviewed run. They
are not invoked by the CLI or the ordinary guard tests. Owner receipts and FEM
application services remain the only source-transfer/preassembly authorities.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib
import inspect
import json
from pathlib import Path
import sys
import tarfile
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / 'reports/prepared-mesh-preassembly/current-2cbb161'
GEOMETRY_COMMIT = '72c1733f434d0bd980408825596816cea1b2c223'
GEOMETRY_MAIN = 'd4958c73c8b53d35d4392992a3233a75edb0feb4'
GEOMETRY_WHEEL = 'cb88cc9156531142b5afcfd54f6cefde87ff97df0abc111bc0e7611039085b52'
MESHER_COMMIT = '2cbb161daa75bb6785c9dc1afd2d558b7ca652f4'
MESHER_ARCHIVE = '75090fb73aa7e8993ac38ef6aa1e0594769085b04e812569e914c74c9e0660ff'
SCOPE_PRESSURE = {'root': 1200, 'child': 300}
FEM_BASE_COMMIT = '784cf2c8ba294d55ff8984fec0fb08869eb4e966'
FEM_SOURCE_DIGEST = '70d04351baa6e7688330f66f83ca22dc2d74c05e464e5f83991cec3af49a8f8a'
FIXTURE_HASHES = {
    'prepared_mesh_remap_fixture.py': '275f1685ad6d0191e684edb92b0ce4c2881ae0b5956c600c2fc4d3f6fe51a07d',
    'prepared_pair_owner_fixture.py': 'f660b83cb438bd960005e3c24b70cf725873aa75f74480f1b00bf9cfef3b82b6',
}
EXECUTION_ENABLED = False  # Review and checked resource enforcement remain outstanding.


def proposal():
    """A prospective experiment, never execution authority or acceptance."""
    return {
        'status': 'prepared; execution pending proposal review',
        'geometry_commit': GEOMETRY_COMMIT, 'geometry_main': GEOMETRY_MAIN,
        'geometry_wheel_sha256': GEOMETRY_WHEEL,
        'mesher_commit': MESHER_COMMIT, 'mesher_archive_sha256': MESHER_ARCHIVE,
        'fem_base_commit': FEM_BASE_COMMIT, 'fem_source_digest': FEM_SOURCE_DIGEST,
        'fixture_source_sha256': FIXTURE_HASHES,
        'engineering_dispatch_enabled': EXECUTION_ENABLED,
        'cases': [
            {'scope': 'root', 'fixture': 'size2_fixture', 'pressure_pa': 1200,
             'claim': 'fresh ORIGINAL root pressure binds every root cell only'},
            {'scope': 'child', 'fixture': 'reopened_child_fixture', 'pressure_pa': 300,
             'claim': 'fresh reopened child pressure remains local to its six cells',
             'limit': 'child case creates its own fresh root triangulation and NEW child Project/closure; never reuse the root case artifact; no separate child engine execution'},
        ],
        'prescribed_material': 'S355', 'thickness_m': .01,
        'support': 'fixed lowest source-face ID other than pressure target',
        'bounded_request': {
            'platform': 'Windows first', 'root_engine_invocations': 2,
            'reason': 'one fresh size2 run per case; child helper internally creates its own fresh prior',
            'case_sequence': 'root then child; stop on first failure, no retry or parameter sweep',
            'process_timeout_seconds_per_case': 180,
            'proposed_committed_memory_limit_mib': 512,
            'limit_enforcement': 'pending; dispatch stays disabled until proposal review and checked supervisor integration',
            's3_quality': 'existing owner criteria unchanged; no new 30-degree repair/target',
        },
        'checks': ['26 T3 cells unchanged', 'exact root/child pressure cell sets',
                   'complete fresh Project manifest and structural occurrences',
                   'unchanged input mesh and Project documents',
                   'existing source-bound S3 production proof and preassembly',
                   'typed refusal for missing/stale/foreign evidence'],
        'capture': 'exact runtime/source hashes; Project/owner identities; every record ID and cell/node scope; complete failure logs; wall time/peak committed memory/owned-process cleanup',
        'mesh_executed': False, 'preassembly_executed': False, 'solver_executed': False,
        'publication_qualified': False, 'workflow_changed': False,
        'frozen_6ff_inputs_replaced': False,
        'exclusions': 'No bare-geometry artifact reuse, native or mixed acceptance, solver, CI, default, release, Git delivery or consumed budget replay',
    }


def _package_hashes(directory):
    return {str(path.relative_to(directory)).replace('\\', '/'): sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()
            and '__pycache__' not in path.parts and path.suffix != '.pyc'}


def verify_artifacts(stage=STAGE):
    """Fail before imports/fixture callbacks on wrong or modified artifacts."""
    for name, expected in (('anygeometry-0.4.5-py3-none-any.whl', GEOMETRY_WHEEL),
                           ('mesher-source.tar', MESHER_ARCHIVE)):
        path = stage / name
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f'wrong exact artifact: {name}')
    # Derive expected bytes from the pinned archives, not mutable side metadata.
    with ZipFile(stage / 'anygeometry-0.4.5-py3-none-any.whl') as bundle:
        geometry = {name.removeprefix('anygeometry/'): sha256(bundle.read(name)).hexdigest()
                    for name in bundle.namelist() if name.startswith('anygeometry/') and not name.endswith('/')}
    with tarfile.open(stage / 'mesher-source.tar') as bundle:
        mesher = {item.name.removeprefix('src/anymesher/'): sha256(bundle.extractfile(item).read()).hexdigest()
                  for item in bundle.getmembers() if item.isfile() and item.name.startswith('src/anymesher/')}
    for directory, expected in ((stage / 'geometry-wheel-import/anygeometry', geometry),
                                (stage / 'mesher-source/src/anymesher', mesher)):
        if _package_hashes(directory) != expected:
            raise ValueError(f'modified staged package: {directory.name}')
    source = _package_hashes(ROOT / 'src/anyfem')
    if sha256(json.dumps(source, sort_keys=True).encode()).hexdigest() != FEM_SOURCE_DIGEST:
        raise ValueError('FEM source package differs from the proposed exact inputs')
    for name, expected in FIXTURE_HASHES.items():
        if sha256((ROOT / 'tests' / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'fixture source changed: {name}')


def check_call_shape(function, positional_count, keywords):
    """Check that the intended Python call binds; make no semantic claim."""
    inspect.signature(function).bind(*([None] * positional_count), **keywords)


def inspect_runtime(stage=STAGE):
    """Imports and intended call shapes only; no constructors or owner queries."""
    verify_artifacts(stage)
    expected = {'anygeometry': stage / 'geometry-wheel-import/anygeometry',
                'anymesher': stage / 'mesher-source/src/anymesher',
                'anyfem': ROOT / 'src/anyfem'}
    modules = {name: importlib.import_module(name) for name in expected}
    for name, module in modules.items():
        if Path(module.__file__).resolve().parent != expected[name].resolve():
            raise ValueError(f'wrong runtime origin: {name}: {module.__file__}')
    contracts = {
        'anygeometry': ('extract_model_closure', 'query_prepared_model_scope',
                        'validate_prepared_model_scope_binding',
                        'validate_material_surface_region_triangles_xyz'),
        'anymesher': ('query_prepared_current_component_associations',
                      'validate_prepared_current_component_associations',
                      'prepare_source_bound_qualified_s3_mesh'),
        'anyfem.prepared_reference_scope': ('extract_prepared_project_closure',
                                          'query_prepared_project_reference_scope',
                                          'validate_prepared_project_reference_scope'),
        'anyfem.prepared_mesh_remap': ('remap_prepared_mesh_to_project',),
        'anyfem.prepared_mesh_preassembly': ('preassemble_prepared_project_mesh',),
    }
    signatures = {}
    shapes = {
        'extract_model_closure': (2, dict(include_structural_closure=True, include_features=False)),
        'query_prepared_model_scope': (1, {}),
        'validate_prepared_model_scope_binding': (2, {}),
        'validate_material_surface_region_triangles_xyz': (4, dict(cancellation_check=None)),
        'query_prepared_current_component_associations': (5, dict(created_material_uv_by_root=None)),
        'validate_prepared_current_component_associations': (6, dict(cancellation_check=None)),
        'prepare_source_bound_qualified_s3_mesh': (8, dict(cancellation_check=None)),
        'extract_prepared_project_closure': (2, dict(include_structural_closure=True)),
        'query_prepared_project_reference_scope': (2, dict(closure=None)),
        'validate_prepared_project_reference_scope': (3, dict(closure=None)),
        'remap_prepared_mesh_to_project': (4, dict(closure=None, association_namespace='prepared_current',
            association_receipt=None, association_component=None, association_registry=None, cell_current_faces=None)),
        'preassemble_prepared_project_mesh': (4, dict(closure=None, association_namespace='prepared_current',
            association_receipt=None, association_component=None, association_registry=None, cell_current_faces=None,
            load_case='default', current_project=None)),
    }
    for name, symbols in contracts.items():
        module = importlib.import_module(name)
        for symbol in symbols:
            function = getattr(module, symbol)
            check_call_shape(function, *shapes[symbol])
            signatures[f'{name}.{symbol}'] = str(inspect.signature(function))
    forbidden = [name for name in sys.modules if name == 'tkinter'
                 or name.startswith(('PySide6', 'anyfem.ui'))]
    if forbidden:
        raise ValueError(f'headless boundary violated: {forbidden}')
    return {'scope': 'imports/signatures only; no engine or FE construction',
            'origins': {name: module.__file__ for name, module in modules.items()},
            'signatures': signatures, 'gui_imports': False,
            'intended_call_shapes_checked': len(signatures), 'fem_source_digest': FEM_SOURCE_DIGEST,
            'mesh_executed': False, 'preassembly_executed': False, 'solver_executed': False}


def assert_pressure_binding(scope, pressure, source_binding, *, case_id):
    """Application intent check after the existing owner-qualified remap."""
    if scope not in SCOPE_PRESSURE:
        raise ValueError('unsupported prospective scope')
    if pressure.ref.kind != 'face' or pressure.value != SCOPE_PRESSURE[scope]:
        raise ValueError('portable pressure target/value differs from the proposal')
    key = f'load_cases/{case_id}/pressures/{pressure.id}'
    matches = [item for item in source_binding.references if item.key == key]
    if len(matches) != 1:
        raise ValueError(f'pressure record binding missing or ambiguous: {key}')
    binding = matches[0]
    expected = set(source_binding.mesh.elements_of_face.get(pressure.ref.id, ()))
    if (not expected or set(binding.cell_ids) != expected
            or len(binding.cell_ids) != len(expected)):
        raise ValueError('pressure binding does not equal its SOURCE face cell scope')
    if scope == 'child' and len(expected) != 6:
        raise ValueError('reopened child pressure widened or lost its six-cell scope')
    if len(source_binding.mesh.tris) != 26:
        raise ValueError('portable fixture changed its 26 T3 cells')
    return binding


def compose_case(scope):
    """ENGINE/FE WORK: prepared entry point; invoke only after proposal review.

    No CLI dispatch calls this. The caller must provide the reviewed bounded
    process supervisor. A bare mesh/saved witness is never accepted as input.
    """
    if scope not in SCOPE_PRESSURE:
        raise ValueError('unsupported prospective scope')
    if not EXECUTION_ENABLED:
        raise RuntimeError('engineering dispatch disabled pending proposal review and checked supervisor integration')
    inspect_runtime()
    previous_path = sys.path[:]
    try:
        sys.path.insert(0, str(ROOT / 'tests'))
        fixtures = importlib.import_module('prepared_mesh_remap_fixture')
    finally:
        sys.path[:] = previous_path
    builder = fixtures.size2_fixture if scope == 'root' else fixtures.reopened_child_fixture
    inputs, pressure, _fixture = builder()  # Fresh invocation; never run during preparation.
    if pressure.ref.kind != 'face' or pressure.value != SCOPE_PRESSURE[scope]:
        raise ValueError('portable pressure target/value differs from the proposal')
    from anygeometry.entities import EntityRef
    from anyfem.model.attributes import fixed
    from anyfem.model.materials import steel
    from anyfem.prepared_reference_scope import query_prepared_project_reference_scope, validate_prepared_project_reference_scope
    from anyfem.prepared_mesh_remap import _snapshot
    from anyfem.prepared_mesh_preassembly import preassemble_prepared_project_mesh
    project = inputs['project']
    project.add_material(steel('S355', .01))
    project.add_plate_section('shell', .01, 'S355')
    project.assign_plates(tuple(project.geometry.faces), 'shell')
    support_face = min(face for face in project.geometry.faces if face != pressure.ref.id)
    project.add_support(fixed(EntityRef('face', support_face)))
    inputs['manifest'] = query_prepared_project_reference_scope(
        project, inputs['prepared_geometry'], closure=inputs['closure'])
    before = _snapshot(inputs['mesh'])
    artifact = preassemble_prepared_project_mesh(**inputs)
    binding = assert_pressure_binding(scope, pressure, artifact.source_binding, case_id=artifact.load_case_id)
    if _snapshot(inputs['mesh']) != before:
        raise ValueError('composition changed the staged input mesh')
    validate_prepared_project_reference_scope(project, inputs['prepared_geometry'],
        inputs['manifest'], closure=inputs['closure'])
    return inputs, pressure, artifact, binding


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('proposal', 'inspect'))
    args = parser.parse_args(argv)
    print(json.dumps(proposal() if args.action == 'proposal' else inspect_runtime(), indent=2))


if __name__ == '__main__':
    main()
