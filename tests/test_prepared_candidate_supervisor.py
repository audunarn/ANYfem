"""Pure mocked control checks for the prepared candidate supervisor.

No OS Job/process backend, fixture, mesh, FE or solver is ever used: every
test injects fakes (FakeTimer/FakeJob/FakeProcess) and exercises only the
preparation-only control contract, the reviewed-source reuse, the source
fingerprint enforcement, the child guard, the sequence claim and the child
result packet validation. Temporary ordinary files stand in for hashed
sources. The execution gate stays closed throughout.
"""
import ast
import contextlib
import hashlib
import importlib
import importlib.util
import io
import itertools
import json
import subprocess
import sys
from dataclasses import dataclass
from uuid import UUID
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / 'tools/prepared_candidate_supervisor.py'
spec = importlib.util.spec_from_file_location('prepared_candidate_supervisor', PATH)
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)

JOB_FLAGS = 0x2200
JOB_BYTES = 512 * 1024 ** 2
BREAKAWAY_FLAGS = 0x1800
NONCE = 'ab' * 32
MANIFEST_SHA = 'a1' * 32


def test_scalar_evidence_preserves_uuid_and_entity_reference():
    @dataclass(frozen=True)
    class Ref:
        kind: str
        id: int

    model_id = UUID('12345678-1234-5678-1234-567812345678')
    payload = supervisor._scalar((model_id, Ref('face', 7)))
    assert json.loads(json.dumps(payload)) == [str(model_id), {'kind': 'face', 'id': 7}]


@pytest.mark.parametrize('flags', [JOB_FLAGS | 1, JOB_FLAGS | 0x100, -1])
def test_limit_flags_refuse_unrequested_bits(flags):
    assert supervisor._limit_flags_acceptable(flags) is False


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeTimer:
    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.daemon = False
        self.started = False
        self.cancelled = False
        self.joined = False
        self.alive_after_join = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        self.joined = True

    def is_alive(self):
        return self.alive_after_join

    def fire(self):
        self.function()


class FakeStdin:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.chunks = []
        self.closed = False

    def write(self, data):
        self.events.append(('handshake', data))
        self.chunks.append(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def text(self):
        return b''.join(self.chunks).decode('utf-8')


class FakeProcess:
    def __init__(self, pid=4242, events=None, returncode=None, wait_result=None):
        self.pid = pid
        self._handle = 0x1234
        self.events = events if events is not None else []
        self.stdin = FakeStdin(self.events)
        self.returncode = returncode
        self.wait_result = wait_result
        self.killed = False
        self.waited = False

    def poll(self):
        return self.returncode

    def kill(self):
        self.events.append(('child-kill', self.pid))
        self.killed = True
        if self.returncode is None:
            self.returncode = 1

    def wait(self, timeout=None):
        self.waited = True
        self.events.append(('child-wait', timeout))
        if self.wait_result == 'timeout':
            raise subprocess.TimeoutExpired(['python'], timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeJob:
    def __init__(self, committed_bytes, *, name=None, events=None,
                 limit_flags=JOB_FLAGS, memory_bytes=None, stubborn=False):
        self.name = name
        self.events = events if events is not None else []
        self.limit_flags = limit_flags
        self.memory_bytes = memory_bytes if memory_bytes is not None else committed_bytes
        self.active = 0
        self.total = 0
        self.terminated = False
        self.closed = False
        self.stubborn = stubborn

    def snapshot(self):
        return {'configured_limit_flags': self.limit_flags,
                'configured_job_memory_limit_bytes': self.memory_bytes,
                'peak_job_committed_bytes': 0,
                'active_processes': self.active,
                'total_processes': self.total}

    def assign_suspended_only(self, process):
        self.events.append(('assign', process.pid))
        self.active += 1
        self.total += 1

    def resume_suspended(self, process, thread_id):
        self.events.append(('resume', process.pid, thread_id))

    def terminate(self):
        self.events.append(('terminate',))
        self.terminated = True
        if not self.stubborn:
            self.active = 0

    def close(self):
        self.events.append(('close',))
        self.closed = True


class NS:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def make_compose_result(case='root'):
    """Fake the exact prospective compose_case tuple with real field names."""
    ref_row = NS(key='load_cases/case-1/pressures/p1', category='pressure',
                 source_targets=(2,), current_targets=(2,),
                 cell_ids=(1, 2), node_ids=(1, 4))
    occ_row = NS(source_sheet_id=1, source_face_use_id=1, source_face_id=2,
                 current_face_use_ids=(3,), cell_ids=(1, 2),
                 source_orientation=1, current_orientations=((1, 0), (0, 1)))
    assoc_row = NS(field='structural_preparation', key=1, source_ids=(1,),
                   namespace='prepared_current')
    source_binding = NS(
        mesh=NS(tris=list(range(26))),
        references=(ref_row,), occurrences=(occ_row,), associations=(assoc_row,),
        source_reference_transfer_qualified=True, solver_admitted=False,
        publication_qualified=False)
    artifact = NS(
        source_binding=source_binding,
        production_record={'schema': 'anymesher.qualified-s3-production-preparation-v2',
                           'status': 'ADMITTED', 'contract_id': 'c1',
                           'formulation_id': 'f1', 'quality_contract_id': 'q1',
                           'repair_contract_id': 'r1',
                           'element_ids': [1, 2],
                           'nodal_normals': {1: [0.0, 0.0, 1.0]},
                           'element_owner_normals': {1: [0.0, 0.0, 1.0]}},
        element_ids=(1, 2), field_inventory=(('tris', 26), ('nodes', 20)),
        load_case_name='default', load_case_id='case-1',
        preassembly_checked=True, solver_executed=False, publication_qualified=False)
    inputs = {
        'project': NS(document_id='doc-1',
                      geometry=NS(model_id='model-1', revision=3)),
        'manifest': NS(schema_version=1, document_id='doc-1',
                      manifest_checksum='m' * 64, project_checksum='p' * 64),
        'association_namespace': 'prepared_current',
    }
    pressure = NS(id='p1', ref=NS(kind='face', id=2), value=1200)
    return inputs, pressure, artifact, ref_row


class FakeResultHarness:
    """Simulates separately reviewed future enablement of the pinned harness."""

    EXECUTION_ENABLED = True

    def __init__(self):
        self.calls = []

    def verify_artifacts(self, *args, **kwargs):
        self.calls.append('verify')

    def compose_case(self, case):
        self.calls.append(('compose', case))
        self.last_result = make_compose_result(case)
        return self.last_result


def fake_evidence(case):
    return {
        'case': case,
        'project_document_id': 'doc-1',
        'geometry_model_id': 'model-1',
        'geometry_revision': 3,
        'load_case_id': 'case-1',
        'load_case_name': 'default',
        'pressure': {'id': 'p1', 'ref_kind': 'face', 'ref_id': '2', 'value': 1200},
        'element_ids': [1, 2, 3],
        'association_namespace': 'prepared_current',
        'association_namespaces': ['prepared_current'],
        'pressure_binding': {'key': 'load_cases/case-1/pressures/p1',
                             'category': 'pressure', 'source_targets': [2],
                             'current_targets': [2], 'cell_ids': [1, 2],
                             'node_ids': [1]},
        'reference_ledger': [{'key': 'k', 'category': 'pressure', 'cell_ids': [1],
                              'node_ids': [1], 'source_targets': [2],
                              'current_targets': [2]}],
        'occurrence_ledger': [{'source_sheet_id': 1, 'source_face_use_id': 1,
                               'source_face_id': 2, 'current_face_use_ids': [3],
                               'cell_ids': [1], 'source_orientation': 1,
                               'current_orientations': [[1, 0]]}],
        't3_cell_count': 26,
        'field_inventory': [['tris', 26]],
        'production_record': {'schema': 'x', 'status': 'ADMITTED',
                              'checksum_sha256': '2' * 64,
                              'nodal_normal_count': 3,
                              'element_owner_normal_count': 3,
                              'element_count': 26},
        'manifest_checksums': {'manifest_checksum': '3' * 64,
                               'project_checksum': '4' * 64},
        'flags': {'preassembly_checked': True, 'solver_executed': False,
                  'publication_qualified': False,
                  'source_reference_transfer_qualified': True,
                  'solver_admitted': False},
        'evidence_gaps': [],
    }


def write_manifest(tmp_path, updates=None):
    manifest = {
        'cases': ['root', 'child'],
        'import_paths': ['C:/staged/geometry-wheel-import',
                         'C:/staged/mesher-source/src',
                         'C:/Github/ANYfem/src'],
        'harness_path': 'C:/Github/ANYfem/tools/prepared_candidate_integration.py',
        'runtime': {'interpreter': 'C:/pinned/python.exe'},
        'source_fingerprints': {
            'fem_source_digest': ('70d04351baa6e7688330f66f83ca22dc'
                                  '2d74c05e464e5f83991cec3af49a8f8a'),
            'geometry_wheel_sha256': ('cb88cc9156531142b5afcfd54f6cefde'
                                      '87ff97df0abc111bc0e7611039085b52'),
            'mesher_commit': '2cbb161daa75bb6785c9dc1afd2d558b7ca652f4',
        },
        'output_dir': str(tmp_path / 'run'),
    }
    if updates:
        manifest.update(updates)
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path, manifest


def echo_child_result_reader(created, manifest, manifest_path):
    """Fake child: answers with the packet identities from its handshake."""
    manifest_sha = supervisor._freeze_manifest_sha256(manifest_path)
    fp_sha, fp_count = supervisor._source_fingerprints_identity(manifest)
    seen = []

    def reader(case):
        child = created['children'][len(seen)]
        seen.append(case)
        handshake = json.loads(child.stdin.text())
        return {
            'status': 'CHILD_CASE_COMPLETED_UNADJUDICATED',
            'case': handshake['case'],
            'job_name': handshake['job_name'],
            'nonce': handshake['nonce'],
            'started': handshake['started'],
            'deadline': handshake['deadline'],
            'manifest_sha256': manifest_sha,
            'source_fingerprints_sha256': fp_sha,
            'source_fingerprint_count': fp_count,
            'guard': {'guard_armed': True, 'job_membership_verified': True},
            'evidence': fake_evidence(handshake['case']),
        }
    return reader


def run_sequence(tmp_path, *, events=None, clock=None, job_factory=None,
                 launcher=None, membership_check=None, thread_id_lookup=None,
                 wait_empty=None, nonce_factory=None, timer_factory=None,
                 manifest_updates=None, case_record_writer=None,
                 sequence_record_writer=None, sequence_claim=None,
                 fingerprint_check=None, child_result_reader=None,
                 packet_mutator=None):
    manifest_path, manifest = write_manifest(tmp_path, manifest_updates)
    events = events if events is not None else []
    clock = clock if clock is not None else FakeClock()
    created = {'jobs': [], 'children': [], 'timers': []}
    counter = itertools.count()

    def default_timer(interval, function):
        events.append(('timer-create', interval))
        timer = FakeTimer(interval, function)
        created['timers'].append(timer)
        return timer

    def default_job_factory(committed, *, name=None):
        events.append(('job-create', name))
        job = FakeJob(committed, name=name, events=events)
        created['jobs'].append(job)
        return job

    def default_launcher(command, **kwargs):
        events.append(('launch', tuple(command)))
        # Keep only control facts; failure diagnostics must not dump inherited
        # credentials or unrelated environment values.
        environment = kwargs.get('env', {})
        events.append(('launch-env', {key: environment[key] for key in (
            supervisor.CHILD_MARKER_ENV, supervisor.NONCE_ENV, 'PYTHONPATH')
            if key in environment}))
        child = FakeProcess(events=events)
        created['children'].append(child)
        return child

    def default_membership(job, child):
        events.append(('membership', job.name))
        return {'member': True, 'job_name': job.name}

    def default_thread_lookup(pid):
        events.append(('thread-id', pid))
        return 777

    def default_wait_empty(job, seconds=2.0):
        events.append(('wait-empty', job.name))
        return job.snapshot()

    @contextlib.contextmanager
    def default_logs(case):
        events.append(('logs-open', case))
        yield io.StringIO(), io.StringIO()

    def default_nonce():
        return f'{next(counter):064x}'

    def default_claim(manifest_sha256):
        events.append(('claim', manifest_sha256))
        return {'status': 'SEQUENCE_CLAIMED', 'manifest_sha256': manifest_sha256}

    def default_fingerprint_check(manifest, **kwargs):
        events.append(('fingerprints', kwargs.get('expected_manifest_sha256')))
        return {'manifest_sha256': kwargs.get('expected_manifest_sha256'),
                'source_fingerprints_sha256': '1' * 64,
                'source_fingerprint_count': 3}

    echo = echo_child_result_reader(created, manifest, manifest_path)

    def default_result_reader(case):
        packet = echo(case)
        if packet_mutator is not None:
            packet_mutator(packet)
        return packet

    record = supervisor._launch_sequence_core(
        manifest, manifest_path=manifest_path, clock=clock,
        timer_factory=timer_factory or default_timer,
        job_factory=job_factory or default_job_factory,
        launcher=launcher or default_launcher,
        membership_check=membership_check or default_membership,
        thread_id_lookup=thread_id_lookup or default_thread_lookup,
        wait_empty=wait_empty or default_wait_empty,
        log_stream_factory=default_logs,
        nonce_factory=nonce_factory or default_nonce,
        case_record_writer=(case_record_writer
                            or (lambda case, rec: events.append(
                                ('case-record', case, rec['status'])))),
        sequence_record_writer=(sequence_record_writer
                                or (lambda rec: events.append(
                                    ('sequence-record', rec['status'])))),
        sequence_claim=sequence_claim or default_claim,
        fingerprint_check=fingerprint_check or default_fingerprint_check,
        child_result_reader=child_result_reader or default_result_reader)
    return {'record': record, 'events': events, 'clock': clock,
            'created': created, 'manifest': manifest,
            'manifest_path': manifest_path}


def names(events):
    return [event[0] for event in events]


# --- Gate: refused first, before any backend/OS/filesystem dispatch ----------


def test_launch_gate_precedes_all_backend_and_filesystem_calls(monkeypatch, tmp_path):
    touched = []

    def recorder(name):
        def boom(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f'{name} dispatched before the control gate')
        return boom

    for name in ('load_manifest', '_reviewed_job_module',
                 '_reviewed_backstop_namespace', '_reviewed_helper_namespace',
                 '_named_job_membership_check', '_verify_source_fingerprints',
                 '_preload_supervisor_dependency'):
        monkeypatch.setattr(supervisor, name, recorder(name))
    with pytest.raises(supervisor.ControlNotReviewed):
        supervisor.launch_sequence(str(tmp_path / 'absent-manifest.json'))
    assert touched == []


def test_child_gate_precedes_all_backend_and_filesystem_calls(monkeypatch, tmp_path):
    touched = []

    def recorder(name):
        def boom(*args, **kwargs):
            touched.append(name)
            raise AssertionError(f'{name} dispatched before the control gate')
        return boom

    monkeypatch.setenv(supervisor.CHILD_MARKER_ENV, 'root')
    monkeypatch.setenv(supervisor.NONCE_ENV, NONCE)
    for name in ('load_manifest', '_reviewed_job_module'):
        monkeypatch.setattr(supervisor, name, recorder(name))
    with pytest.raises(supervisor.ControlNotReviewed):
        supervisor.child_entry(str(tmp_path / 'absent-manifest.json'), 'root')
    assert touched == []


def test_gate_refusal_touches_no_artifacts(tmp_path):
    path, _manifest = write_manifest(tmp_path)
    output = tmp_path / 'run'
    with pytest.raises(supervisor.ControlNotReviewed):
        supervisor.launch_sequence(str(path))
    assert not output.exists()


def test_cli_launch_and_child_refuse_while_unreviewed(tmp_path):
    with pytest.raises(supervisor.ControlNotReviewed):
        supervisor.main(['launch', '--manifest', str(tmp_path / 'm.json')])
    with pytest.raises(supervisor.ControlNotReviewed):
        supervisor.main(['child', '--manifest', str(tmp_path / 'm.json'),
                         '--case', 'root'])


def test_cli_contract_prints_preparation_only_record(tmp_path, capsys):
    path, _manifest = write_manifest(tmp_path)
    supervisor.main(['contract', '--manifest', str(path)])
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'PREPARED_UNEXECUTED'
    assert report['control_reviewed'] is False
    assert report['execution_enabled'] is False


def test_contract_is_preparation_only(tmp_path):
    path, manifest = write_manifest(tmp_path)
    contract = supervisor.control_contract(path)
    assert contract['control_reviewed'] is False
    assert contract['limits']['wall_seconds_total'] == 180.0
    assert contract['limits']['job_memory_limit_mib'] == 512
    assert contract['limits']['job_memory_limit_bytes'] == JOB_BYTES
    assert contract['limits']['required_limit_flags'] == JOB_FLAGS
    assert contract['limits']['refused_limit_flags'] == BREAKAWAY_FLAGS
    assert contract['sequence']['cases'] == ['root', 'child']
    assert contract['sequence']['failure_policy'] == 'stop on first failure; no retry'
    child_cmd = contract['commands']['child']
    launch_cmd = contract['commands']['launch']
    assert child_cmd[1:3] == ['-I', '-S']
    assert launch_cmd[1:3] == ['-I', '-S']
    assert contract['commands']['launch'][0] == manifest['runtime']['interpreter']
    assert all('PYTHONPATH' not in ' '.join(command)
               for command in (launch_cmd, child_cmd))
    assert all('--launch' not in command and '--child' not in command
               for command in (launch_cmd, child_cmd))
    assert 'manifest_sha256' in contract['handshake_fields']
    assert contract['source_verification']['enforced'] is True
    assert contract['source_verification']['fail_closed'].startswith(
        'missing or mismatched fingerprints refuse the case')
    assert contract['sequence_claim']['one_shot'] is True
    assert contract['child_result']['required_for_completion'].startswith(
        'exit 0 alone is insufficient')
    assert contract['runtime_probe'] is False
    assert contract['mesh_executed'] is False
    assert contract['solver_executed'] is False
    assert contract['manifest']['source_fingerprints']['mesher_commit'] \
        == '2cbb161daa75bb6785c9dc1afd2d558b7ca652f4'
    assert contract['reviewed_control_sources'] == {
        '_owned_windows_job_proposal.py': supervisor.JOB_HELPER_SHA256,
        'reviewed_geometry_driver_extract.py': supervisor.REVIEWED_DRIVER_SHA256,
        'reviewed_supervisor_helpers_extract.py': supervisor.REVIEWED_HELPERS_SHA256,
    }
    origins = contract['reviewed_control_source_origins']
    assert origins['reviewed_geometry_driver.py'] == \
        supervisor.REVIEWED_DRIVER_ORIGIN_SHA256
    assert origins['reviewed_supervisor_helpers.py'] == \
        supervisor.REVIEWED_HELPERS_ORIGIN_SHA256
    assert isinstance(origins['note'], str)


# --- Commands bind to the actual parser; no dispatch --------------------------


def test_contract_commands_bind_to_the_actual_parser(tmp_path):
    path, _manifest = write_manifest(tmp_path)
    contract = supervisor.control_contract(path)
    launch = contract['commands']['launch']
    child = contract['commands']['child']
    args = supervisor._build_parser().parse_args(launch[4:])
    assert args.action == 'launch' and args.manifest == str(path)
    for case in ('root', 'child'):
        concrete = [part if part != '<case>' else case for part in child]
        args = supervisor._build_parser().parse_args(concrete[4:])
        assert args.action == 'child' and args.case == case
        assert args.manifest == str(path)


def test_launched_child_command_binds_to_the_parser(tmp_path):
    run = run_sequence(tmp_path)
    commands = [event[1] for event in run['events'] if event[0] == 'launch']
    assert len(commands) == 2
    for command, case in zip(commands, ('root', 'child')):
        assert tuple(command[1:3]) == ('-I', '-S')
        args = supervisor._build_parser().parse_args(list(command[4:]))
        assert args.action == 'child' and args.case == case
        assert args.manifest == str(run['manifest_path'])


# --- Manifest: exact parent-supplied argument, fail closed -------------------


@pytest.mark.parametrize('updates', [
    {'cases': ['child', 'root']},
    {'cases': ['root']},
    {'import_paths': []},
    {'import_paths': ['']},
    {'import_paths': 'C:/staged'},
    {'harness_path': ''},
    {'runtime': {}},
    {'runtime': {'interpreter': ''}},
    {'source_fingerprints': {}},
    {'source_fingerprints': {'digest': 1}},
    {'output_dir': ''},
])
def test_malformed_manifest_refuses(tmp_path, updates):
    path, _manifest = write_manifest(tmp_path, updates)
    with pytest.raises(ValueError):
        supervisor.load_manifest(path)


def test_missing_manifest_field_refuses(tmp_path):
    path, _manifest = write_manifest(tmp_path)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    del manifest['output_dir']
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='output_dir'):
        supervisor.load_manifest(path)


# --- Reviewed-source reuse: hash-checked, minimal, no inherited acceptance ----


def test_reviewed_extraction_is_hash_checked_and_minimal():
    backstop = supervisor._reviewed_backstop_namespace()
    assert set(backstop) <= {'__builtins__', 'annotations', 'time', 'threading',
                            'OwnedDeadlineBackstop', '_cleanup_owned'}
    assert 'main' not in backstop and '_run' not in backstop
    assert 'RUN_CONTROL_REVIEWED' not in backstop
    assert 'prepare' not in backstop
    helpers = supervisor._reviewed_helper_namespace()
    assert set(helpers) <= {'__builtins__', 'time',
                            '_suspended_thread_id', '_wait_job_empty'}
    job = supervisor._reviewed_job_module()
    assert job.OwnedWindowsJob and job.current_process_job_facts
    assert supervisor.CONTROL_REVIEWED is False  # no inherited reviewed acceptance


def test_tampered_control_source_refuses(tmp_path):
    tampered = tmp_path / 'tampered.py'
    tampered.write_bytes(supervisor.REVIEWED_DRIVER_PATH.read_bytes() + b'\n')
    with pytest.raises(RuntimeError, match='changed'):
        supervisor._extract_reviewed_functions(
            tampered, supervisor.REVIEWED_DRIVER_SHA256,
            ('OwnedDeadlineBackstop',), {'time': supervisor.time,
                                         'threading': supervisor.threading})


def top_level_segments(path, names):
    text = Path(path).read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    segments = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            segments[node.name] = ''.join(lines[node.lineno - 1:node.end_lineno])
    return segments


def test_portable_control_sources_are_minimal_tracked_extracts():
    controls = Path(__file__).resolve().parents[1] / 'tools/prepared_candidate_controls'
    expected = {
        'reviewed_geometry_driver_extract.py':
            {'OwnedDeadlineBackstop', '_cleanup_owned'},
        'reviewed_supervisor_helpers_extract.py':
            {'_suspended_thread_id', '_wait_job_empty'},
    }
    for name, defs in expected.items():
        text = (controls / name).read_text(encoding='utf-8')
        tree = ast.parse(text)
        assert {node.name for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef))} == defs
        # Only the provenance docstring, standard library imports and the
        # required definitions: no executable main or reviewed-gate block.
        for node in tree.body:
            assert isinstance(node, (ast.Expr, ast.Import, ast.ImportFrom,
                                     ast.FunctionDef, ast.ClassDef))
        assert '__main__' not in text
        assert 'RUN_CONTROL_REVIEWED' not in text
        assert 'HardWallUnqualified' not in text
    # The exact reviewed Windows Job helper is carried unchanged.
    helper = controls / '_owned_windows_job_proposal.py'
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == \
        supervisor.JOB_HELPER_SHA256


def test_portable_control_source_hashes_match_recorded_constants():
    for path, expected in ((supervisor.JOB_HELPER_PATH,
                            supervisor.JOB_HELPER_SHA256),
                           (supervisor.REVIEWED_DRIVER_PATH,
                            supervisor.REVIEWED_DRIVER_SHA256),
                           (supervisor.REVIEWED_HELPERS_PATH,
                            supervisor.REVIEWED_HELPERS_SHA256)):
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected
        assert Path(path).is_relative_to(Path(__file__).resolve().parents[1])


def test_portable_extract_bodies_match_reviewed_origin_segments():
    root = Path(__file__).resolve().parents[1]
    pairs = (
        (supervisor.REVIEWED_DRIVER_PATH, supervisor.REVIEWED_DRIVER_ORIGIN_PATH,
         supervisor.REVIEWED_DRIVER_ORIGIN_SHA256,
         ('OwnedDeadlineBackstop', '_cleanup_owned')),
        (supervisor.REVIEWED_HELPERS_PATH,
         supervisor.REVIEWED_HELPERS_ORIGIN_PATH,
         supervisor.REVIEWED_HELPERS_ORIGIN_SHA256,
         ('_suspended_thread_id', '_wait_job_empty')),
    )
    for extract_path, origin_rel, origin_sha, names in pairs:
        origin = root / origin_rel
        if not origin.exists():
            pytest.skip('reviewed origin evidence checkout is not present')
        assert hashlib.sha256(origin.read_bytes()).hexdigest() == origin_sha
        assert top_level_segments(extract_path, names) == \
            top_level_segments(origin, names)


def test_supervisor_source_imports_no_engineering_packages():
    tree = ast.parse(PATH.read_text(encoding='utf-8'))
    engineering = {'anyfem', 'anymesher', 'anygeometry', 'numpy', 'scipy',
                   'tkinter'}
    for node in tree.body:
        inside = node.name if isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                for alias in sub.names:
                    root = alias.name.split('.')[0]
                    assert root not in engineering
                    if root == 'psutil':
                        # Supervisor-only dependency, imported strictly inside
                        # the explicit manifest path scope helper.
                        assert inside == '_preload_supervisor_dependency'
            if isinstance(sub, ast.ImportFrom) and sub.module:
                assert sub.module.split('.')[0] not in engineering


# --- Source fingerprint enforcement: fail closed, deadline-checked ------------


def write_fingerprinted_manifest(tmp_path, *, interpreter=None, drop=None,
                                 tamper=None):
    files = {}
    for name in ('a.py', 'b.py'):
        item = tmp_path / name
        item.write_text(f'content-{name}', encoding='utf-8')
        files[str(item)] = hashlib.sha256(item.read_bytes()).hexdigest()
    harness = tmp_path / 'harness.py'
    harness.write_text('harness', encoding='utf-8')
    files[str(harness)] = hashlib.sha256(harness.read_bytes()).hexdigest()
    if drop is not None:
        files.pop(str(tmp_path / drop))
    if tamper is not None:
        (tmp_path / tamper).write_text('tampered', encoding='utf-8')
    manifest = {
        'cases': ['root', 'child'],
        'import_paths': [str(tmp_path)],
        'harness_path': str(harness),
        'runtime': {'interpreter': interpreter or sys.executable},
        'source_fingerprints': files,
        'output_dir': str(tmp_path / 'out'),
    }
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path, manifest


def test_fingerprint_verification_accepts_exact_sources(tmp_path):
    path, manifest = write_fingerprinted_manifest(tmp_path)
    summary = supervisor._verify_source_fingerprints(
        manifest, manifest_path=path, clock=FakeClock(), deadline=1180.0,
        interpreter=sys.executable)
    assert summary['manifest_sha256'] == hashlib.sha256(
        path.read_bytes()).hexdigest()
    expected_sha, expected_count = supervisor._source_fingerprints_identity(manifest)
    assert summary['source_fingerprints_sha256'] == expected_sha
    assert summary['source_fingerprint_count'] == expected_count == 3


def test_fingerprint_verification_refuses_tampered_source(tmp_path):
    path, manifest = write_fingerprinted_manifest(tmp_path, tamper='a.py')
    with pytest.raises(supervisor.SourceVerificationRefused, match='changed'):
        supervisor._verify_source_fingerprints(
            manifest, manifest_path=path, clock=FakeClock(), deadline=1180.0)


def test_fingerprint_verification_refuses_missing_source(tmp_path):
    path, manifest = write_fingerprinted_manifest(tmp_path)
    (tmp_path / 'b.py').unlink()
    with pytest.raises(supervisor.SourceVerificationRefused, match='missing'):
        supervisor._verify_source_fingerprints(
            manifest, manifest_path=path, clock=FakeClock(), deadline=1180.0)


def test_fingerprint_verification_refuses_changed_manifest_bytes(tmp_path):
    path, manifest = write_fingerprinted_manifest(tmp_path)
    frozen = supervisor._freeze_manifest_sha256(path)
    path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
    with pytest.raises(supervisor.SourceVerificationRefused,
                       match='manifest bytes changed'):
        supervisor._verify_source_fingerprints(
            manifest, manifest_path=path, clock=FakeClock(), deadline=1180.0,
            expected_manifest_sha256=frozen)


def test_fingerprint_verification_requires_pinned_harness_coverage(tmp_path):
    path, manifest = write_fingerprinted_manifest(tmp_path, drop='harness.py')
    with pytest.raises(supervisor.SourceVerificationRefused, match='harness'):
        supervisor._verify_source_fingerprints(
            manifest, manifest_path=path, clock=FakeClock(), deadline=1180.0)


def test_fingerprint_verification_requires_manifest_interpreter(tmp_path):
    path, manifest = write_fingerprinted_manifest(
        tmp_path, interpreter='C:/other/python.exe')
    with pytest.raises(supervisor.SourceVerificationRefused,
                       match='interpreter'):
        supervisor._verify_source_fingerprints(
            manifest, manifest_path=path, clock=FakeClock(), deadline=1180.0,
            interpreter=sys.executable)


def test_fingerprint_verification_checks_deadline_during_hashing(tmp_path):
    path, manifest = write_fingerprinted_manifest(tmp_path)

    class MarchingClock:
        def __init__(self):
            self.now = 1000.0

        def __call__(self):
            self.now += 100.0
            return self.now

    with pytest.raises(TimeoutError):
        supervisor._verify_source_fingerprints(
            manifest, manifest_path=path, clock=MarchingClock(), deadline=1180.0)


# --- Supervisor dependency scope: explicit paths, restored afterwards ---------


def test_manifest_dependency_scope_installs_and_restores(tmp_path):
    (tmp_path / 'scope_mod_xyz.py').write_text('VALUE = 42\n', encoding='utf-8')
    manifest = {'import_paths': [str(tmp_path)]}
    before = list(sys.path)
    with supervisor._manifest_dependency_scope(manifest):
        assert sys.path[0] == str(tmp_path)
        assert importlib.import_module('scope_mod_xyz').VALUE == 42
    assert sys.path == before
    sys.modules.pop('scope_mod_xyz', None)


def test_manifest_dependency_scope_restores_on_error(tmp_path):
    manifest = {'import_paths': [str(tmp_path)]}
    before = list(sys.path)
    with pytest.raises(RuntimeError):
        with supervisor._manifest_dependency_scope(manifest):
            raise RuntimeError('boom')
    assert sys.path == before


# --- Core sequencing: claim, fingerprints, timer, assignment, handshake -------


def test_happy_path_orders_claim_fingerprints_timer_assignment_resume(tmp_path):
    run = run_sequence(tmp_path)
    order = names(run['events'])
    assert order.index('claim') < order.index('timer-create')
    assert order.index('timer-create') < order.index('fingerprints')
    assert order.index('fingerprints') < order.index('job-create')
    assert order.index('job-create') < order.index('launch')
    assert order.index('assign') < order.index('membership') < order.index('resume')
    assert order.index('resume') < order.index('handshake')
    assert run['created']['timers'][0].interval == pytest.approx(180.0)
    assert run['record']['status'] == 'SEQUENCE_COMPLETED_UNADJUDICATED'
    assert [case['status'] for case in run['record']['case_records']] == \
        ['CASE_COMPLETED_UNADJUDICATED', 'CASE_COMPLETED_UNADJUDICATED']
    assert [case['child_exit_code'] for case in run['record']['case_records']] == [0, 0]


def test_exact_unique_named_job_per_case_and_isolated_child_command(tmp_path):
    run = run_sequence(tmp_path)
    job_names = [job.name for job in run['created']['jobs']]
    assert len(job_names) == 2 and len(set(job_names)) == 2
    assert all(name.startswith(supervisor.JOB_NAME_PREFIX)
               for name in job_names)
    for event in run['events']:
        if event[0] == 'launch':
            command = event[1]
            assert command[0] == 'C:/pinned/python.exe'
            assert tuple(command[1:3]) == ('-I', '-S')
            assert command[4] == 'child'
            assert '--child' not in command and '--launch' not in command
            assert '--manifest' in command
        if event[0] == 'launch-env':
            env = event[1]
            assert 'PYTHONPATH' not in env
            assert env[supervisor.CHILD_MARKER_ENV] in ('root', 'child')
            assert len(env[supervisor.NONCE_ENV]) == 64


def test_sequence_claim_is_one_shot_and_refuses_replay(tmp_path):
    events = []
    output = tmp_path / 'run'
    writer = supervisor._make_sequence_claim_writer(output)
    def claim(manifest_sha256):
        record = writer(manifest_sha256)
        events.append(('claim', manifest_sha256))
        return record
    run_sequence(tmp_path, events=events, sequence_claim=claim)
    assert (output / 'sequence-claim.json').exists()
    with pytest.raises(supervisor.SequenceReplayRefused, match='replay'):
        run_sequence(tmp_path, events=events, sequence_claim=claim)
    # The replay was refused before any case control work ran again.
    assert names(events).count('job-create') == 2
    assert names(events).count('claim') == 1


def test_per_case_backstop_loading_is_charged_to_deadline(tmp_path, monkeypatch):
    clock = FakeClock()
    load = supervisor._reviewed_backstop_namespace

    def delayed_load():
        clock.advance(4.0)
        return load()

    monkeypatch.setattr(supervisor, '_reviewed_backstop_namespace', delayed_load)
    run = run_sequence(tmp_path, clock=clock)
    assert [row['started'] for row in run['record']['case_records']] == [1000.0, 1004.0]
    assert [timer.interval for timer in run['created']['timers']] == [176.0, 176.0]


def test_fingerprint_check_runs_per_case_under_the_armed_timer(tmp_path):
    checks = []
    events = []

    def recording_check(manifest, **kwargs):
        checks.append(dict(kwargs))
        events.append(('fingerprints', kwargs.get('expected_manifest_sha256')))
        return {'manifest_sha256': kwargs['expected_manifest_sha256'],
                'source_fingerprints_sha256': '1' * 64,
                'source_fingerprint_count': 3}

    run = run_sequence(tmp_path, events=events, fingerprint_check=recording_check)
    order = names(run['events'])
    assert order.index('timer-create') < order.index('fingerprints')
    assert order.index('fingerprints') < order.index('job-create')
    frozen = supervisor._freeze_manifest_sha256(run['manifest_path'])
    assert [check['expected_manifest_sha256'] for check in checks] == \
        [frozen, frozen]
    for check, record in zip(checks, run['record']['case_records']):
        assert check['deadline'] == record['started'] + 180.0
        assert check['interpreter'] == sys.executable
        assert Path(check['manifest_path']) == run['manifest_path']


def test_fingerprint_failure_fails_closed_before_job_creation(tmp_path):
    def refusing_check(manifest, **kwargs):
        raise supervisor.SourceVerificationRefused(
            'fingerprinted source changed: C:/staged/a.py')

    run = run_sequence(tmp_path, fingerprint_check=refusing_check)
    order = names(run['events'])
    assert 'job-create' not in order and 'launch' not in order
    root = run['record']['case_records'][0]
    assert root['status'] == ('FAILED_SUPERVISOR:SourceVerificationRefused:'
                              'fingerprinted source changed: C:/staged/a.py')
    assert root['source_verification'] is None
    assert root['owned_process_count_zero'] is True
    assert run['record']['status'].startswith('SEQUENCE_STOPPED_FIRST_FAILURE:root')
    assert len(run['created']['jobs']) == 0


def test_handshake_carries_observed_limits_common_deadline_and_manifest_hash(tmp_path):
    run = run_sequence(tmp_path)
    child = run['created']['children'][0]
    assert child.stdin.closed
    handshake = json.loads(child.stdin.text())
    root = run['record']['case_records'][0]
    frozen = supervisor._freeze_manifest_sha256(run['manifest_path'])
    assert handshake['nonce'] == root['job_name'].rsplit('-', 1)[-1]
    assert handshake['case'] == 'root'
    assert handshake['job_name'] == root['job_name']
    assert handshake['deadline'] - handshake['started'] == pytest.approx(180.0)
    assert handshake['deadline'] == root['deadline']
    assert handshake['manifest_sha256'] == frozen == root['manifest_sha256']
    assert handshake['limits'] == {
        'configured_limit_flags': JOB_FLAGS,
        'configured_job_memory_limit_bytes': JOB_BYTES}
    assert handshake['guard_armed'] is True
    # Observed queried values are reported, not intended settings.
    assert root['queried_job_assigned']['configured_limit_flags'] == JOB_FLAGS
    assert root['queried_membership']['member'] is True


def test_deadline_expiry_before_assignment_blocks_resume_and_handshake(tmp_path):
    clock = FakeClock()

    def late_thread_lookup(pid):
        clock.advance(181.0)
        return 777

    run = run_sequence(tmp_path, clock=clock, thread_id_lookup=late_thread_lookup)
    order = names(run['events'])
    assert 'assign' not in order and 'resume' not in order
    assert run['created']['children'][0].stdin.chunks == []
    root = run['record']['case_records'][0]
    assert root['status'] == 'INCOMPLETE_PREFLIGHT_OR_SETUP_TIMEOUT'
    assert root['deadline_backstop']['expired'] is True
    assert root['owned_process_count_zero'] is True
    assert run['record']['status'] == 'SEQUENCE_STOPPED_FIRST_FAILURE:root:' \
        'INCOMPLETE_PREFLIGHT_OR_SETUP_TIMEOUT'
    assert len(run['created']['jobs']) == 1  # second case never started


def test_deadline_expiry_before_resume_blocks_resume_and_handshake(tmp_path):
    clock = FakeClock()

    def late_membership(job, child):
        clock.advance(181.0)
        return {'member': True, 'job_name': job.name}

    run = run_sequence(tmp_path, clock=clock, membership_check=late_membership)
    order = names(run['events'])
    assert 'assign' in order and 'resume' not in order
    assert run['created']['children'][0].stdin.chunks == []
    root = run['record']['case_records'][0]
    assert root['status'] == 'INCOMPLETE_PREFLIGHT_OR_SETUP_TIMEOUT'
    assert root['owned_process_count_zero'] is True


def test_wrong_configured_memory_fails_closed_before_child_creation(tmp_path):
    events = []

    def wrong_memory(committed, *, name=None):
        events.append(('job-create', name))
        return FakeJob(committed, name=name, events=events,
                       memory_bytes=committed // 2)

    run = run_sequence(tmp_path, events=events, job_factory=wrong_memory)
    root = run['record']['case_records'][0]
    assert root['status'].startswith('FAILED_SUPERVISOR')
    assert 'launch' not in names(events)
    assert root['queried_job_setup']['configured_job_memory_limit_bytes'] \
        == JOB_BYTES // 2  # observed value reported
    assert root['owned_process_count_zero'] is True


def test_wrong_configured_flags_fail_closed(tmp_path):
    events = []

    def wrong_flags(committed, *, name=None):
        events.append(('job-create', name))
        return FakeJob(committed, name=name, events=events, limit_flags=0x0200)

    run = run_sequence(tmp_path, events=events, job_factory=wrong_flags)
    root = run['record']['case_records'][0]
    assert root['status'].startswith('FAILED_SUPERVISOR')
    assert 'launch' not in names(events)


def test_breakaway_job_limits_refuse_before_child_creation(tmp_path):
    events = []

    def breakaway_factory(committed, *, name=None):
        events.append(('job-create', name))
        return FakeJob(committed, name=name, events=events,
                       limit_flags=JOB_FLAGS | 0x0800)

    run = run_sequence(tmp_path, events=events, job_factory=breakaway_factory)
    root = run['record']['case_records'][0]
    assert root['status'].startswith('FAILED_SUPERVISOR')
    assert 'launch' not in names(events)
    assert root['queried_job_setup']['configured_limit_flags'] == JOB_FLAGS | 0x0800


def test_foreign_job_membership_refuses_before_resume(tmp_path):
    events = []

    def foreign_membership(job, child):
        events.append(('membership', job.name))
        return {'member': False, 'job_name': job.name}

    run = run_sequence(tmp_path, events=events, membership_check=foreign_membership)
    order = names(events)
    assert 'assign' in order and 'resume' not in order
    assert run['created']['children'][0].stdin.chunks == []
    root = run['record']['case_records'][0]
    assert root['status'].startswith('FAILED_SUPERVISOR')
    assert root['queried_membership']['member'] is False


def test_nonzero_child_exit_stops_the_sequence(tmp_path):
    events = []

    def failing_launcher(command, **kwargs):
        events.append(('launch', tuple(command)))
        child = FakeProcess(events=events, returncode=3)
        return child

    run = run_sequence(tmp_path, events=events, launcher=failing_launcher)
    root = run['record']['case_records'][0]
    assert root['status'] == 'FAILED_CHILD'
    assert root['child_exit_code'] == 3
    assert run['record']['status'] == 'SEQUENCE_STOPPED_FIRST_FAILURE:root:FAILED_CHILD'
    assert len(run['created']['jobs']) == 1  # child case never started
    assert run['record']['cases_started'] == ['root']


def test_wall_timeout_on_child_wait_is_incomplete(tmp_path):
    events = []

    def hanging_launcher(command, **kwargs):
        events.append(('launch', tuple(command)))
        return FakeProcess(events=events, wait_result='timeout')

    run = run_sequence(tmp_path, events=events, launcher=hanging_launcher)
    root = run['record']['case_records'][0]
    assert root['status'] == 'INCOMPLETE_WALL_TIMEOUT'
    assert root['owned_process_count_zero'] is True
    assert run['record']['status'].startswith('SEQUENCE_STOPPED_FIRST_FAILURE:root')


# --- Child result packet: exit 0 alone is never sufficient ---------------------


def test_valid_child_result_packet_identities_are_validated(tmp_path):
    run = run_sequence(tmp_path)
    for record in run['record']['case_records']:
        assert record['status'] == 'CASE_COMPLETED_UNADJUDICATED'
        assert record['child_result_complete'] is True
        assert record['child_result_status'] == 'CHILD_CASE_COMPLETED_UNADJUDICATED'
        assert record['child_result_problem'] is None


def test_exit_zero_without_result_packet_is_incomplete(tmp_path):
    run = run_sequence(tmp_path, child_result_reader=lambda case: None)
    root = run['record']['case_records'][0]
    assert root['status'] == 'INCOMPLETE_CHILD_RESULT_PACKET'
    assert root['child_exit_code'] == 0
    assert root['child_result_complete'] is False
    assert 'absent' in root['child_result_problem']
    assert run['record']['status'] == ('SEQUENCE_STOPPED_FIRST_FAILURE:root:'
                                        'INCOMPLETE_CHILD_RESULT_PACKET')
    assert len(run['created']['jobs']) == 1  # second case never started


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(status='CHILD_CASE_FAILED'),
    lambda p: p.update(case='child'),
    lambda p: p.update(nonce='cd' * 32),
    lambda p: p.update(job_name='ANYfem-prepared-candidate-root-foreign'),
    lambda p: p.update(deadline=p['deadline'] - 1),
    lambda p: p.update(manifest_sha256='0' * 64),
    lambda p: p.update(source_fingerprints_sha256='0' * 64),
    lambda p: p.update(source_fingerprint_count=99),
    lambda p: p.pop('evidence'),
    lambda p: p['evidence'].pop('occurrence_ledger'),
    lambda p: p['evidence'].pop('manifest_checksums'),
    lambda p: p['evidence'].update(element_ids=[]),
    lambda p: p['evidence']['flags'].update(solver_executed=True),
    lambda p: p['evidence']['flags'].update(publication_qualified=True),
    lambda p: p['evidence']['flags'].update(preassembly_checked=False),
    lambda p: p['evidence'].pop('production_record'),
    lambda p: p['guard'].update(job_membership_verified=False),
])
def test_incomplete_or_foreign_child_result_packet_blocks_completion(tmp_path, mutate):
    run = run_sequence(tmp_path, packet_mutator=mutate)
    root = run['record']['case_records'][0]
    assert root['status'] == 'INCOMPLETE_CHILD_RESULT_PACKET'
    assert root['child_exit_code'] == 0
    assert root['child_result_complete'] is False
    assert root['child_result_problem']
    assert run['record']['status'].startswith('SEQUENCE_STOPPED_FIRST_FAILURE:root')


def test_child_result_reader_reads_or_reports_absence(tmp_path):
    output = tmp_path / 'run'
    output.mkdir()
    reader = supervisor._make_child_result_reader(output)
    assert reader('root') is None
    (output / 'child-child-result.json').write_text(
        '{"status": "CHILD_CASE_FAILED"}', encoding='utf-8')
    assert reader('child') == {'status': 'CHILD_CASE_FAILED'}


def test_child_recorder_writes_identity_packet(tmp_path):
    _path, manifest = write_manifest(tmp_path)
    output = tmp_path / 'run'
    manifest = dict(manifest, output_dir=str(output))
    write = supervisor._make_child_recorder(manifest)
    guard = {'guard_armed': True, 'case': 'root', 'nonce': NONCE,
             'job_name': supervisor.JOB_NAME_PREFIX + 'root-' + NONCE,
             'started': 1000.0, 'deadline': 1180.0,
             'manifest_sha256': MANIFEST_SHA,
             'job_membership_verified': True,
             'source_verification': {'source_fingerprints_sha256': 'f' * 64,
                                     'source_fingerprint_count': 3}}
    record = {'status': 'CHILD_CASE_COMPLETED_UNADJUDICATED', 'case': 'root',
              'guard': guard, 'control_reviewed': False, 'runtime_probe': False,
              'evidence': fake_evidence('root')}
    write(record, ('inputs', 'pressure', 'artifact', 'binding'))
    packet = json.loads((output / 'root-child-result.json').read_text(
        encoding='utf-8'))
    assert packet['status'] == 'CHILD_CASE_COMPLETED_UNADJUDICATED'
    assert packet['nonce'] == NONCE
    assert packet['job_name'] == supervisor.JOB_NAME_PREFIX + 'root-' + NONCE
    assert packet['manifest_sha256'] == MANIFEST_SHA
    assert packet['source_fingerprints_sha256'] == 'f' * 64
    assert packet['source_fingerprint_count'] == 3
    assert packet['evidence']['t3_cell_count'] == 26
    with pytest.raises(FileExistsError):
        write(record, None)  # write-once evidence


# --- Cleanup: attempted despite failure, quiescence before handle close -------


def test_job_creation_failure_still_quiesces_timer(tmp_path):
    events = []

    def boom(committed, *, name=None):
        events.append(('job-create-failed',))
        raise RuntimeError('job backend unavailable')

    run = run_sequence(tmp_path, events=events, job_factory=boom)
    root = run['record']['case_records'][0]
    assert root['status'] == 'FAILED_SUPERVISOR:RuntimeError:job backend unavailable'
    assert root['deadline_backstop']['armed'] is True
    assert root['deadline_backstop']['job_quiesced'] is True
    timer = run['created']['timers'][0]
    assert timer.cancelled and timer.joined
    assert root['cleanup_errors'] == []
    assert root['owned_process_count_zero'] is True  # no Job or child was created
    assert root['owned_cleanup_basis'] == 'no Job or child created'


def test_launcher_failure_still_terminates_and_closes_owned_job(tmp_path):
    events = []

    def boom_launcher(command, **kwargs):
        events.append(('launch-failed',))
        raise OSError('no process')

    run = run_sequence(tmp_path, events=events, launcher=boom_launcher)
    root = run['record']['case_records'][0]
    assert root['status'] == 'FAILED_SUPERVISOR:OSError:no process'
    order = names(events)
    assert 'terminate' in order and 'close' in order
    assert order.index('terminate') < order.index('close')
    assert root['owned_process_count_zero'] is True
    assert root['deadline_backstop']['job_quiesced'] is True


def test_cleanup_terminates_child_and_verifies_zero_active_processes(tmp_path):
    run = run_sequence(tmp_path)
    order = names(run['events'])

    def last(name):
        return len(order) - 1 - order[::-1].index(name)

    assert last('terminate') < last('child-wait') < last('wait-empty') < last('close')
    root = run['record']['case_records'][0]
    assert root['owned_process_count_zero'] is True
    assert root['cleanup_errors'] == []


def test_live_descendants_after_termination_is_incomplete(tmp_path):
    events = []

    def stubborn_job(committed, *, name=None):
        events.append(('job-create', name))
        return FakeJob(committed, name=name, events=events, stubborn=True)

    run = run_sequence(tmp_path, events=events, job_factory=stubborn_job)
    root = run['record']['case_records'][0]
    # Any cleanup error fails the case (reviewed semantics); the live
    # descendants are also reported through the zero-process verification.
    assert root['status'] == 'INCOMPLETE_CLEANUP'
    assert any('live descendants' in row for row in root['cleanup_errors'])
    assert root['owned_process_count_zero'] is False
    assert root['queried_after_cleanup']['active_processes'] == 1


def test_unquiesced_deadline_callback_fails_the_case(tmp_path):
    events = []
    timers = []

    def bad_timer(interval, function):
        events.append(('timer-create', interval))
        timer = FakeTimer(interval, function)
        timer.alive_after_join = True
        timers.append(timer)
        return timer

    run = run_sequence(tmp_path, events=events, timer_factory=bad_timer)
    root = run['record']['case_records'][0]
    assert root['status'] == 'INCOMPLETE_CLEANUP'
    assert any('deadline backstop stop' in row for row in root['cleanup_errors'])
    order = names(events)
    # The timer callback is stopped before the Job handle closes.
    assert timers[0].joined
    assert 'close' in order


def test_cleanup_owned_order_is_reused_from_reviewed_source(tmp_path):
    events = []
    backstop = supervisor._reviewed_backstop_namespace()
    guard = backstop['OwnedDeadlineBackstop'](1180.0, clock=FakeClock(),
                                              timer_factory=FakeTimer)
    guard.arm()
    job = FakeJob(JOB_BYTES, name='ANYfem-prepared-candidate-root-x', events=events)
    guard.register_job(job)
    child = FakeProcess(events=events)
    guard.register_child(child)
    job.assign_suspended_only(child)
    guard.mark_assigned()

    def wait_empty(j, seconds=2.0):
        events.append(('wait-empty', j.name))
        return j.snapshot()

    peak, final, errors, interrupt = backstop['_cleanup_owned'](
        job, child, wait_empty, guard=guard)
    order = names(events)
    assert order == ['assign', 'terminate', 'child-kill', 'child-wait',
                     'wait-empty', 'close']
    assert peak['active_processes'] == 1 and final['active_processes'] == 0
    assert errors == [] and interrupt is None
    assert guard.snapshot()['job_quiesced'] is True


# --- Child guard: exact named Job membership and observed limits --------------


def good_handshake(**overrides):
    handshake = {
        'nonce': NONCE,
        'case': 'root',
        'job_name': supervisor.JOB_NAME_PREFIX + 'root-' + NONCE,
        'started': 1000.0,
        'deadline': 1180.0,
        'limits': {'configured_limit_flags': JOB_FLAGS,
                   'configured_job_memory_limit_bytes': JOB_BYTES},
        'manifest_sha256': MANIFEST_SHA,
        'guard_armed': True,
    }
    handshake.update(overrides)
    return handshake


def guard_run(handshake, *, nonce=NONCE, case='root', facts=None, clock=None,
              raw_line=None):
    stream = io.StringIO(raw_line if raw_line is not None
                         else json.dumps(handshake) + '\n')
    facts = facts if facts is not None else {
        'member': True, 'configured_limit_flags': JOB_FLAGS,
        'configured_job_memory_limit_bytes': JOB_BYTES}
    queries = []

    def job_query(name):
        queries.append(name)
        return facts

    guard = supervisor._child_guard(case, nonce, input_stream=stream,
                                    job_query=job_query,
                                    clock=clock if clock is not None else FakeClock())
    return guard, queries


@pytest.mark.parametrize('started', [0.1, 63.99999999999999, 127.99999999999999, 12345.678901234])
def test_child_guard_accepts_exact_parent_deadline_with_fractional_start(started):
    deadline = started + supervisor.WALL_SECONDS
    guard, _ = guard_run(good_handshake(started=started, deadline=deadline),
                         clock=FakeClock(started))
    assert guard['deadline'] == deadline


def test_child_guard_accepts_and_reports_observed_job_facts():
    guard, queries = guard_run(good_handshake())
    assert queries == [supervisor.JOB_NAME_PREFIX + 'root-' + NONCE]
    assert guard['guard_armed'] is True
    assert guard['job_membership_verified'] is True
    assert guard['observed_limit_flags'] == JOB_FLAGS
    assert guard['observed_job_memory_limit_bytes'] == JOB_BYTES
    assert guard['deadline'] == 1180.0 and guard['started'] == 1000.0
    assert guard['nonce'] == NONCE
    assert guard['manifest_sha256'] == MANIFEST_SHA


@pytest.mark.parametrize('overrides', [
    {'nonce': 'cd' * 32},
    {'case': 'child'},
    {'job_name': 'ANYmesher-authored-foreign'},
    {'guard_armed': False},
    {'deadline': 1179.0},
    {'deadline': float('inf')},
    {'started': float('inf')},
    {'manifest_sha256': 'too-short'},
    {'manifest_sha256': 123},
    {'limits': {'configured_limit_flags': JOB_FLAGS,
                'configured_job_memory_limit_bytes': JOB_BYTES // 2}},
    {'limits': {'configured_limit_flags': 0x0200,
                'configured_job_memory_limit_bytes': JOB_BYTES}},
    {'limits': {'configured_limit_flags': JOB_FLAGS | 0x0800,
                'configured_job_memory_limit_bytes': JOB_BYTES}},
    {'limits': {'configured_limit_flags': JOB_FLAGS | 0x1000,
                'configured_job_memory_limit_bytes': JOB_BYTES}},
])
def test_child_guard_rejects_malformed_foreign_or_expired_handshake(overrides):
    with pytest.raises(supervisor.GuardRefused):
        guard_run(good_handshake(**overrides))


def test_child_guard_rejects_expired_common_deadline():
    with pytest.raises(supervisor.GuardRefused):
        guard_run(good_handshake(), clock=FakeClock(1180.0))


def test_child_guard_rejects_malformed_handshake_line():
    with pytest.raises(supervisor.GuardRefused):
        guard_run(None, raw_line='not-json\n')


@pytest.mark.parametrize('facts', [
    {'member': False, 'configured_limit_flags': JOB_FLAGS,
     'configured_job_memory_limit_bytes': JOB_BYTES},
    {'member': True, 'configured_limit_flags': JOB_FLAGS,
     'configured_job_memory_limit_bytes': JOB_BYTES // 2},
    {'member': True, 'configured_limit_flags': 0x0200,
     'configured_job_memory_limit_bytes': JOB_BYTES},
    {'member': True, 'configured_limit_flags': JOB_FLAGS | 0x0800,
     'configured_job_memory_limit_bytes': JOB_BYTES},
    {'member': True, 'configured_limit_flags': JOB_FLAGS | 0x1000,
     'configured_job_memory_limit_bytes': JOB_BYTES},
    {'member': True, 'configured_limit_flags': JOB_FLAGS},
])
def test_child_guard_rejects_wrong_membership_or_limit_facts(facts):
    with pytest.raises(supervisor.GuardRefused):
        guard_run(good_handshake(), facts=facts)


def test_child_entry_core_requires_owned_marker_and_nonce(tmp_path, monkeypatch):
    path, manifest = write_manifest(tmp_path)
    monkeypatch.delenv(supervisor.CHILD_MARKER_ENV, raising=False)
    monkeypatch.delenv(supervisor.NONCE_ENV, raising=False)
    with pytest.raises(supervisor.GuardRefused, match='marker'):
        supervisor._child_entry_core(manifest, 'root', manifest_path=path)
    monkeypatch.setenv(supervisor.CHILD_MARKER_ENV, 'root')
    with pytest.raises(supervisor.GuardRefused, match='nonce'):
        supervisor._child_entry_core(manifest, 'root', manifest_path=path)


def test_child_entry_core_verifies_fingerprints_after_guard_before_paths(
        tmp_path, monkeypatch):
    monkeypatch.setenv(supervisor.CHILD_MARKER_ENV, 'root')
    monkeypatch.setenv(supervisor.NONCE_ENV, NONCE)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    path, manifest = write_manifest(tmp_path)
    frozen = supervisor._freeze_manifest_sha256(path)
    handshake = good_handshake()
    handshake['manifest_sha256'] = frozen
    checks = []

    def recording_check(m, **kwargs):
        checks.append((kwargs['expected_manifest_sha256'], list(sys.path)))
        return {'manifest_sha256': kwargs['expected_manifest_sha256'],
                'source_fingerprints_sha256': 'f' * 64,
                'source_fingerprint_count': 3}

    harness = FakeResultHarness()
    record = supervisor._child_entry_core(
        manifest, 'root', manifest_path=path,
        input_stream=io.StringIO(json.dumps(handshake) + '\n'),
        job_query=lambda name: {'member': True,
                                'configured_limit_flags': JOB_FLAGS,
                                'configured_job_memory_limit_bytes': JOB_BYTES},
        clock=FakeClock(), harness_loader=lambda m: harness,
        recorder=lambda rec, result: None,
        fingerprint_check=recording_check)
    assert record['status'] == 'CHILD_CASE_COMPLETED_UNADJUDICATED'
    assert len(checks) == 1
    assert checks[0][0] == frozen
    # Verification ran BEFORE any engineering import path was added.
    assert manifest['import_paths'][0] not in checks[0][1]
    # Dispatch added the explicit manifest paths only after guard+verification.
    assert sys.path[0] == manifest['import_paths'][0]
    assert harness.calls == ['verify', ('compose', 'root')]
    assert record['guard']['source_verification']['source_fingerprint_count'] == 3


def test_child_entry_core_refuses_changed_manifest_between_parent_and_child(
        tmp_path, monkeypatch):
    monkeypatch.setenv(supervisor.CHILD_MARKER_ENV, 'root')
    monkeypatch.setenv(supervisor.NONCE_ENV, NONCE)
    path, manifest = write_manifest(tmp_path)
    loaded = []

    def refusing_check(m, **kwargs):
        raise supervisor.SourceVerificationRefused(
            'manifest bytes changed between the parent freeze and this check')

    with pytest.raises(supervisor.SourceVerificationRefused,
                       match='manifest bytes changed'):
        supervisor._child_entry_core(
            manifest, 'root', manifest_path=path,
            input_stream=io.StringIO(json.dumps(good_handshake()) + '\n'),
            job_query=lambda name: {'member': True,
                                    'configured_limit_flags': JOB_FLAGS,
                                    'configured_job_memory_limit_bytes': JOB_BYTES},
            clock=FakeClock(), harness_loader=lambda m: loaded.append(m),
            recorder=lambda *args: None, fingerprint_check=refusing_check)
    assert loaded == []
    assert manifest['import_paths'][0] not in sys.path  # no engineering paths


# --- Child dispatch: pinned harness, evidence capture, no fabrication ----------


class DisabledHarness:
    EXECUTION_ENABLED = False

    def __init__(self):
        self.calls = []

    def verify_artifacts(self, *args, **kwargs):
        self.calls.append('verify')

    def compose_case(self, case):
        self.calls.append(('compose', case))
        raise RuntimeError(
            'engineering dispatch disabled pending proposal review and '
            'checked supervisor integration')


def test_child_dispatch_refuses_while_compose_case_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    _path, manifest = write_manifest(tmp_path)
    guard = {'guard_armed': True, 'case': 'root',
             'job_membership_verified': True}
    harness = DisabledHarness()
    recorded = []
    with pytest.raises(RuntimeError, match='disabled'):
        supervisor._child_dispatch(manifest, 'root', guard,
                                   harness_loader=lambda m: harness,
                                   recorder=lambda *args: recorded.append(args))
    # The full failure is preserved in the write-once packet.
    assert len(recorded) == 1
    failure = recorded[0][0]
    assert failure['status'] == 'CHILD_CASE_FAILED'
    assert failure['error']['type'] == 'RuntimeError'
    assert 'disabled' in failure['error']['message']
    assert 'evidence' not in failure
    assert harness.calls == ['verify', ('compose', 'root')]
    assert harness.EXECUTION_ENABLED is False  # no permission fabrication
    # Manifest import paths are added explicitly only after the guard.
    assert sys.path[0] == manifest['import_paths'][0]


def test_child_dispatch_records_only_after_compose_case(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    _path, manifest = write_manifest(tmp_path)
    guard = {'guard_armed': True, 'case': 'root',
             'job_membership_verified': True}
    harness = FakeResultHarness()
    recorded = []
    record = supervisor._child_dispatch(manifest, 'root', guard,
                                        harness_loader=lambda m: harness,
                                        recorder=lambda *args: recorded.append(args))
    assert record['status'] == 'CHILD_CASE_COMPLETED_UNADJUDICATED'
    assert len(recorded) == 1
    assert harness.calls == ['verify', ('compose', 'root')]
    assert recorded[0][1] is harness.last_result
    assert record['evidence']['case'] == 'root'


def test_child_dispatch_captures_scalar_evidence_from_compose_case(tmp_path):
    inputs, pressure, artifact, binding = make_compose_result('root')
    evidence = supervisor._extract_case_evidence('root', (inputs, pressure,
                                                          artifact, binding))
    assert evidence['project_document_id'] == 'doc-1'
    assert evidence['geometry_model_id'] == 'model-1'
    assert evidence['geometry_revision'] == 3
    assert evidence['load_case_id'] == 'case-1'
    assert evidence['load_case_name'] == 'default'
    assert evidence['pressure'] == {'id': 'p1', 'ref_kind': 'face',
                                    'ref_id': '2', 'value': 1200}
    assert evidence['element_ids'] == [1, 2]
    assert evidence['association_namespace'] == 'prepared_current'
    assert evidence['association_namespaces'] == ['prepared_current']
    assert evidence['t3_cell_count'] == 26
    assert evidence['field_inventory'] == [['tris', 26], ['nodes', 20]]
    assert evidence['pressure_binding']['key'] == 'load_cases/case-1/pressures/p1'
    assert evidence['pressure_binding']['cell_ids'] == [1, 2]
    assert evidence['reference_ledger'][0]['category'] == 'pressure'
    assert evidence['occurrence_ledger'][0]['source_orientation'] == 1
    assert evidence['occurrence_ledger'][0]['current_orientations'] == [[1, 0], [0, 1]]
    assert evidence['production_record']['status'] == 'ADMITTED'
    assert evidence['production_record']['nodal_normal_count'] == 1
    assert len(evidence['production_record']['checksum_sha256']) == 64
    assert evidence['manifest_checksums']['manifest_checksum'] == 'm' * 64
    assert evidence['manifest_checksums']['project_checksum'] == 'p' * 64
    assert evidence['flags'] == {'preassembly_checked': True,
                                 'solver_executed': False,
                                 'publication_qualified': False,
                                 'source_reference_transfer_qualified': True,
                                 'solver_admitted': False}
    assert evidence['evidence_gaps'] == []
    # Scalar JSON only; no live owner proof object is ever serialized.
    json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False)


def test_extract_case_evidence_refuses_non_scalar_values():
    inputs, pressure, artifact, binding = make_compose_result('root')
    pressure.value = object()  # a live value must not be serialized
    evidence = supervisor._extract_case_evidence(
        'root', (inputs, pressure, artifact, binding))
    assert evidence['pressure']['value'] is None
    assert 'pressure.value' in evidence['evidence_gaps']


def test_extract_case_evidence_rejects_foreign_result_shape():
    with pytest.raises(ValueError, match='tuple'):
        supervisor._extract_case_evidence('root', ('only-one',))
    with pytest.raises(ValueError, match='mapping'):
        supervisor._extract_case_evidence('root', ('inputs', 'pressure',
                                                   'artifact', 'binding'))


def test_child_dispatch_preserves_full_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    _path, manifest = write_manifest(tmp_path)
    guard = {'guard_armed': True, 'case': 'root',
             'job_membership_verified': True}

    class ExplodingHarness(FakeResultHarness):
        def compose_case(self, case):
            self.calls.append(('compose', case))
            raise ValueError('owner refused the composition')

    harness = ExplodingHarness()
    recorded = []
    with pytest.raises(ValueError, match='owner refused'):
        supervisor._child_dispatch(manifest, 'root', guard,
                                   harness_loader=lambda m: harness,
                                   recorder=lambda *args: recorded.append(args))
    assert len(recorded) == 1
    failure, result = recorded[0]
    assert result is None
    assert failure['status'] == 'CHILD_CASE_FAILED'
    assert failure['error'] == {'type': 'ValueError',
                                'message': 'owner refused the composition'}
    assert 'evidence' not in failure


def test_child_dispatch_requires_existing_harness_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    _path, manifest = write_manifest(tmp_path)
    guard = {'guard_armed': True, 'case': 'root',
             'job_membership_verified': True}

    class BareHarness:
        def compose_case(self, case):
            raise AssertionError('compose_case must not run unverified')

    recorded = []
    with pytest.raises(RuntimeError, match='artifact verification'):
        supervisor._child_dispatch(manifest, 'root', guard,
                                  harness_loader=lambda m: BareHarness(),
                                  recorder=lambda rec, result: recorded.append(rec))
    assert recorded[0]['status'] == 'CHILD_CASE_FAILED'
    assert recorded[0]['error']['type'] == 'RuntimeError'


def test_child_dispatch_requires_verified_guard(tmp_path):
    _path, manifest = write_manifest(tmp_path)
    with pytest.raises(supervisor.GuardRefused):
        supervisor._child_dispatch(manifest, 'root', {'guard_armed': False})
