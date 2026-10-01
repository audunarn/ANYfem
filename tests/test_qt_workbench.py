"""Opt-in real Qt workflow checks, separate from numerical qualification."""
from __future__ import annotations

import os
import time
from dataclasses import replace
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("set ANYFEM_RUN_QT_TESTS=1 for real Qt widgets",allow_module_level=True)
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from anyfem.ui.qt.app import QtFemWindow
from anyfem import commands as cmd
from anyfem.model.attributes import Support


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp,tmp_path,monkeypatch):
    from anyfem.io import recovery
    monkeypatch.setattr(recovery,"default_recovery_root",lambda:tmp_path/"recovery")
    window=QtFemWindow(viewer_backend=os.environ.get("ANYFEM_QT_TEST_BACKEND","software"))
    window.show();qapp.processEvents()
    yield window
    window.session.mark_saved()
    window.close();qapp.processEvents()


def wait_until(qapp,predicate,timeout=30):
    end=time.monotonic()+timeout
    while not predicate() and time.monotonic()<end:
        qapp.processEvents()
        time.sleep(0.01)
    assert predicate(), "Qt workflow did not reach the expected state within its bounded wait"


def cantilever(window):
    from uuid import uuid4
    a=window.run(cmd.AddPoint(0,0,0));b=window.run(cmd.AddPoint(1,0,0))
    edge=window.run(cmd.AddLine(a,b))
    window.run(cmd.AddBeamSection(replace(window.project.beam_sections["stiffener"],id=str(uuid4()),name="centerline",offset_mode="centerline")))
    window.run(cmd.AssignBeam(edge,"centerline"))
    window.run(cmd.AddSupport(Support("fixed",window.project.geometry.entity_ref("vertex",a),dict.fromkeys(["ux","uy","uz","rx","ry","rz"],0))))
    window.run(cmd.AddPointLoad(window.project.geometry.entity_ref("vertex",b),force=(0,0,-1000)))
    return a,b


def test_requested_viewer_backend_is_active(window,qapp,tmp_path):
    requested=os.environ.get("ANYFEM_QT_TEST_BACKEND","software")
    # An explicit GPU suite must not pass merely because automatic fallback works.
    image=tmp_path/"requested-backend.png"
    window.viewport.capture_png(image)
    qapp.processEvents()
    assert window.viewport.active_backend==requested, window.viewport.backend_diagnostics
    assert image.exists() and image.stat().st_size>0


def test_property_edits_preserve_identity_and_undo(window,qapp):
    a,b=cantilever(window);qapp.processEvents()
    section=window.project.beam_sections["centerline"]
    window.tree.edit_record(("beam_sections",section.id))
    panel=window.panels["Sections"]
    panel.record_fields["section"][1]["flange_thickness"][0].setText(str(section.flange_thickness*2))
    panel.execute()
    assert window.project.beam_sections["centerline"].id==section.id
    assert window.project.beam_sections["centerline"].flange_thickness==section.flange_thickness*2
    window.commands.undo()
    assert window.project.beam_sections["centerline"].flange_thickness==section.flange_thickness
    support=window.project.supports[0]
    window.tree.edit_record(("supports",support.id))
    panel=window.panels["Loads & BC"]
    panel.record_fields["replacement"][1]["constraints"][0].setText('{"ux":0,"uy":0,"uz":0}')
    panel.execute()
    assert window.project.supports[0].id==support.id
    assert len(window.project.supports[0].constraints)==3
    window.commands.undo()
    assert len(window.project.supports[0].constraints)==6


def test_script_transcript_output_and_replacement(window,qapp):
    panel=window.panels["Scripts"]
    window.run(cmd.AddPoint(3,0,0));window.commands.undo();window.commands.redo()
    transcript=panel.command_stream.toPlainText()
    assert "AddPoint" in transcript and "undo" in transcript and "redo" in transcript
    panel.copy_commands_to_editor()
    assert transcript in panel.source.toPlainText()
    panel.source.setPlainText("print('visible console output')")
    panel.start();wait_until(qapp,lambda:panel.task is None)
    assert "visible console output" in panel.output.toPlainText()
    old_stack=window.commands
    window.new_project();panel.command_stream.clear()
    window.run(cmd.AddPoint(4,0,0))
    assert "AddPoint" in panel.command_stream.toPlainText()
    assert panel._observed_stack is window.commands
    assert panel._observed_stack is not old_stack


def test_all_visualization_settings_and_color_validation(window,qapp):
    panel=window.panels["Visualization"]
    panel.appearance["show_beam_sections"].setChecked(False)
    panel.appearance["show_result_nodes"].setChecked(True)
    panel.appearance["geometry_detail"].setCurrentText("Fast")
    panel.appearance["legend_width"].setText("300")
    panel.apply()
    assert not window.viewport.visualization.show_beam_sections
    assert window.viewport.visualization.show_result_nodes
    assert window.viewport.visualization.geometry_detail=="Fast"
    assert window.viewport.visualization.legend_width==300
    results=window.panels["Results"]
    results.minimum.setText("2");results.maximum.setText("1")
    with pytest.raises(ValueError,match="minimum"):results.colour_limits()
    results.minimum.setText("0");results.maximum.setText("1")
    assert results.colour_limits()==(0,1)


def test_typed_analysis_controls_and_mesh_pins(window,qapp):
    a=window.run(cmd.AddPoint(0,0,0));b=window.run(cmd.AddPoint(1,0,0));edge=window.run(cmd.AddLine(a,b))
    window.selection.set_mode("edge");window.selection.select(window.project.geometry.entity_ref("edge",edge))
    mesh=window.panels["Mesh"];mesh.divisions.setValue(7);mesh.pin()
    assert window.seeding_overrides[edge]==7
    assert mesh.settings()["structured_controls"].maximum_candidates_per_component==256
    mesh.refine();assert len(window.project.refinements)==1
    mesh.clear_refinements();assert not window.project.refinements
    window.commands.undo();assert len(window.project.refinements)==1
    solve=window.panels["Solve"]
    solve.analysis.setCurrentText("Modal");solve.controls.fields["num_modes"][0].setText("2")
    assert solve.settings()["num_modes"]==2
    solve.analysis.setCurrentText("Arc length")
    solve.record_controls["control"][1].fields["max_steps"][0].setText("12")
    assert solve.settings()["control"].max_steps==12
    solve.analysis.setCurrentText("Impact")
    solve.record_controls["collision"][1].fields["mass"][0].setText("200")
    assert solve.settings()["collision"].mass==200
    solve.use_resources.setChecked(True);solve.resources.fields["solver_threads"][0].setText("1")
    assert solve.settings()["resource_config"].solver_threads==1


def test_generators_regions_units_presets_and_record_selection(window,qapp):
    from PySide6.QtCore import QItemSelectionModel
    generators=window.panels["Generators"]
    feature=generators.execute();qapp.processEvents()
    window.tree.toggle_feature_topology([feature.feature_id]);window.show_geometry()
    assert feature.feature_id in window.tree.exploded_feature_ids
    face=next(iter(window.project.geometry.faces))
    window.selection.set_mode("face");window.selection.select(window.project.geometry.entity_ref("face",face))
    definitions=window.panels["Definitions"];region=definitions.create_region()
    assert region.id in window.project.regions
    definitions.profile.setCurrentText("Custom");definitions.unit_fields["length"].setCurrentText("mm");definitions.apply_units()
    assert window.project.units.symbol("length")=="mm"
    sections=window.panels["Sections"];section=sections.create_plate()
    assert section.material in window.project.materials
    supports=window.panels["Loads & BC"];supports.preset.setCurrentText("pinned");supports.support_preset()
    assert len(window.project.supports[0].constraints)==3
    qapp.processEvents();item=window.tree._groups["Plate sections"].child(0)
    window.tree.selectionModel().select(item.index(),QItemSelectionModel.ClearAndSelect|QItemSelectionModel.Rows)
    assert not window.selection.items
    assert window.tree.selectionModel().isSelected(item.index())
    window.refresh_all();assert window.tree.selectionModel().isSelected(item.index())


@pytest.mark.parametrize("kind,values",[("Plate",{}),("Bulkhead",{}),("Frame",{}),("Girder",{}),("Stiffener",{}),("Stiffened panel",{"length":1,"width":1,"longitudinal_spacing":0.25}),("Cylinder",{"radius":1,"height":1}),("Cone",{"radius_start":1,"radius_end":0.5,"height":1})])
def test_qt_typed_generators(window,qapp,kind,values):
    panel=window.panels["Generators"];panel.kind.setCurrentText(kind)
    panel.form.set_values(values);panel.execute();qapp.processEvents()
    assert window.project.geometry.features.records
    assert window.project.geometry.vertices


def test_replacement_resets_typed_mesh_controls(window,qapp):
    panel=window.panels["Mesh"]
    panel.native_controls.fields["max_insertions"][0].setText("123")
    panel.structured_controls.fields["maximum_blocks"][0].setText("321")
    panel.strategy.setCurrentText("native")
    window.new_project()
    assert panel.native_controls.values()["max_insertions"]==10000
    assert panel.structured_controls.values()["maximum_blocks"]==100000
    assert panel.strategy.currentText()==window._project_mesh_strategy(window.project,None)


def test_feature_tree_exposure_search_and_undo(window,qapp):
    from anyfem.commands import RenameFeature
    feature=window.panels["Generators"].execute()
    tree=window.tree;tree.refresh()
    assert tree._groups["Points"].rowCount()==0
    row=tree._rows["Features"][feature.feature_id][0]
    assert row.rowCount()==0
    tree.toggle_feature_topology([feature.feature_id])
    assert row.rowCount()>0
    ref=row.child(0).child(0).data(Qt.UserRole)
    tree.search(str(ref.id))
    assert row.child(0).rowCount()==1
    window.selection.set_mode(ref.kind);window.selection.select(ref)
    assert any(index.data(Qt.UserRole)==ref for index in tree.selectionModel().selectedRows())
    original=window.project.geometry.features.get(feature.feature_id).name
    window.workbench.execute(RenameFeature(feature.feature_id,"Renamed plate"),solver_affecting=False)
    window.undo()
    assert window.project.geometry.features.get(feature.feature_id).name==original
    window._tree_action("suppress",(f"feature:{feature.feature_id}",))
    assert window.project.geometry.features.get(feature.feature_id).suppressed
    window.undo()
    assert not window.project.geometry.features.get(feature.feature_id).suppressed


def test_command_editor_project_units_and_explicit_suffixes(window,qapp):
    from anyfem.model.units import UNIT_PROFILES
    window.run(cmd.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    panel=window.panels["Geometry"];panel.choice.setCurrentIndex(panel.choice.findData("AddPoint"));panel.rebuild()
    panel.fields["x"][0].setText("1000")
    panel.fields["y"][0].setText("2 cm")
    panel.fields["z"][0].setText("0")
    identifier=panel.execute()
    assert tuple(window.project.geometry.vertex_position(identifier))==pytest.approx((1,0.02,0))
    definitions=window.panels["Definitions"]
    definitions.choice.setCurrentIndex(definitions.choice.findData("AddCoordinateSystem"))
    fields=definitions.record_fields["system"][1]
    fields["name"][0].setText("Unit origin")
    fields["origin"][0].setText("1000, 2 cm, 0")
    system=definitions.execute()
    assert tuple(system.origin)==pytest.approx((1,.02,0))


@pytest.mark.parametrize("operation",["CopyEntities","MirrorEntities","LinearPattern","CircularPattern","Extrude","Revolve","ReverseEntity","SplitEdge","SplitFace","StripFace","SetFaceCorners"])
def test_qt_geometry_operations_and_undo(window,qapp,operation):
    from anyfem.io.project_file import project_to_dict
    def topology():
        # Geometry reserves allocated IDs across undo and advances its revision.
        # Compare the restored topology/features, not allocator history.
        return {key:value for key,value in project_to_dict(window.project)["geometry"].items() if key not in {"id_state","checksum","revision"}}
    points=[window.run(cmd.AddPoint(*point)) for point in ((1,0,0),(2,0,0),(2,1,0),(1,1,0))]
    edges=[window.run(cmd.AddLine(a,b)) for a,b in zip(points,points[1:]+points[:1])]
    face=window.run(cmd.AddFace(edges))
    before=topology()
    panel=window.panels["Geometry"];panel.choice.setCurrentIndex(panel.choice.findData(operation))
    values={
        "CopyEntities":{"references":f"face:{face}","translation":"0, 0, 1"},
        "MirrorEntities":{"references":f"face:{face}"},
        "LinearPattern":{"references":f"face:{face}","spacing":"2","count":"2"},
        "CircularPattern":{"references":f"face:{face}","angle_step":"90 deg","count":"2"},
        "Extrude":{"edge_ids":str(edges[0]),"vector":"0, 0, 1"},
        "Revolve":{"edge_ids":str(edges[3]),"angle":"90 deg"},
        "ReverseEntity":{"reference":f"face:{face}"},
        "SplitEdge":{"edge_id":str(edges[0])},
        "SplitFace":{"face_id":str(face)},
        "StripFace":{"face_id":str(face)},
        "SetFaceCorners":{"face_id":str(face),"corners":"0, 1, 2, 3"},
    }[operation]
    for name,value in values.items():panel.fields[name][0].setText(value)
    panel.execute();qapp.processEvents()
    assert topology()!=before
    window.undo()
    assert topology()==before


@pytest.mark.parametrize("operation",["TriangleToQuads","ButterflyHoleDecomposition","NeutralTrimHole","FragmentPlateOverlaps","JoinSheet"])
def test_qt_special_geometry_actions(window,qapp,operation):
    panel=window.panels["Geometry"]
    if operation=="JoinSheet":
        from test_join_sheet_command import _owned_project
        project,first,second,_=_owned_project(frozen=True);window._set_project(project)
        values={"references":f"face:{first}, face:{second}","name":"Joined"}
    elif operation=="TriangleToQuads":
        points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(2,0),(1,1.6))]
        edges=window.run(cmd.AddPolyline(points,close=True))
        values={"edge_ids":", ".join(map(str,edges))}
    else:
        def rectangle(x0,x1):
            points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((x0,0),(x1,0),(x1,3),(x0,3))]
            return window.run(cmd.AddPlate(points))
        face=rectangle(0,4)
        if operation=="FragmentPlateOverlaps":values={"face_ids":f"{face}, {rectangle(2,6)}"}
        else:values={"face_id":str(face),"centre":"2, 1.5, 0","radius":"0.5"}
    before=len(window.project.geometry.features.records)
    panel.choice.setCurrentIndex(panel.choice.findData(operation))
    for name,value in values.items():panel.fields[name][0].setText(value)
    panel.execute();qapp.processEvents()
    if operation=="JoinSheet":assert len(window.project.geometry.sheets)==1
    elif operation!="TriangleToQuads":assert len(window.project.geometry.features.records)==before+1
    if operation=="TriangleToQuads":assert len(window.project.geometry.faces)==3
    elif operation=="NeutralTrimHole":assert len(window.project.geometry.faces[face].holes)==1
    elif operation=="ButterflyHoleDecomposition":assert len(window.project.geometry.faces)>1
    elif operation=="FragmentPlateOverlaps":assert len(window.project.geometry.faces)==3
    window.undo()
    assert len(window.project.geometry.features.records)==before


def test_qt_boolean_region_and_typed_output_request(window,qapp):
    from anyfem.model.records import AnalysisDefinition
    a,b=cantilever(window)
    panel=window.panels["Definitions"];regions=[]
    for index,identifier in enumerate((a,b)):
        window.selection.set_mode("vertex");window.selection.restore((window.project.geometry.entity_ref("vertex",identifier),))
        panel.region_name.setText(f"Point-{index+1}");regions.append(panel.create_region())
    panel.refresh()
    for index,identifier in enumerate(panel._region_ids):panel.operands.item(index).setSelected(identifier in {region.id for region in regions})
    panel.region_name.setText("Both points");combined=panel.create_boolean()
    members=window.project.regions.resolve(combined.id,geometry=window.project.geometry,feature_resolver=lambda anchor: window.project.geometry.features.resolve(anchor,window.project.geometry),candidates=tuple(window.project.geometry.entity_ref("vertex",identifier) for identifier in (a,b)),properties=lambda ref:{"id":ref.id,"kind":ref.kind})
    assert {ref.id for ref in members}=={a,b}
    analysis=window.project.add_analysis(AnalysisDefinition("Output scope"))
    panel.choice.setCurrentIndex(panel.choice.findData("AddOutputRequest"))
    fields=panel.record_fields["request"][1]
    for name,value in {"quantity_keys":"displacement","region":combined.id,"location":"node","label":"Point translations"}.items():fields[name][0].setText(value)
    panel.fields["analysis_ids"][0].setText(analysis.id)
    request=panel.execute();qapp.processEvents()
    assert window.project.analyses[analysis.id].output_request_ids==(request.id,)
    assert request.region.id==combined.id
    window.undo();assert request.id not in window.project.output_requests


def test_qt_file_inspector_reports_and_canonical_records(window,qapp,tmp_path,monkeypatch):
    source=tmp_path/"sample.FEM"
    def record(name,*values):return f"{name:<8}"+"".join(f"{value:16.8E}" for value in values)
    source.write_text("\n".join([record("GCOORD",1,0,0,0),record("GCOORD",2,1,0,0),record("UNKNOWN",4,3,2,1)])+"\n",encoding="utf-8")
    inspector=window.open_file_inspector(str(source))
    wait_until(qapp,lambda:inspector.result is not None)
    assert inspector.result.summary["nodes"]==2
    assert inspector.records.values.rowCount()==3
    report=tmp_path/"inspector.json";monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(report))
    inspector.save_report();assert '"nodes": 2' in report.read_text()
    canonical=tmp_path/"canonical.FEM";monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(canonical))
    inspector.canonicalize()
    assert "UNKNOWN" in canonical.read_text()
    inspector.close();qapp.processEvents()


@pytest.mark.parametrize("suffix",["inp","dat","frd","SIF"])
def test_qt_file_inspector_formats(window,qapp,tmp_path,suffix):
    path=tmp_path/f"structural.{suffix}"
    if suffix=="inp":path.write_text("*NODE\n1,0,0,0\n2,1,0,0\n*ELEMENT, TYPE=B31, ELSET=BEAM\n1,1,2\n")
    elif suffix=="dat":path.write_text("\n     B U C K L I N G   F A C T O R   O U T P U T\n\n  MODE NO       BUCKLING\n                 FACTOR\n\n         1   2.5430000E+00\n         2   4.1120000E+00\n\n")
    elif suffix=="SIF":
        from test_interop_results import SHELL_SIF
        path.write_text(SHELL_SIF)
    else:
        from test_interop_results import write_frd
        from test_io import write_sesam_plate
        # Format inspection consumes a retained mesh and does not require the
        # independently owned geometry-to-mesh candidate to be published.
        window.import_sesam_model(str(write_sesam_plate(tmp_path/"source.FEM")))
        window.solve();wait_until(qapp,lambda:window.solution is not None)
        write_frd(path,window.mesh,window.solution.built,window.solution.displacements)
    inspector=window.open_file_inspector(str(path))
    wait_until(qapp,lambda:inspector.result is not None)
    summary=inspector.result.summary
    if suffix=="dat":assert summary["buckling_factors"]==pytest.approx([2.543,4.112])
    elif suffix=="frd":assert summary["displacement_nodes"]==window.mesh.num_nodes
    elif suffix=="SIF":assert summary["results"]["element_stress"]==1
    else:assert summary["node_count"]==2 and summary["element_count"]==1
    assert inspector.summary.toPlainText()
    inspector.close();qapp.processEvents()


def test_qt_file_inspector_failed_replacement_clears_old_data(window,qapp,tmp_path):
    from test_io import sesam_record
    source=tmp_path/"valid.FEM"
    source.write_text(sesam_record("GCOORD",1,0,0,0)+"\n")
    inspector=window.open_file_inspector(str(source))
    wait_until(qapp,lambda:inspector.result is not None)
    assert inspector.records.values.rowCount()==1
    original_diagnostics=list(inspector.diagnostics.values.rows)
    missing=tmp_path/"missing.FEM"
    inspector.load(str(missing))
    assert inspector.records.values.rowCount()==0
    assert inspector.summary.toPlainText()==""
    assert all(not button.isEnabled() for button in inspector.export_buttons)
    wait_until(qapp,lambda:"Could not read" in inspector.status.text())
    assert inspector.result is None
    assert inspector.diagnostics.values.rowCount()==1
    assert "missing.FEM" in window.statusBar().currentMessage()
    inspector.load(str(source))
    wait_until(qapp,lambda:inspector.result is not None)
    assert inspector.records.values.rowCount()==1
    assert inspector.diagnostics.values.rows==original_diagnostics
    assert all(button.isEnabled() for button in inspector.export_buttons)
    inspector.close();qapp.processEvents()


@pytest.mark.parametrize("action",["replace","close"])
def test_qt_file_inspector_late_read_cannot_update_replaced_or_closed_dialog(window,qapp,tmp_path,monkeypatch,action):
    from threading import Event
    from anyfem.presentation.file_inspection import FileInspection
    import anyfem.ui.qt.inspector as module
    started,release,finished=Event(),Event(),Event()
    old=tmp_path/"old.FEM";new=tmp_path/"new.FEM"
    def held_read(path):
        if Path(path)==old:
            started.set()
            try:
                assert release.wait(10)
                return FileInspection(old,"old",summary={"old":True})
            finally:finished.set()
        return FileInspection(new,"new",summary={"new":True})
    from pathlib import Path
    monkeypatch.setattr(module,"inspect_file",held_read)
    inspector=window.open_file_inspector(str(old))
    try:
        wait_until(qapp,started.is_set)
        if action=="replace":inspector.load(str(new))
        else:inspector.close();qapp.processEvents()
    finally:release.set()
    wait_until(qapp,finished.is_set)
    if action=="replace":
        wait_until(qapp,lambda:inspector.result is not None)
        assert inspector.result.path==new and inspector.result.summary=={"new":True}
        inspector.close();qapp.processEvents()
    else:
        from shiboken6 import isValid
        wait_until(qapp,lambda:not isValid(inspector))
        assert inspector.result is None


def test_qt_file_inspector_record_preview_preserves_full_canonical_document(window,qapp,tmp_path,monkeypatch):
    from test_io import sesam_record
    source=tmp_path/"bounded.FEM"
    source.write_text("\n".join(sesam_record("UNKNOWN",index) for index in range(5001))+"\n")
    inspector=window.open_file_inspector(str(source))
    wait_until(qapp,lambda:inspector.result is not None)
    assert inspector.result.summary["records"]==5001
    assert inspector.records.values.rowCount()==5000
    assert "5000 of 5001" in inspector.status.text()
    canonical=tmp_path/"canonical.FEM"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(canonical))
    inspector.canonicalize()
    from anyfileio import read_sesam_fem_document
    records=read_sesam_fem_document(canonical,strict=False).raw_records
    assert [record.numeric_fields for record in records if record.name=="UNKNOWN"]==[(float(index),) for index in range(5001)]
    inspector.close();qapp.processEvents()


def test_qt_file_inspector_malformed_document_retains_owner_diagnostics(window,qapp,tmp_path):
    from anyfem.presentation.file_inspection import inspect_file
    source=tmp_path/"malformed.FEM"
    source.write_text("GCOORD  broken numeric fields\n")
    expected=inspect_file(source)
    assert expected.diagnostics
    inspector=window.open_file_inspector(str(source))
    wait_until(qapp,lambda:inspector.result is not None)
    assert inspector.result.report()==expected.report()
    assert inspector.diagnostics.values.rows==[(item.severity,item.code,item.line_start or "",item.message) for item in expected.diagnostics]
    inspector.close();qapp.processEvents()


def test_saved_result_playback_tables_reports_gif_and_mesh_identity(window,qapp,tmp_path,monkeypatch):
    cantilever(window);window.generate_mesh_async(0.25,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    window.save_project(path=str(tmp_path/"retained.anyfem"));window.flush_project_writes()
    window.new_project();window.open_project(str(tmp_path/"retained.anyfem"))
    panel=window.panels["Results"];panel.play();qapp.processEvents()
    assert window.viewport.canvas.animation_frames==len(window.result_datasets[window.active_job_id].frames)
    window.viewport.canvas.stop_animation()
    panel.quantities.setCurrentIndex(panel.quantities.findText("Field: displacement"));panel.inspect_quantity()
    assert panel.table.values.rowCount()==window.mesh.num_nodes
    retained=window.retained_result_mesh(window.active_job_id)
    window.mesh=None
    panel.show_results()
    assert window.mesh is None and window.retained_result_mesh(window.active_job_id) is retained
    revision=window.session.revision
    for _ in range(2):
        window._actions["Attributes / imperfections"].trigger();qapp.processEvents()
        assert window._view_mode=="results"
        assert window.mesh is None and window.retained_result_mesh(window.active_job_id) is retained
        assert window.session.revision==revision
    report=tmp_path/"report.html";monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(report))
    panel.export_report();assert report.stat().st_size>100
    gif=tmp_path/"retained.gif";monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(gif))
    panel.export_gif();wait_until(qapp,lambda:gif.exists())
    from PIL import Image
    with Image.open(gif) as image:assert image.size[0]>0


def test_real_qt_mesh_solve_save_reopen(window,qapp,tmp_path):
    cantilever(window)
    mesh_panel=window.panels["Mesh"]
    mesh_panel.size.setText("0.25")
    mesh_panel.strategy.setCurrentText("auto")
    mesh_panel.start()
    wait_until(qapp,lambda:window.mesh is not None or not window.mesh_job_running)
    assert window.mesh is not None, window._status.get()
    window.solve()
    wait_until(qapp,lambda:window.solution is not None)
    assert window.solution.max_translation()[1]>0
    assert "failed" not in window._status.get().casefold()
    path=tmp_path/"cantilever.anyfem"
    window.save_project(path=str(path))
    assert path.exists()
    node_count=window.mesh.num_nodes
    window.new_project()
    window.open_project(str(path))
    assert window.mesh.num_nodes==node_count
    assert window.result_datasets
    window.panels["Results"].show_results()
    qapp.processEvents()
    assert window.viewport.capture_png(tmp_path/"results.png").stat().st_size>100


@pytest.mark.parametrize("damage",["missing","corrupt"])
def test_qt_damaged_result_keeps_editable_project_and_valid_mesh(window,qapp,tmp_path,damage):
    from anyfem.io.artifacts import ArtifactStore
    cantilever(window)
    mesh_panel=window.panels["Mesh"];mesh_panel.size.setText("0.25")
    mesh_panel.strategy.setCurrentText("auto");mesh_panel.start()
    wait_until(qapp,lambda:window.mesh is not None or not window.mesh_job_running)
    assert window.mesh is not None,window._status.get()
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    job_id=window.active_job_id
    path=tmp_path/"damaged-result.anyfem";window.save_project(path=str(path))
    entities=window.project.geometry.entity_keys()
    node_count=window.mesh.num_nodes
    artifact=window.project.artifacts[window.project.jobs[job_id].result_artifact_id]
    sidecar=ArtifactStore(path).resolve(artifact.uri)
    original=sidecar.read_bytes()
    window.new_project()
    if damage=="missing":sidecar.unlink()
    else:sidecar.write_bytes(original[:32])
    window.open_project(str(path));qapp.processEvents()
    assert window.project.geometry.entity_keys()==entities
    assert window.mesh.num_nodes==node_count
    assert not window.result_datasets and window.solution is None
    assert any("result artifact unavailable" in item.get("message","")
        for item in window.project.jobs[job_id].diagnostics)
    with pytest.raises(ValueError,match="no available retained result"):
        window.panels["Results"].activate_job(job_id)
    point=window.run(cmd.AddPoint(2,0,0));window.undo()
    assert window.project.geometry.entity_keys()==entities
    window.redo();assert point in window.project.geometry.vertices
    window.session.mark_saved();window.new_project()
    sidecar.write_bytes(original)
    window.open_project(str(path));qapp.processEvents()
    assert job_id in window.result_datasets
    window.panels["Results"].activate_job(job_id)
    assert window.viewport.capture_png(tmp_path/f"recovered-{damage}.png").stat().st_size>100


def test_qt_auto_deformation_and_retained_summary_preserve_solver_data(window,qapp,tmp_path,monkeypatch):
    import json
    import numpy as np
    from anyfem.presentation.scene import build_mesh_scene
    cantilever(window);window.generate_mesh_async(0.25,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None or not window.mesh_job_running)
    assert window.mesh is not None,window._status.get()
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    panel=window.panels["Results"];shape=window.current_shape()
    assert panel.scale.text()=="auto"
    _node,magnitude=shape.max_translation()
    expected_scale=.08*build_mesh_scene(window.project,shape.built.mesh).characteristic_size()/magnitude
    original=shape.deformed_positions(1).copy()
    window.viewport.set_visualization(replace(window.viewport.visualization,show_result_nodes=True))
    for text in ("auto","", " AUTO "):
        panel.scale.setText(text);assert panel.scale_value(shape)==pytest.approx(expected_scale)
        panel.show_results()
        nodes={point.ref.id:point.position for point in window.viewport._scene.points if point.ref is not None and point.ref.kind=="node"}
        np.testing.assert_allclose(np.stack([nodes[node] for node in sorted(shape.built.mesh.nodes)]),shape.deformed_positions(expected_scale))
    for text,expected in (("0",0),("-1",-1),("2.5",2.5)):
        panel.scale.setText(text);assert panel.scale_value(shape)==expected
    for text in ("nan","inf","-inf"):
        panel.scale.setText(text)
        with pytest.raises(ValueError,match="finite"):panel.scale_value(shape)
    np.testing.assert_array_equal(shape.deformed_positions(1),original)
    panel.scale.setText("auto")
    job_id=window.active_job_id
    section=window.project.beam_sections["centerline"]
    window.run(cmd.AddBeamSection(replace(section,web_height=section.web_height*40,
        flange_width=section.flange_width*40,rotation_deg=45)))
    assert window.solution is None and window._job_is_stale(window.project.jobs[job_id])
    panel.activate_job(job_id)
    assert window.solution is not None
    assert panel.scale_value(shape)==pytest.approx(expected_scale)
    panel.show_results()
    nodes={point.ref.id:point.position for point in window.viewport._scene.points if point.ref is not None and point.ref.kind=="node"}
    np.testing.assert_allclose(np.stack([nodes[node] for node in sorted(shape.built.mesh.nodes)]),shape.deformed_positions(expected_scale))
    window.undo();panel.activate_job(job_id)
    path=tmp_path/"auto-display.anyfem";job_id=window.active_job_id
    window.save_project(path=str(path));window.flush_project_writes()
    saved=window.result_datasets[job_id].field("displacement").read(0).copy()
    window.new_project();window.open_project(str(path));panel.activate_job(job_id)
    dataset=window.result_datasets[job_id];summary=dataset.metadata("summary")
    assert json.dumps(summary,indent=2,sort_keys=True) in panel.report.toPlainText()
    assert str(summary["status"]) in panel.outcome.text()
    assert "Deformation available" in panel.report.toPlainText()
    for text in ("auto",""):
        panel.scale.setText(text);assert panel.scale_value(None)==1
        panel.show_results();assert window._view_mode=="results"
    np.testing.assert_array_equal(dataset.field("displacement").read(0),saved)
    metadata=dataset.metadata
    def legacy_metadata(name):
        value=metadata(name)
        if name=="summary":value={key:item for key,item in value.items() if key!="status"}
        return value
    monkeypatch.setattr(dataset,"metadata",legacy_metadata)
    panel.refresh();assert "stored status unavailable" in panel.outcome.text()


def test_qt_submit_preserves_selected_output_requests(window,qapp,tmp_path,monkeypatch):
    import json
    from anyfem.model.records import OutputRequest
    a,b=cantilever(window)
    definitions=window.panels["Definitions"]
    window.selection.set_mode("vertex")
    window.selection.restore((window.project.geometry.entity_ref("vertex",b),))
    definitions.region_name.setText("Tip output")
    region=definitions.create_region()
    request=OutputRequest(("displacement.uz",),region.id,"node",label="Tip movement")
    window.run(cmd.AddOutputRequest(request))
    solve=window.panels["Solve"]
    solve.output_requests.item(0).setSelected(True)
    window.run(cmd.EditOutputRequest(request.id,replace(request,label="Renamed tip movement")))
    assert solve.output_requests.selectedItems()[0].data(Qt.UserRole)==request.id
    window.panels["Mesh"].strategy.setCurrentText("auto")
    window.panels["Mesh"].start()
    wait_until(qapp,lambda:window.mesh is not None or not window.mesh_job_running)
    assert window.mesh is not None,window._status.get()
    solve.submit.click()
    wait_until(qapp,lambda:window.solution is not None)
    job=window.project.jobs[window.active_job_id]
    definition=window.project.analyses[job.analysis_id]
    assert definition.output_request_ids==(request.id,)
    assert "output_request_ids" not in definition.settings
    submitted=json.loads(window.submitted_input_reports[job.id])
    assert submitted["output_requests"][0]["region"]==region.id
    assert submitted["output_requests"][0]["id"]==request.id
    expected_ids=window.mesh.nodes_on(window.project.geometry.entity_ref("vertex",b))
    assert submitted["output_request_scopes"][0]["node_ids"]==sorted(expected_ids)
    destination=tmp_path/"requested-output.anyfem"
    window.save_project(path=str(destination))
    dataset=window.result_datasets[job.id]
    outcome=dataset.metadata("provenance")["output_request_outcomes"][0]
    assert outcome["status"]=="available"
    key=outcome["fields"][0]
    assert dataset.table(f"{key}_node_ids").tolist()==sorted(expected_ids)
    assert dataset.field(key).descriptor.components==("uz",)
    assert dataset.field(key).read(0).shape==(len(expected_ids),1)
    window.new_project();window.open_project(str(destination))
    assert window.project.analyses[job.analysis_id].output_request_ids==(request.id,)
    results=window.panels["Results"];results.activate_job(job.id)
    index=results.quantities.findText(f"Output: {dataset.field(key).descriptor.label}")
    assert index>=0
    results.quantities.setCurrentIndex(index)
    results.inspect_quantity()
    csv_path=tmp_path/"tip-output.csv"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **kwargs:str(csv_path))
    results.export_quantity_csv()
    import csv
    with csv_path.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert {int(row["node_id"]) for row in rows}==set(expected_ids)
    assert "uz [m]" in rows[0] and not any("ux" in name for name in rows[0])
    assert all(float(row["uz [m]"])<0 for row in rows)


def test_qt_submit_rejects_unavailable_request_before_queuing(window,qapp):
    from anyfem.model.records import OutputRequest
    a,b=cantilever(window)
    definitions=window.panels["Definitions"]
    window.selection.set_mode("vertex")
    window.selection.restore((window.project.geometry.entity_ref("vertex",b),))
    region=definitions.create_region()
    request=OutputRequest(("frequency",),region.id,"global")
    window.run(cmd.AddOutputRequest(request))
    # Admission must happen before adding an analysis or entering the manager.
    window.mesh=object()
    before=set(window.project.analyses)
    with pytest.raises(ValueError,match="unavailable for analysis"):
        window.solve("Linear static",output_request_ids=(request.id,))
    assert set(window.project.analyses)==before
    assert not window.project.jobs


def test_qt_requested_scope_does_not_follow_an_edit_during_solve(window,qapp,tmp_path,monkeypatch):
    import json
    import threading
    from anyfem.application.workflow import ANALYSES
    from anyfem.model.records import OutputRequest
    from anyfem.model.regions import ManualRegion, Region

    a,b=cantilever(window)
    root=window.run(cmd.AddRegion(Region("Root","geometry","vertex",ManualRegion((window.project.point(a),)))))
    tip=window.run(cmd.AddRegion(Region("Tip","geometry","vertex",ManualRegion((window.project.point(b),)))))
    request=OutputRequest(("displacement.uz",),tip.id,"node")
    window.run(cmd.AddOutputRequest(request))
    window.generate_mesh_async(.25,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    expected=sorted(window.mesh.nodes_on(window.project.point(b)))
    started=threading.Event();release=threading.Event()
    original=ANALYSES["Linear static"]
    def held_solve(*,cancellation_token,**kwargs):
        started.set()
        while not release.wait(.01):cancellation_token.raise_if_cancelled()
        return original(cancellation_token=cancellation_token,**kwargs)
    monkeypatch.setitem(ANALYSES,"Linear static",held_solve)
    window.solve(output_request_ids=(request.id,))
    job=window.project.jobs[window.active_job_id]
    try:
        wait_until(qapp,started.is_set)
        window.run(cmd.EditOutputRequest(request.id,replace(request,region=root.id)))
        release.set();wait_until(qapp,lambda:job.id in window.solutions)
        submitted=json.loads(window.submitted_input_reports[job.id])
        assert submitted["output_request_scopes"][0]["node_ids"]==expected
        assert submitted["output_request_scopes"][0]["request"]["region"]==tip.id
        window.save_project(path=str(tmp_path/"frozen-output.anyfem"))
        dataset=window.result_datasets[job.id]
        outcome=dataset.metadata("provenance")["output_request_outcomes"][0]
        key=outcome["fields"][0]
        assert dataset.table(f"{key}_node_ids").tolist()==expected
        assert (dataset.field(key).read(0)<0).all()
    finally:release.set()


@pytest.mark.parametrize("policy,text",[("last","value 7"),("envelope","coordinate 0 is synthetic")])
def test_qt_named_view_discloses_its_local_frame(window,tmp_path,policy,text):
    from test_output_request_views import payload,scoped
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.output_views import add_output_request_views
    from anyfem.model.records import OutputRequest
    request=OutputRequest(("displacement.uz",),"region","node",frame_policy=policy)
    result=add_output_request_views(payload(),(scoped(request),))
    key=result.provenance["output_request_outcomes"][0]["fields"][0]
    store=ArtifactStore(tmp_path/"frame-view.anyfem")
    artifact=store.write_result(job_id="frame-job",document_id="document",mesh_id="mesh",
        model_hash="model",mesh_hash="mesh",analysis_hash="analysis",**result.write_result_inputs())
    window.active_job_id="frame-job";window.result_datasets["frame-job"]=store.open_result(artifact)
    results=window.panels["Results"];results.refresh()
    results.frame.setValue(0)
    index=results.quantities.findText(f"Output: {result.fields[key][0].label}")
    assert index>=0
    results.quantities.setCurrentIndex(index);results.inspect_quantity()
    assert text in results.report.toPlainText()
    assert f"Frame policy: {policy}" in results.report.toPlainText()
    assert results.table.values.headers==["node_id","uz"]
    assert results.table.values.rows[0][0]==42


def test_qt_repeated_frame_coordinates_keep_source_identity_and_qualification(window,tmp_path):
    from test_output_request_views import repeated_coordinate_payload,scoped
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.output_views import add_output_request_views
    from anyfem.model.records import OutputRequest
    request=OutputRequest(("stress.global_xx_top",),"region","node",recovery="patch",
                          frame_policy="selected",frame_indices=(1,0))
    result=add_output_request_views(repeated_coordinate_payload(),(scoped(request),))
    key=result.provenance["output_request_outcomes"][0]["fields"][0]
    store=ArtifactStore(tmp_path/"repeated-frames.anyfem")
    artifact=store.write_result(job_id="frame-job",document_id="document",mesh_id="mesh",
        model_hash="model",mesh_hash="mesh",analysis_hash="analysis",**result.write_result_inputs())
    window.active_job_id="frame-job";window.result_datasets["frame-job"]=store.open_result(artifact)
    results=window.panels["Results"];results.refresh()
    index=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",key))
    results.quantities.setCurrentIndex(index)
    for local_index,source_index,label,status,value in [(0,1,"descending","fallback",-3.),(1,0,"ascending","qualified",1.)]:
        results.frame.setValue(local_index);results.inspect_quantity()
        text=results.report.toPlainText()
        assert "value 0.5" in text and f"source frame index: {source_index}" in text
        assert f"load case: {label}" in text
        assert ("fallback/unclassified nodes" in text)==(status=="fallback")
        assert results.table.values.headers==["node_id","recovery_status","global_xx_top"]
        assert results.table.values.rows==[[42,status,value]]


def test_qt_duplicate_output_labels_keep_selected_scope_on_refresh(window,tmp_path,monkeypatch):
    from test_output_request_views import payload,scoped
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.output_views import add_output_request_views
    from anyfem.model.records import OutputRequest
    requests=[OutputRequest(("displacement.uz",),"region","node",label="Same name") for _ in range(2)]
    result=add_output_request_views(payload(),(scoped(requests[0],(42,)),scoped(requests[1],(11,))))
    selected_key=result.provenance["output_request_outcomes"][1]["fields"][0]
    store=ArtifactStore(tmp_path/"duplicate-labels.anyfem")
    artifact=store.write_result(job_id="view-job",document_id="document",mesh_id="mesh",
        model_hash="model",mesh_hash="mesh",analysis_hash="analysis",**result.write_result_inputs())
    window.active_job_id="view-job";window.result_datasets["view-job"]=store.open_result(artifact)
    results=window.panels["Results"];results.refresh()
    index=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",selected_key))
    label=results.quantities.itemText(index)
    assert sum(results.quantities.itemText(i)==label for i in range(results.quantities.count()))==2
    results.quantities.setCurrentIndex(index)
    # Force a legitimate rebuild of the available-choice inventory.
    results.quantities.addItem("Removed quantity",("field","removed"))
    results.refresh()
    assert tuple(results.quantities.currentData())==("field",selected_key)
    results.inspect_quantity()
    assert results.table.values.rows[0][0]==11
    csv_path=tmp_path/"selected-scope.csv"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **kwargs:str(csv_path))
    results.export_quantity_csv()
    import csv
    with csv_path.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert {int(row["node_id"]) for row in rows}=={11}


def test_qt_selected_mode_request_form_solve_reopen_and_export(window,qapp,tmp_path,monkeypatch):
    import csv
    import numpy as np
    from test_imported_persistence import _write_sesam_plate
    from anyfem.selection import MeshEntityRef

    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    window.import_sesam_model(str(_write_sesam_plate(tmp_path/"modal.FEM")))
    window.selection.set_mode("node");window.selection.select(MeshEntityRef("node",3))
    definitions=window.panels["Definitions"]
    definitions.region_name.setText("Modal observation node")
    region=definitions.create_region()
    definitions.choice.setCurrentIndex(definitions.choice.findData("AddOutputRequest"))
    fields=definitions.record_fields["request"][1]
    for name,value in {"quantity_keys":"displacement.uz","region":region.id,"location":"node",
                       "label":"Selected modes","frame_policy":"selected","frame_indices":"[1, 0]"}.items():
        fields[name][0].setText(value)
    request=definitions.execute()
    assert request.frame_indices==(1,0)
    solve=window.panels["Solve"]
    solve.analysis.setCurrentText("Modal");solve.controls.fields["num_modes"][0].setText("2")
    solve.output_requests.item(0).setSelected(True);solve.submit.click()
    wait_until(qapp,lambda:window.solution is not None)
    job_id=window.active_job_id
    frequencies=tuple(shape.value for shape in window.solution.shapes)
    assert len(frequencies)==2 and all(value>0 for value in frequencies)
    destination=tmp_path/"selected-modes.anyfem"
    window.save_project(path=str(destination))
    dataset=window.result_datasets[job_id]
    key=dataset.metadata("provenance")["output_request_outcomes"][0]["fields"][0]
    native_ids=dataset.table("displacement_node_ids").tolist()
    native=dataset.field("displacement").read(None)
    component=dataset.field("displacement").descriptor.components.index("uz")
    expected=native[[1,0],native_ids.index(3),component]
    np.testing.assert_array_equal(dataset.field(key).read(None)[:,0,0],expected)
    assert dataset.field(key).descriptor.frames==(frequencies[1],frequencies[0])
    window.new_project();window.open_project(str(destination))
    assert window.project.output_requests[request.id].frame_indices==(1,0)
    results=window.panels["Results"];results.activate_job(job_id)
    index=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",key))
    results.quantities.setCurrentIndex(index);results.inspect_quantity()
    assert "Frame policy: selected" in results.report.toPlainText()
    csv_path=tmp_path/"selected-modes.csv"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **kwargs:str(csv_path))
    results.export_quantity_csv()
    with csv_path.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert {int(row["node_id"]) for row in rows}=={3}
    np.testing.assert_allclose([float(row["frame_value"]) for row in rows],[frequencies[1],frequencies[0]])
    # Modal eigenvectors are normalized shapes, with the persisted field's
    # unit metadata rather than an assumed physical displacement unit.
    descriptor=dataset.field(key).descriptor
    column=f"uz [{descriptor.unit}]" if descriptor.unit else "uz"
    np.testing.assert_allclose([float(row[column]) for row in rows],expected)


def test_load_edit_preserves_mesh_and_invalidates_solution(window,qapp,tmp_path):
    cantilever(window)
    window.generate_mesh_async(0.5,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    window.panels["Solve"].start()
    wait_until(qapp,lambda:window.solution is not None)
    window.save_project(path=str(tmp_path/"load-edit.anyfem"));window.flush_project_writes()
    mesh=window.mesh;job_id=window.active_job_id
    load=window.project.load_cases["default"].point_loads[0]
    window.run(cmd.EditAttribute(replace(load,force=(0,0,-2000))))
    assert window.mesh is mesh and window.solution is None
    qapp.processEvents()
    assert "Retained result (stale)" in window.panels["Results"].report.toPlainText()
    assert window._job_is_stale(window.project.jobs[job_id])
    window.panels["Solve"].refresh()
    assert window.panels["Solve"].submit.isEnabled()
    window.undo();assert window.mesh is mesh and window.solution is None
    window.run(cmd.AddPoint(3,0,0))
    assert window.mesh is None


def test_new_solve_clears_previous_result_previews(window,qapp):
    from anyfem.post.history import Series
    import numpy as np
    cantilever(window);window.generate_mesh_async(.5,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    previous=window.solution;panel=window.panels["Results"]
    panel.table.show_rows(["Previous job"],[[1]])
    panel.plot.show_series([Series("Previous job",np.array([0,1]),np.array([0,1]))])
    window.solve();wait_until(qapp,lambda:window.solution is not None and window.solution is not previous)
    assert panel.table.values.rowCount()==0 and panel.plot.series==[]


def test_qt_live_result_playback(window,qapp):
    cantilever(window)
    window.generate_mesh_async(0.25,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    window.panels["Results"].play();qapp.processEvents()
    assert window.viewport.canvas.animation_frames==len(getattr(window.solution,"shapes",()) or (window.solution,))
    window.viewport.canvas.stop_animation()
    assert not window.viewport.canvas._animation_frame_active
    window.show_geometry();qapp.processEvents()
    assert window.viewport.canvas._display_entries() is window.viewport.canvas._entries


@pytest.mark.parametrize("analysis,values",[
    ("Modal",{"num_modes":2}),
    ("Batch linear static",{}),
    ("Nonlinear static",{"num_steps":2,"max_load_factor":0.01}),
    ("Transient",{"dt":0.001,"t_end":0.002}),
    ("Arc length",{}),
    ("Buckling",{"num_modes":1}),
    ("Capacity",{"num_buckling_modes":1,"num_steps":2,"max_load_factor":0.01}),
])
def test_qt_advanced_analysis_jobs(window,qapp,analysis,values):
    cantilever(window)
    if analysis in {"Buckling","Capacity"}:
        load=window.project.load_cases["default"].point_loads[0]
        window.run(cmd.EditAttribute(replace(load,force=(-1000,0,0))))
    window.generate_mesh_async(0.5,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    panel=window.panels["Solve"];panel.analysis.setCurrentText(analysis)
    if analysis=="Arc length":panel.record_controls["control"][1].set_values({"max_steps":2,"maximum_absolute_load_factor":0.01})
    panel.controls.set_values(values);panel.start()
    job_id=window.active_job_id
    wait_until(qapp,lambda:window.solution is not None or str(getattr(window.project.jobs[job_id].status,"value",window.project.jobs[job_id].status))=="failed")
    job=window.project.jobs[job_id]
    assert str(getattr(job.status,"value",job.status))=="completed",panel.transcript.toPlainText()
    assert window.solution is not None
    window.panels["Results"].show_results();qapp.processEvents()
    result_panel=window.panels["Results"]
    assert result_panel.outcome.text()
    assert "Constitutive response" in result_panel.frame_details.text()
    assert window.submitted_input_reports[job_id] in result_panel.submitted_inputs.toPlainText()
    if analysis in {"Nonlinear static","Arc length","Capacity"}:
        assert "Last / target" in result_panel.outcome.text()


def plate(window):
    points=[window.run(cmd.AddPoint(*point)) for point in ((0,0,0),(1,0,0),(1,1,0),(0,1,0))]
    edges=[window.run(cmd.AddLine(a,b)) for a,b in zip(points,points[1:]+points[:1])]
    face=window.run(cmd.AddFace(edges));window.run(cmd.AssignPlate(face,"plate"))
    for edge in edges:window.run(cmd.AddSupport(Support(f"fixed-{edge}",window.project.geometry.entity_ref("edge",edge),dict.fromkeys(["ux","uy","uz","rx","ry","rz"],0))))
    return face


@pytest.mark.parametrize("strategy",["auto","mapped","native","quad_first"])
def test_qt_plate_mesh_routes(window,qapp,strategy):
    plate(window);panel=window.panels["Mesh"]
    panel.strategy.setCurrentText(strategy);panel.size.setText("0.25");panel.start()
    wait_until(qapp,lambda:window.mesh is not None or not window.mesh_job_running)
    assert window.mesh is not None,window._status.get()
    assert window.mesh.num_elements>0


def test_qt_inspection_only_mesh_stays_unadmitted(window,qapp,monkeypatch):
    from anyfem.model.project import Project
    original=Project.generate_mesh
    def inspection_mesh(project,*args,**kwargs):
        mesh=original(project,*args,**kwargs)
        mesh.hybrid_diagnostics["automation"]={"status":"inspection_only","reason":"deliberate unadmitted candidate"}
        return mesh
    monkeypatch.setattr(Project,"generate_mesh",inspection_mesh)
    cantilever(window);window.generate_mesh_async(.5,strategy="auto")
    record=next(reversed(window.project.mesh_records.values()))
    wait_until(qapp,lambda:record.status=="inspection_only")
    assert window.mesh is None and window._inspection_mesh is not None
    window.panels["Solve"].refresh();assert not window.panels["Solve"].submit.isEnabled()
    with pytest.raises(ValueError,match="inspection-only"):window.solve()
    assert record.summary["solver_admission"]=="BLOCKED"
    window.show_geometry();window.show_mesh();assert window._view_mode=="inspection_mesh"


def test_qt_automatic_mesh_budget_retains_incomplete_outcome(window,qapp,monkeypatch):
    from itertools import count
    import anymesher.recovery as recovery
    ticks=count(1000)
    # Exercise an expired owner deadline independently of platform clock resolution.
    monkeypatch.setattr(recovery,"monotonic",lambda:float(next(ticks)))
    plate(window)
    panel=window.panels["Mesh"]
    panel.strategy.setCurrentText("quad_first")
    panel.automation_controls.fields["max_seconds"][0].setText("1e-12")
    revision=window.project.geometry.revision
    panel.generate.click()
    record=next(reversed(window.project.mesh_records.values()))
    wait_until(qapp,lambda:not window.mesh_job_running)
    assert record.status=="incomplete", record.diagnostics
    assert window.mesh is None and window._inspection_mesh is None
    assert window.project.geometry.revision==revision
    assert record.diagnostics[-1]["type"]=="MeshRecoveryIncomplete"
    assert "time budget expired" in window._status.get()
    assert not window.panels["Solve"].submit.isEnabled()


def test_qt_automatic_mesh_with_remaining_budget_is_admitted(window,qapp,monkeypatch):
    import anymesher.recovery as recovery
    # Several budget checks may observe one tick on a coarse monotonic clock.
    monkeypatch.setattr(recovery,"monotonic",lambda:1000.0)
    plate(window)
    panel=window.panels["Mesh"]
    panel.strategy.setCurrentText("quad_first")
    panel.automation_controls.fields["max_seconds"][0].setText("1")
    panel.generate.click()
    record=next(reversed(window.project.mesh_records.values()))
    wait_until(qapp,lambda:not window.mesh_job_running)
    assert window.mesh is not None,record.diagnostics
    assert record.summary["solver_admission"]=="ADMITTED"
    assert window._inspection_mesh is None


def test_qt_strict_mesh_policy_retains_owner_refusal(window,qapp,monkeypatch):
    import anymesher.recovery as recovery
    from anymesher.s3_repair import S3RepairError
    def refuse(*args,**kwargs):
        raise S3RepairError("strict method admission refused",attempts=())
    monkeypatch.setattr(recovery,"generate_hybrid_mesh_result",refuse)
    cantilever(window)
    panel=window.panels["Mesh"]
    panel.strategy.setCurrentText("quad_first")
    panel.automation_controls.fields["strict_method"][0].setChecked(True)
    panel.generate.click()
    record=next(reversed(window.project.mesh_records.values()))
    wait_until(qapp,lambda:record.status=="failed")
    assert window.mesh is None
    assert record.diagnostics[-1]["type"]=="S3RepairError"
    assert "strict method admission refused" in window._status.get()
    assert not window.panels["Solve"].submit.isEnabled()


def test_qt_structured_preview_commit_and_undo(window,qapp):
    from test_mesh_layout_commit import _neutral_rectangle
    project,_=_neutral_rectangle();window._set_project(project)
    panel=window.panels["Mesh"]
    panel.strategy.setCurrentText("auto");panel.size.setText("0.25")
    before=len(window.project.geometry.features.records)
    panel.preview();assert window._mesh_layout_preview is not None
    window.commit_mesh_layout_preview();qapp.processEvents()
    assert len(window.project.geometry.features.records)==before+1
    window.undo();assert len(window.project.geometry.features.records)==before


def test_qt_impact_job(window,qapp):
    plate(window);window.generate_mesh_async(0.25,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    panel=window.panels["Solve"];panel.analysis.setCurrentText("Impact")
    panel.controls.set_values({"dt":1e-5,"t_end":2e-5,"strict":False})
    panel.record_controls["collision"][1].set_values({"mass":200,"radius":0.15,"start":(0.5,0.5,0.15),"speed":1})
    panel.record_controls["contact"][1].set_values({"penalty_stiffness":1e6})
    panel.start();job_id=window.active_job_id
    wait_until(qapp,lambda:window.solution is not None or str(getattr(window.project.jobs[job_id].status,"value",window.project.jobs[job_id].status))=="failed")
    assert window.solution is not None,panel.transcript.toPlainText()
    window.panels["Results"].show_results();qapp.processEvents()


def test_qt_mesh_native_import_region_and_roundtrip(window,qapp,tmp_path,monkeypatch):
    from test_imported_persistence import _write_sesam_plate
    from anyfem.selection import MeshEntityRef
    source=_write_sesam_plate(tmp_path/"import.FEM")
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    window.import_sesam_model(str(source));qapp.processEvents()
    assert window.project.mesh_only and window.mesh.num_nodes==4
    window.selection.set_mode("node");window.selection.select(MeshEntityRef("node",1))
    region=window.panels["Definitions"].create_region()
    assert region.mesh_id in window.project.mesh_records
    window.save_project(path=str(tmp_path/"imported.anyfem"))
    from anyfem.io.artifacts import ArtifactStore
    from anyfem.io.sesam import import_sesam_artifact
    record=list(window.project.mesh_records.values())[-1]
    artifact=window.project.artifacts[record.artifact_id]
    store=ArtifactStore(tmp_path/"imported.anyfem")
    assert store.read_mesh(artifact).num_nodes==4
    assert import_sesam_artifact(store,artifact).mesh.num_nodes==4
    window.new_project();window.open_project(str(tmp_path/"imported.anyfem"))
    assert window.project.mesh_only and window.mesh.num_nodes==4
    assert window.project.regions[region.id].mesh_id==region.mesh_id


def test_qt_imported_group_load_and_snapshot_solve(window,qapp,tmp_path,monkeypatch):
    from test_io import write_sesam_plate
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    window.import_sesam_model(str(write_sesam_plate(tmp_path/"plate.FEM")))
    loads=window.panels["Loads & BC"]
    loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["ref"][0].setText("group:group 1")
    loads.fields["value"][0].setText("20000");loads.execute()
    window.run(cmd.AddCombination("ULS",{"default":1.5}))
    solve=window.panels["Solve"];solve.case.setCurrentText("combination: ULS");solve.start()
    wait_until(qapp,lambda:window.solution is not None)
    assert window.solution.max_translation()[1]>0
    assert window.solution.built.project is not window.project


@pytest.mark.parametrize("component,basis", [("von_mises","element_local"), ("von_mises","local"),
    ("von_mises","element"), ("global_xx_top","global"), ("global_xx_bot","global")])
def test_qt_sample_reduced_stress_solve_reopen_inspect_export(window,qapp,tmp_path,monkeypatch,component,basis):
    import csv
    import numpy as np
    from test_io import write_sesam_plate, sesam_record
    from anyfem.selection import MeshEntityRef
    from anyfem.model.records import OutputRequest
    from anyfem.post.fields import _reduce
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    source=write_sesam_plate(tmp_path/"plate.FEM")
    if basis=="global":
        lines=source.read_text().splitlines()
        for index,line in enumerate(lines):
            if line.startswith("GCOORD"):
                node=int(line[8:24]);x=float(line[24:40]);y=float(line[40:56]);z=float(line[56:72])
                lines[index]=sesam_record("GCOORD",node,(x-y)/np.sqrt(2),(x+y)/np.sqrt(2),z)
        source.write_text("\n".join(lines)+"\n")
    window.import_sesam_model(str(source))
    loads=window.panels["Loads & BC"]
    loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["ref"][0].setText("group:group 1")
    loads.fields["value"][0].setText("20000");loads.execute()
    element_id=next(iter(window.mesh.shells))
    window.selection.set_mode("element");window.selection.select(MeshEntityRef("element",element_id))
    region=window.panels["Definitions"].create_region()
    request=OutputRequest((f"stress.{component}",),region.id,"element",basis=basis,reduction="mean")
    window.run(cmd.AddOutputRequest(request))
    solve=window.panels["Solve"];solve.output_requests.item(0).setSelected(True);solve.start()
    wait_until(qapp,lambda:window.solution is not None)
    job_id=window.active_job_id
    path=tmp_path/"reduced-stress.anyfem";window.save_project(path=str(path))
    dataset=window.result_datasets[job_id]
    outcome=dataset.metadata("provenance")["output_request_outcomes"][0]
    assert outcome["status"]=="available",outcome
    key=outcome["fields"][0]
    source=dataset.field(key).descriptor.provenance["source_quantity"]
    raw=dataset.field(source).read(0)
    ids=dataset.table(f"{source}_element_ids").tolist()
    expected=_reduce(raw[ids.index(element_id)],"mean")
    np.testing.assert_allclose(dataset.field(key).read(0)[0,0],expected)
    assert abs(expected)>0
    if basis=="global":
        recovered=window.solution._requested_global_stress
        assert recovered.provenance.return_global
        assert not window.solution._stress.provenance.return_global
        np.testing.assert_allclose(expected,_reduce(recovered.element_stresses[element_id][component],"mean"))
    window.new_project();window.open_project(str(path))
    results=window.panels["Results"];results.activate_job(job_id)
    index=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",key))
    results.quantities.setCurrentIndex(index);results.inspect_quantity()
    assert "Reduction: mean" in results.report.toPlainText()
    assert f"Basis: {'element_local' if basis in {'local','element'} else basis}" in results.report.toPlainText()
    assert window.project.output_requests[request.id].basis==basis
    destination=tmp_path/"reduced.csv"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **kwargs:str(destination))
    results.export_quantity_csv()
    with destination.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert len(rows)==1 and int(rows[0]["element_id"])==element_id
    np.testing.assert_allclose(float(rows[0][f"{component} [Pa]"]),expected)


@pytest.mark.parametrize("component,basis", [("von_mises","element_local"), ("global_xx_top","global"), ("global_xx_bot","global")])
def test_qt_batch_requested_stresses_keep_selected_case_names(window,qapp,tmp_path,monkeypatch,component,basis):
    import csv
    import numpy as np
    from test_io import write_sesam_plate
    from anyfem.selection import MeshEntityRef
    from anyfem.model.records import OutputRequest
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    window.import_sesam_model(str(write_sesam_plate(tmp_path/"batch.FEM")))
    loads=window.panels["Loads & BC"]
    loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["ref"][0].setText("group:group 1")
    loads.fields["value"][0].setText("20000");loads.execute()
    window.run(cmd.AddLoadCase("live"))
    loads.fields["case"][0].setText("live")
    loads.fields["value"][0].setText("40000");loads.execute()
    element_id=next(iter(window.mesh.shells))
    window.selection.set_mode("element");window.selection.select(MeshEntityRef("element",element_id))
    region=window.panels["Definitions"].create_region()
    request=OutputRequest((f"stress.{component}",),region.id,"element",basis=basis,
        reduction="mean",frame_policy="selected",frame_indices=(1,0))
    window.run(cmd.AddOutputRequest(request))
    solve=window.panels["Solve"];solve.analysis.setCurrentText("Batch linear static")
    solve.output_requests.item(0).setSelected(True);solve.start()
    wait_until(qapp,lambda:window.solution is not None)
    assert window.solution.case_names==("default","live")
    assert all(shape._stress is not None for shape in window.solution.shapes)
    job_id=window.active_job_id
    path=tmp_path/"batch-stress.anyfem";window.save_project(path=str(path))
    dataset=window.result_datasets[job_id]
    outcome=dataset.metadata("provenance")["output_request_outcomes"][0]
    assert outcome["status"]=="available",outcome
    key=outcome["fields"][0]
    values=dataset.field(key).read(None)[:,0,0]
    assert abs(values[1])>0
    if component=="von_mises":assert values[1]>0
    np.testing.assert_allclose(values[0],2*values[1])
    assert dataset.field(key).descriptor.provenance["frame_labels"]==["live","default"]
    window.new_project();window.open_project(str(path))
    results=window.panels["Results"];results.activate_job(job_id)
    index=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",key))
    results.quantities.setCurrentIndex(index);results.frame.setValue(0);results.inspect_quantity()
    assert "load case: live" in results.report.toPlainText()
    destination=tmp_path/"batch.csv"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **kwargs:str(destination))
    results.export_quantity_csv()
    with destination.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert [row["frame_label"] for row in rows]==["live","default"]
    assert [float(row["frame_value"]) for row in rows]==[1.,0.]
    np.testing.assert_allclose([float(row[f"{component} [Pa]"]) for row in rows],values)


@pytest.mark.parametrize("batch", [False,True])
@pytest.mark.parametrize("fallback", [False,True])
def test_qt_patch_recovery_keeps_owner_qualification_through_export(window,qapp,tmp_path,monkeypatch,batch,fallback):
    import csv
    import numpy as np
    import anysolver
    from test_io import write_sesam_plate
    from anyfem.selection import MeshEntityRef
    from anyfem.model.records import OutputRequest
    if fallback:
        config=anysolver.PatchRecoveryConfig
        # Tighten the owner guard to exercise its real continuity-preserving
        # fallback; do not weaken qualification or substitute application values.
        monkeypatch.setattr(anysolver,"PatchRecoveryConfig",lambda:config(condition_limit=1.01))
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    window.import_sesam_model(str(write_sesam_plate(tmp_path/"patch.FEM")))
    loads=window.panels["Loads & BC"];loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["ref"][0].setText("group:group 1");loads.fields["value"][0].setText("20000");loads.execute()
    if batch:
        window.run(cmd.AddLoadCase("live"));loads.fields["case"][0].setText("live")
        loads.fields["value"][0].setText("40000");loads.execute()
    window.selection.set_mode("node");window.selection.select(MeshEntityRef("node",6))
    region=window.panels["Definitions"].create_region()
    request=OutputRequest(("stress.von_mises",),region.id,"node",recovery="patch",
        frame_policy="selected" if batch else "all",frame_indices=(1,0) if batch else ())
    window.run(cmd.AddOutputRequest(request))
    solve=window.panels["Solve"]
    if batch:solve.analysis.setCurrentText("Batch linear static")
    solve.output_requests.item(0).setSelected(True);solve.start()
    wait_until(qapp,lambda:window.solution is not None)
    solutions=window.solution.shapes if batch else [window.solution]
    expected=[solution._requested_patch_stress.nodal_stresses["nodal"][6]["von_mises"] for solution in solutions]
    if batch:expected=expected[::-1]
    status="fallback" if fallback else "qualified"
    for solution in solutions:
        assert solution._requested_patch_stress.nodal_stresses["node_diagnostics"][6]["status"]==status
        assert not solution._stress.provenance.return_global
    job_id=window.active_job_id;path=tmp_path/"patch.anyfem";window.save_project(path=str(path))
    dataset=window.result_datasets[job_id]
    outcome=dataset.metadata("provenance")["output_request_outcomes"][0]
    assert outcome["status"]==("partial" if fallback else "available"),outcome
    key=outcome["fields"][0]
    np.testing.assert_array_equal(dataset.field(key).read(None)[:,0,0],expected)
    window.new_project();window.open_project(str(path))
    results=window.panels["Results"];results.activate_job(job_id)
    index=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",key))
    results.quantities.setCurrentIndex(index);results.inspect_quantity()
    assert "Recovery: patch" in results.report.toPlainText()
    if fallback:assert "fallback/unclassified nodes: [6]" in results.report.toPlainText()
    destination=tmp_path/"patch.csv";monkeypatch.setattr(window.dialogs,"save_file",lambda **kwargs:str(destination))
    results.export_quantity_csv()
    with destination.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert [row["recovery_status"] for row in rows]==[status]*len(expected)
    np.testing.assert_array_equal([float(row["von_mises [Pa]"]) for row in rows],expected)
    assert {int(row["node_id"]) for row in rows}=={6}
    native_index=next(i for i in range(results.quantities.count())
                      if tuple(results.quantities.itemData(i))==("field","stress_patch_von_mises"))
    results.quantities.setCurrentIndex(native_index);results.inspect_quantity()
    assert "recovery_status" in results.table.values.headers
    assert next(row for row in results.table.values.rows if row[0]==6)[1]==status
    if fallback:assert "fallback/unclassified nodes:" in results.report.toPlainText()


@pytest.mark.parametrize("kind",["native","global","patch"])
@pytest.mark.parametrize("kinematics",["von_karman","corotational"])
def test_qt_committed_nonlinear_stress_frames_solve_reopen_export(window,qapp,tmp_path,monkeypatch,kind,kinematics):
    import csv,numpy as np
    from test_io import write_sesam_plate
    from anyfem.selection import MeshEntityRef
    from anyfem.model.records import OutputRequest
    from anysolver import recover_stress_result,PatchRecoveryConfig
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    window.import_sesam_model(str(write_sesam_plate(tmp_path/"increments.FEM")))
    loads=window.panels["Loads & BC"];loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["ref"][0].setText("group:group 1");loads.fields["value"][0].setText("20000");loads.execute()
    patch=kind=="patch";entity=6 if patch else next(iter(window.mesh.shells))
    window.selection.set_mode("node" if patch else "element")
    window.selection.select(MeshEntityRef("node" if patch else "element",entity))
    region=window.panels["Definitions"].create_region()
    component="von_mises" if kind=="native" else "global_xx_top"
    request=OutputRequest((f"stress.{component}",),region.id,"node" if patch else "element",
        basis="element_local" if kind=="native" else "global",recovery="patch" if patch else "native",
        reduction="none" if patch else "max",frame_policy="selected",frame_indices=(1,0))
    window.run(cmd.AddOutputRequest(request))
    solve=window.panels["Solve"];solve.analysis.setCurrentText("Nonlinear static")
    solve.controls.set_values({"num_steps":2,"max_load_factor":.01,
        "record_increment_snapshots":True,"kinematics":kinematics})
    solve.output_requests.item(0).setSelected(True);solve.start()
    job_id=window.active_job_id
    wait_until(qapp,lambda:window.solution is not None or str(getattr(window.project.jobs[job_id].status,"value",window.project.jobs[job_id].status))=="failed")
    assert window.solution is not None,solve.transcript.toPlainText()
    snapshots=window.solution.shapes;assert len(snapshots)>=2
    expected=[]
    for index in (1,0):
        shape=snapshots[index]
        options={"patch_config":PatchRecoveryConfig()} if patch else ({"return_global":True} if kind=="global" else {})
        owner=recover_stress_result(shape.built.fe_model,shape.displacements,
            nonlinear_result=shape.raw_result,kinematics=kinematics,**options)
        assert shape._stress.provenance.analysis_context["kinematics"]==kinematics
        expected.append(owner.nodal_stresses["nodal"][entity][component] if patch
                        else float(np.max(owner.element_stresses[entity][component])))
    path=tmp_path/"increments.anyfem";window.save_project(path=str(path))
    dataset=window.result_datasets[job_id]
    outcome=dataset.metadata("provenance")["output_request_outcomes"][0]
    assert outcome["status"] in {"available","partial"},outcome
    key=outcome["fields"][0];descriptor=dataset.field(key).descriptor
    assert descriptor.frames==tuple(snapshots[index].value for index in (1,0))
    assert descriptor.provenance["step_indices"]==[snapshots[index].raw_result.step_index for index in (1,0)]
    assert [entry["analysis_context"]["load_factor"] for entry in descriptor.provenance["frame_recovery"]]==list(descriptor.frames)
    np.testing.assert_array_equal(dataset.field(key).read(None)[:,0,0],expected)
    window.new_project();window.open_project(str(path))
    results=window.panels["Results"];results.activate_job(job_id)
    choice=next(i for i in range(results.quantities.count()) if tuple(results.quantities.itemData(i))==("field",key))
    results.quantities.setCurrentIndex(choice);results.frame.setValue(0);results.inspect_quantity()
    assert "source frame index: 1" in results.report.toPlainText()
    destination=tmp_path/"increments.csv";monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(destination))
    results.export_quantity_csv()
    with destination.open(newline="") as stream:rows=list(csv.DictReader(stream))
    assert [int(row["source_frame_index"]) for row in rows]==[1,0]
    np.testing.assert_array_equal([float(row[f"{component} [Pa]"]) for row in rows],expected)


def test_qt_patch_request_validation_blocks_submit_before_job_creation(window,qapp,tmp_path,monkeypatch):
    from test_io import write_sesam_plate
    from anyfem.selection import MeshEntityRef
    from anyfem.model.records import OutputRequest
    window.import_sesam_model(str(write_sesam_plate(tmp_path/"invalid-patch.FEM")))
    window.selection.set_mode("node");window.selection.select(MeshEntityRef("node",6))
    region=window.panels["Definitions"].create_region()
    window.run(cmd.AddOutputRequest(OutputRequest(("stress.von_mises",),region.id,"node",
                                                 recovery="patch",basis="element_local")))
    solve=window.panels["Solve"];solve.output_requests.item(0).setSelected(True)
    before=set(window.project.analyses);solve.submit.click();qapp.processEvents()
    assert "global basis" in window.statusBar().currentMessage()
    assert set(window.project.analyses)==before and not window.project.jobs


def test_qt_sesam_stress_only_result_roundtrip(window,qapp,tmp_path):
    from test_interop_results import plate as imported_plate,SHELL_SIF
    window._set_project(imported_plate())
    window.generate_mesh_async(1.0,strategy="mapped")
    wait_until(qapp,lambda:window.mesh is not None)
    element_id=next(iter(window.mesh.shells))
    sif="\n".join(line.replace("100",f"{element_id:3d}",1) if line.startswith(("GELMNT1","GELREF1","RVSTRESS")) else line for line in SHELL_SIF.splitlines())+"\n"
    path=tmp_path/"shell.SIF";path.write_text(sif)
    window.import_sesam_result(str(path));qapp.processEvents()
    solution=window.solution;job_id=window.active_job_id
    assert solution is not None and "magnitude" not in solution.available_fields()
    panel=window.panels["Results"];assert panel.field_name() in solution.available_fields()
    panel.show_results()
    window.save_project(path=str(tmp_path/"sesam-result.anyfem"));window.flush_project_writes()
    window.new_project();window.open_project(str(tmp_path/"sesam-result.anyfem"))
    panel.activate_job(job_id);qapp.processEvents()
    assert panel.field_name()!="magnitude"
    assert job_id in window.result_datasets


def test_qt_deck_and_calculix_result_import(window,qapp,tmp_path):
    from test_interop_results import write_frd
    cantilever(window);window.generate_mesh_async(0.25,strategy="auto")
    wait_until(qapp,lambda:window.mesh is not None)
    window.solve();wait_until(qapp,lambda:window.solution is not None)
    shape=window.current_shape();built=shape.built
    deck=tmp_path/"model.inp";window.export_deck(str(deck))
    assert "*NODE" in deck.read_text().upper()
    path=write_frd(tmp_path/"model.frd",window.mesh,built,shape.displacements)
    window.import_calculix_result(str(path));qapp.processEvents()
    assert window.solution is not None
    imported_job=window.active_job_id
    assert window.project.jobs[imported_job].name.startswith("Imported")
    window.panels["Results"].refresh()
    assert "unavailable from imported result" in window.panels["Results"].frame_details.text()
    window.save_project(path=str(tmp_path/"external.anyfem"));window.flush_project_writes()
    window.new_project();window.open_project(str(tmp_path/"external.anyfem"))
    assert imported_job in window.result_datasets
    window.panels["Results"].activate_job(imported_job);qapp.processEvents()


@pytest.mark.parametrize("format",["sesam-stress","calculix-displacement"])
def test_qt_imported_mesh_external_result_portable_roundtrip(window,qapp,tmp_path,monkeypatch,format):
    import csv
    import numpy as np
    from test_interop_results import SHELL_SIF,write_frd
    source=tmp_path/"source.FEM"
    if format=="sesam-stress":
        source.write_text("\n".join(line for line in SHELL_SIF.splitlines() if not line.startswith("RVSTRESS"))+"\n")
        window.import_sesam_model(str(source))
        result_path=tmp_path/"external.SIF";result_path.write_text(SHELL_SIF)
        window.import_sesam_result(str(result_path))
        assert "magnitude" not in window.solution.available_fields()
        assert not window.solution.components
        assert window.solution.results.element_stresses
    else:
        from test_io import write_sesam_plate
        window.import_sesam_model(str(write_sesam_plate(source)))
        loads=window.panels["Loads & BC"]
        loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
        loads.fields["ref"][0].setText("group:group 1")
        loads.fields["value"][0].setText("20000");loads.execute()
        window.solve();wait_until(qapp,lambda:window.solution is not None)
        shape=window.current_shape()
        deck=tmp_path/"external.inp";window.export_deck(str(deck))
        assert "*NODE" in deck.read_text().upper()
        assert "*ELEMENT" in deck.read_text().upper()
        result_path=write_frd(tmp_path/"external.frd",window.mesh,shape.built,shape.displacements)
        window.import_calculix_result(str(result_path))
        assert window.solution.components==frozenset(("ux","uy","uz"))
        with pytest.raises(KeyError,match="does not store"):window.solution.component("rx")
        assert window.solution.max_translation()[1]>0
    qapp.processEvents()
    job_id=window.active_job_id
    assert window.project.jobs[job_id].name.startswith("Imported")
    assert window.solution.built.project is window.project
    path=tmp_path/"portable-external.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    dataset=window.result_datasets[job_id]
    if format=="sesam-stress":
        for name,field in window.solution.fields.items():
            values=[field.values[identifier] for identifier in sorted(field.values)]
            np.testing.assert_array_equal(dataset.field(f"stress_{name}").read(0)[:,0],values)
    else:
        values=[window.solution.results.displacements[identifier] for identifier in sorted(window.solution.results.displacements)]
        np.testing.assert_array_equal(dataset.field("displacement").read(0),values)
    expected={key:dataset.field(key).read(0).copy() for key in dataset.field_keys}
    assert expected and any(np.any(values!=0) for values in expected.values())
    if format=="sesam-stress":assert "displacement" not in expected
    else:assert dataset.field("displacement").descriptor.components==("ux","uy","uz")
    window.panels["Results"].show_results();qapp.processEvents()
    window.new_project()
    # Retained answers and embedded model must survive missing source files.
    source.unlink();result_path.unlink()
    window.open_project(str(path));qapp.processEvents()
    panel=window.panels["Results"];panel.activate_job(job_id);qapp.processEvents()
    retained=window.result_datasets[job_id]
    assert set(retained.field_keys)==set(expected)
    if format=="sesam-stress":
        assert "Deformation unavailable (undeformed display)" in panel.report.toPlainText()
        assert panel.scale_value(window.solution)==1
    for key,values in expected.items():np.testing.assert_array_equal(retained.field(key).read(0),values)
    if format=="sesam-stress":assert panel.field_name()!="magnitude"
    else:assert retained.field("displacement").descriptor.components==("ux","uy","uz")
    key=next(iter(expected));panel.field.setCurrentText(key);panel.show_results();qapp.processEvents()
    csv_path=tmp_path/"external.csv"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(csv_path))
    panel.export_csv()
    rows=list(csv.DictReader(csv_path.read_text().splitlines()))
    assert rows
    descriptor=retained.field(key).descriptor
    headers=[f"{component} [{descriptor.unit}]" if descriptor.unit else component for component in descriptor.components]
    np.testing.assert_array_equal([[float(row[header]) for header in headers] for row in rows],expected[key])
    kind="node" if descriptor.location=="node" else "element"
    assert [int(row[f"{kind}_id"]) for row in rows]==retained.table(f"{key}_{kind}_ids").tolist()
    window.viewport.capture_png(tmp_path/"external.png")
    assert (tmp_path/"external.png").stat().st_size>100


def test_qt_imported_transient_retained_playback_reports_and_gif(window,qapp,tmp_path,monkeypatch):
    import numpy as np
    from PIL import Image
    from test_io import write_sesam_plate
    window.import_sesam_model(str(write_sesam_plate(tmp_path/"transient.FEM")))
    mesh_id=window.mesh_record_id
    mesh_record=window.project.mesh_records[mesh_id]
    assert mesh_record.kind=="imported" and mesh_record.mesh_hash
    assert not mesh_record.artifact_id
    loads=window.panels["Loads & BC"]
    loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["ref"][0].setText("group:group 1")
    loads.fields["value"][0].setText("20000");loads.execute()
    solve=window.panels["Solve"];solve.analysis.setCurrentText("Transient")
    solve.controls.set_values({"dt":0.001,"t_end":0.003});solve.start()
    wait_until(qapp,lambda:window.solution is not None)
    solution=window.solution;job_id=window.active_job_id
    assert window.project.jobs[job_id].mesh_hash==mesh_record.mesh_hash
    assert len(solution.shapes)>1
    expected=np.stack([np.column_stack([shape.component(name) for name in ("ux","uy","uz","rx","ry","rz")]) for shape in solution.shapes])
    panel=window.panels["Results"];panel.history()
    assert panel.plot.series
    assert panel.playback_fps()==4
    for fps,interval in (("0.5",2000),("2",500),("8",125)):
        panel.playback_speed.setCurrentText(fps);panel.play()
        assert window.viewport.canvas._animation_after_id.interval()==interval
        assert window.viewport.canvas.animation_frames==len(solution.shapes)
        window.viewport.canvas.stop_animation()
    np.testing.assert_array_equal(np.stack([np.column_stack([shape.component(name) for name in ("ux","uy","uz","rx","ry","rz")]) for shape in solution.shapes]),expected)
    path=tmp_path/"transient.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    window.new_project();window.open_project(str(path));qapp.processEvents()
    panel.activate_job(job_id);qapp.processEvents()
    dataset=window.result_datasets[job_id]
    assert dataset.identity["mesh_id"]==mesh_id
    assert dataset.identity["mesh_hash"]==mesh_record.mesh_hash
    assert dataset.field("displacement").descriptor.frames==tuple(shape.value for shape in solution.shapes)
    for index in range(len(solution.shapes)):
        np.testing.assert_array_equal(dataset.field("displacement").read(index),expected[index])
    retained=window.retained_result_mesh(job_id)
    window.mesh=None
    for fps,interval in (("0.5",2000),("2",500),("8",125)):
        panel.playback_speed.setCurrentText(fps);panel.play()
        assert window.viewport.canvas._animation_after_id.interval()==interval
        assert window.viewport.canvas.animation_frames==len(solution.shapes)
        window.viewport.canvas.stop_animation()
    for index in range(len(solution.shapes)):
        np.testing.assert_array_equal(dataset.field("displacement").read(index),expected[index])
    assert window.mesh is None and window.retained_result_mesh(job_id) is retained
    for suffix in ("md","html"):
        report=tmp_path/f"transient.{suffix}"
        monkeypatch.setattr(window.dialogs,"save_file",lambda report=report,**_:str(report))
        panel.export_report()
        assert "displacement" in report.read_text().lower()
    gif=tmp_path/"transient.gif"
    monkeypatch.setattr(window.dialogs,"save_file",lambda **_:str(gif))
    panel.export_gif();wait_until(qapp,lambda:gif.exists())
    with Image.open(gif) as image:
        assert image.format=="GIF" and min(image.size)>0


def test_qt_combination_target_and_appearance_reset(window,qapp):
    window.run(cmd.AddLoadCase("default"))
    window.run(cmd.AddCombination("ULS",{"default":1.5}))
    panel=window.panels["Solve"];panel.case.setCurrentText("combination: ULS")
    assert panel.settings()["combination"]=="ULS"
    visual=window.panels["Visualization"]
    visual.appearance["background"].setText("#112233");visual.apply();visual.reset()
    assert visual.appearance["background"].text()==window.viewport.visualization.background


def test_qt_edit_undo_redo_and_selection(window,qapp):
    panel=window.panels["Geometry"]
    for key,value in {"x":"1","y":"2","z":"3"}.items():panel.fields[key][0].setText(value)
    identifier=panel.execute()
    ref=window.project.geometry.entity_ref("vertex",identifier)
    window.selection.select(ref);qapp.processEvents()
    assert window.tree.selectionModel().selectedRows()
    window.undo();assert not window.project.geometry.vertices
    window.redo();assert identifier in window.project.geometry.vertices


def test_qt_legacy_project_upgrade_keeps_entity_identity(window,qapp,tmp_path):
    import json
    from anyfem.io.project_file import FORMAT_VERSION
    path=tmp_path/"legacy.anyfem"
    path.write_text(json.dumps({"anyfem":{"format":2},"name":"legacy geometry",
        "geometry":{"vertices":[{"id":1,"position":[0.,0.,0.]},
            {"id":2,"position":[1.,0.,0.]}],
            "edges":[{"id":1,"start":1,"end":2,"curve":{"kind":"line"}}],
            "faces":[],"next_id":{"vertex":3,"edge":2,"face":1}}}),encoding="utf-8")
    window.open_project(str(path));qapp.processEvents()
    expected={("vertex",1),("vertex",2),("edge",1)}
    assert window.project.geometry.entity_keys()==expected
    edge=window.project.geometry.entity_ref("edge",1)
    window.selection.set_mode("edge");window.selection.select(edge);qapp.processEvents()
    assert window.tree.selectionModel().selectedRows()
    point=window.run(cmd.AddPoint(2,0,0))
    assert point==3
    window.commands.undo();assert window.project.geometry.entity_keys()==expected
    window.commands.redo();assert ("vertex",3) in window.project.geometry.entity_keys()
    upgraded=tmp_path/"upgraded.anyfem";window.save_project(path=str(upgraded))
    assert json.loads(upgraded.read_text(encoding="utf-8"))["anyfem"]["format"]==FORMAT_VERSION
    window.new_project();window.open_project(str(upgraded));qapp.processEvents()
    assert window.project.geometry.entity_keys()==expected|{("vertex",3)}
    assert window.project.geometry.entity_ref("edge",1)==edge
    assert window.project.geometry.id_state()["vertex"]>=4
    window.viewport.capture_png(tmp_path/"upgraded.png")


@pytest.mark.parametrize("payload,diagnostic",[
    ("{not json","not valid JSON"),
    ('{"name":"missing header"}',"format header"),
    ('{"anyfem":{"format":99}}',"upgrade ANYfem"),
])
def test_qt_failed_open_preserves_dirty_document_and_owned_lock(window,qapp,tmp_path,monkeypatch,payload,diagnostic):
    from anyfem.io.recovery import ProjectLock
    path=tmp_path/"current.anyfem";window.save_project(path=str(path))
    point=window.run(cmd.AddPoint(2,3,4))
    ref=window.project.geometry.entity_ref("vertex",point)
    window.selection.select(ref);qapp.processEvents()
    project=window.project;stack=window.commands;revision=window.session.revision
    held_lock=window._project_lock
    malformed=tmp_path/"rejected.anyfem";malformed.write_text(payload,encoding="utf-8")
    monkeypatch.setattr(window,"_confirm_discard",lambda:True)
    monkeypatch.setattr(window.dialogs,"open_file",lambda **_:str(malformed))
    window._actions["Open"].trigger();qapp.processEvents()
    assert diagnostic in window.statusBar().currentMessage()
    assert diagnostic in window.log.toPlainText()
    assert window._error_diagnostics
    assert window.project is project and window.commands is stack
    assert window.session.revision==revision and window.session.dirty
    assert window.selection.items==[ref]
    assert window.path==path and window._project_lock is held_lock
    assert not ProjectLock(malformed).path.exists()
    contender=ProjectLock(path);assert not contender.acquire().acquired
    window.commands.undo();assert point not in window.project.geometry.vertices
    window.commands.redo();assert point in window.project.geometry.vertices


def test_qt_locked_project_save_as(window,qapp,tmp_path):
    from anyfem.io.recovery import ProjectLock
    from anyfem.io.project_file import save_project
    path=tmp_path/"locked.anyfem";save_project(window.project,path)
    lock=ProjectLock(path);decision=lock.acquire()
    assert not decision.read_only
    try:
        window.open_project(str(path))
        assert window.session.read_only
        with pytest.raises(PermissionError):window.save_project(path=str(path))
        window.save_project(path=str(tmp_path/"copy.anyfem"))
        assert not window.session.read_only
    finally:lock.release()


def test_replacement_keeps_lock_until_pending_writer_finishes(window,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from anyfem.io.recovery import ProjectLock
    path=tmp_path/"owned.anyfem"
    window.save_project(path=str(path))
    decisions=[]
    def writer():
        contender=ProjectLock(path)
        decision=contender.acquire()
        decisions.append(decision.read_only)
        if decision.acquired:contender.release()
        (tmp_path/"writer-finished").write_text("finished")
        return object()
    with ThreadPoolExecutor(max_workers=1) as executor:
        window._recovery_future=executor.submit(writer)
        window.new_project()
    assert decisions==[True]
    assert (tmp_path/"writer-finished").exists()
    lock=ProjectLock(path)
    assert lock.acquire().acquired
    lock.release()


def test_qt_script_commits_and_invalid_input_preserves_document(window,qapp):
    editor=window.panels["Geometry"]
    editor.fields["x"][0].setText("invalid")
    with pytest.raises(ValueError):editor.execute()
    assert not window.project.geometry.vertices
    script=window.panels["Scripts"]
    script.source.setPlainText("commands.run(commands.AddPoint(1, 2, 3))")
    script.start()
    wait_until(qapp,lambda:script.task is None)
    assert len(window.project.geometry.vertices)==1
    window.undo()
    assert not window.project.geometry.vertices


def test_open_menu_preserves_dirty_document(window,monkeypatch):
    window.run(cmd.AddPoint(1,0,0))
    monkeypatch.setattr(window.dialogs,"confirm_save",lambda *args:None)
    monkeypatch.setattr(window.dialogs,"open_file",lambda **kwargs:pytest.fail("file chooser should not run after cancel"))
    window._actions["Open"].trigger()
    assert len(window.project.geometry.vertices)==1


def test_qt_workplane_construction_commits_once_and_cancels(window,qapp):
    panel=window.panels["Construction"]
    panel.mode.setCurrentText("line");panel.start()
    for point in ("0,0","2,0"):
        panel.coordinates.setText(point);panel.add_point()
    assert not window.project.geometry.vertices
    panel.apply();qapp.processEvents()
    assert len(window.project.geometry.edges)==1
    window.undo();assert not window.project.geometry.edges
    panel.start();panel.coordinates.setText("5,0");panel.add_point()
    window.viewport.cancel_construction();assert not window.project.geometry.vertices


@pytest.mark.parametrize("explicit", [False, True])
def test_qt_construction_lengths_follow_project_units(window,qapp,tmp_path,explicit):
    from anyfem.model.units import UNIT_PROFILES
    window.run(cmd.SetUnitProfile(UNIT_PROFILES["SI-mm-N-MPa"]))
    panel=window.panels["Construction"]
    def length(value):return f"{value} mm" if explicit else str(value)
    panel.offset.setText(length(250));panel.spacing.setText(length(100))
    panel.tolerance.setText(length(5))
    plane=panel.workplane()
    assert (plane.offset,plane.grid_spacing,plane.snap_tolerance)==pytest.approx((.25,.1,.005))
    panel.offset.setText("0");panel.tolerance.setText("0.1 mm")
    points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(cmd.AddPlate(points))
    window.selection.set_mode("face");window.selection.select(window.project.geometry.entity_ref("face",face))
    panel.sketch()
    for u,v in ((500,500),(1500,500),(1500,1500),(500,1500)):
        panel.coordinates.setText(f"{length(u)}, {length(v)}");panel.add_point()
    task=window.viewport.construction_task
    assert task.points[0]==pytest.approx((.5,.5,0))
    panel.pair.setText("1,2");panel.distance.setText(length(1000))
    panel.extrusion.setText(length(250));panel.add_constraint("distance")
    panel.apply();qapp.processEvents()
    feature=window.project.geometry.features.records[-1]
    assert feature.parameters["extrusion"]==pytest.approx(.25)
    assert feature.parameters["constraints"][0]["value"]==pytest.approx(1)
    assert panel.edit_sketch(feature.feature_id)
    assert window.project.units.parse(panel.extrusion.text(),"length")==pytest.approx(.25)
    panel.apply()
    assert window.project.geometry.features.get(feature.feature_id).parameters["extrusion"]==pytest.approx(.25)
    path=tmp_path/"millimetre-sketch.anyfem";window.save_project(path=str(path))
    window.flush_project_writes();window.open_project(str(path))
    reopened=window.project.geometry.features.get(feature.feature_id)
    assert window.project.units.symbol("length")=="mm"
    assert reopened.parameters["extrusion"]==pytest.approx(.25)
    assert reopened.parameters["constraints"][0]["value"]==pytest.approx(1)


def test_qt_sketch_extrusion_selects_outputs_for_follow_on_load(window,qapp):
    points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(cmd.AddPlate(points))
    window.selection.set_mode("face");window.selection.select(window.project.geometry.entity_ref("face",face))
    panel=window.panels["Construction"];panel.sketch()
    for point in ("0.5,0.5","1.5,0.5","1.5,1.5","0.5,1.5"):
        panel.coordinates.setText(point);panel.add_point()
    panel.extrusion.setText("0.2");panel.apply();qapp.processEvents()
    feature=window.project.geometry.features.records[-1]
    made=tuple(ref for key,ref in feature.outputs.items() if key.startswith("extrusion/face/"))
    assert made and set(window.selection.ordered_items)==set(made)
    assert panel.edit_sketch(feature.feature_id)
    panel.extrusion.setText("0.3");panel.apply();qapp.processEvents()
    updated=window.project.geometry.features.get(feature.feature_id)
    made=tuple(ref for key,ref in updated.outputs.items() if key.startswith("extrusion/face/"))
    assert set(window.selection.ordered_items)==set(made)
    window.selection.restore((made[0],))
    loads=window.panels["Loads & BC"];loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["value"][0].setText("1000")
    load=loads.execute()
    assert load.ref==made[0]
    window.undo();window.undo();window.redo()
    assert window.project.geometry.features.get(feature.feature_id).parameters["extrusion"]==pytest.approx(.3)


def test_qt_combined_sketch_constraints_keep_boundary_and_identity(window,qapp,tmp_path):
    points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(cmd.AddPlate(points))
    window.selection.set_mode("face");window.selection.select(window.project.geometry.entity_ref("face",face))
    panel=window.panels["Construction"];panel.sketch();panel.closed.setChecked(False)
    for point in ("0,0","1,0","1,1","0,0"):
        panel.coordinates.setText(point);panel.add_point()
    panel.pair.setText("1,2");panel.distance.setText("1");panel.add_constraint("distance")
    panel.pair.setText("1,4");panel.add_constraint("coincident")
    panel.extrusion.setText("0.2");panel.apply();qapp.processEvents()
    feature=window.project.geometry.features.records[-1]
    definition=feature.parameters.copy()
    assert {item["kind"] for item in definition["constraints"]}=={"on_vertex","on_edge","distance","coincident"}
    assert not definition["closed"]
    assert feature.outputs["point/p1"]==feature.outputs["point/p4"]
    made={key:ref for key,ref in feature.outputs.items() if key.startswith("extrusion/face/")}
    assert len(made)==3
    assert panel.edit_sketch(feature.feature_id)
    panel.extrusion.setText("0.3");panel.apply()
    window.undo();assert window.project.geometry.features.get(feature.feature_id).parameters==definition
    window.redo();updated=window.project.geometry.features.get(feature.feature_id)
    assert updated.parameters["constraints"]==definition["constraints"]
    assert updated.outputs["point/p1"]==updated.outputs["point/p4"]
    path=tmp_path/"combined-sketch.anyfem";window.save_project(path=str(path));window.flush_project_writes()
    window.open_project(str(path))
    reopened=window.project.geometry.features.get(feature.feature_id)
    assert reopened.parameters==updated.parameters
    assert reopened.outputs==updated.outputs


def test_qt_dependent_feature_edit_preserves_engineering_attachments(window,qapp,tmp_path):
    import json
    generators=window.panels["Generators"];generators.kind.setCurrentText("Plate")
    generators.form.fields["length"][0].setText("3");generators.form.fields["width"][0].setText("3")
    parent=generators.execute()
    support=next(ref for ref in parent.outputs.values() if ref.kind=="face")
    window.selection.set_mode("face");window.selection.restore((support,))
    construction=window.panels["Construction"];construction.sketch()
    for point in ("0.5,0.5","1.5,0.5","1.5,1.5","0.5,1.5"):
        construction.coordinates.setText(point);construction.add_point()
    construction.extrusion.setText("0.2");construction.apply()
    child=window.project.geometry.features.records[-1]
    target=next(ref for key,ref in child.outputs.items() if key.startswith("extrusion/face/"))
    section=next(iter(window.project.plate_sections))
    window.run(cmd.AssignPlate(target.id,section))
    window.selection.restore((target,))
    loads=window.panels["Loads & BC"];loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
    loads.fields["value"][0].setText("1000");pressure=loads.execute()
    boundary=child.outputs["point/p1"]
    fixed=Support("sketch anchor",boundary,{"ux":0,"uy":0,"uz":0})
    window.run(cmd.AddSupport(fixed))
    parameters=dict(parent.parameters);parameters["length"]=4;parameters["origin"]=(1,0,0)
    editor=window.panels["Geometry"];editor.choice.setCurrentIndex(editor.choice.findData("EditFeature"))
    editor.fields["feature_id"][0].setText(str(parent.feature_id))
    editor.fields["parameters"][0].setText(json.dumps(parameters));editor.execute();qapp.processEvents()
    def assert_attachments(expected_length,origin):
        assert max(window.project.geometry.vertex_position(identifier)[0] for identifier in window.project.geometry.vertices)==pytest.approx(expected_length+origin)
        current=window.project.geometry.features.get(child.feature_id)
        assert window.project.geometry.vertex_position(current.outputs["point/p1"].id)==pytest.approx((origin+.5,.5,0))
        face_refs={ref for key,ref in current.outputs.items() if key.startswith("extrusion/face/")}
        active_pressure=next(item for item in window.project.load_case().pressures if item.id==pressure.id)
        active_support=next(item for item in window.project.supports if item.id==fixed.id)
        assert active_pressure.ref in face_refs
        assert active_support.ref==current.outputs["point/p1"]
        assert active_pressure.value==1000
        assert dict(active_support.constraints)=={"ux":0,"uy":0,"uz":0}
        assert active_support.coordinate_system_id==fixed.coordinate_system_id
        assert window.project.face_sections[active_pressure.ref.id]==section
        assert current.state=="ok"
    assert window.project.geometry.features.get(parent.feature_id).parameters["length"]==4
    assert_attachments(4,1);window.undo();assert_attachments(3,0)
    assert window.project.geometry.features.get(parent.feature_id).parameters["length"]==3
    window.redo();assert_attachments(4,1)
    path=tmp_path/"dependent-feature.anyfem";window.save_project(path=str(path));window.flush_project_writes()
    window.open_project(str(path));assert_attachments(4,1)
    assert window.project.geometry.features.get(parent.feature_id).parameters["length"]==4


def test_qt_optional_section_fields_and_face_sketch(window,qapp):
    panel=window.panels["Sections"]
    panel.choice.setCurrentIndex(panel.choice.findData("AddBeamSection"))
    record,fields=panel.record_fields["section"]
    fields["name"][0].setText("new beam")
    fields["profile"][0].setText("Flatbar")
    fields["material"][0].setText(next(iter(window.project.materials)))
    fields["flange_width"][0].setText("0.2")
    fields["flange_thickness"][0].setText("0.1")
    panel.execute();assert "new beam" in window.project.beam_sections
    points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(cmd.AddPlate(points))
    window.selection.set_mode("face");window.selection.select(window.project.geometry.entity_ref("face",face))
    construction=window.panels["Construction"];construction.sketch()
    for point in ("0.5,0.5","1.5,0.5","1.5,1.5","0.5,1.5"):
        construction.coordinates.setText(point);construction.add_point()
    before=len(window.project.geometry.features.records)
    construction.apply();qapp.processEvents()
    assert len(window.project.geometry.features.records)==before+1
    feature=window.project.geometry.features.records[-1]
    original=feature.parameters.copy()
    assert construction.edit_sketch(feature.feature_id)
    construction.pair.setText("1,2");construction.distance.setText("1")
    construction.add_constraint("distance")
    construction.extrusion.setText("0.2");construction.apply()
    assert window.project.geometry.features.get(feature.feature_id).parameters["extrusion"]==0.2
    assert any(item["kind"]=="distance" for item in window.project.geometry.features.get(feature.feature_id).parameters["constraints"])
    window.undo()
    assert window.project.geometry.features.get(feature.feature_id).parameters==original


@pytest.mark.parametrize("failure",["extrusion", "distance", "coincident"])
def test_qt_failed_sketch_constraint_preserves_working_copy(window,qapp,failure):
    from anygeometry import GeometryError
    points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(cmd.AddPlate(points))
    window.selection.set_mode("face")
    window.selection.select(window.project.geometry.entity_ref("face",face))
    panel=window.panels["Construction"];panel.sketch()
    for point in ("0.5,0.5","1.5,0.5","1.5,1.5","0.5,1.5"):
        panel.coordinates.setText(point);panel.add_point()
    panel.pair.setText("1,2");panel.distance.setText("1")
    panel.add_constraint("distance")
    task=window.viewport.construction_task
    original_points=task.points;original_constraints=tuple(task.constraints)
    original_editor=panel.constraints.toPlainText()
    original_revision=window.project.geometry.revision
    if failure=="extrusion":panel.extrusion.setText("invalid")
    else:panel.distance.setText("2")
    with pytest.raises(ValueError if failure=="extrusion" else GeometryError):
        panel.add_constraint("coincident" if failure=="coincident" else "distance")
    assert task.points==original_points
    assert tuple(task.constraints)==original_constraints
    assert panel.constraints.toPlainText()==original_editor
    assert window.project.geometry.revision==original_revision
    # The valid working copy remains usable after the failed preview.
    panel.extrusion.setText("0")
    before=len(window.project.geometry.features.records)
    panel.apply();qapp.processEvents()
    assert len(window.project.geometry.features.records)==before+1
    window.undo()
    assert len(window.project.geometry.features.records)==before


@pytest.mark.parametrize("editing",[False,True])
@pytest.mark.parametrize("failure",["extrusion","constraints","command"])
def test_qt_failed_sketch_apply_preserves_preview_and_retry(window,qapp,tmp_path,monkeypatch,editing,failure):
    import json
    from copy import deepcopy
    from anygeometry import GeometryError
    points=[window.run(cmd.AddPoint(x,y,0)) for x,y in ((0,0),(3,0),(3,3),(0,3))]
    face=window.run(cmd.AddPlate(points))
    window.selection.set_mode("face")
    window.selection.select(window.project.geometry.entity_ref("face",face))
    panel=window.panels["Construction"];panel.sketch()
    for point in ("0.5,0.5","1.5,0.5","1.5,1.5","0.5,1.5"):
        panel.coordinates.setText(point);panel.add_point()
    if editing:
        panel.apply()
        feature=window.project.geometry.features.records[-1]
        assert panel.edit_sketch(feature.feature_id)
    task=window.viewport.construction_task
    previous=(task.points,tuple(task.constraints),task.close)
    feature_parameters=[deepcopy(record.parameters) for record in window.project.geometry.features.records]
    revision=window.project.geometry.revision
    panel.closed.setChecked(False)
    constraints=[{"kind":"distance","first":task.point_keys[0],"second":task.point_keys[1],"value":2}]
    if failure=="constraints":constraints.append(dict(constraints[0],value=1))
    panel.constraints.setPlainText(json.dumps(constraints))
    if failure=="extrusion":panel.extrusion.setText("invalid")
    attempted_editor=panel.constraints.toPlainText()
    with monkeypatch.context() as patch:
        if failure=="command":
            def reject(command):
                assert isinstance(command,cmd.EditFeature if editing else cmd.AddSketch)
                raise GeometryError("dependent feature refused the sketch update")
            patch.setattr(window,"run",reject)
        with pytest.raises(ValueError if failure=="extrusion" else GeometryError):panel.apply()
    assert window.viewport.construction_task is task
    assert (task.points,tuple(task.constraints),task.close)==previous
    assert panel.constraints.toPlainText()==attempted_editor
    assert window.project.geometry.revision==revision
    assert [record.parameters for record in window.project.geometry.features.records]==feature_parameters
    # Correct the rejected edit and retry through the actual command/session.
    panel.extrusion.setText("0")
    panel.constraints.setPlainText(json.dumps(constraints[:1]))
    panel.apply();qapp.processEvents()
    assert window.viewport.construction_task is None
    accepted=window.project.geometry.features.records[-1]
    assert accepted.parameters["constraints"][0]["value"]==2
    path=tmp_path/"sketch-retry.anyfem"
    window.save_project(path=str(path));window.flush_project_writes()
    window.new_project();window.open_project(str(path));qapp.processEvents()
    restored=window.project.geometry.features.get(accepted.feature_id)
    assert restored.parameters==accepted.parameters


def test_qt_attribute_visibility_and_viewport_keys_preserve_document(window,qapp):
    import numpy as np
    from PySide6.QtTest import QTest
    a,b=cantilever(window);qapp.processEvents()
    revision=window.session.revision;dirty=window.session.dirty
    entities=window.project.geometry.entity_keys()
    arrows=list(window.viewport._scene.arrows)
    assert arrows
    attributes=window._actions["Attributes / imperfections"]
    assert attributes.isChecked()
    attributes.trigger();qapp.processEvents()
    assert not attributes.isChecked() and not window._show_attributes.get()
    assert not window.viewport._scene.arrows
    attributes.trigger();qapp.processEvents()
    assert len(window.viewport._scene.arrows)==len(arrows)
    for restored,original in zip(window.viewport._scene.arrows,arrows):
        np.testing.assert_array_equal(restored.start,original.start)
        np.testing.assert_array_equal(restored.end,original.end)
        assert restored.color==original.color
    framed=[]
    window.viewport.set_frame_selection_handler(lambda items:framed.append(tuple(items)) or True)
    for backend in (window.viewport.active_backend,"software"):
        window.switch_viewer_backend(backend);qapp.processEvents()
        ref=window.project.geometry.entity_ref("vertex",b)
        window.selection.set_mode("vertex");window.selection.select(ref)
        surface=window.viewport.canvas.event_widget
        QApplication.setActiveWindow(window);surface.setFocus();qapp.processEvents()
        QTest.keyClick(surface,Qt.Key_F);qapp.processEvents()
        assert framed[-1]==(ref,)
        QTest.keyClick(surface,Qt.Key_Escape);qapp.processEvents()
        assert not window.selection.items and not window.tree.selectionModel().selectedRows()
        panel=window.panels["Construction"];panel.start()
        panel.coordinates.setText("2,3");panel.add_point()
        assert window.viewport.construction_active
        surface.setFocus();QTest.keyClick(surface,Qt.Key_Escape);qapp.processEvents()
        assert not window.viewport.construction_active
        assert window.project.geometry.entity_keys()==entities
    assert window.session.revision==revision and window.session.dirty==dirty


def test_qt_initial_docks_leave_viewport_space_and_restore_layout(qapp,tmp_path,monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QDockWidget
    # Isolate this first-run layout from both user settings and other tests.
    from anyfem.ui.qt import app as qt_app
    settings=QSettings(str(tmp_path/"layout.ini"),QSettings.IniFormat)
    monkeypatch.setattr(qt_app,"QSettings",lambda *_:settings)
    window=QtFemWindow(viewer_backend="software")
    restored=None
    try:
        window.show();window.resize(1024,768);qapp.processEvents()
        assert window.viewport.widget.width()>=350
        assert window.viewport.widget.height()>=450
        docks={dock.objectName():dock for dock in window.findChildren(QDockWidget)}
        docks["Messages"].hide()
        window.resizeDocks([docks["Tasks"]],[300],Qt.Horizontal)
        qapp.processEvents()
        expected_width=docks["Tasks"].width()
        window.session.mark_saved();window.close();qapp.processEvents()
        restored=QtFemWindow(viewer_backend="software")
        restored.show();qapp.processEvents()
        restored_docks={dock.objectName():dock for dock in restored.findChildren(QDockWidget)}
        assert restored_docks["Messages"].isHidden()
        assert abs(restored_docks["Tasks"].width()-expected_width)<=2
    finally:
        for candidate in (window,restored):
            if candidate is not None:
                candidate.session.mark_saved();candidate.close()
        qapp.processEvents()


def test_qt_viewport_click_shortcut_and_docking(window,qapp):
    from PySide6.QtCore import QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDockWidget
    point=window.run(cmd.AddPoint(2,0,0));window.viewport.fit();qapp.processEvents()
    projected=window.viewport.project_point((2,0,0))
    surface=window.viewport.canvas.event_widget
    ratio=surface.devicePixelRatioF()
    QTest.mouseClick(surface,Qt.LeftButton,Qt.NoModifier,QPoint(round(projected[0]/ratio),round(projected[1]/ratio)))
    qapp.processEvents()
    assert window.selection.items==[window.project.geometry.entity_ref("vertex",point)]
    assert window.tree.selectionModel().selectedRows()
    # Wayland does not activate programmatically shown windows automatically.
    # Establish Qt's active window before synthesizing a window shortcut.
    QApplication.setActiveWindow(window);surface.setFocus();qapp.processEvents()
    QTest.keyClick(surface,Qt.Key_Z,Qt.ControlModifier);qapp.processEvents()
    assert not window.project.geometry.vertices
    dock=next(item for item in window.findChildren(QDockWidget) if item.objectName()=="Tasks")
    dock.setFloating(True);qapp.processEvents();assert dock.isFloating()
    dock.setFloating(False);qapp.processEvents();assert not dock.isFloating()


def test_qt_project_replacement_cancels_uncommitted_construction(window):
    panel=window.panels["Construction"];panel.start()
    panel.coordinates.setText("2,3");panel.add_point()
    window.new_project()
    assert not window.viewport.construction_active
    assert not window.project.geometry.vertices


def test_qt_autosave_recovery_round_trip(window,monkeypatch):
    window.run(cmd.AddPoint(1,2,3))
    window._write_recovery();window.flush_project_writes()
    assert window._status.get()=="autosaved recovery snapshot"
    window.new_project();assert not window.project.geometry.vertices
    monkeypatch.setattr(window.dialogs,"confirm",lambda *args:True)
    window.recover_autosave()
    assert len(window.project.geometry.vertices)==1
    assert window.session.dirty and window.path is None


@pytest.mark.parametrize("mesh_source,action",[("geometry","cancel"),("geometry","edit"),("geometry","replace"),
    ("imported","cancel"),("imported","edit"),("imported","replace"),("imported","close")])
def test_qt_inflight_solve_cancellation_and_stale_ownership(window,qapp,monkeypatch,tmp_path,mesh_source,action):
    import threading
    from anyfem.application.workflow import ANALYSES
    from anyfem.model.records import JobStatus
    started=threading.Event();release=threading.Event()
    original=ANALYSES["Linear static"]
    def held_solve(*,cancellation_token,**kwargs):
        started.set()
        while not release.wait(.01):cancellation_token.raise_if_cancelled()
        return original(cancellation_token=cancellation_token,**kwargs)
    monkeypatch.setitem(ANALYSES,"Linear static",held_solve)
    if mesh_source=="geometry":
        cantilever(window);window.generate_mesh_async(.25,strategy="auto")
        wait_until(qapp,lambda:window.mesh is not None)
    else:
        from test_io import write_sesam_plate
        window.import_sesam_model(str(write_sesam_plate(tmp_path/"held.FEM")))
        loads=window.panels["Loads & BC"]
        loads.choice.setCurrentIndex(loads.choice.findData("AddPressure"))
        loads.fields["ref"][0].setText("group:group 1")
        loads.fields["value"][0].setText("20000");loads.execute()
    submitted_mesh_id=window.mesh_record_id
    window.solve();wait_until(qapp,started.is_set)
    manager=window.job_manager;job=next(reversed(window.project.jobs.values()))
    try:
        if action=="cancel":
            window.cancel_solve();wait_until(qapp,lambda:job.status==JobStatus.CANCELLED)
            assert window.solution is None
        elif action=="edit":
            if mesh_source=="geometry":window.run(cmd.AddPoint(5,0,0))
            else:
                loads.fields["value"][0].setText("10000");loads.execute()
            release.set()
            wait_until(qapp,lambda:job.id in window.solutions)
            assert window._job_is_stale(job)
            assert window.solution is None and window._view_mode==("geometry" if mesh_source=="geometry" else "mesh")
            window.save_project(path=str(tmp_path/"stale.anyfem"))
            assert window.result_datasets[job.id].identity["mesh_id"]==submitted_mesh_id
            if mesh_source=="imported":
                import numpy as np
                solution=window.solutions[job.id]
                assert solution.built.project is not window.project
                assert len(solution.built.project.load_cases["default"].pressures)==1
                assert len(window.project.load_cases["default"].pressures)==2
                expected=window.result_datasets[job.id].field("displacement").read(0).copy()
                window.new_project();window.open_project(str(tmp_path/"stale.anyfem"));qapp.processEvents()
                assert window._job_is_stale(window.project.jobs[job.id])
                np.testing.assert_array_equal(window.result_datasets[job.id].field("displacement").read(0),expected)
                window.panels["Results"].activate_job(job.id);qapp.processEvents()
                assert window.retained_result_mesh(job.id) is not None
        elif action=="replace":
            window.new_project();release.set()
            manager.wait(job.id,timeout=5)
            qapp.processEvents()
            assert not window.project.jobs and not window.solutions
            assert window.solution is None
            assert not window.panels["Solve"].transcript.toPlainText()
            assert not window.panels["Results"].report.toPlainText()
        else:
            window.session.mark_saved();window.close();release.set()
            manager.wait(job.id,timeout=5);qapp.processEvents()
            assert window._closing and job.status==JobStatus.CANCELLED
            assert not window.solutions and window.solution is None
    finally:release.set()


@pytest.mark.parametrize("action",["cancel","edit","replace","failure"])
def test_qt_inflight_mesh_ownership(window,qapp,monkeypatch,action):
    import threading
    from anyfem.model.project import Project
    started=threading.Event();release=threading.Event()
    original=Project.generate_mesh
    def held_mesh(project,*args,cancellation_check,**kwargs):
        started.set()
        while not release.wait(.01):cancellation_check("held meshing phase")
        if action=="failure":raise RuntimeError("deliberate mesh failure")
        return original(project,*args,cancellation_check=cancellation_check,**kwargs)
    monkeypatch.setattr(Project,"generate_mesh",held_mesh)
    cantilever(window);window.generate_mesh_async(.25,strategy="auto")
    wait_until(qapp,started.is_set)
    record=next(reversed(window.project.mesh_records.values()))
    manager=window.mesh_task_manager;future=manager._active.future
    try:
        if action=="cancel":window.cancel_mesh()
        elif action=="edit":window.run(cmd.AddPoint(5,0,0))
        elif action=="replace":window.new_project()
        release.set()
        wait_until(qapp,future.done)
        if action=="replace":
            qapp.processEvents();assert not window.project.mesh_records and window.mesh is None
        else:
            expected={"cancel":"cancelled","edit":"stale","failure":"failed"}[action]
            wait_until(qapp,lambda:record.status==expected)
            assert window.mesh is None
            if action=="failure":assert "deliberate mesh failure" in str(record.diagnostics)
    finally:release.set()
