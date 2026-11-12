"""Results fatigue controls, using manufactured shell stresses and typed viewport picks."""

import os
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets", allow_module_level=True)
pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from anyfem.application.fatigue import FatigueInput
from anyfem.ui.qt import fatigue
from anyfem.ui.qt.tasks import ResultsTask


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def stress_input():
    pytest.importorskip("anyfatigue.plate")
    from anyfatigue.model import ArrayStressSource, SurfaceMesh
    from anymesher import Mesh

    nodes = {k: np.array(p, dtype=float) for k, p in enumerate((
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)
    ), start=1)}
    shells = {14: (1, 2, 3, 4), 21: (2, 5, 6, 3)}
    mesh = Mesh(nodes=nodes, quads=shells, elements_of_face={7: [14, 21]},
                nodes_of_edge={8: [1, 4], 9: [4, 3, 6], 10: [5, 6]}, node_of_vertex={23: 4})
    surface = SurfaceMesh(list(nodes), list(nodes.values()), list(shells), list(shells.values()))
    history = np.zeros((5, 2, 6))
    history[:, :, 0] = np.outer([0, 1, 0, -1, 0], [10, 60])
    source = ArrayStressSource(surface, list("ABCDE"), history, -0.7 * history, times=range(5))
    return FatigueInput(source, mesh, face_thicknesses_m={7: 0.020})


class Picks(QObject):
    def __init__(self, app, on_pick, on_cancel, *, on_selection, on_miss):
        super().__init__()
        self.on_selection, self.on_cancel, self.on_miss = on_selection, on_cancel, on_miss
        self.kind = None
        self.overlay = None
        self.selected = ()

    @property
    def active(self):
        return self.kind is not None

    def start(self, kind, mesh, *, element_ids=(), selected=(), through_node=None):
        self.kind = kind
        self.scope = tuple(element_ids)
        self.selected = tuple(selected)
        self.through_node = through_node

    def click(self, kind, identifier, *, add=False):
        assert self.kind == {"node": "point", "vertex": "point", "edge": "line",
                              "mesh_edge": "line"}.get(kind, kind)
        if kind == "mesh_edge":
            from anyfem.application.fatigue import FatigueMeshEdgeRef
            ref = FatigueMeshEdgeRef(identifier)
        else:
            ref = SimpleNamespace(kind=kind, id=identifier)
        self.on_selection(ref, add)

    def set_selected(self, refs):
        self.selected = tuple(refs)

    def cancel(self):
        self.kind = None

    def clear_preview(self):
        self.overlay = None

    def clear(self):
        self.cancel()
        self.clear_preview()
        self.selected = ()

    def preview(self, mesh, element_ids, stations, *, governing_index=None):
        self.overlay = (mesh, element_ids, stations, governing_index)


class Panel(QWidget):
    open_fatigue = ResultsTask.open_fatigue
    use_retained = ResultsTask.use_retained

    def __init__(self):
        super().__init__()
        self.fatigue_dialog = None
        self._display_source = "live"
        data = self.data = stress_input()
        self.requests = []
        self.loads = []
        self.app = SimpleNamespace(
            project=object(), session=object(), active_job_id="Job 1", solution=object(),
            result_datasets={"Job 1": object()}, retained_result_mesh=lambda job: data.mesh,
            _request_result=self.request,
        )

    def load(self, **kwargs):
        self.loads.append(kwargs)
        return self.data

    def request(self, load, publish, **args):
        self.requests.append(lambda: publish(load(Event())))

    def finish(self):
        self.requests.pop(0)()


@pytest.fixture
def panel(qapp, monkeypatch):
    panel = Panel()
    monkeypatch.setattr(fatigue, "load_fatigue_input", panel.load)
    monkeypatch.setattr(fatigue, "FatiguePicking", Picks)
    panel.show()
    dialog = panel.open_fatigue()
    panel.finish()
    qapp.processEvents()
    assert dialog.data is panel.data
    yield panel
    if panel.fatigue_dialog is not None:
        panel.fatigue_dialog.close()
    panel.close()
    qapp.processEvents()


def click(button):
    assert button.isEnabled()
    QTest.mouseClick(button, Qt.LeftButton)


def select(combo, value):
    index = combo.findData(value)
    assert index >= 0
    combo.setCurrentIndex(index)


def test_direct_shells_weibull_inputs_damage_and_replacing_a_pick(panel, monkeypatch):
    d = panel.fatigue_dialog
    frames_read = []
    def select_cases(labels, *, cancelled):
        assert not cancelled()
        frames_read.append(labels)
        return panel.data.source
    monkeypatch.setattr(panel.data.source, "select_cases", select_cases, raising=False)
    assert not d.isModal() and panel.open_fatigue() is d
    assert panel.loads[-1]["solution"] is panel.app.solution
    assert panel.loads[-1]["dataset"] is None
    click(d.pick_shell)
    d.picking.click("element", 14)
    panel.finish()
    assert d.thickness.text() == "20"
    click(d.add_shell)
    d.picking.click("element", 21)
    panel.finish()
    assert d.picking.overlay[1] == (14, 21)
    assert d.picking.overlay[3] is None
    assert "Selected shell elements · 2" in d.preview_details.toPlainText()
    assert d.measure.currentData() == "component:xx"
    assert not frames_read  # Picking and preview require geometry only.
    d.case.setCurrentText("B")
    select(d.range_choice, 1.0)
    d.dff.setText("2")
    click(d.run_button)
    panel.finish()
    assert d.result.method == "simplified" and d.table.rowCount() == 2
    assert frames_read == [("B",)]
    assert d.picking.overlay[3] == 1
    assert [index.row() for index in d.table.selectionModel().selectedRows()] == [1]
    assert "21 top" in d.summary.text() and "Job 1" in d.summary.text()
    assert float(d.table.item(1, 3).text()) == pytest.approx(d.result.critical.damage * 2, rel=1e-5)
    assert "2010" in d.warnings.toPlainText()
    first = d.result.critical.damage
    select(d.range_choice, 2.0)
    assert d.result is None and d.table.rowCount() == 0
    click(d.run_button)
    panel.finish()
    assert d.result.critical.damage > first
    d.tabs.setCurrentIndex(0)
    click(d.pick_shell)
    d.picking.click("element", 14)
    panel.finish()
    assert d.element_ids == (14,)


def test_weld_and_point_controls_use_plate_probes_and_pick_cancel(panel):
    d = panel.fatigue_dialog
    select(d.location_kind, "line")
    click(d.pick_shell)
    d.picking.click("element", 14)
    assert not d.run_button.isEnabled()
    click(d.pick_target)
    d.picking.click("edge", 8)
    panel.finish()
    assert d.prepared is not None, d.status.text()
    assert d.prepared.face_id == 7 and d.measure.currentData() == "normal_to_weld"
    assert "0.5t, 1.5t" in d.preview_details.toPlainText()
    assert "Eligible shell elements on plate face 7 · 2" in d.preview_details.toPlainText()
    assert d.prepared.stations[0].probes[0] == pytest.approx((0.01, 0, 0))
    click(d.run_button)
    panel.finish()
    assert [d.table.item(i, 1).text() for i in range(d.table.rowCount())] == ["0", "1"]
    d.tabs.setCurrentIndex(0)
    click(d.pick_target)
    click(d.cancel_pick)
    assert d.picking.kind is None and d.target.id == 8 and d.picking.overlay is None
    select(d.location_kind, "point")
    click(d.pick_target)
    d.picking.click("vertex", 23)
    panel.finish()
    assert d.prepared is None and ("direction" in d.status.text() or "reference" in d.status.text())
    click(d.pick_reference)
    d.picking.click("edge", 8)
    panel.finish()
    assert d.prepared is not None, d.status.text()
    assert d.reference_edge.id == 8 and d.prepared.stations[0].direction == pytest.approx((1, 0, 0))
    d.direction.setText("1, 0, 0")
    assert d.reference_edge is None
    select(d.surface, "bottom")
    d.case.setCurrentText("B")
    click(d.run_button)
    panel.finish()
    assert d.result.critical.surface == "bottom" and d.prepared.face_id == 7
    assert "nodal stress" in d.warnings.toPlainText().lower()
    d.tabs.setCurrentIndex(0)
    click(d.pick_target)
    d.picking.click("node", 1)
    panel.finish()
    assert d.prepared is not None, d.status.text()
    assert not d.direction.text() and d.reference_edge is None
    assert d.prepared.stations[0].direction == pytest.approx((1, 0, 0))


def test_rainflow_case_order_and_record_duration(panel, monkeypatch):
    d = panel.fatigue_dialog
    frames_read = []
    def select_cases(labels, *, cancelled):
        frames_read.append(labels)
        return panel.data.source
    monkeypatch.setattr(panel.data.source, "select_cases", select_cases, raising=False)
    click(d.pick_shell)
    d.picking.click("element", 21)
    panel.finish()
    select(d.method, "time_series")
    d.duration.setText("12")
    d.case_series.item(2).setCheckState(Qt.Unchecked)
    click(d.run_button)
    panel.finish()
    assert d.result is not None, d.status.text()
    assert d.result.method == "time_series"
    assert frames_read == [tuple("ABDE")]
    assert d.result.settings["blocks"][0]["cases"] == list("ABDE")
    assert d.result.points[0].parts[0].values["record_duration_s"] == 12
    d.tabs.setCurrentIndex(1)
    d.case_series.setCurrentRow(1)
    click(d.up)
    click(d.run_button)
    panel.finish()
    assert d.result is None and "strictly increasing" in d.status.text()


def test_mixed_or_missing_thickness_needs_manual_input_and_resets_on_repick(panel):
    from anymesher import Mesh

    d = panel.fatigue_dialog
    old = panel.data
    mesh = Mesh(nodes=old.mesh.nodes, quads=old.mesh.shells, elements_of_face={7: [14], 8: [21]})
    panel.data = d.data = FatigueInput(old.source, mesh, face_thicknesses_m={7: .016, 8: .032})
    click(d.pick_shell)
    d.picking.click("element", 14)
    panel.finish()
    assert d.thickness.text() == "16"
    d.thickness.setText("18")
    click(d.add_shell)
    d.picking.click("element", 21)
    panel.finish()
    assert not d.thickness.text()
    click(d.run_button)
    assert not panel.requests and "thickness in millimetres" in d.status.text()
    d.thickness.setText("18")
    click(d.run_button)
    panel.finish()
    assert d.result.settings["thickness_mm"] == 18
    d.tabs.setCurrentIndex(0)
    click(d.pick_shell)
    d.picking.click("element", 21)
    panel.finish()
    assert d.thickness.text() == "32"
    panel.data = d.data = FatigueInput(old.source, mesh)
    click(d.pick_shell)
    d.picking.click("element", 14)
    panel.finish()
    assert not d.thickness.text()


def test_inflight_result_discarded_on_edit_pick_job_change_and_close(panel):
    d = panel.fatigue_dialog
    click(d.pick_shell)
    d.picking.click("element", 14)
    panel.finish()
    click(d.run_button)
    d.period.setText("4")
    panel.finish()
    assert d.result is None and not d.summary.text()
    click(d.run_button)
    click(d.add_shell)
    panel.finish()
    assert d.result is None and d.picking.kind == "element"
    d.picking.cancel()  # a replaced display cancels the scoped viewport command
    d.sync_source()
    assert "cancelled" in d.status.text() and not d.cancel_pick.isEnabled()
    click(d.run_button)
    panel.app.active_job_id = "Saved"
    panel.app.result_datasets["Saved"] = object()
    panel._display_source = "retained"
    panel.finish()
    assert d.result is None and d.table.rowCount() == 0
    d.sync_source()
    panel.finish()
    assert panel.loads[-1]["dataset"] is panel.app.result_datasets["Saved"]
    assert panel.loads[-1]["solution"] is None and not d.element_ids
    click(d.pick_shell)
    d.picking.click("element", 21)
    panel.finish()
    click(d.run_button)
    click(d.close_button)
    panel.finish()
    assert panel.fatigue_dialog is None and d.result is None


@pytest.mark.parametrize("kind,method,picks", [
    ("line","simplified",(("edge",8),("mesh_edge",(5,6)))),
    ("point","time_series",(("node",1),("node",5))),
])
def test_shift_add_runs_all_hot_spots_and_replaces_while_armed(panel, kind, method, picks):
    d = panel.fatigue_dialog
    select(d.location_kind, kind)
    select(d.method, method)
    d.case.setCurrentText("B")
    click(d.pick_shell)
    d.picking.click("element",14)
    click(d.pick_target)
    assert set(d.picking.scope) == {14,21}
    d.picking.click(*picks[0])
    assert d.picking.active and d.run_button.isEnabled()
    d.picking.click(*picks[1], add=True)
    assert len(d.targets) == 2
    assert len(d.picking.selected) == 3  # supporting shell and both targets
    d.picking.click(*picks[1], add=True)
    assert len(d.targets) == 2 and len(panel.requests) == 2
    panel.finish()  # first selection is stale
    assert d.prepared is None
    panel.finish()
    assert len(d.prepared.locations) == 2
    assert d.picking.active
    click(d.run_button)
    panel.finish()
    assert d.result is not None, d.status.text()
    assert len(d.result.points) == len(d.prepared.stations) == (4 if kind == "line" else 2)
    governing = next(i for i, p in enumerate(d.result.points) if p is d.result.critical)
    assert governing >= len(d.result.points)//2
    assert d.picking.overlay[3] == governing
    assert d.table.currentRow() == governing
    assert d.result.critical.label == d.table.item(governing,0).text()
    assert d.result.critical.label in d.summary.text()
    d.picking.click(*picks[0])
    assert len(d.targets) == 1 and d.result is None
    panel.finish()
    assert len(d.prepared.locations) == 1 and d.picking.active


def test_mixed_direct_shells_and_wrong_face_shift_keep_selection(panel):
    from anymesher import Mesh
    d = panel.fatigue_dialog
    old = panel.data
    mesh = Mesh(nodes=old.mesh.nodes, quads=old.mesh.shells, elements_of_face={3:[14], 4:[21]})
    panel.data = d.data = FatigueInput(old.source, mesh, face_thicknesses_m={3:.012,4:.012})
    click(d.pick_shell)
    d.picking.click("element",14)
    d.picking.click("element",21,add=True)
    assert d.element_ids == (14,21) and d.picking.active
    select(d.location_kind,"line")
    assert not d.pick_target.isEnabled() and "plate" in d.status.text()
    assert not d.picking.active
    click(d.pick_shell)
    d.picking.click("element",14)
    d.picking.click("element",21,add=True)
    assert d.element_ids == (14,) and "plate" in d.status.text()
    click(d.pick_target)
    assert d.picking.scope == (14,)
    d.picking.on_miss("Choose an edge on this plate.")
    assert not d.targets and "on this plate" in d.status.text()


def test_multiple_toes_share_a_reference_and_refuse_unrelated_points(panel):
    d=panel.fatigue_dialog
    select(d.location_kind,"point")
    click(d.pick_shell)
    d.picking.click("element",14)
    click(d.pick_target)
    d.picking.click("vertex",23)
    panel.finish()
    click(d.pick_reference)
    assert d.picking.through_node == 4
    d.picking.click("edge",8)
    panel.finish()
    assert d.reference_edge.id == 8 and not d.picking.active
    click(d.pick_target)
    d.picking.click("node",5,add=True)
    assert len(d.targets) == 1 and d.reference_edge.id == 8 and "misses node 5" in d.status.text()
    d.picking.click("node",1,add=True)
    panel.finish()
    assert len(d.prepared.locations) == 2 and d.reference_edge.id == 8
    click(d.pick_reference)
    assert d.picking.through_node == (4,1)
    d.picking.click("edge",10)
    assert d.reference_edge.id == 8 and "vertex 23" in d.status.text() and "node 1" in d.status.text()
    assert d.picking.active
    d.picking.click("edge",8)
    panel.finish()
    assert d.reference_edge.id == 8 and not d.picking.active
    d.direction.setText("1, 0, 0")
    click(d.pick_target)
    d.picking.click("node",1)
    panel.finish()
    assert d.direction.text() == "1, 0, 0" and d.reference_edge is None
    d.picking.click("node",5,add=True)
    assert len(d.targets) == 2 and d.pick_reference.isEnabled()
    assert "Multiple toes" in d.pick_hint.text()


def test_opening_preset_replaces_an_armed_selection_and_stale_request(panel):
    d = panel.fatigue_dialog
    click(d.pick_shell)
    d.picking.click("element",21)
    assert d.picking.active
    d.configure_weld_history(dict(element_id=14,edge_id=8,face_id=7,surface="top",side="auto"))
    assert not d.picking.active and d.element_ids == (14,) and d.target.id == 8
    panel.finish()
    assert d.prepared is None
    panel.finish()
    assert d.prepared is not None and d.prepared.face_id == 7


def test_results_panel_button_opens_fatigue_and_reports_missing_extra(qapp, tmp_path, monkeypatch):
    from anyfem.io import recovery
    from anyfem.ui.qt.app import QtFemWindow
    monkeypatch.setattr(recovery, "default_recovery_root", lambda: tmp_path / "recovery")
    window = QtFemWindow(viewer_backend="software")
    try:
        window.show()
        window.notebook.select("Results")
        panel = window.panels["Results"]
        button, = [b for b in panel.findChildren(QPushButton) if b.text() == "Fatigue…"]
        try:
            from anyfatigue.plate import plate_stress_source
        except ImportError:
            pass
        else:
            click(button)
            assert panel.fatigue_dialog is not None
            assert "select its result" in panel.fatigue_dialog.status.text()
            click(panel.fatigue_dialog.close_button)
        def unavailable():
            raise ValueError("Install the ANYfem[fatigue] extra.")
        monkeypatch.setattr("anyfem.application.fatigue.require_fatigue", unavailable)
        click(button)
        assert panel.fatigue_dialog is None and "ANYfem[fatigue]" in panel.report.toPlainText()
    finally:
        window.session.mark_saved()
        window.close()
        qapp.processEvents()
