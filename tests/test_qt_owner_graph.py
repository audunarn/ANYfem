"""Qt checks use the approved owner graph without moving physical solver laws.

Since 9f8254f the Qt release smoke (``qt-tests.yml``) and the headless matrix
(``tests.yml``) carry no checkouts of their own: every job calls the bounded
application lane, which owns the single pinned owner graph. These checks keep
the two routes on that one graph and keep its pins and install rules exact.
"""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'
QT = WORKFLOWS / 'qt-tests.yml'
HEADLESS = WORKFLOWS / 'tests.yml'
LANE = WORKFLOWS / 'application-lane.yml'
LANE_USE = './.github/workflows/application-lane.yml'
PHYSICAL_SOLVER = 'f54e3cde5abf70cb8c989eb106119eedd3e33025'
RESULTS_SOLVER = 'f54e3cde5abf70cb8c989eb106119eedd3e33025'
POST = '9eb9713b92c1b3a476301150b5e89e350d515844'
VIEWER = '6fa628bc3a9e1c74548901678ff2ee8033e6c5dc'
GEOMETRY = '5f68177e42fe0a53341dceb80cc8e4fb73482745'
MESHER = 'b16ac98d20973e603a0f61696e3d346a80116480'


def _checkouts(text):
    result = {}
    for step in text.split('      - '):
        fields = {name: re.search(rf'^          {name}: (.+)$', step, re.M)
                  for name in ('repository', 'ref', 'path')}
        if all(fields.values()):
            assert fields['path'][1] not in result, f"duplicate checkout {fields['path'][1]}"
            result[fields['path'][1]] = (fields['repository'][1], fields['ref'][1])
    return result


def _jobs(text):
    body = text.split('\njobs:\n', 1)[1]
    return dict(re.findall(r'^  (\w+):\n(.*?)(?=^  \w+:\n|\Z)', body, re.M | re.S))


def _input_default(text, name):
    match = re.search(rf"^      {name}:\n(?:^        .*\n)*?^        default: '?([^'\n]+)'?$", text, re.M)
    assert match is not None, name
    return match[1]


@pytest.mark.parametrize('workflow', (QT, HEADLESS), ids=('qt', 'headless'))
def test_every_job_runs_the_single_application_lane_owner_graph(workflow):
    text = workflow.read_text()
    jobs = _jobs(text)
    assert jobs
    for name, job in jobs.items():
        assert re.search(rf'^    uses: {re.escape(LANE_USE)}$', job, re.M), name
    # No route may pin owners of its own; the lane is the only owner graph.
    assert _checkouts(text) == {}
    assert 'actions/checkout' not in text


def test_qt_default_and_automatic_owner_refs_match_headless():
    lane, headless = LANE.read_text(), HEADLESS.read_text()
    owners = _checkouts(lane)
    assert owners['.ecosystem/ANYgeometry'] == ('audunarn/ANYgeometry', GEOMETRY)
    assert owners['.ecosystem/ANYmesh'] == ('audunarn/ANYmesh', '${{ inputs.mesher_ref }}')
    assert _input_default(lane, 'mesher_ref') == MESHER
    # A manual development dispatch may override the mesher, but its default
    # is the same pin, and it is forwarded rather than dropped.
    assert _input_default(headless, 'mesher_ref') == MESHER
    jobs = _jobs(headless)
    assert re.search(r'^      mesher_ref: \$\{\{ inputs\.mesher_ref \}\}$', jobs['development'], re.M)
    for name, job in {**jobs, **_jobs(QT.read_text())}.items():
        if name != 'development':
            assert 'mesher_ref' not in job, name


def test_qt_preserves_physical_solver_and_separates_results_owner():
    lane = LANE.read_text()
    owners = _checkouts(lane)
    assert owners['.ecosystem/ANYfeSolver'] == ('audunarn/ANYfeSolver', PHYSICAL_SOLVER)
    assert owners['.ecosystem/ANYfeSolver-results'] == ('audunarn/ANYfeSolver', RESULTS_SOLVER)
    assert '-e .ecosystem/ANYfeSolver -e .ecosystem/ANYpost' in lane
    assert 'python -m pip install --no-deps .ecosystem/ANYfeSolver-results/packages/results' in lane
    assert '-e .ecosystem/ANYfeSolver-results/packages/results' not in lane
    assert '.ecosystem/ANYfeSolver/packages/results' not in lane


def test_qt_post_owner_matches_headless():
    assert _checkouts(LANE.read_text())['.ecosystem/ANYpost'] == ('audunarn/ANYpost', POST)


def test_qt_keeps_viewer_runtime_matrix_and_native_mesher_gates():
    qt, lane = QT.read_text(), LANE.read_text()
    assert _checkouts(lane)['.ecosystem/ANY3dView'] == ('audunarn/ANY3dView', VIEWER)
    jobs = _jobs(qt)
    assert set(jobs) == {'qt', 'gpu'}
    assert 'os: [windows-latest, ubuntu-latest]' in jobs['qt']
    assert "python: ['3.11', '3.14']" in jobs['qt']
    for job in jobs.values():
        assert re.search(r'^      profile: qt$', job, re.M)
    assert re.search(r'^      backend: gpu$', jobs['gpu'], re.M)
    assert 'backend' not in jobs['qt']
    assert '    timeout-minutes: 45' in lane.split('  application:\n', 1)[1].split('    steps:', 1)[0]
    assert "ANYMESHER_REQUIRE_NATIVE: '1'" in lane
    assert "ANYMESHER_DISABLE_NATIVE: '0'" in lane
    assert 'python tools/check_licenses.py --check-installed' in lane
    assert "if: inputs.backend != 'gpu'" in lane
    assert '--profile ${{ inputs.profile }} --backend software' in lane
    assert "if: inputs.backend == 'gpu'" in lane
    assert '--backend gpu' in lane
