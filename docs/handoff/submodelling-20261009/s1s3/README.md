# S1 + S3a — transfer mechanics (ANYsolver) and planar correspondence (ANYmesher)

Status 2026-10-10: implemented on branches, **approved** by independent review (GPT-6-Astra via
Codex CLI, high effort, six rounds, session `01a122a0-e317-7e83-8a14-7ab28b9a9dd0`; rounds 2-6 were
static reviews of pasted files because Codex's Windows sandbox could not start a shell). Not
committed; awaiting the owner's decision.

| Slice | Worktree / branch | Files | Tests |
|---|---|---|---|
| S1 | `C:/ANYworkspaceWork/sm1`, `claude/submodel-s1` from ANYsolver `da1ba2e9` | `src/anysolver/submodel_transfer.py`, `tests/test_submodel_transfer.py`, `docs/SUBMODEL_TRANSFER_CONTRACT.md` | 49 passed |
| S3a | `C:/ANYworkspaceWork/sm3`, `claude/submodel-s3a` from ANYmesh `d7e7414` | `src/anymesher/submodel_correspondence.py`, `tests/test_submodel_correspondence.py`, `docs/SUBMODEL_CORRESPONDENCE_CONTRACT.md` | 14 passed |

Commands:

```text
cd C:/ANYworkspaceWork/sm1 && PYTHONPATH=C:\ANYworkspaceWork\sm1\src C:/Github/ANYsolver/.venv/Scripts/python.exe -m pytest tests/test_submodel_transfer.py -q
cd C:/ANYworkspaceWork/sm3 && PYTHONPATH=C:\ANYworkspaceWork\sm3\src;C:\Github\ANYgeometry\src C:/Python/Python314/python.exe -m pytest tests/test_submodel_correspondence.py -q
C:/Python/Python314/python.exe integration_a1.py   (this folder; S0 snapshot environment + both worktrees)
```

Integration (`integration_a1.py`, S0 element-aligned A1 case through ANYfem model, ANYmesher
locator, ANYsolver trace, solve and interface forces): interior error 1.6e-3 of the allowance,
Newton across the cut 1.8e-3 of the force allowance, free body 1e-9 N on 1600 N. Releasing the
drilling rotation (P1) changes the coupled field by 2.5e-4 relative, as in S0.

Mutation checks: transport rule, inconsistency refusal, conformity refusal, inside test and the
alternative residual each fail their tests when disabled.

Not done: physical/numerical force partition; D3 drilling decision experiment; curved faces (S3b);
the ANYfem workflow (S4a, which needs the volume cut S2a).
