"""Bounded native tree interaction timings; no meshing or solver work."""
import json
import os
from pathlib import Path
from time import perf_counter

import numpy as np
import pytest

if os.environ.get("ANYFEM_RUN_QT_TESTS") != "1":
    pytest.skip("enable Qt tests explicitly", allow_module_level=True)

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from anyfem import commands
from anyfem.application.controller import default_project
from test_qt_geometry_actions import qapp, window


@pytest.mark.parametrize("spacing,case", [(.75, "review"), (.15, "large")])
def test_tree_inspection_latency_without_document_or_scene_rebuild(window, qapp, monkeypatch, spacing, case):
    project = default_project()
    commands.AddStiffenedPanel(10, 30, spacing, beam_section="stiffener", plate_section="plate").do(project)
    edges = tuple(project.geometry.edges)
    records = [commands.AddLineLoad(project.geometry.entity_ref("edge", edge), force_per_length=(0, 0, -1000)).do(project)
               for edge in edges[:2]]
    window._set_project(project)
    window.resize(1900, 1000)
    window.show_geometry(reset_view=True)
    window.notebook.select("Loads & BC")
    tree = window.tree
    tree.expand(tree._groups["Loads"].index())
    QTest.qWait(220)
    qapp.processEvents()
    scene, mesh, revision = window.viewport._scene, window.mesh, project.geometry.revision
    history = len(window.commands.history())
    calls = {"scene": 0, "tree": 0}
    def forbidden_scene(*args, **kwargs):
        calls["scene"] += 1
        raise AssertionError("Inspecting an attribute must not rebuild the scene")
    def forbidden_tree(*args, **kwargs):
        calls["tree"] += 1
        raise AssertionError("Inspecting an attribute must not rebuild the whole tree")
    monkeypatch.setattr(window, "show_geometry", forbidden_scene)
    monkeypatch.setattr(tree, "refresh", forbidden_tree)
    samples = []
    profiler = None
    profile_path = os.environ.get("ANYFEM_TREE_PERF_PROFILE")
    if profile_path:
        profile_path = str(Path(profile_path).with_stem(Path(profile_path).stem+"-"+case))
    if profile_path:
        import cProfile
        profiler = cProfile.Profile()
    for index in range(12):
        record = records[index % 2]
        item = tree._rows["Loads"][record.id][0]
        tree.scrollTo(item.index()); qapp.processEvents()
        point = tree.visualRect(item.index()).center()
        started = perf_counter()
        if profiler and index == 4:
            profiler.enable()
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=point)
        qapp.processEvents()
        if profiler and index == 4:
            profiler.disable(); profiler.dump_stats(profile_path)
        samples.append((perf_counter()-started)*1000)
        panel = window.panels["Loads & BC"]
        assert panel.choice.currentData() == "AddLineLoad"
        assert panel.vector.value() == pytest.approx((0, 0, -1000))
        assert {record.ref} <= window.viewport._collection_refs
    measured = {
        "backend": "software", "logical_window": [1900, 1000],
        "vertices": len(project.geometry.vertices), "edges": len(project.geometry.edges),
        "faces": len(project.geometry.faces), "beams": len(project.geometry.members),
        "samples_ms": samples, "first_click_ms": samples[0],
        "warm_median_ms": float(np.median(samples[2:])),
        "warm_p95_ms": float(np.percentile(samples[2:], 95)),
        "full_rebuild_calls": calls,
    }
    evidence = os.environ.get("ANYFEM_TREE_PERF_EVIDENCE")
    if evidence:
        path = Path(evidence)
        path.with_stem(path.stem+"-"+case).write_text(json.dumps(measured, indent=2), encoding="utf-8")
    assert window.viewport._scene is scene and window.mesh is mesh
    assert project.geometry.revision == revision and len(window.commands.history()) == history
    assert calls == {"scene": 0, "tree": 0}
