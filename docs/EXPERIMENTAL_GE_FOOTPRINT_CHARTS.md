# Experimental GE footprint chart inputs

`ExperimentalGENodalRigidFootprintPanel` can explicitly select the solver's
`GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2` policy. The default retains the
existing V1 schema, serialized fields and unrebased admission. V2 has its own
`anyfem.experimental-ge-nodal-rigid-footprint-chart-panel-v2` schema and stores
the selected policy. Reopening never silently changes that choice.

```python
from dataclasses import replace
from anysolver import stiffened_shell

charted = replace(existing_footprint_panel,
    footprint_chart_policy=stiffened_shell.GE_NODAL_FOOTPRINT_INCREMENT_ORIGIN_CHART_V2)
```

Save/reopen the input with `save_case`/`load_case` or an explicit mesh-native
`AnalysisDefinition.experimental_point_panel`. The ordinary Project dispatch
creates the public solver owner. Use that owner for physical recovery,
`rebase_rotations(expected_generation=...)`, checkpoint, fresh-owner restore and
continued stepping. Saving an input Project does not save an accepted solver state.

Run the installed public example with an exclusive output directory:

```powershell
python examples/stiffened_shell_footprint_chart_consumer.py C:/Temp/footprint-chart-demo
```

The example writes the input Project, accepted/rebased/continued checkpoints and
a results summary. It checks direct versus restored chart continuation and
selected-group force/moment balance.

This workflow remains `EXPERIMENTAL_NOT_QUALIFIED`. Its scope is unstaged elastic
generalized beams and elastic Q4/mixed/all-S3 selected-node footprints, with each
shell rotation triple fully fixed or fully free. Selected nodes follow one beam
pose. This model does not represent a distributing footprint or a continuously
welded area. Plastic/fibre histories, weak lines, partial shell rotation supports
and incompatible policies fail explicitly. Input parsing performs structural
admission; the public solver owns all physical validation and mechanics.
