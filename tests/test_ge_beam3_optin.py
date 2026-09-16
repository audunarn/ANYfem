import numpy as np
import pytest

from anyfem.solve.ge_beam3 import B3GENativeOptIn, GeBeam3OptIn
from anysolver.ge_beam3_element import GeometricallyExactBeam3D3NElement


def _b3_ge_opt_in():
    from anysolver import b3_ge

    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((1.0e5, 1.0e5, 1.0e5, 1.0e4, 1.0e4, 1.0e4)),
        np.eye(6),
        1.0e9,
        1.0,
    )
    definition = b3_ge.define_beam(
        b3_ge.SELECTOR,
        7,
        (1, 2, 3),
        np.array(((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (1.0, 0.0, 0.0))),
        np.repeat(np.eye(3)[None, :, :], 3, axis=0),
        section,
        np.diag((2.0, 2.0, 2.0, 0.07, 0.09, 0.11)),
    )
    return B3GENativeOptIn((definition,))


def test_ge_beam3_consumer_is_explicit_persisted_and_does_not_change_defaults():
    definition = GeBeam3OptIn(np.diag((10., 11., 12., 13., 14., 15.)),
                              np.diag((2., 2., 2., .1, .2, .3)), (0., 1., 0.),
                              contact_radius=.04)
    restored = GeBeam3OptIn.from_dict(definition.to_dict())
    element = restored.build(7, (1, 2, 3), "steel")
    assert type(element) is GeometricallyExactBeam3D3NElement
    assert element.selector == "ge-beam3"
    assert element.cross_section["contact_radius"] == .04
    from anyfem.model.project import Project
    assert not hasattr(Project(), "ge_beam3_opt_in")


def test_b3_ge_native_consumer_roundtrip_and_legacy_default():
    from anysolver import b3_ge
    from anysolver.boundary import BoundaryCondition
    from anysolver.elements import QuadraticBeamElement, create_element

    section = b3_ge.EllipsoidalGeneralizedSection(
        np.diag((10.0, 11.0, 12.0, 13.0, 14.0, 15.0)), np.eye(6), 1.0e6, 1.0
    )
    definition = b3_ge.define_beam(
        b3_ge.SELECTOR, 7, (1, 2, 3),
        np.array(((0.0, 0.0, 0.0), (0.5, 0.0, 0.0), (1.0, 0.0, 0.0))),
        np.repeat(np.eye(3)[None, :, :], 3, axis=0), section,
        np.diag((2.0, 2.0, 2.0, 0.07, 0.09, 0.11)),
    )
    policy = B3GENativeOptIn((definition,))
    restored = B3GENativeOptIn.from_dict(policy.to_dict())
    owner = restored.create_analysis((BoundaryCondition(
        "fixed", [1], {"ux": 0.0, "uy": 0.0, "uz": 0.0, "rx": 0.0, "ry": 0.0, "rz": 0.0}
    ),))
    assert owner.identity
    assert restored.policy["selector"] == "b3-ge"
    assert restored.policy["legacy_b3_default"] is True
    assert type(create_element("quadratic_beam", 99, [1, 2, 3])) is QuadraticBeamElement
    oversized = policy.to_dict()
    oversized["definitions"][0]["raw_base64"] = "a" * 2_800_001
    with pytest.raises(ValueError, match="bounded"):
        B3GENativeOptIn.from_dict(oversized)


def test_analysis_configuration_requires_a_dedicated_explicit_b3_ge_choice():
    from anyfem.model.records import AnalysisDefinition

    default = AnalysisDefinition("ordinary static")
    assert default.beam_formulation == "legacy-b3"
    assert default.beam_formulation_options == {}
    with pytest.raises(ValueError, match="legacy B3"):
        default.create_b3_ge_analysis(())

    configured = AnalysisDefinition.b3_ge("native beam", _b3_ge_opt_in())
    assert configured.type == "b3_ge_native"
    assert configured.target_kind == "none"
    assert configured.beam_formulation == "b3-ge"
    assert configured.beam_formulation_options["consumer_policy"][
        "explicit_opt_in"
    ] is True

    with pytest.raises(ValueError, match="dedicated"):
        AnalysisDefinition(
            "unsafe",
            beam_formulation="b3-ge",
            beam_formulation_options=_b3_ge_opt_in().to_dict(),
        )


def test_b3_ge_choice_roundtrips_and_missing_legacy_field_never_opts_in(tmp_path):
    from anyfem.io.project_file import load_project, project_to_dict, save_project
    from anyfem.model.project import Project
    from anyfem.model.records import AnalysisDefinition

    project = Project("explicit-b3-ge")
    configured = AnalysisDefinition.b3_ge("native beam", _b3_ge_opt_in())
    project.add_analysis(configured)
    path = save_project(project, tmp_path / "explicit.anyfem")
    reopened = load_project(path)
    restored = reopened.analyses[configured.id]
    assert restored.beam_formulation == "b3-ge"
    assert restored.beam_formulation_options == configured.beam_formulation_options

    legacy = Project("legacy")
    ordinary = legacy.add_analysis(AnalysisDefinition("ordinary static"))
    payload = project_to_dict(legacy)
    entry = next(item for item in payload["analyses"] if item["id"] == ordinary.id)
    entry.pop("beam_formulation")
    entry.pop("beam_formulation_options")
    from anyfem.io.project_file import project_from_dict

    migrated = project_from_dict(payload).analyses[ordinary.id]
    assert migrated.beam_formulation == "legacy-b3"
    assert migrated.beam_formulation_options == {}


def test_explicit_b3_ge_analysis_runs_separately_from_legacy_default():
    from anyfem.model.records import AnalysisDefinition
    from anysolver import b3_ge
    from anysolver.boundary import BoundaryCondition
    from anysolver.elements import QuadraticBeamElement, create_element

    # The unchanged default still constructs the ordinary B3 element.
    assert type(create_element("quadratic_beam", 99, [1, 2, 3])) is QuadraticBeamElement

    configured = AnalysisDefinition.b3_ge("bounded native run", _b3_ge_opt_in())
    owner = configured.create_b3_ge_analysis(
        (
            BoundaryCondition(
                "fixed",
                [1],
                {
                    "ux": 0.0,
                    "uy": 0.0,
                    "uz": 0.0,
                    "rx": 0.0,
                    "ry": 0.0,
                    "rz": 0.0,
                },
            ),
        )
    )
    load = b3_ge.DistributedPattern(
        b3_ge.LinePattern(((7, 0.0, -1.0, 0.0),)), ()
    )
    result = owner.solve_distributed(load, steps=1, max_iterations=12)

    assert result.status == "completed"
    assert len(result.checkpoint) > 0
    assert configured.beam_formulation == "b3-ge"
