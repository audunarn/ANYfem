from anygeometry import EntityRef
from anyfem.model.project import Project
from anyfem.selection import Selection,SelectionFilter,plate_boundary_edge_ids


def test_plate_boundary_filter_programmatic_and_manual_shared_edges():
    project=Project(); g=project.geometry
    points=g.add_points(((0,0,0),(1,0,0),(1,1,0),(0,1,0),(2,0,0),(2,1,0)))
    first=g.add_plate(points[:4]); shared=g.faces[first].loop[1].edge
    # A separate plate deliberately uses the same canonical topology edge.
    edges=(shared,g.add_line(points[2],points[5]),g.add_line(points[5],points[4]),g.add_line(points[4],points[1]))
    g.add_face(edges)
    loose=g.add_line(points[0],points[5])
    eligible=plate_boundary_edge_ids(project)
    assert shared in eligible and loose not in eligible
    selection=Selection(); selection.set_filter(SelectionFilter(kinds=frozenset({'edge'}),flavor='plate_edge',eligible_ids=eligible))
    change=selection.apply([EntityRef('edge',shared),EntityRef('edge',loose)])
    assert change.accepted==(EntityRef('edge',shared),) and len(change.rejected)==1
    assert selection.mode=='edge'
    selection.set_mode('edge');assert not selection.filter.flavor
    assert selection.filter.accepts(EntityRef('edge',loose))


def test_empty_plate_boundary_filter_refuses_edges():
    selection=Selection(); selection.set_filter(SelectionFilter(kinds=frozenset({'edge'}),flavor='plate_edge'))
    assert not selection.filter.accepts(EntityRef('edge',1))


def test_plate_edge_canvas_signature_preserves_highlight_noop_contract(monkeypatch):
    from test_viewport_commercial_selection import _viewport
    selection=Selection('edge')
    selection.set_filter(SelectionFilter(kinds=frozenset({'edge'}),flavor='plate_edge',eligible_ids=frozenset({1,2})))
    viewport=_viewport(monkeypatch,selection)
    selection.apply([EntityRef('edge',1)])
    assert viewport.canvas.config_updates==[]
    viewport.configure_selection(tool='box',depth='through')
    before=len(viewport.canvas.config_updates)
    selection.apply([EntityRef('edge',2)])
    assert len(viewport.canvas.config_updates)==before
    selection.set_filter(SelectionFilter(kinds=frozenset({'edge'}),flavor='plate_edge',eligible_ids=frozenset({1,2,3})))
    assert len(viewport.canvas.config_updates)==before+1


def test_manual_plate_hole_edges_are_all_eligible():
    from anygeometry import punch_hole
    project=Project();g=project.geometry
    face=g.add_plate(g.add_points(((0,0,0),(4,0,0),(4,4,0),(0,4,0))))
    punch_hole(g,face,(2,2,0),.5)
    expected={item.edge for loop in (g.faces[face].loop,)+g.faces[face].holes for item in loop}
    assert expected==plate_boundary_edge_ids(project)


def test_rotated_generator_perimeter_excludes_internal_members():
    from anygeometry.generators import stiffened_panel
    from anygeometry import feature_entity_owners
    # Captured generator output ownership is the contract used by the model UI.
    project=Project();g=project.geometry;g.features.capture_baseline(g)
    record=g.features.append('generator.stiffened_panel',parameters={'length':4,'width':6,'longitudinal_spacing':1,'u_direction':(0,1,0),'v_direction':(0,0,1)})
    assert g.regenerate_features().success
    eligible=plate_boundary_edge_ids(project)
    assert len(eligible)==14
    assert all(len(g.faces_using_edge(i))==1 for i in eligible)
    internal={i for i in g.edges if g.members_using_edge(i)}
    assert internal and not internal&eligible
    exploded=plate_boundary_edge_ids(project,(record.feature_id,))
    assert internal<=exploded
