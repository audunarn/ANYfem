from pathlib import Path
import hashlib,json,subprocess
ROOT=Path(__file__).resolve().parent;REPO=ROOT.parents[2]
SOLVER=Path('C:/Github/ANYsolver/.perf2-worktrees/stiffened-main-reconcile-20261001')
DOC=SOLVER/'docs/agent_plans';GROUP=DOC/'development/r8-distributing-reference-linear-20261004'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
record=json.loads((ROOT/'installed-runtime-v1.json').read_text());example=json.loads((ROOT/'example-runtime-v1.json').read_text())
assert record['exit_code']==example['exit_code']==0
FW=record['consumer_wheel_sha256'];SW=record['solver_wheel_sha256']
def write(path,text):path.write_bytes(text.replace('\r\n','\n').replace('\n','\r\n').encode())
report=f'''# Finite distributing consumer — installed development slice, 4 October 2026

IMPLEMENTED_NOT_QUALIFIED. `ExperimentalGEFiniteDistributingPanel` carries the
explicit experimental finite law, separate `anyfem.experimental-ge-finite-distributing-panel-v1`
schema and monotone translation-force admission. Old reference records are never
upgraded; both routes reject mismatched law/schema. Input ownership and strict
framing reuse existing consumer helpers; no mechanics or solver state is copied.
Native Project dispatch reuses its explicit GE selector with strict inner schema.

Standalone and Project input save/reopen, public construction/preflight/force
solve, selected current physical wrenches, accepted-prefix cancellation, binary
checkpoint/restore and continued stepping work through the installed public API.
Project finite solve requires explicit targets and rejects adaptive/batch controls
before assembly. Saving input alone never implies solved/qualified state.
No material, chart, spectrum, capacity, topology or external claim is added.

Source-v1:56PASS12.87s /15.5023s whole /sample187650048B. This precedes the final
malformed-unused-field/capability guards; exact source snapshots retained.
Final installed:59PASS13.52s /15.4795s whole /sample187691008B. Ten imported
solver/consumer files match actual final source bytes. Example:PASS4.3557s whole,
sample175673344B, factors.25/.5/1,2355byte checkpoint, equilibrium8.943e-10,
gap1.206e-17, exact resumed displacement parity. Empty stderr, pools1/JIToff,
600s/2GiB supervised; measured sample memory is not true guaranteed peak memory.
No optimization/platform/physical acceptance follows from these timings.

Solverwheel `{SW}` unchanged; consumerwheel `{FW}`. Raw
`.qualification/stiffened-shell/r8-ge-finite-distributing-consumer-dev-20261004-v1`
contains commands, resource outcomes, source/import/test/wheel hashes, original
source bytes, installed example inputs/checkpoint/observations and supporting
Read review. Wheel/site files remain disposable; exact artifact identities stay.
Reviewer found malformed empty reference fields were being discarded; fixed with
strict admission and direct negatives. No reviewed development blocker remains.
OpenAI supporting review is not different-family scientific acceptance.

Next use the completed solver/consumer slice while addressing physical connection
acceptance from the saved torque/shear evidence. Do not add unrelated profile
combinations or silently relax convergence/authority/guards. Aggregate programme
requirements and full scientific/platform/release acceptance remain open.
'''
write(REPO/'docs/development/r8-ge-finite-distributing-consumer-20261004.md',report)
active=DOC/'B3_GE_E4_PL_ACTIVE_STATE.md'
s=active.read_text();old='''Next implementation: one finite ANYfem construction/persistence/solve/recovery/
checkpoint-resume slice, using this explicit new record and existing consumer
services. Preserve historical reference-linear project format; no silent upgrade.
Current ANYfem change only fixes empty-field compatibility and rejects finite
records from unsupported reference/modal import routes; no finite consumer yet.
Do not broaden controllers/materials or add a framework while finishing this exit.'''
new='''Finite ANYfem exit is also complete: explicit new input schema, standalone and
native Project save/reopen, solve/selected recovery/cancel/accepted-prefix binary
resume.56source before final strict guards;59final installed checks and installed
consumer example PASS with exact resumed displacement parity. Old reference
formats retained; unsupported finite/modal imports and adaptive/batch controls
fail closed. Consumerwheel{FW} binds ten actual solver/consumer imports.
Next priority is physical connection acceptance: interpret saved native/reference
torque convergence and model assumptions in one bounded mechanics review, identify
the missing criterion/equation/observable, and choose the discriminating check.
No automatic refinement or optional feature stream follows this delivery.'''.replace('{FW}',FW)
assert old in s;s=s.replace(old,new)
s=s.replace('Preceding HEAD9269719e; current','HEAD8d03f522; current')
s=s.replace('preceding HEAD6013db0. Three narrow\ncompatibility source/test changes are root-owned.','preceding HEADfef92e2. Finite consumer\nsource/example/tests/evidence are root-owned.')
s=s.replace('complete\nGE/B2 scale/platform/consumer/external/different-family/release remain open.','complete\nGE/B2 scale/platform/full-consumer/external/different-family/release remain open.')
write(active,s)
for name in ('B3_GE_E4_PL_REVISION_8_PLAN.md','B3_GE_E4_PL_REVISION_8_GOAL.md'):
 p=DOC/name;s=p.read_text()
 if name.endswith('PLAN.md'):
  old='''  aggregate qualification follows. Next finish the distinct finite ANYfem
  construction/persistence/recovery/checkpoint-resume exit in existing services;
  current consumer changes only preserve empty-field compatibility and reject
  unsupported finite import. Keep later material/controller/spectral obligations
  visible and avoid optional feature breadth until that exit works.'''
  new='''  aggregate qualification follows. The distinct finite ANYfem consumer exit
  is now implemented too:59final installed checks and public Project/input/
  checkpoint-resume example pass; old reference formats remain unchanged.
  Next resolve the physical torque convergence/model-assumption question from
  the saved evidence before selecting a new discriminating run. Keep later
  material/controller/spectral obligations visible; no automatic refinement
  or optional breadth follows this completed implementation slice.'''
  s=s.replace('finite consumer, fully converged physical fidelity and full F04 remain open.','finite consumer now has installed development evidence; fully converged physical fidelity and full F04 remain open.')
 else:
  old='''installed solve/recovery/restart evidence; next finish its distinct ANYfem
consumer/persistence route without changing the old reference format. Physical
torque convergence and aggregate acceptance remain open.'''
  new='''installed solve/recovery/restart evidence; its distinct ANYfem consumer now
also completes installed input/Project save/reopen and checkpoint resume while
preserving the old reference format. Physical torque convergence and aggregate
acceptance remain open; use saved evidence for the next bounded decision.'''
 assert old in s,name;s=s.replace(old,new);write(p,s)
p=DOC/'B3_GE_E4_PL_REVISION_8_ACCEPTANCE_MATRIX.md'
write(p,p.read_text().rstrip()+'''

F04 finite distributing ANYfem consumer, 4 October — IMPLEMENTED_NOT_QUALIFIED.
Distinct finite input/schema/law, public owner, standalone/native Project input
save/reopen, explicit monotone force targets, selected physical recovery,
cancellation/accepted prefix and binary restore/continue now operate on installed
artifacts.56source precedes final guard tightening;59final installed and public
example PASS. Old reference formats unchanged; malformed/unsupported requests
fail closed. Different-family/physical/full-material/full-topology/platform/
scale/release and aggregateF04/G0/G2/G6 remain OPEN. FormalF03 UNCONSUMED.
[Scope and exact artifact evidence](development/r8-distributing-reference-linear-20261004/results.md).
''')
p=GROUP/'results.md'
write(p,p.read_text().rstrip()+f'''

### Finite ANYfem consumer exit — completed after the owner slice

ANYfem explicit finite input/persistence route now reuses the public finite owner:
standalone/native Project save/reopen, preflight, explicit monotone force targets,
current selected physical recovery, cancel/accepted prefix and binary resume.
Distinct schema/law prevents old reference upgrade. No copied mechanics/state.
Source56 precedes final malformed-unused-field/capability guard tightening;
exact bytes preserved. Final installed59PASS13.52s/15.4795s whole, sample187691008B;
examplePASS4.3557s/sample175673344B, exact resumed displacement parity.
Effective pools1/JIToff,600s/2GiB, empty stderr; no speed or true peak claim.
Solverwheel{SW}; finalconsumerwheel{FW}; ten actual imported files match source.
ANYfem raw .qualification/stiffened-shell/r8-ge-finite-distributing-consumer-dev-20261004-v1
retains exact commands/source/test/import/runtime identities and example outputs.
Its docs/development/r8-ge-finite-distributing-consumer-20261004.md describes scope.
Supporting review closed malformed-field discard; not different-family acceptance.
Complete F04/G0/G2, physical convergence, platform/full-consumer/release remain open.
Next bounded decision interprets saved physical torque convergence and idealization
limits; no automatic numerical refinement or unrelated feature stream.
''')
attrs=REPO/'.gitattributes'
write(attrs,attrs.read_text().rstrip()+'''

tests/test_stiffened_shell_ge_finite_distributing_consumer.py -text
examples/stiffened_shell_ge_finite_distributing_consumer.py -text
docs/development/r8-ge-finite-distributing-consumer-20261004.md -text
.qualification/stiffened-shell/r8-ge-finite-distributing-consumer-dev-20261004-v1/** -text -whitespace
''')
print('Consumer delivery and canonical current state updated; prior evidence retained.')
