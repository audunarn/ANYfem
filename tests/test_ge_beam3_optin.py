import numpy as np

from anyfem.solve.ge_beam3 import GeBeam3OptIn
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
