import numpy as np

from anyfem.solve.ge_beam3 import B3GENativeOptIn, GeBeam3OptIn
from anysolver.ge_beam3_element import GeometricallyExactBeam3D3NElement


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
