"""Apply the corrected axial assertion to immutable V1 workflow evidence only."""
from pathlib import Path
import hashlib, json
ROOT = Path(__file__).resolve().parent
path = ROOT/'source-temp-v1/test_project_fixed_translation0/workflow.json'
result = json.loads(path.read_text())
assert result['status'] == 'completed' and not result['production_qualified']
assert result['shell'] == 's3' and result['schema'] == 'anyfem.experimental-ge-bl5-panel-native-mixed-j2-v6'
assert result['policy'] == 'BL5_NATIVE_FIBRE_MIXED_SHELL_MATERIAL_V1_DEV'
assert result['target'] == 5e-6
assert result['shell_s3_material'] and result['beam_fibre_material'] and not result['beam_generalized_material']
assert max(value for item in result['shell_s3_material'] for value in item['alpha']) == 0.
assert result['physical_fibre_alpha_max'] > 0.
assert {item['element_id'] for item in result['beam_fibre_material']} == {item['element_id'] for item in result['physical_fibre_members']}
assert result['input_bytes_equal'] and result['force_seed_restore_bytes_equal']
assert result['fresh_restore_bytes_equal'] and result['continued_bytes_equal']
assert result['recovered_generation'] == result['generation']
assert result['free_equilibrium_ratio'] <= 1e-8 and result['constraint_max'] <= 1e-9
record = dict(status='completed', scope='metadata replay of immutable complete source V1 axial workflow; not a fresh pytest/numerical run',
    path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    replay_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(ROOT/'source-v1-axial-metadata-replay.json').write_text(json.dumps(record, indent=2)+'\n')
print(json.dumps(record))
