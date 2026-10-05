"""Freeze source/runtime/command inputs without OS probes or engineering imports."""
from hashlib import sha256
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/prepared-mesh-preassembly/current-2cbb161'


def _files(directory):
    return {str(path.resolve()): sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob('*') if path.is_file()
            and '__pycache__' not in path.parts and path.suffix != '.pyc'}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze():
    harness = _load('candidate_harness_freeze', ROOT / 'tools/prepared_candidate_integration.py')
    harness.verify_artifacts()
    imports = [str(OUT / 'geometry-wheel-import'), str(ROOT / 'src'),
               str(OUT / 'mesher-source/src')]
    imports.extend(str(Path(path).resolve()) for path in sys.path if 'site-packages' in path)
    # psutil is a supervisor-only dependency; make its path explicit, never
    # inherit a .pth bootstrap or substitute global scientific dependencies.
    global_site = Path(sys.base_prefix) / 'Lib/site-packages'
    if str(global_site) not in imports:
        imports.append(str(global_site))
    fingerprints = {}
    components = {}
    for name in ('anygeometry', 'anymesher', 'anyfem', 'anysolver', 'anymaterial',
                 'anyfileio', 'numpy', 'scipy', 'h5py', 'platformdirs', 'psutil', 'shapely'):
        spec = importlib.machinery.PathFinder.find_spec(name, imports)
        if spec is None or not spec.origin:
            raise ValueError(f'explicit runtime dependency unavailable: {name}')
        origin = Path(spec.origin).resolve()
        directory = origin.parent if spec.submodule_search_locations else None
        files = _files(directory) if directory else {str(origin): sha256(origin.read_bytes()).hexdigest()}
        adjacent = []
        if directory:
            for candidate in directory.parent.glob(name + '*libs'):
                if candidate.is_dir():
                    files.update(_files(candidate))
                    adjacent.append(str(candidate))
        fingerprints.update(files)
        components[name] = {'origin': str(origin), 'files': len(files), 'adjacent_libraries': adjacent,
                            'file_map_sha256': sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}
    sources = [ROOT / 'tools/prepared_candidate_integration.py',
               ROOT / 'tools/prepared_candidate_supervisor.py', Path(__file__).resolve(),
               ROOT / 'tests/prepared_mesh_remap_fixture.py', ROOT / 'tests/prepared_pair_owner_fixture.py',
               OUT / 'anygeometry-0.4.5-py3-none-any.whl', OUT / 'mesher-source.tar',
               Path(sys.executable).resolve(), Path(sys._base_executable).resolve(),
               Path(sys.prefix) / 'pyvenv.cfg']
    # Pin the portable control sources (exact reviewed extracts checked into
    # the repository); the gitignored original evidence checkouts stay
    # untouched and are never pinned or read here.
    sources += list((ROOT / 'tools/prepared_candidate_controls').glob('*.py'))
    # Pin the relevant CPython binary foundations without loading their DLLs.
    sources += list(Path(sys.base_prefix).glob('python*.dll'))
    for source in sources:
        fingerprints[str(source.resolve())] = sha256(source.read_bytes()).hexdigest()
    manifest = {
        'schema_version': 1,
        'scope': 'Prepared frozen control/source/runtime inputs; not execution authority or runtime acceptance',
        'cases': ['root', 'child'], 'import_paths': imports,
        'harness_path': str(ROOT / 'tools/prepared_candidate_integration.py'),
        'runtime': {'interpreter': str(Path(sys.executable).resolve()),
                    'base_interpreter': str(Path(sys._base_executable).resolve()),
                    'python_version': sys.version, 'isolation': ['-I', '-S'],
                    'components': components, 'package_imports_executed': False,
                    'thread_environment': {name: os.environ.get(name) for name in (
                        'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                        'NUMEXPR_NUM_THREADS', 'BLIS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}},
        'source_fingerprints': fingerprints,
        'output_dir': str(OUT / 'prospective-root-child-attempt'),
        'proposal': harness.proposal(),
        'runtime_probe_executed': False, 'mesh_executed': False,
        'preassembly_executed': False, 'solver_executed': False,
    }
    path = OUT / 'supervisor-manifest.json'
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps({'manifest': str(path), 'fingerprinted_files': len(fingerprints),
                      'components': {name: item['files'] for name, item in components.items()},
                      'imports_executed': False, 'os_probe_executed': False}, indent=2))
    return manifest


if __name__ == '__main__':
    freeze()
