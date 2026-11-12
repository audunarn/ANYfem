# OrcaWave and ANYhydro prescribed pressure

Open **Hydrodynamic wave load → OrcaWave / ANYhydro pressures…** on a meshed project with wet
surfaces. Read a completed `.owr` or a portable pressure export, select the body,
pressure composition and solved heading, and select the structural wet surfaces.
Declare the rigid alignment and projection/transfer error limits. Prepare, inspect
the source/target overlay and time snapshots, then apply the history. Application
creates one undoable load series and a batch linear-static analysis definition.

The `orcawave` application extra installs the neutral owner dependencies.
Reading `.owr` additionally needs a separately installed compatible OrcFxAPI and
an available Orcina licence. No OrcaWave or ANYhydro calculation is started.
`anyfileio.read_orcawave_pressure(path).save(portable_path)` creates a portable
JSON export. Applied projects embed their bank and realized waves and reopen
without OrcFxAPI or the original results file.

The same entry also reads a canonical portable pressure bank from ANYhydro.
Its body, heading and composition are already selected and remain fixed.
For a stored native `pressure_jump` dataset, export it using
`anyhydro.bank_from_anyhydro_pressure_jump(dataset, bodies, request,
body_id="original-body-id", heading=heading_radians).save(path)`.
Supply the complete original body/request inventory, including attached sheet
meshes. The dataset must already carry a verified saved excitation-source
declaration with explicit phase origin. `attach_excitation_source` may create
that declaration only after an actual convention audit; attaching it does not
qualify the hydrodynamic source. Original panel/surface/body identities, coupled
calculation provenance and qualification are retained. This native adapter is
sheet-only: its field contains no hull pressure. The preview shows this scope
and the retained qualification. No stored backend data is recalculated.
The admitted subset requires the declaration's complete six-DOF inventory and
nonzero native frequencies; schema 1.2 `pressure_jump` presence alone is insufficient.

## Physics and conventions

Fixed pressure is incident plus diffraction. Total pressure already includes the
motion response calculated by OrcaWave; no additional motion multiplier is used.
The importer converts force/length-cubed to Pa/m, conjugates the provider phasor,
and gives body panels outward normals. Canonical pressure is positive inward,
and histories use `Re[P a exp(i phase - i omega t)]`. Source wave phase remains
referenced to the OrcaWave global origin when geometry is aligned to the project.

Dipole panels carry the signed jump `p_minus - p_plus`. Positive jump acts along
the provider's minus-to-plus normal. The canonical reference normal is the
opposite normal, so traction remains `-P*n_reference`. Select one structural
midsurface per physical sheet and mark its wet-surface ID as a **Dipole
midsurface**. The jump acts once, with no second face contribution, hydrostatic
jump or additional motion RAO. Reversing sheet winding changes only its reference;
reversing source normal and pressure sign together preserves physical loading.
Per-patch orientation is retained when a target element spans opposed source
references. ANYhydro's stored jump phasors already use `exp(-i omega t)` and are
not conjugated; their total fixed excitation maps to `fixed_diffraction`.

Regular waves use an available frequency, with amplitude equal to half the wave
height. Irregular waves use one solved heading and the native positive frequency
grid. Midpoint bin boundaries span the available frequency band. Continuous
JONSWAP energy is normalized to the requested significant height before taking
the available bins. Omitted energy is reported and is never redistributed.
Phases and amplitudes, seed and bin-energy report are persisted.

## Mapping and admission

Source and target are convex planar facets; a curved hull is represented by such
facets. Physical panel normals, areas and centroids are checked against the
geometry owner. Matching facets use exact overlap. Independent facets use bounded
closest-surface assignment, including closest boundary edges within the declared
gap. Target integration uses the solver's actual element pressure work, including
native triangular-shell nodal moments. A sparse operator is built once per mesh.

Projection limits do not certify pressure accuracy. The unresolved-area bound is
geometric. Separate frequency-by-frequency complex force and moment errors must
meet the declared absolute-plus-relative limits; moments use the transformed
source origin. No load rescaling compensates for mapping errors. Bounds, gap,
normal, ambiguity and budget failures refuse preparation.

Body pressure and dipole jump use separate source/target roles. Body opposing
normals remain refused; dipole roles explicitly permit either sheet winding.
Duplicate projected sheet footprints, including opposed duplicates and copies
crossing coplanar panel seams, are refused. Every dipole source panel needs
actual interior projected coverage within the declared coverage limit; proximity
to a target edge alone is insufficient. These checks are independent of pressure
values, so zero total force/moment cannot hide a missing loaded sheet. Distinct
parallel sheets retain their separate source support. Resultant limits apply
to each pressure kind as well as the combined load. Bounded approximate sheet
mapping can conservatively fail interior coverage when its assignment remains
unresolved; it never enlarges tolerances to pass.
Local-coordinate projection and clipping carry an engineering roundoff-area
bound, including possible partition overlaps; it is deducted from source
coverage. A requested coverage allowance below this bound is refused. This is
an engineering precision estimate, not a formal interval-arithmetic certificate.

On remeshing, the retained source rebuilds the mapping. Batch assembly reuses it
within the guarded job. Geometry, wet-side or shell-formulation changes require
new preparation. This initial route conservatively invalidates on changes to
the whole geometry and region records. Imperfect reference geometry and follower pressure
are refused. Numerical lids do not load the structure. Nonplanar source facets
are unsupported. Changed sheet-role selection invalidates the retained mapping.

The dialog exposes coverage and unresolved-area limits, refinement depth and
triangle budgets. Preparation has a configurable time limit (600 seconds by
default) and can be cancelled. The independently faceted synthetic hull check
took about two minutes on the development machine; large-model performance has
not been established.

## Structural scope and verification

The new history contains prescribed pressure loads only. Hydrostatics, gravity,
acceleration and inertia remain separate model decisions. A coupled floating
history cannot be combined with this history. This feature does not resolve the
deferred L01 damping or mechanism-certification issues and does not establish a
validated free-floating L01 stress solution.

Synthetic verification covers ingestion, SI/phase/normal conversion, analytical
spectral energy, geometry refusal and coverage, nodal assembly, frame transforms,
project reopen, undo, remeshing, guarded batch reuse and offscreen Qt operation.
Read-only native checks against retained local `.owr` files (extraction, total-pressure
composition, load-RAO integration, portable reopen with OrcFxAPI blocked) were run
outside this repository. Their numbers, files and commands are restricted evidence
kept with the owner's records and are deliberately not reproduced here.

The original licence error was sandbox access isolation: the local licence
works outside that sandbox. The inspected barge file itself has both panel
pressure and intermediate output disabled. Fixed-body diffraction requires
saved intermediate results; total pressure requires saved panel-pressure output.
See [Orcina output options](https://www.orcina.com/webhelp/OrcaWave/Content/html/Data,Calculationandoutput.htm)
and [panel results](https://www.orcina.com/webhelp/OrcaWave/Content/html/Results,Panelresults.htm).

These checks establish API extraction/convention agreement for the identified
records, not hydrodynamic model accuracy or a validated structural solution.
A separate synthetic test covers ANYhydro stored jumps through the same route;
it preserves its development-fixture qualification. Restricted evidence is
referred to by the owner's records, not by path or derived numbers in this repository.
