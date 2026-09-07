"""Bounded consumer tests: no Tk roots, mesh generation or native workloads."""

from dataclasses import replace
from types import MethodType, SimpleNamespace

import pytest

from anyfem.document import DocumentSession
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.mesh_controls import MeshControls
from anyfem.mesh_jobs import MeshSettings, MeshTaskManager, _cancellation_token
from anyfem.model.project import Project
from anyfem.ui.app import AnyFemApp
from anyfem.ui.panels import MeshPanel
from anyfem.native_meshing import NativeMeshSettings


pytestmark = pytest.mark.usefixtures("mesher_native_v2_capability")


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def panel_for(project=None):
    panel = SimpleNamespace(
        app=SimpleNamespace(project=project or Project("controls")),
        _controls_dirty=False, _loading_mesh_controls=False,
        _show_native_controls=Value(False), _toggle_native_controls=lambda: None,
        _native_filling_status=SimpleNamespace(configure=lambda **kwargs: None),
        _native_backend=Value("Automatic"), _recombine=Value(True),
        _point_placement=Value(MeshPanel._PLACEMENT_LABELS["legacy_lattice"]),
        _metric_mode=Value(MeshPanel._METRIC_LABELS["legacy"]),
        _certification=Value(MeshPanel._CERTIFICATION_LABELS["interactive"]),
        _native_max_insertions=Value("10000"),
        _native_max_operations=Value("1000000"),
        _native_cancel_interval=Value("256"),
        _quality_jacobian=Value("0.1"), _quality_aspect=Value("5"),
        _quality_min_angle=Value("20"), _quality_max_angle=Value("160"),
        _quality_warpage=Value("0.1"), _method_value=lambda: "native",
        _control_choice=MeshPanel._control_choice,
    )
    for name in ("_PLACEMENT_LABELS", "_METRIC_LABELS", "_CERTIFICATION_LABELS", "_NATIVE_BACKEND_LABELS"):
        setattr(panel, name, getattr(MeshPanel, name))
    return panel


def store(project, controls):
    AnyFemApp._store_mesh_strategy(
        SimpleNamespace(project=project), "native", target_size=0.25,
        element_order="linear", mesh_controls=controls,
        quality_policy={"maximum_aspect_ratio": 3.5},
    )


def alpha_controls():
    return MeshControls(
        recombine=False, certification_mode="strict",
        point_placement="frontal_delaunay", metric_mode="isotropic_spatial",
        max_insertions=321, max_topology_operations=4321, cancellation_interval=16,
    )


def legacy_controls():
    return MeshControls(recombine=False, certification_mode="strict")


CONTROL_FACTORIES = [
    pytest.param(legacy_controls, id="legacy"),
    pytest.param(alpha_controls, id="alpha", marks=pytest.mark.native_v2),
]


def store_saved_alpha_intent(project):
    """Create an existing-schema fixture without constructing native-v2 objects."""
    project.set_native_mesh_settings(NativeMeshSettings.create(
        0.25, backend="native", certification_mode="strict", parameters={
            "recombine": False, "native_point_placement": "frontal_delaunay",
            "native_metric_mode": "isotropic_spatial", "native_max_insertions": 321,
            "native_max_topology_operations": 4321, "native_cancellation_interval": 16,
        },
    ))


def test_real_capability_branch_reopens_saved_intent(mesher_native_v2_capability):
    project = Project("real capability branch")
    store_saved_alpha_intent(project)
    reopened = project_from_dict(project_to_dict(project))
    before = project_to_dict(reopened)
    panel = panel_for(reopened)
    MeshPanel._refresh_mesh_controls(panel)
    assert panel._point_placement.get() == MeshPanel._PLACEMENT_LABELS["frontal_delaunay"]
    assert panel._native_max_insertions.get() == "321"
    if mesher_native_v2_capability:
        assert MeshPanel._mesh_controls_value(panel) == alpha_controls()
    else:
        assert "Saved mesh controls cannot run" in panel._controls_error
        with pytest.raises(ValueError, match="Saved mesh controls cannot run"):
            MeshPanel._mesh_controls_value(panel)
    assert project_to_dict(reopened) == before


def test_legacy_defaults_are_available_in_either_lane():
    assert MeshPanel._mesh_controls_value(panel_for()) == MeshControls()


@pytest.mark.native_v2
def test_defaults_match_public_owner_options():
    from anymesher.native_v2 import NativeMeshingOptions
    assert MeshControls().native_options().to_dict() == NativeMeshingOptions().to_dict()
    assert MeshPanel._mesh_controls_value(panel_for()) == MeshControls()


def test_missing_native_v2_retains_legacy_and_refuses_alpha(monkeypatch):
    monkeypatch.setattr("anyfem.mesh_controls.native_v2_options_type", lambda: None)
    assert MeshControls().native_options() is None
    with pytest.raises(ValueError, match="does not provide native-v2"):
        alpha_controls()


@pytest.mark.native_v2
def test_saved_alpha_reopens_without_api_preserves_intent_and_blocks_execution(monkeypatch):
    from anyfem.mesh_controls import native_v2_options_type
    available = native_v2_options_type()
    project = Project("saved alpha")
    store_saved_alpha_intent(project)
    wire = project_to_dict(project)
    monkeypatch.setattr("anyfem.mesh_controls.native_v2_options_type", lambda: None)
    reopened = project_from_dict(wire)
    before = project_to_dict(reopened)
    panel = panel_for(reopened)
    messages = []
    panel._native_filling_status.configure = lambda **kwargs: messages.append(kwargs["text"])
    MeshPanel._refresh_mesh_controls(panel)
    assert "Saved mesh controls cannot run" in messages[-1]
    assert "compatible ANYmesher" in messages[-1]
    assert panel._point_placement.get() == MeshPanel._PLACEMENT_LABELS["frontal_delaunay"]
    assert panel._metric_mode.get() == MeshPanel._METRIC_LABELS["isotropic_spatial"]
    assert panel._native_max_insertions.get() == "321"
    with pytest.raises(ValueError, match="Saved mesh controls cannot run"):
        MeshPanel._mesh_controls_value(panel)
    assert project_to_dict(reopened) == before
    # Restoring capability/reopening needs no settings repair or downgrade.
    monkeypatch.setattr("anyfem.mesh_controls.native_v2_options_type", lambda: available)
    MeshPanel._refresh_mesh_controls(panel)
    assert panel._controls_error is None
    assert MeshPanel._mesh_controls_value(panel) == alpha_controls()
    assert project_to_dict(reopened) == before


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_explicit_same_id_reopen_discards_draft_and_rehydrates_saved_controls(controls_factory):
    project = Project("same id")
    controls = controls_factory()
    store(project, controls)
    panel = panel_for(project)
    MeshPanel._refresh_mesh_controls(panel)
    panel._native_max_insertions.set("999")
    panel._controls_dirty = True
    panel.app.project = project_from_dict(project_to_dict(project))
    MeshPanel.reset_mesh_drafts(panel)
    MeshPanel._refresh_mesh_controls(panel)
    assert panel._native_max_insertions.get() == str(controls.max_insertions)
    assert MeshPanel._mesh_controls_value(panel) == controls


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_build_trace_sequence_and_first_hydration_without_tk(monkeypatch, controls_factory):
    """Execute the real panel builder with widget/variable doubles, no Tcl."""
    from anyfem.ui import panels

    class TracedValue(Value):
        def __init__(self, value=None):
            super().__init__(value)
            self.callbacks = []

        def trace_add(self, mode, callback):
            self.callbacks.append(callback)

        def set(self, value):
            super().set(value)
            for callback in self.callbacks:
                callback("name", "index", "write")

    class Widget:
        def __init__(self, parent=None, **kwargs):
            self.children = []
            self.options = kwargs
            self.manager = ""
            if parent is not None and isinstance(parent, Widget):
                parent.children.append(self)

        def pack(self, **kwargs):
            self.manager = "pack"
            self.options.update(kwargs)

        def pack_forget(self):
            self.manager = ""

        def winfo_manager(self):
            return self.manager

        def winfo_children(self):
            return self.children

        def configure(self, **kwargs):
            self.options.update(kwargs)

        def bind(self, *args):
            pass

    class Entry(Widget):
        pass

    for name in ("Frame", "LabelFrame", "Label", "Button", "Combobox", "Radiobutton", "Checkbutton"):
        monkeypatch.setattr(panels.ttk, name, Widget)
    monkeypatch.setattr(panels.ttk, "Entry", Entry)
    monkeypatch.setattr(panels.tk, "StringVar", TracedValue)
    monkeypatch.setattr(panels.tk, "BooleanVar", TracedValue)
    project = Project("hydration")
    controls = controls_factory()
    store(project, controls)
    panel = object.__new__(MeshPanel)
    panel.app = SimpleNamespace(project=project)
    panel.presenter = None
    panel.build()
    assert panel._controls_dirty is False
    panel._refresh_mesh_controls()
    assert panel._mesh_controls_value() == controls
    frontal = controls.point_placement == "frontal_delaunay"
    assert panel._metric_combo.options["state"] == ("readonly" if frontal else "disabled")
    assert panel._controls_dirty is False
    assert panel._show_native_controls.get() == frontal
    panel._point_placement.set(MeshPanel._PLACEMENT_LABELS["legacy_lattice"])
    assert panel._metric_mode.get() == MeshPanel._METRIC_LABELS["legacy"]
    assert panel._metric_combo.options["state"] == "disabled"
    assert panel._controls_dirty
    # An incompatible saved project disables Generate through the real method
    # controls updater, while the existing snapshot is left unchanged.
    panel.reset_mesh_drafts()
    store_saved_alpha_intent(project)
    monkeypatch.setattr("anyfem.mesh_controls.native_v2_options_type", lambda: None)
    panel._refresh_mesh_controls()
    panel._update_method_controls()
    assert panel._generate_button.options["state"] == "disabled"
    assert "Saved mesh controls cannot run" in panel._mapped_status.options["text"]


@pytest.mark.parametrize("changes", [
    {"metric_mode": "isotropic_spatial"}, {"point_placement": "field_guided"},
    {"max_insertions": 0}, {"max_topology_operations": -1},
    {"cancellation_interval": True}, {"max_insertions": 1.5},
    {"certification_mode": "none"}, {"recombine": "yes"},
])
def test_invalid_combinations_are_refused(changes):
    with pytest.raises(ValueError):
        MeshControls(**changes)


@pytest.mark.parametrize("value", ["", "0", "-1", "1.5", "nan"])
def test_invalid_budget_has_actionable_error(value):
    panel = panel_for()
    panel._point_placement.set(MeshPanel._PLACEMENT_LABELS["frontal_delaunay"])
    panel._native_max_insertions.set(value)
    with pytest.raises(ValueError, match="max insertions must be a positive integer"):
        MeshPanel._mesh_controls_value(panel)


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_persistence_snapshot_and_refresh_keep_exact_controls(controls_factory):
    project = Project("controls")
    controls = controls_factory()
    store(project, controls)
    reopened = project_from_dict(project_to_dict(project))
    assert MeshControls.from_settings(reopened.native_mesh_settings) == controls
    assert MeshControls.from_settings(DocumentSession(reopened).snapshot().thaw().native_mesh_settings) == controls
    panel = panel_for(reopened)
    MeshPanel._refresh_mesh_controls(panel)
    assert MeshPanel._mesh_controls_value(panel) == controls
    assert panel._quality_aspect.get() == "3.5"
    panel._native_backend.set("Compiled native")
    panel._native_max_insertions.set("555")
    MeshPanel._mesh_control_edited(panel)
    MeshPanel._refresh_mesh_controls(panel)
    assert panel._native_backend.get() == "Compiled native"
    assert panel._native_max_insertions.get() == "555"


def test_new_document_resets_drafts_but_ordinary_refresh_preserves_them():
    panel = panel_for()
    MeshPanel._refresh_mesh_controls(panel)
    panel._point_placement.set(MeshPanel._PLACEMENT_LABELS["frontal_delaunay"])
    panel._controls_dirty = panel._method_dirty = panel._preference_dirty = True
    panel.app.project = Project("other document")
    MeshPanel._refresh_mesh_controls(panel)
    assert MeshPanel._mesh_controls_value(panel) == MeshControls()
    assert not panel._controls_dirty
    assert not panel._method_dirty
    assert not panel._preference_dirty


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_legacy_ignores_disabled_budget_draft_and_retains_saved_budget(controls_factory):
    project = Project("legacy")
    controls = controls_factory()
    store(project, controls)
    panel = panel_for(project)
    panel._native_max_insertions.set("")
    assert MeshPanel._mesh_controls_value(panel).max_insertions == controls.max_insertions


@pytest.mark.native_v2
def test_clean_refresh_preserves_collapsed_alpha_disclosure():
    project = Project("disclosure")
    store(project, alpha_controls())
    panel = panel_for(project)
    MeshPanel._refresh_mesh_controls(panel)
    assert panel._show_native_controls.get()
    panel._show_native_controls.set(False)
    MeshPanel._refresh_mesh_controls(panel)
    assert not panel._show_native_controls.get()


def test_invalid_control_does_not_mutate_order_or_submit():
    panel = panel_for()
    calls = []
    panel.app.run = lambda command: calls.append(command)
    panel.app.generate_mesh_async = lambda *a, **kw: calls.append(kw)
    panel._size = Value("0.25")
    panel._order = Value("quadratic")
    panel.number = lambda variable, label: float(variable.get())
    panel._structure_preference_value = lambda: "balanced"
    panel._NATIVE_BACKEND_VALUES = MeshPanel._NATIVE_BACKEND_VALUES
    panel._mesh_controls_value = MethodType(MeshPanel._mesh_controls_value, panel)
    panel._point_placement.set(MeshPanel._PLACEMENT_LABELS["frontal_delaunay"])
    panel._native_max_insertions.set("bad")
    with pytest.raises(ValueError, match="positive integer"):
        MeshPanel._generate(panel)
    assert calls == []


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_mapped_ignores_hidden_invalid_fields_but_keeps_saved_native_options(controls_factory):
    project = Project("mapped")
    saved = controls_factory()
    store(project, saved)
    panel = panel_for(project)
    panel._method_value = lambda: "mapped"
    panel._native_max_insertions.set("invalid hidden value")
    controls = MeshPanel._mesh_controls_value(panel)
    assert controls == replace(saved, certification_mode="interactive")
    assert controls.effective_dict("mapped") == {"certification_mode": "interactive"}


@pytest.mark.native_v2
def test_job_identity_includes_effective_controls_only():
    def settings(strategy, controls):
        return MeshSettings.create(0.2, element_order="linear", strategy=strategy, controls=controls)
    baseline = MeshControls()
    for name, value in (
        ("recombine", False), ("certification_mode", "strict"),
        ("point_placement", "frontal_delaunay"), ("max_insertions", 11),
        ("max_topology_operations", 33), ("cancellation_interval", 12),
    ):
        altered = replace(baseline, **{name: value})
        assert settings("native", altered).input_hash != settings("native", baseline).input_hash
        if name != "certification_mode":
            assert settings("mapped", altered).input_hash == settings("mapped", baseline).input_hash
    with pytest.raises(TypeError, match="MeshControls"):
        settings("native", {})


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_async_submission_records_controls_without_starting_worker(controls_factory):
    project = Project("queued")
    captured = []
    app = SimpleNamespace(
        project=project, session=DocumentSession(project), seeding_overrides={},
        mesh_task_manager=SimpleNamespace(busy=False, submit=lambda *args: captured.append(args)),
        _project_mesh_strategy=AnyFemApp._project_mesh_strategy,
        _project_structure_preference=AnyFemApp._project_structure_preference,
        set_status=lambda message: None, refresh_panels=lambda: None,
    )
    app._store_mesh_strategy = MethodType(AnyFemApp._store_mesh_strategy, app)
    controls = controls_factory()
    record = AnyFemApp.generate_mesh_async(app, 0.2, strategy="native", mesh_controls=controls)
    _, snapshot, settings = captured[0]
    assert settings.controls == controls
    assert MeshControls.from_settings(snapshot.thaw().native_mesh_settings) == controls
    assert record.summary["controls_requested"] == controls.effective_dict("native")


@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_worker_propagates_controls_to_project_without_meshing(monkeypatch, controls_factory):
    captured = []
    def stop_before_meshing(self, *args, **kwargs):
        captured.append(kwargs)
        raise RuntimeError("test boundary: no mesh generated")
    monkeypatch.setattr(Project, "generate_mesh", stop_before_meshing)
    project = Project("worker")
    controls = controls_factory()
    store(project, controls)
    manager = MeshTaskManager()
    try:
        manager._run("test", DocumentSession(project).snapshot(),
                     MeshSettings.create(0.2, element_order="linear"), _cancellation_token())
        assert captured[0]["mesh_controls"] == controls
        assert any(event.kind == "failed" and "test boundary" in event.message for event in manager.poll())
    finally:
        manager.shutdown()


@pytest.mark.parametrize("strategy", ["auto", "native", "mapped"])
@pytest.mark.parametrize("controls_factory", CONTROL_FACTORIES)
def test_project_passes_public_owner_options_without_meshing(monkeypatch, strategy, controls_factory):
    project = Project("public boundary")
    project.geometry.add_plate(project.geometry.add_points(
        ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0))))
    captured = []
    def stop_before_meshing(*args, **kwargs):
        captured.append(kwargs)
        raise RuntimeError("test boundary: no mesh generated")
    monkeypatch.setattr("anyfem.model.project.generate_hybrid_mesh", stop_before_meshing)
    controls = controls_factory()
    with pytest.raises(RuntimeError, match="test boundary"):
        project.generate_mesh(0.25, strategy=strategy, mesh_controls=controls)
    assert captured[0]["certification_mode"] == "strict"
    assert captured[0]["recombine"] is False
    if strategy == "mapped" or controls.native_options() is None:
        assert "native_options" not in captured[0]
    else:
        assert captured[0]["native_options"].to_dict() == controls.native_options().to_dict()


def test_legacy_job_hashes_include_recombine_and_audit():
    def hash_for(controls):
        return MeshSettings.create(0.2, element_order="linear", strategy="native", controls=controls).input_hash
    defaults = MeshControls()
    assert hash_for(defaults) != hash_for(replace(defaults, recombine=False))
    assert hash_for(defaults) != hash_for(replace(defaults, certification_mode="strict"))
