# Copy the current diagnosis

Choose **Help → Copy diagnosis**, then paste the report into a support message. The action always creates a fresh report, including for an empty project or a session where no script has run. A status message confirms that it was copied. **Scripts → Copy diagnosis** includes the same current-state report plus a bounded recent command history; it does not copy the script editor's source.

The JSON report keeps its existing project, application, error, runtime, package and recent-command fields. Its `current_state` contains four sections:

- `geometry_input`: geometry counts, bounded raw vertex/edge/face inputs, materials, plate and beam sections, assignment samples and coordinate definitions.
- `meshing`: configured target/settings, named sizes, refinements, mesh records, already loaded mesh counts and existing quality/preparation metadata.
- `solver_input`: load cases and load types, supports, masses, connections, imperfections, analysis settings and output requests. These are configured inputs; diagnosis does not compile a solver model or establish readiness.
- `solver_output`: recorded job statuses, summaries, outcomes and errors, artifact registration, already loaded result metadata and the active lazy dataset's availability.

Collections include their total count, included count and truncation flag. Large arrays are represented by shape/type information and, for generic input metadata, a small bounded preview. Loaded displacement/stress arrays are not copied. Raw geometry samples help describe manual inputs; full feature histories are omitted because the public history accessor deep-copies their data.

Unavailable or broken optional data is marked in the affected section. A recorded result is distinguished from a loaded result, and registered artifacts are not claimed to exist on disk. Model-hash comparisons identify known stale metadata without asserting solver success or complete freshness. Lazy result datasets are not opened to fill the report.

Copying diagnosis does not modify or save the project, generate/audit a mesh, compile solver input, run an analysis, recover stress, poll a worker or read result sidecars. It is a bounded snapshot of current memory and recorded metadata.
