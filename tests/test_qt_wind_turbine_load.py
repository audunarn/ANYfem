"""Offscreen wind turbine load editor (Loads & BC) and its named selection in the floating transient dialog:
create / edit / delete through the command path, owned import samples and retained Kaimal realization, reopen without
rerandomizing, and load_fields() of the selected record (body -> CG conversion at that boundary only)."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

try:
    from tests.test_floating_transient import make_input, world  # noqa: F401
except ImportError:  # pragma: no cover
    from test_floating_transient import make_input, world  # noqa: F401

from anyfem.ui.qt.wind_turbine import WindTurbineLoadEditor  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class Panel(QWidget):
    def __init__(self):
        super().__init__()
        from anyfem.model import Project
        self.commands = []
        self.project = Project()

        def run(cmd):
            self.commands.append(cmd)
            return cmd.do(self.project)
        self.app = SimpleNamespace(project=self.project, run=run, set_status=lambda text: None)


def _fill(editor, **values):
    for key, value in values.items():
        editor.fields[key].setText(str(value))


def test_editor_creates_edits_and_deletes_through_undoable_commands(qapp):
    from anyfem.commands import AddWindTurbineLoad, DeleteWindTurbineLoad, EditWindTurbineLoad
    panel = Panel()
    from anyfem.ui.qt.load_panel import LoadTask
    editor = LoadTask.open_wind_turbine_loads(panel)                  # the Loads & BC launcher
    assert isinstance(editor, WindTurbineLoadEditor) and editor.list.count() == 0
    editor.new(); _fill(editor, name="T1", hub_node="4")
    created = editor.save()
    assert created is not None, editor.status.text()
    assert isinstance(panel.commands[-1], AddWindTurbineLoad) and panel.project.wind_turbine_load("T1") is created
    assert created.wind_mode == "steady" and created.hub_body == (0.0, 0.0, 10.0) and editor.list.count() == 1
    n = len(panel.commands)
    assert editor.save() is created and len(panel.commands) == n          # unchanged: nothing applied
    _fill(editor, hub_node="5")
    edited = editor.save()
    edit_command = panel.commands[-1]
    assert isinstance(edit_command, EditWindTurbineLoad) and edited.id == created.id and edited.hub_node_id == 5
    _fill(editor, thrust_curve="10:1, 5:2")
    assert editor.save() is None and "Refused" in editor.status.text()
    assert panel.project.wind_turbine_loads[created.id] is edited          # a refused edit applies nothing
    editor.new(); _fill(editor, name="T1", hub_node="2")
    assert editor.save() is None and "already exists" in editor.status.text()
    editor.select("T1")
    removed = editor.delete()
    delete_command = panel.commands[-1]
    assert isinstance(delete_command, DeleteWindTurbineLoad) and removed is edited
    assert panel.project.wind_turbine_loads == {} and editor.list.count() == 0
    delete_command.undo(panel.project)
    assert panel.project.wind_turbine_loads[created.id] is edited
    edit_command.undo(panel.project)                                      # LIFO: undo the edit
    assert panel.project.wind_turbine_loads[created.id] is created


def test_kaimal_and_import_definitions_own_their_wind_and_reopen_without_rerandomizing(qapp, tmp_path, monkeypatch):
    import anyloads.wind_turbine as awt
    from anyfem.io.project_file import project_from_dict, project_to_dict
    panel = Panel()
    editor = WindTurbineLoadEditor(panel)
    editor.new(); _fill(editor, name="K", hub_node="3", ti="0.12", seed="11", dt="0.5", duration="200")
    editor.wind_mode.setCurrentIndex(editor.wind_mode.findData("kaimal"))
    k = editor.save()
    assert k is not None and k.wind_mode == "kaimal" and k.max_frequency == 1.0 and len(k.phases) > 0, \
        editor.status.text()
    assert "omitted" in editor.summary.text()

    def forbidden(**kw):
        raise AssertionError("reopening / renaming must not regenerate the realization")
    monkeypatch.setattr(awt, "generate_kaimal_realization", forbidden)
    back = project_from_dict(project_to_dict(panel.project))               # project reopen: no regeneration
    assert back.wind_turbine_loads[k.id] == k
    reopened = WindTurbineLoadEditor(panel)
    reopened.select("K"); _fill(reopened, name="K renamed", hub="0 0 12")
    renamed = reopened.save()
    assert renamed is not None, reopened.status.text()
    assert renamed.phases == k.phases and renamed.hub_body == (0.0, 0.0, 12.0)
    monkeypatch.undo()
    csv = tmp_path / "wind.csv"
    t = np.linspace(0.0, 20.0, 41)
    csv.write_text("\n".join(f"{x},{10 + 0.1 * np.sin(x)},0.5,0" for x in t), encoding="utf-8")
    reopened.new(); _fill(reopened, name="I", hub_node="3", import_file=str(csv))
    reopened.wind_mode.setCurrentIndex(reopened.wind_mode.findData("import"))
    i = reopened.save()
    assert i is not None and len(i.import_times) == 41 and i.import_velocity[0][1] == 0.5, reopened.status.text()
    csv.unlink()                                                           # samples are owned, not the file path
    reopened.select("I"); _fill(reopened, hub_node="4")
    i2 = reopened.save()
    assert i2 is not None and i2.import_times == i.import_times and i2.hub_node_id == 4, reopened.status.text()


def test_floating_transient_selects_a_named_turbine_and_load_fields_reads_the_record(qapp, world):
    from anyfem.commands import AddWindTurbineLoad, DeleteWindTurbineLoad
    from anyfem.model.wind_turbine import WindTurbineLoad
    from anyfem.ui.qt.floating_transient import FloatingTransientDialog
    base = make_input(world)
    panel = Panel()
    dt = float(np.max(np.diff(base.times)))
    load = WindTurbineLoad.create("Rotor A", thrust_speeds=[0.0, 10.0, 20.0], thrust_values=[0.0, 1000.0, 1500.0],
                                  hub_body=[0.5, 0.0, 10.0], hub_node_id=int(world["dry"].node_ids[-1]),
                                  wind_mode="kaimal", reference_mean_world=[10.0, 0.0, 0.0], rotor_yaw_deg=5.0,
                                  rotor_tilt_deg=0.0, turbulence_intensity=0.1, integral_scale=340.2, seed=3,
                                  timestep=2.0 * dt, duration=float(base.times[-1]))
    panel.app.run(AddWindTurbineLoad(load))
    d = FloatingTransientDialog(panel, fe_model_provider=lambda: world["model"])
    assert d.turbine.count() == 2 and d.turbine.currentData() is None      # "No turbine" + the saved record
    out, prov = d.load_fields(base)
    assert out["wind_velocity"] is None and out["hub_node_id"] is None and prov is None
    d.select_turbine("Rotor A")
    out, prov = d.load_fields(base)
    np.testing.assert_array_equal(out["hub_offset_body"], np.array(load.hub_body) - np.asarray(base.cg_body))
    np.testing.assert_array_equal(out["rotor_axis_body"], load.rotor_axis_body())
    assert out["thrust_values"].tolist() == [0.0, 1000.0, 1500.0] and out["hub_node_id"] == load.hub_node_id
    np.testing.assert_array_equal(out["wind_velocity"], load.kaimal_realization().to_wind_history(base.times).velocity)
    assert prov["turbine"]["name"] == "Rotor A" and prov["phases"] == list(load.phases)
    again, _ = d.load_fields(base)                                         # the retained realization, not a redraw
    np.testing.assert_array_equal(again["wind_velocity"], out["wind_velocity"])
    panel.app.run(DeleteWindTurbineLoad(load.id))
    with pytest.raises(ValueError, match="no longer exists"):
        d.load_fields(base)
    d.refresh_turbines()
    assert d.turbine.count() == 1 and d.turbine.currentData() is None


def test_editor_attachment_mass_and_undo(qapp):
    panel=Panel(); editor=WindTurbineLoadEditor(panel)
    _fill(editor,hub_node='2',attachment_element='10',hub='1 0 2',mass='100',mass_cg='.5 0 1',inertia='3 4 5 0 0 0')
    editor.hub_reference.setCurrentIndex(editor.hub_reference.findData('attachment'))
    editor.mass_mode.setCurrentIndex(editor.mass_mode.findData('additional'))
    saved=editor.save(); assert saved is not None,editor.status.text()
    assert saved.mass==100 and saved.attachment_element_id==10 and saved.hub_reference=='attachment'
    editor.load_record(saved); assert editor.record_from_fields(saved)==saved
    _fill(editor,inertia='1 1 9 0 0 0')
    assert editor.save() is None
    assert panel.project.wind_turbine_loads[saved.id]==saved
    panel.commands[-1].undo(panel.project)
    assert not panel.project.wind_turbine_loads
    editor.close()
