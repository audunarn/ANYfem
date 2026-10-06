# Existing setup/AST child accounting evidence

No child was rerun to reconstruct timing. All precise child durations below refer
to the recorded supervised run only. The setup/AST children's individual precise
durations are UNKNOWN. Containing shell/tool bounds must not be relabelled as
precise Python-child timings. Ledger ownership now belongs exclusively to root;
this report makes no ledger adjustment and grants no further execution authority.

Known combined exec_command records (same containing shell invokes setup first,
then the exactly supervised job). All paths below are relative to the isolated
ANYfem checkout under
.qualification/r8-b2-physical-footprint-consumer-20261006-v1/:

1. Source V1 containing wall_time_seconds 3.6061053:
   `& 'C:/Python/Python314/python.exe' <raw>/prepare-check.py; & 'C:/Python/Python314/python.exe' -c "import ast,pathlib; p=pathlib.Path('<raw>'); [ast.parse((p/f).read_text()) for f in ('runner.py','entry.py')]; print('Check harness AST passed')"; & 'C:/Python/Python314/python.exe' <raw>/runner.py source v1`
   Supervised source child 3.2973269999492913. Its already-accounted interval is
   contained in that shell; setup + harness AST + all remaining shell/supervisor
   overhead fit within the containing interval outside that accounted child.

2. Source V2 containing wall_time_seconds 3.54795:
   `& 'C:/Python/Python314/python.exe' <raw>/prepare-v2.py; & 'C:/Python/Python314/python.exe' <raw>/runner-v2.py source v2`
   Supervised source child 3.268318900023587.

3. Source V3 containing wall_time_seconds 7.759364:
   `& 'C:/Python/Python314/python.exe' <raw>/prepare-v3.py; & 'C:/Python/Python314/python.exe' <raw>/runner-v3.py source v3`
   Supervised source child 7.434684699983336.

4. Build V1 containing wall_time_seconds 4.0357184:
   `& 'C:/Python/Python314/python.exe' <raw>/prepare-v4.py; & 'C:/Python/Python314/python.exe' <raw>/runner-v4.py build v1`
   Supervised build child 3.6710703999269754.

5. Bookkeeping V1 containing wall_time_seconds 0.4651658:
   `& 'C:/Python/Python314/python.exe' <raw>/prepare-bookkeeping.py; & 'C:/Python/Python314/python.exe' <raw>/runner-bookkeeping.py bookkeeping v1`
   Supervised bookkeeping child 0.20541319996118546. It failed only on the
   workflow's truncated pytest directory name, after artifact provenance creation.

Separate initial owned-file AST check:

`& 'C:/Python/Python314/python.exe' -c "import ast,pathlib; fs=['src/anyfem/solve/stiffened_shell_b2.py','src/anyfem/solve/stiffened_shell_project.py','tests/test_stiffened_shell_b2_physical_footprint_consumer.py','examples/stiffened_shell_b2_physical_footprint_consumer.py']; [ast.parse(pathlib.Path(f).read_text()) for f in fs]; print('AST parsed',len(fs),'owned files')"; git diff --stat`

The exec_command return was forwarded as `.output` only; its individual
wall_time_seconds was not printed or saved. The existing outer functions.exec
result reports 10.5 seconds, containing both the preceding apply_patch creation
of the new test file and this AST/Git command. That is only a coarse upper bound
including a separate editing operation, not a precise AST-child duration or a
tight process-only envelope. AST output confirmed four files parsed. No tighter
existing duration is available in the worker context; root may recover a nested
tool record if present, but this worker did not rerun or infer a duration.

Install V1's containing shell 2.4053183000000002 seconds versus its supervised
child 2.1488742999499664 had no additional Python setup command. Installed V1's
containing shell 6.4440251 seconds versus its supervised child 6.201009300071746
also had no additional Python setup command. These are ordinary shell/supervisor
envelopes, retained here for completeness, not extra numerical cases.
