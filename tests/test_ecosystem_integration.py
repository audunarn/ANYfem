"""Contracts at the extracted ANYmaterial/ANYmesher/ANYfileio boundaries."""

from __future__ import annotations

import pytest

from anyfem import Project, steel
from anyfem.io.project_file import project_from_dict, project_to_dict


def test_geometry_and_mesh_compatibility_paths_are_owner_types():
    from anymesher import GeometryModel as OwnerGeometryModel
    from anymesher import Mesh as OwnerMesh
    from anymesher.mapped import generate_mesh as owner_generate_mesh

    from anyfem.geometry import GeometryModel
    from anyfem.mesh import Mesh, generate_mesh

    assert GeometryModel is OwnerGeometryModel
    assert Mesh is OwnerMesh
    assert generate_mesh is owner_generate_mesh


def test_project_material_is_an_anymaterial_spec_and_builds_live():
    from anymaterial import IsotropicMaterial, MaterialSpec

    material = steel("S355", 0.010, nonlinear=True)
    assert isinstance(material, MaterialSpec)
    assert isinstance(material.build(), IsotropicMaterial)
    assert material.hardening == {
        "kind": "dnv_c208",
        "grade": "S355",
        "thickness": pytest.approx(0.010),
    }


def test_new_material_json_and_version_one_json_both_load():
    project = Project("materials")
    project.add_material(steel("S355", 0.010, nonlinear=True))
    encoded = project_to_dict(project)
    assert encoded["materials"][0]["symmetry"] == "isotropic"
    assert "constants" in encoded["materials"][0]
    assert project_from_dict(encoded).materials["S355"].build().name == "S355"

    legacy = {
        "anyfem": {"format": 1},
        "name": "legacy",
        "geometry": {},
        "materials": [
            {
                "name": "S355",
                "elastic_modulus": 210.0e9,
                "poisson_ratio": 0.3,
                "density": 7850.0,
                "yield_stress": 355.0e6,
                "hardening": ["dnv_c208", "S355", 0.010],
            }
        ],
    }
    restored = project_from_dict(legacy).materials["S355"]
    assert restored.constants["elastic_modulus"] == pytest.approx(210.0e9)
    assert restored.hardening["kind"] == "dnv_c208"


def test_result_adapters_import_parsers_from_anyfileio(monkeypatch, tmp_path):
    import anyfileio

    from anyfileio import CalculixParsedResults
    from anyfem.io.results import import_calculix_results

    source = tmp_path / "result.frd"
    source.write_text("placeholder", encoding="utf-8")
    parsed = CalculixParsedResults(displacements={1: (1.0, 2.0, 3.0)})
    monkeypatch.setattr(anyfileio, "parse_frd", lambda _path: parsed)
    result = import_calculix_results(source)
    assert result.displacements[1] == pytest.approx((1.0, 2.0, 3.0))
