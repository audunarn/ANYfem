# Experimental stiffened-shell arc workflow

The existing experimental BL5 Project record returns the ANYsolver owner through
`record.create_bl5_panel_analysis()`. That owner now admits local physical arc
continuation for accepted coupled Q4-J2/generalized BL5 states with affine nodal
dead forces. ANYfem does not implement another continuation algorithm.

After constructing an admitted panel, saving/reopening the Project and obtaining
an accepted force endpoint on its owner:

```python
from anysolver import stiffened_shell as ss

preflight = owner.preflight()
node_order = preflight["arc_metric_node_order"]
metric = ss.PhysicalChordMetric(reference_weights, characteristic_length_m)
control = ss.ArcLengthControl(
    "beam", tip_node, 2, metric, radius=radius, orientation=-1,
)
result = owner.continue_arc_length(load_direction, control, steps=2)
checkpoint_path.write_bytes(owner.checkpoint())
```

Weights follow `node_order` and require an explicit physical interpretation.
Equal nodal weights are only a fixed-mesh development choice, not a validated
mesh-independent metric. A callback can cancel between accepted steps; the owner
then retains the complete accepted prefix. Save that checkpoint separately from
the native Project input. Reopen the Project, create a fresh owner and call
`restore(checkpoint_path.read_bytes())`. Continue using the same load direction,
metric, orientation, radius and iteration/cutback controls. Their bindings are
validated by the solver checkpoint; the requested number of additional steps
may change. The Project definition does not store an active run's controller.

`steps` counts accepted increments; cutbacks can reduce actual distance.
`result.history` reports actual radii and fixed-seed load amplitudes. Attempt
history includes rejected work. Selected physical/material recovery uses the
same public `GERecoverySelection` as other accepted finite results.

This route is experimental and not capacity-qualified. It excludes external
load contexts, nodal moments and chart rebasing during arc history. Completion
of a call does not establish a first limit point, branch stability or a safe
capacity prediction. Native input persistence and the solver's accepted-state
checkpoint have separate identities and purposes.

The installed regression `tests/test_bl5_arc_consumer.py` covers standalone
input and native Project round trips, accepted-prefix cancellation, restored
continuation and selected material recovery. Its small elastic-prefix case
checks consumer integration, not yielding or physical capacity acceptance.
