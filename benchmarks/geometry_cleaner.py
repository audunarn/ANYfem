"""Reproducible full geometry-diagnosis benchmark; no solver execution.

Run with --output an unused evidence directory. The supervisor bounds the child
process tree and retains exact command/source/environment, stdout and stderr.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import perf_counter


def fixture(size, dense=False):
    from anyfem.model.project import Project
    from anygeometry.model import GeometryModel
    from anygeometry.surfaces import Plane
    import numpy as np
    project = Project(name='geometry-diagnosis-benchmark')
    model = project.geometry
    side = max(1, int(size ** .5))
    with model.transaction():
        vertices = model.add_points((float(x), float(y), 0.) for y in range(side + 1) for x in range(side + 1))
        vertex = lambda x, y: vertices[y * (side + 1) + x]
        horizontal = model.add_lines((vertex(x, y), vertex(x + 1, y)) for y in range(side + 1) for x in range(side))
        vertical = model.add_lines((vertex(x, y), vertex(x, y + 1)) for y in range(side) for x in range(side + 1))
        h = lambda x, y: horizontal[y * side + x]
        v = lambda x, y: vertical[y * (side + 1) + x]
        loops = [(h(x, y), v(x + 1, y), h(x, y + 1), v(x, y)) for y in range(side) for x in range(side)]
        model.add_faces(loops, surfaces=[Plane(np.array([x, y, 0.]), np.array([1., 0, 0]), np.array([0, 1., 0])) for y in range(side) for x in range(side)])
        model.add_member([h(x, 0) for x in range(side)], name='boundary beam')
        if dense:
            # Offset endpoints create near gaps, short lines, crossings and
            # exact duplicates without changing the supported planar domain.
            for index in range(min(side, 32)):
                a = model.add_point(index + .25, .000002)
                b = model.add_point(index + .75, .000002)
                model.add_line(a, b)
                model.add_line(a, b)
    return project


def measure(args):
    from anyfem.geometry_cleaner import diagnose_project
    from anygeometry.cleaner import CleanerOptions
    start = perf_counter()
    project = fixture(args.faces, args.dense)
    construction = perf_counter() - start
    print(json.dumps({'event': 'fixture', 'construction_seconds': construction, 'faces': len(project.geometry.faces),
                      'edges': len(project.geometry.edges), 'vertices': len(project.geometry.vertices),
                      'members': len(project.geometry.members)}), flush=True)
    start = perf_counter()
    if args.profile:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    report = diagnose_project(project, CleanerOptions(), progress=lambda phase, fraction:
        print(json.dumps({'event': 'phase', 'phase': phase, 'fraction': fraction}), flush=True))
    diagnosis = perf_counter() - start
    if args.profile:
        profiler.disable()
        profiler.dump_stats(str(Path(args.output) / 'diagnosis.prof'))
        import pstats
        with (Path(args.output) / 'profile.txt').open('w', encoding='utf-8') as stream:
            pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(40)
    start = perf_counter()
    encoded = json.dumps(report.to_dict(), sort_keys=True, ensure_ascii=False, allow_nan=False)
    json_seconds = perf_counter() - start
    start = perf_counter()
    markdown = report.to_markdown()
    markdown_seconds = perf_counter() - start
    start = perf_counter()
    folder = Path(args.output)
    (folder / 'diagnosis.json').write_text(encoded, encoding='utf-8')
    (folder / 'diagnosis.md').write_text(markdown, encoding='utf-8')
    export_seconds = perf_counter() - start
    try:
        import psutil
        rss = psutil.Process().memory_info().rss
    except ImportError:
        rss = None
    result = {'faces': len(project.geometry.faces), 'edges': len(project.geometry.edges), 'vertices': len(project.geometry.vertices),
              'members': len(project.geometry.members), 'dense': args.dense, 'construction_seconds': construction,
              'diagnosis_seconds': diagnosis, 'json_seconds': json_seconds, 'markdown_seconds': markdown_seconds,
              'file_export_seconds': export_seconds, 'json_bytes': len(encoded.encode()),
              'markdown_bytes': len(markdown.encode()), 'rss_bytes': rss, 'complete': report.complete,
              'clean': report.clean, 'counts': report.counts, 'checks': [vars(check) for check in report.checks],
              'timings': dict(report.timings), 'audit_execution': report.source.get('audit_execution')}
    (folder / 'measurement.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--faces', type=int, default=1024)
    parser.add_argument('--dense', action='store_true')
    parser.add_argument('--output', required=True)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--child', action='store_true')
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    if args.child:
        measure(args)
        return
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('bounded_process', Path.home() / 'bin/claude-review.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--faces', str(args.faces), '--output', str(folder), '--child']
    if args.dense:
        command.append('--dense')
    if args.profile:
        command.append('--profile')
    env = dict(os.environ)
    settings = {'PYTHONPATH': 'C:/Github/ANYfem/src;C:/Github/ANYsolver/src;C:/Github/ANYgeometry/src;C:/Github/ANYmesh/src;C:/Github/ANYloads/src;C:/Github/ANYpost/src',
                'PYTHONDONTWRITEBYTECODE': '1', 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
    env.update(settings)
    roots = [Path('C:/Github/ANYgeometry/src/anygeometry'), Path('C:/Github/ANYfem/src/anyfem')]
    identities = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for root in roots for name in
                  ('cleaner.py', 'cleaner_diagnostics.py', 'geometry_cleaner.py', 'strict_audit.py', 'audit_parallel.py', 'audit.py', 'model.py', 'predicates.py', 'surfaces.py') if (path := root / name).is_file()}
    identities[str(Path(__file__).resolve())] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runner.save(folder / 'command.json', {'command': command, 'cwd': 'C:/Github/ANYfem', 'timeout_seconds': args.timeout,
                                         'environment': settings, 'input': {'faces_requested': args.faces, 'dense': args.dense}, 'source_sha256': identities})
    receipt, _ = runner.run_process(command, folder, 'stdout', args.timeout, env, Path('C:/Github/ANYfem'), True)
    runner.save(folder / 'result.json', receipt)
    print(json.dumps(receipt), flush=True)
    if (folder / 'measurement.json').is_file():
        print((folder / 'measurement.json').read_text(), flush=True)


if __name__ == '__main__':
    main()
