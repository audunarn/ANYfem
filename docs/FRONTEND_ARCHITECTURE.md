# Frontend architecture

ANYfem's engineering behavior is owned by headless packages. Desktop
toolkits are adapters, not alternate implementations of the model workflow.

## Stable boundary

- `anyfem.application.WorkbenchController` owns the active project, document
  session, command stack, selection, background job managers, active mesh and
  retained results.
- `TaskPresenter` exposes common task state, command execution, selection
  requirements and diagnostic reporting without importing a GUI toolkit.
- `anyfem.application.ports` defines scheduling, dialog, clipboard, status and
  viewport behavior. Toolkit packages implement those protocols.
- `anyfem.presentation` is the public toolkit-neutral namespace for scenes,
  visualization policy, result units, result summaries and live charts.
- `anyfem.ui.tk` is the current Tk adapter. `anyfem.ui.app` remains available
  as a compatibility import during migration.
- `anyfem.application.workflow.WorkbenchWorkflow` owns the shared document,
  mesh/solve, sidecar and recovery operations. Adapter hooks provide dialogs,
  task views and scheduling. Tk and Qt use the same operations.
- `anyfem.presentation.viewport.SceneViewport` shares scene translation,
  picking and construction behavior. Each toolkit owns widget lifecycle.
- `anyfem.ui.qt` is the optional PySide6 candidate. Launch with `anyfem-qt`
  or `python -m anyfem.ui.qt`; Tk remains the default until parity acceptance.

No module outside `anyfem.ui` may import Tk, ANYtk3D, PySide or PyQt. The
default package and all headless verification entry points must remain usable
without loading a desktop toolkit.

## Adding a Qt frontend

A Qt frontend should construct one `WorkbenchController`, bind widgets to its
coarse `WorkbenchEvent` stream and use the existing document/selection events
for incremental updates. It implements the toolkit ports and renders
`anyfem.presentation.Scene`; it must not duplicate commands or edit `Project`
objects directly.

ANY3DView accepts a `ViewerHostAdapter`. A Qt renderer host creates a QWidget
that satisfies `ViewerBackend`; the historical Tk host remains the default.
This keeps camera, selection, retained mesh and scene semantics shared across
frontends.

## Migration rule

New workflow logic belongs in the controller, a presenter, or a headless
domain module. Tk panels may format widgets and translate signals, but should
not acquire new solver, mesher, file-format or result-interpretation logic.
Existing panel actions can be migrated incrementally because legacy
`AnyFemApp` state attributes delegate to the controller.
