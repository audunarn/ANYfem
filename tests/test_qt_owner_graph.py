"""Qt checks use the approved owner graph without moving physical solver laws."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
QT = ROOT / '.github/workflows/qt-tests.yml'
HEADLESS = ROOT / '.github/workflows/tests.yml'
PHYSICAL_SOLVER = 'adb0d6ac775d5c266a083e4ffa1bd86b6e7b6ea2'
RESULTS_SOLVER = 'e78cccd3a76feb1793a38568f59d6d68f8224cb5'
POST = 'fb06cacd6b98964a6748ea1c222a4bc974c00fcd'


def _checkouts(text):
    result = {}
    for step in text.split('      - '):
        fields = {name: re.search(rf'^          {name}: (.+)$', step, re.M)
                  for name in ('repository', 'ref', 'path')}
        if all(fields.values()):
            result[fields['path'][1]] = (fields['repository'][1], fields['ref'][1])
    return result


@pytest.mark.parametrize(('input_name', 'path', 'expected'), (
    ('geometry_ref', '.ecosystem/ANYgeometry', '33dfbaad89a76dd9e925fd6c628602213ce8c2b8'),
    ('mesher_ref', '.ecosystem/ANYmesh', '3823b6912cd54f1f6a913a661a9f6739b3f55f2d'),
))
def test_qt_default_and_automatic_owner_refs_match_headless(input_name, path, expected):
    qt, headless = QT.read_text(), HEADLESS.read_text()
    default_ref = "${{ inputs." + input_name + " || '" + expected + "' }}"
    assert _checkouts(headless)[path][1] == (default_ref if input_name == 'mesher_ref' else expected)
    if input_name == 'mesher_ref':
        assert re.search(rf"^      mesher_ref:\n.*?^        default: '{expected}'$", headless, re.M | re.S)
    block = re.search(rf'^      {input_name}:\n(.*?)(?=^      \w+:|^permissions:)',
                      qt, re.M | re.S)
    assert block is not None
    assert re.search(rf'^        default: {expected}$', block[1], re.M)
    assert _checkouts(qt)[path][1] == default_ref


def test_qt_preserves_physical_solver_and_separates_results_owner():
    qt, headless = QT.read_text(), HEADLESS.read_text()
    for text in (qt, headless):
        owners = _checkouts(text)
        assert owners['.ecosystem/ANYsolver'] == ('audunarn/ANYsolver', PHYSICAL_SOLVER)
        assert owners['.ecosystem/ANYsolver-results'] == ('audunarn/ANYsolver', RESULTS_SOLVER)
    assert '-e .ecosystem/ANYsolver -e .ecosystem/ANYpost' in qt
    assert 'python -m pip install --no-deps .ecosystem/ANYsolver-results/packages/results' in qt
    assert '-e .ecosystem/ANYsolver-results/packages/results' not in qt
    assert '.ecosystem/ANYsolver/packages/results' not in qt


def test_qt_post_owner_matches_headless():
    for text in (QT.read_text(), HEADLESS.read_text()):
        assert _checkouts(text)['.ecosystem/ANYpost'] == ('audunarn/ANYpost', POST)


def test_qt_keeps_viewer_runtime_matrix_and_opt_in_schema6_gates():
    qt = QT.read_text()
    viewer = '6fa628bc3a9e1c74548901678ff2ee8033e6c5dc'
    assert f'default: {viewer}' in qt
    assert _checkouts(qt)['.ecosystem/ANY3dView'][1] == "${{ inputs.viewer_ref || '" + viewer + "' }}"
    assert '    timeout-minutes: 40' in qt.split('  qt:\n', 1)[1].split('    strategy:', 1)[0]
    assert 'name: Shell and solid submodel jobs and Qt results\n        timeout-minutes: 10' in qt
    assert 'os: [windows-latest, ubuntu-latest]' in qt
    assert "python: ['3.11', '3.14']" in qt
    assert re.search(r'^      schema6_fixtures:\n.*?^        default: false$', qt, re.M | re.S)
    assert "ANYMESHER_REQUIRE_NATIVE: '1'" in qt
    assert "ANYMESHER_DISABLE_NATIVE: '0'" in qt
    assert 'python tools/check_licenses.py --check-installed' in qt
    for backend in ('software', 'gpu'):
        assert f'--profile qt --backend {backend}' in qt
    assert "if: inputs.schema6_fixtures && runner.os == 'Windows'" in qt
    assert "if: inputs.schema6_fixtures && runner.os == 'Linux'" in qt
