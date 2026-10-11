"""Frozen-input, unrounded representative mesh comparisons; run under the limiter."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from benchmark import Timings, configure, dump

CASES = ('mapped37k', 'stiffened16', 'grid32', 'native_plates4',
         'hole_native', 'disjoint64', 'curved_cylinder')


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--tree', choices=('baseline', 'candidate'), required=True)
    parser.add_argument('--freeze-inputs', action='store_true')
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    roots = configure(args.tree, args.evidence)
    import anygeometry
    import anymesher
    from anygeometry import from_dict, to_dict
    from anymesher.serialize import mesh_to_dict
    import anymesher.native_cpp as native
    capabilities = {name: bool(getattr(native, name)) for name in (
        'NATIVE_CPP_AVAILABLE', 'COMPILED_NATIVE_V2_AVAILABLE',
        'COMPILED_QUALITY_PIPELINE_AVAILABLE', 'COMPILED_TRIANGULATION_AVAILABLE')}
    assert all(capabilities.values())
    expected = (args.evidence / 'artifacts' / args.tree / 'installed').resolve()
    assert Path(anygeometry.__file__).resolve().parent == expected / 'anygeometry'
    assert Path(anymesher.__file__).resolve().parent == expected / 'anymesher'
    inputs = args.evidence / 'representative-inputs'
    if args.freeze_inputs:
        inputs.mkdir(exist_ok=False)
        sys.path.insert(0, str(args.evidence / 'external/benchmarks/speed_sweep'))
        from workloads import WORKLOADS
        from native_hybrid_performance import _cylinder
        for case in CASES:
            geometry, options = ((_cylinder(), {'target_size': .2, 'strategy': 'native'})
                                 if case == 'curved_cylinder' else WORKLOADS[case]())
            dump(inputs / f'{case}.json', {'geometry': to_dict(geometry), 'options': options})
        dump(inputs / 'manifest.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in sorted(inputs.glob('*.json'))})
        print(json.dumps({'frozen_cases': list(CASES)}), flush=True)
        return
    if args.case is None or args.output is None:
        parser.error('--case and --output required for measurements')
    args.output.mkdir(parents=True, exist_ok=False)
    source = inputs / f'{args.case}.json'
    payload = json.loads(source.read_text())
    geometry = from_dict(payload['geometry'])
    original = to_dict(geometry)
    timing = Timings()
    timing.install()
    start = time.perf_counter()
    result = anymesher.generate_hybrid_mesh_result(geometry, **payload['options'])
    elapsed = time.perf_counter() - start
    assert to_dict(geometry) == original, 'public meshing mutated authored geometry'
    mesh = mesh_to_dict(result.mesh)
    material = {key: value for key, value in mesh.items() if key not in
                ('geometry_model_id', 'geometry_revision', 'structural_preparation', 'hybrid_diagnostics')}
    measurement = {'case': args.case, 'tree': args.tree, 'mesh_wall_seconds': elapsed,
        'nodes': result.mesh.num_nodes, 'shells': len(result.mesh.shells),
        'couplings': len(result.mesh.couplings), 'material_hash': digest(material),
        'source_geometry_hash': digest(original),
        'calls': dict(timing.calls), 'seconds': dict(timing.seconds),
        'runtime': result.mesh.hybrid_diagnostics.get('runtime', {})}
    dump(args.output / 'measurement.json', measurement)
    from threadpoolctl import threadpool_info
    dump(args.output / 'runtime.json', {'python': sys.executable, 'version': sys.version,
        'geometry': anygeometry.__file__, 'mesher': anymesher.__file__,
        'native_path': native._compiled.__file__,
        'native_sha256': hashlib.sha256(Path(native._compiled.__file__).read_bytes()).hexdigest(),
        'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'import_roots': roots, 'capabilities': capabilities,
        'thread_environment': {key: os.environ.get(key) for key in
            ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')},
        'threadpools': threadpool_info(),
        'source_manifest_sha256': hashlib.sha256((args.evidence / f'{args.tree}-manifest.json').read_bytes()).hexdigest(),
        'artifact_manifest_sha256': hashlib.sha256((args.evidence / 'artifacts' / args.tree / 'manifest.json').read_bytes()).hexdigest()})
    print(json.dumps(measurement), flush=True)


if __name__ == '__main__':
    main()
