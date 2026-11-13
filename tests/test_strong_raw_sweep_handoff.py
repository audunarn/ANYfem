"""Reported four-face source uses the public geometry/mesher proof bridge."""
from fractions import Fraction
from anygeometry import query_translational_profile_interfaces
from anygeometry.serialization import to_dict
from anyfem.structural_preparation import prepare_structural_connectivity
from spline_ownership_helpers import reported_unassigned_extrusion


def test_reported_four_face_public_owner_bridge_keeps_nonzero_bound_and_source():
    project=reported_unassigned_extrusion();source=project.geometry;before=to_dict(source)
    evidence=query_translational_profile_interfaces(source,tuple(source.faces))
    assert evidence is not None and len(evidence['pairs'])==6
    bounds=[Fraction(*row['upper_L1']) for face in evidence['faces'] for row in face['connectors']]
    assert 0<max(bounds)<=Fraction(source.tolerance.length)
    working=source.clone(include_features=False)
    report=prepare_structural_connectivity(working,member_ids=())
    assert len(working.sheets)==4
    assert any('temporary Sheet' in item for item in report.diagnostics)
    assert to_dict(source)==before and not source.sheets and not source.members
