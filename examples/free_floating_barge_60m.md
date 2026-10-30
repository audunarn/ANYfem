# 60 m drawing barge — closed, ballasted and free floating in an irregular sea

`free_floating_barge_60m.py` starts from `anyfem.application.examples.drawing_barge_60m()`:
the 60 m × 5 m × 2 m hull with the 6 m high simplified end houses (X = 0–5 m and 55–60 m).
That generator is unchanged. All its plates, stiffeners, girders, materials and sections
are kept. The demonstration content is removed: the fixed X = 0 supports, the end bending
loads (and any end connection), every load case and the old analysis.

## Model additions

The drawing hull is open at both ends, so it cannot float as generated. Two additions
are made, and only these two:

- **Transoms.** One closing plate at X = 0 m and one at X = 60 m (Z 0–2 m, Y ±2.5 m),
  section `Hull 6 mm`. Each plate is built on the four hull corner edges that already
  exist, so no vertex, edge or duplicate plate is added. The internal bulkheads at 5 m
  and 55 m stay dry.
- **Ballast.** One positive `Mass` on the bottom plate with `total_distributed`. It is
  modelled ballast approved for a 1 m mean draft. The target displacement is
  ρ·L·B·T = 1025 × 60 × 5 × 1 = 307 500 kg. The ballast value is the target minus the
  structural mass from the assembled FE model (`dry_mass_properties`, which calls the
  public `anysolver.calculate_mass_properties`). The FE model is then rebuilt to verify
  the total and to get the final mass, centre of gravity and inertia. Material density
  and mass matrices are not rescaled. The ballast has no sloshing, and it is fixed to the
  plate.

The 1 m draft is a target, not a pose. The real heel, trim and draft come only from the
owner equilibrium.

## Wet hull

The wet exterior is the bottom, the port and starboard sides and the two transoms. The
script finds the existing plates from their exact geometry and stops if a match is
missing or not unique. Each wet face is an owner `AddWetSurface` with
`fluid_side="away_from_point"` and the hull centre (30, 0, 1) m as the interior point. The
deck is not wet. It is passed only as the explicit envelope closing face
(`envelope_extra_face_ids`). The geometry owner must accept the closed envelope with a
volume of exactly 600 m³. The FE mesh is stationary (`follow_surface=False`).

## Mesh

The example uses the generator's **0.5 m quadratic** quad-first mesh. Existing
plating and stiffeners remain, with the two explicit closing transoms described above;
local sizes follow the stiffener
stations. `--mesh-size` and `--element-order` select other values. With `--model`, the
generated mesh is checked and a different element order is refused. Its node, element
and identity data are saved in `model_manifest.json`.

The real 60 m example generated 20,551 nodes, 4,684 quads, 52 triangles and 3,320
beams. Its assembled steel mass was 85,486.5 kg and its explicit bottom ballast
222,013.5 kg, giving 307,500 kg after save/reopen. Earlier 1.25 m and 0.625 m linear
candidates refused aspect-ratio and S3-admission checks respectively; those checks
were preserved. These measurements establish model generation, not a solved wave case.

**Measured status, 2026-10-30:** the ballasted setup project saves and reopens, but
floating-wave preparation is blocked. Both the automatic 372-panel source and a
1.25 m, 592-panel source computed all 32 native frequency families, then failed the
unchanged radiation-damping passivity gate. At the lowest frequency, the relative
negative eigenvalue improved from -1.024e-7 to -2.631e-8; the existing limit is -1e-9.
Respectively 20 and 11 of the 32 retained matrices fail that check. No floating load
history or structural result is published. Further source-convergence/backend work
is required before this can be called a runnable irregular-wave batch example.

## Sea state

| Setting | Value | Source |
|---|---|---|
| Hs | 1 m | user |
| Tp | 6 s | user |
| JONSWAP γ | 3.3 | illustrative default |
| seed | 0 | illustrative default |
| heading | 0° | illustrative default |
| duration / dt | 120 s / 0.1 s (1201 samples) | illustrative default |
| depth | 100 m | illustrative default |
| components | 32 (0.5–3.0 ωp) | illustrative default |

`heading` is the **propagation direction**: 0° means the waves travel towards +X and reach
the X = 0 m transom first. It is not a naval "coming from" direction. The barge is
symmetric, so "head sea" here means only that the hull length is aligned with this
direction.

All of these can be overridden on the command line (`--hs --tp --gamma --seed --heading
--duration --dt --depth --components`). `IrregularWaveInput` validates the values, and
invalid values are refused (exit code 2, nothing written). The 32 components are never
reduced silently.

## Three levels

| Command | Mesh | Ballast | BEM | Result |
|---|---|---|---|---|
| `OUT` | no | pending | no | `free_floating_barge_60m_unballasted.anyfem`, state `pending_ballast` |
| `OUT --model` | yes | assigned | no | `free_floating_barge_60m_ballasted.anyfem`, state `model_ready` |
| `OUT --prepare ...` | yes | assigned | yes | `free_floating_barge_60m_floating.anyfem`, state `floating_load_applied` |

Saving and opening a project never run BEM. The unballasted file is not the final
ballasted project.

### `--model`

    python examples/free_floating_barge_60m.py OUT --model [--deadline 600]

This writes the following files to `OUT`:

- `free_floating_barge_60m_ballasted.anyfem`: the project with the ballast assigned.
- `model_manifest.json`: the ballast value, distribution and target, structural and total
  mass, centre of gravity, inertia, envelope and mesh identity, and the reopened mass.
- `wave_request.json`: the exact wave input, the realization digest and its components,
  and the free-free batch settings that Apply will create. It contains no runtime paths.
- `status.json`: `model_ready`, with `floating_load_applied: false` and `solved: false`.

The project is reopened from the staged file and its mass is verified before anything is
published. A refusal gives `model_refused` with the stage and the error.

### `--prepare`

    python examples/free_floating_barge_60m.py OUT --prepare --allow-development \
        --equilibrium-python PATH --owner-wheel PATH [--stability-source PATH] [--deadline 600]

The runtime values come from these arguments or from the environment keys
`ANYFEM_FLOATING_EQUILIBRIUM_PYTHON`, `ANYFEM_FLOATING_OWNER_WHEEL` and
`ANYFEM_FLOATING_STABILITY_SOURCE`. No path is guessed, and `--allow-development` must
be given explicitly. If the runtime is incomplete, the script refuses before any mesh.
Otherwise it runs these steps:

1. Publishes the `--model` files.
2. Runs `prepare_floating_job` in a contained worker.
3. Runs `reopen_floating_job` offline (no BEM).
4. Applies `ApplyFloatingLoad`. This creates one load series and its free-free batch
   with `constraint_mode=nullspace`, `inertia_relief=True` and
   `allow_unbalanced_free_free=False`.
5. Saves `free_floating_barge_60m_floating.anyfem`, which embeds the neutral payload for
   offline reopening, next to `free_floating_barge_60m_floating_record.npz`.

The BEM cache is `OUT/floating-cache`. The batch is **not solved**. `status.json` reports
the actual draft, roll and pitch under `mean_pose` once the owner has accepted a pose.

On any refusal (envelope, equilibrium, 600-panel source, rotation, passivity and other
gates, mapping or time limit), `status.json` becomes `prepare_refused` with the stage and
the error. The ballasted model stays. No loaded project is written, and no load is partly
applied. A record that was published before a later refusal is flagged as
`unapplied_record_present`. Physics, tolerances and gates are the production ones and are
not changed.

## Existing outputs

Existing output files are never replaced. Without `--overwrite` the script stops with exit
code 3 and leaves every file untouched. With `--overwrite`, the earlier outputs are moved
into a new `superseded-NNN` folder, so a prior refusal stays available as evidence. Only the
named outputs are moved; `floating-cache` and any other file stay where they are.

Files are staged first, then moved into place one at a time with the status last. No hard
links are needed. `status.json` is the commit marker: a set of outputs is complete only when
it says so. If a move fails, the files already placed by that run are removed and no success
status is written; if that removal also fails, the error names the incomplete outputs.

Each file move is no-clobber. On Windows it is one atomic rename. On POSIX the absent name is
claimed exclusively and the staged file then renamed onto that claim. A tool that ignores
exclusive creation and writes to the same name during that short step can still lose its
write, so do not share an output folder between concurrent runs. The publication of several
files is not atomic as a group.

## Limitations

- The transoms are unstiffened closing plates. They are connected through the hull corner
  edges and are not tied to the centreline bulkhead end along its height.
- The ballast is shared equally over the bottom plate nodes, not per area. Its centroid
  therefore follows the node layout. The manifest holds the resulting centre of gravity.
- The native BEM source is limited to 600 panels and is not coarsened silently.
- No irregular-frequency lid is qualified.
- The loaded project does not store a mesh record. Reopening regenerates the mesh and uses
  the offline reopen/remap (no BEM).
- This is an illustrative workflow example, not a scientific or class qualification.
