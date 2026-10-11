# A5 stiffened panel: error budget (S4b, comparative evidence, Level A)

Script: `test_a5_budget.py` (data in `a5_budget.json`). Run from the worktree root:

```
python docs/handoff/submodelling-20261009/s4a/run_tests.py docs/handoff/submodelling-20261009/s4b/test_a5_budget.py -s --basetemp=<writable dir>
```

It takes about 3 minutes and uses ANYgeometry at S2b (`C:/ANYworkspaceWork/sm5`).

## Case

- **Panel:** 1 x 1 plate in a 3 x 3 grid of faces (lines at 0, 0.3, 0.7 and 1), t = 0.01, S355.
- **Load:** pressure 10 kPa, with the in-plane edge loads and supports of the S4a end-to-end
  case.
- **Stiffeners:** flat bars 80 x 10 on y = 0.3 and x = 0.7, with an automatic neutral-axis offset.
- **Box:** (0.2, 0.2)-(0.8, 0.8). It cuts both stiffeners.
- **Quantities:**
  - plate deflection w at (0.5, 0.5);
  - stiffener deflection w at (0.5, 0.3);
  - section rotation rx at (0.5, 0.3).
- **Oracle:** none analytical for this panel. The reference is the full stiffened model at
  h = 0.1, 0.05, 0.025 and 0.0125, with the observed order and a Richardson estimate taken from
  the three finest levels. The full model shares ANYsolver's element mathematics, so it is
  comparative evidence only (PLAN.md section 7).

All errors below are relative to the Richardson reference.

## Reference

| Quantity | Observed order | Reference | Finest full h = 0.0125 | h = 0.1 parent |
|---|---|---|---|---|
| w centre | 0.50 | 1.7331e-3 | -0.83 % | -2.46 % |
| w stiffener | 0.50 | 1.3975e-3 | -0.82 % | -2.43 % |
| rx stiffener | 0.19 | 3.0143e-3 | -2.73 % | -3.73 % |

The full model is **not** in its asymptotic range:
- The observed order is about 0.5 for the deflections and 0.2 for the rotation. Q4 would give 2
  for a smooth solution.
- So the Richardson reference carries an uncertainty of the order of the last correction (0.8 %,
  and 2.7 % for rx). It is not a converged value.
- Likely causes are not investigated here: the pinned-to-roller support corners, and stiffener
  ends at supported edges.

## Submodels from the h = 0.1 parent

| Quantity | hc | Transfer (child − full at hc) | Submodel discretisation (full at hc − ref) | Total (child − ref) |
|---|---|---|---|---|
| w centre | 0.05 | -1.13 % | -1.66 % | -2.79 % |
| | 0.025 | -1.65 % | -1.17 % | -2.82 % |
| | 0.0125 | -2.00 % | -0.83 % | -2.82 % |
| w stiffener | 0.05 | -1.27 % | -1.62 % | -2.89 % |
| | 0.025 | -1.80 % | -1.15 % | -2.95 % |
| | 0.0125 | -2.15 % | -0.82 % | -2.97 % |
| rx stiffener | 0.05 | +1.58 % | -3.57 % | -1.98 % |
| | 0.025 | -0.63 % | -3.12 % | -3.76 % |
| | 0.0125 | -0.97 % | -2.74 % | -3.71 % |

Refining the child alone does not move the total. The child converges to the field its coarse
driver imposes, and the total stays at about the coarse parent's own error.

## Child hc = 0.0125 against parent refinement

| Quantity | Parent h | Parent error | Transfer | Total |
|---|---|---|---|---|
| w centre | 0.1 | -2.46 % | -2.00 % | -2.82 % |
| | 0.05 | -1.66 % | -0.90 % | -1.73 % |
| | 0.025 | -1.17 % | -0.35 % | -1.18 % |
| w stiffener | 0.1 | -2.43 % | -2.15 % | -2.97 % |
| | 0.05 | -1.62 % | -0.93 % | -1.74 % |
| | 0.025 | -1.15 % | -0.36 % | -1.17 % |
| rx stiffener | 0.1 | -3.73 % | -0.97 % | -3.71 % |
| | 0.05 | -3.57 % | -0.69 % | -3.42 % |
| | 0.025 | -3.12 % | -0.31 % | -3.05 % |

The transfer error falls by a factor of about 2.2–2.6 per parent halving for the deflections,
and 1.4–2.2 for rx. The child total
follows the parent's error at the cut.

## Reading

- **Every child run is in equilibrium:** free-body ratios are below 1e-10. The same-mesh A5
  regression in `tests/test_submodel_cut.py` reproduces the parent exactly (1e-9) for vector
  and scalar offsets.
- **The submodel is no better than its driver.** With an h = 0.1 parent:
  - the deflections are within 3 % of the reference (2.8 % and 3.0 %);
  - the rotation is not (3.7 %).

  Against the proposed 3 % limit, rx needs an h ≤ 0.025 parent, where it reaches 3.05 %, still
  borderline. The reference itself is uncertain by about 2.7 % for rx.
- **What is missing for acceptance:**
  - This is a Level A evidence record, not acceptance A5. The budget's dominant term is a
    reference that is not in its asymptotic range.
  - A defensible acceptance needs either a case with a smooth reference (supports and stiffener
    ends away from singular corners) or an independent oracle.
  - The proposed 3 % limit still has to be justified in writing before the run.

  These belong to S6, Level B with a named assessor.
