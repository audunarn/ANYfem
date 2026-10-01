# Experimental mixed Q4/S3 B2 consumer — 2 October 2026

The existing native Project bridge now saves and reopens explicit static
`B2MixedPanelDefinition` inputs through `ExperimentalB2MixedPointPanel` and
`anyfem.experimental-b2-mixed-point-panel-v1`. Reopen reconstructs detached
inputs without assembling or solving. Q4 and native S3-V2D are identified per
shell, including authoritative normals. Historical Q4 schemas, selectors,
defaults and package version remain unchanged.

The public example `examples/stiffened_shell_b2_mixed_panel_consumer.py` accepts
an output `.anyfem` path and `--cases 1|10|100`. Each installed example completes
Project construction, save/reopen, selected displacement/point/member recovery
and the requested batch with one eligible factorization. This is input
persistence, not an accepted-state checkpoint or a qualified engineering model.

## Verification and identity

- Initial source campaign: 166 passed, one failed negative fixture. The malformed
  `geometric={}` was rejected by the solver before reaching consumer admission.
  A first repair referenced a nonexistent facade export and failed that one
  fixture. The final fixture constructs typed private metadata solely to test
  explicit unsupported-metadata rejection; its source check passed 1/1.
- Final fresh installed campaign: 167/167 across the changed mixed consumer,
  historical B2 static, footprint, reference-modal and Project modules.
- Installed Project examples: 1/10/100 cases completed with factorization counts
  1/1/1 and solve counts 1/10/100. Checked mutation isolation, detached round-trip,
  strict schema/analysis admission, complete-prefix cancellation and failed-status
  forwarding; owners close on success and exceptions.
- Python3.14.2, numpy2.4.6, scipy1.17.1, numba0.65.1, pytest9.0.3. Actual BLAS,
  OpenMP and Numba numerical settings are one thread, JIT disabled. Portable
  reused supervision enforces600s/1GiB; installed checks took9.758s including
  startup and sampled183,828,480bytes process-tree RSS. These are observations,
  not benchmark or development-speed claims. Seven process threads include
  nonnumerical infrastructure and do not mean seven BLAS threads.

Consumer parent350c98016b35e861bfb0a43e2c24fe1c0ee48039; product Python source,
wheel and installed bytes match all98 consumer modules. Consumer wheel SHA256:
`15eb01d0ac2a0ae1e378a676cdf26bd27ea2bd5f0696ff66a374c0dab79b76f6`.
Unchanged solver product46026dca55eb939bb7f1051542239a693e5890aa; all391 solver
modules match source/wheel/site, wheel SHA256:
`fb12ac6637098f32477616a402ed3cae79ae28c49edb81ff30d35d57087d75fd`.
All275 imported consumer/solver modules in the installed test process resolve
under the designated isolated site; no source fallback.

Independent Read review by `/root/e02_rigid_tip_runner`, who did not implement
the consumer, found no unresolved correctness finding in schema dispatch,
historical compatibility, ownership, numerical failure/cancellation forwarding
or cleanup. Root independently inspected the four-file scope, repaired and
executed the checks, and verified artifact bytes. This is a separate OpenAI
review, not different-model-family acceptance or mechanical qualification.

## Evidence and remaining boundary

[Raw inventory](raw-v1.json) binds [72 retained files](raw-v1.zip), including both
failed fixture versions, commands, full logs, junit inventories, source snapshots,
effective settings, imports, examples and the consumer wheel. Archive SHA256
`508557acd35f49afe5bd3bd1540b67ed1153a3c7a279e77964a8dbc562a0becd`,678,069bytes.
Original root:
`C:/Users/AudunArnesenNyhus/AppData/Local/Temp/r8-anyfem-b2-mixed-panel-installed-v1`.
The unchanged solver wheel remains in its earlier protected mixed-panel evidence.

Mixed-shell mass/modes, footprints, finite/material history, general F10–F13,
physical external accuracy, practical scale, platforms and aggregate M3/G0–G6
acceptance remain open. Density, beam mass/geometric data, footprints, unknown
schemas/formulations and missing installed solver support fail explicitly.
No legacy route fallback, version/default change, publication or main merge.
