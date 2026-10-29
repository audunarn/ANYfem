"""Structural-preparation failures stay distinguishable from element generation and keep the owner's evidence."""
import pytest

from anyfem.mesh_jobs import _exception_diagnostic
from anyfem.structural_preparation import StructuralPreparationError

anygeometry_errors = pytest.importorskip("anygeometry.errors")


class _OwnerRefusal(anygeometry_errors.GeometryError):
    def to_diagnostic(self):
        return {"reason": "window_end", "face_pair": (199, 200), "residual": 0.4066, "tolerance": 1e-9}


def _wrapped():
    try:
        try:
            raise _OwnerRefusal("owner refused")
        except anygeometry_errors.GeometryError as error:
            raise StructuralPreparationError(f"cannot prepare structural intersection batch: {error}") from error
    except StructuralPreparationError as error:
        return error


def test_structural_error_names_its_phase_and_retains_pair_residual_tolerance():
    diagnostic = _wrapped().to_diagnostic()
    assert diagnostic["phase"] == "preparing structural connections"
    assert diagnostic["element_generation_started"] is False
    assert diagnostic["cause"]["face_pair"] == (199, 200)
    assert diagnostic["cause"]["residual"] == pytest.approx(0.4066)
    assert diagnostic["cause"]["tolerance"] == 1e-9
    assert diagnostic["cause_type"] == "_OwnerRefusal"


def test_job_exception_diagnostic_transports_the_structured_details():
    payload = _exception_diagnostic(_wrapped())
    assert payload["type"] == "StructuralPreparationError"
    assert payload["details"]["phase"] == "preparing structural connections"
    assert payload["details"]["cause"]["reason"] == "window_end"


def test_error_without_an_owner_cause_still_reports_the_phase():
    diagnostic = StructuralPreparationError("unclassified").to_diagnostic()
    assert diagnostic == {"phase": "preparing structural connections", "element_generation_started": False}
