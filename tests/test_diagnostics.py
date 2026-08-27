"""Headless support-report contracts."""

import json

from anyfem import Project
from anyfem.diagnostics import ErrorDiagnostic, build_diagnostic_report
from anyfem.native_meshing import NativeMeshSettings


def test_report_contains_full_error_and_reproducibility_context() -> None:
    project = Project("diagnostic plate")
    project.geometry.add_point(0.0, 0.0, 0.0)
    project.native_mesh_settings = NativeMeshSettings.create(
        0.25, backend="automatic"
    )
    error = ErrorDiagnostic.capture(
        "mesh generation failed: full message",
        details={
            "type": "MeshValidityError",
            "message": "non-manifold edge (1, 32) belongs to 3 active elements",
            "traceback": "Traceback: complete worker trace",
        },
        project_id=project.document_id,
        view="geometry",
    )

    report = build_diagnostic_report(
        project,
        errors=(error,),
        context={"details_page": "Mesh", "selection": {"items": []}},
        recent_commands=("commands.run(commands.AddPoint(x=0.0, y=0.0))",),
        version_reader=lambda name: {"ANYfem": "0.3.2"}.get(name, "test"),
        origin_reader=lambda module: f"C:/test/{module}.py",
    )
    payload = json.loads(report)

    assert payload["errors"][0]["details"]["traceback"].endswith("worker trace")
    assert payload["project"]["geometry"]["points"] == 1
    assert payload["project"]["mesh_settings"]["target_size"] == 0.25
    assert payload["application"]["details_page"] == "Mesh"
    assert payload["recent_gui_commands"][-1].startswith("commands.run")
    assert payload["packages"][0]["version"] == "0.3.2"


def test_report_retains_only_ten_recent_errors_and_one_hundred_commands() -> None:
    project = Project()
    errors = tuple(
        ErrorDiagnostic.capture(f"error {index}") for index in range(12)
    )
    report = json.loads(
        build_diagnostic_report(
            project,
            errors=errors,
            recent_commands=(f"command {index}" for index in range(105)),
            version_reader=lambda _name: "test",
            origin_reader=lambda module: module,
        )
    )

    assert [item["message"] for item in report["errors"]] == [
        f"error {index}" for index in range(2, 12)
    ]
    assert report["recent_gui_commands"][0] == "command 5"
