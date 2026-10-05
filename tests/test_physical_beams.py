"""Physical beam membership preserves fine axes, assignments and undo."""
import pytest
from anyfem import commands
from anyfem.model.project import Project
from anyfem.model.sections import BeamSection
from anyfem.model.materials import steel
from anyfem.io.project_file import project_from_dict, project_to_dict
from anyfem.application.examples import floating_barge_section


def axis():
    p = Project()
    p.add_material(steel("S355", .01))
    p.add_beam_section(BeamSection("beam", "T-bar", "S355", web_height=.2,
                                 web_thickness=.008, flange_width=.1, flange_thickness=.01))
    vertices = [p.geometry.add_point(x,0,0) for x in (0,1,3,5)]
    edges = [p.geometry.add_line(a,b) for a,b in zip(vertices,vertices[1:])]
    return p, edges


def test_chain_assignment_undo_redo_and_reopen():
    p, edges = axis()
    stack = commands.CommandStack(p)
    member = stack.run(commands.AddBeamMember([edges[2], edges[0], edges[1]], "beam", "Long beam"))
    uses = [p.geometry.member_edge_uses[u] for u in p.geometry.members[member].edge_use_ids]
    assert [u.edge_id for u in uses] == edges
    assert len(p.geometry.members) == 1
    assert p.edge_sections == dict.fromkeys(edges, "beam")
    restored = project_from_dict(project_to_dict(p))
    assert restored.geometry.members == p.geometry.members
    assert restored.geometry.member_edge_uses == p.geometry.member_edge_uses
    assert restored.edge_sections == p.edge_sections
    stack.undo()
    assert not p.geometry.members and not p.edge_sections
    stack.redo()
    assert p.geometry.members[member].name == "Long beam"
    assert p.edge_sections == dict.fromkeys(edges, "beam")


def test_invalid_or_already_owned_axis_is_atomic():
    p, edges = axis()
    stack = commands.CommandStack(p)
    with pytest.raises(ValueError):stack.run(commands.AddBeamMember([edges[0], edges[2]], "beam"))
    assert not p.geometry.members and not p.edge_sections
    with pytest.raises(ValueError, match="No beam axis edge"):stack.run(commands.AddBeamMember([9999], "beam"))
    with pytest.raises(ValueError):stack.run(commands.AddBeamMember(edges, "missing section"))
    assert not p.geometry.members and not p.edge_sections
    stack.run(commands.AddBeamMember(edges, "beam"))
    before = project_to_dict(p)
    with pytest.raises(ValueError):stack.run(commands.AddBeamMember(edges, "beam"))
    assert project_to_dict(p) == before


def test_barge_has_physical_members_not_axis_fragments():
    original = floating_barge_section()
    p = project_from_dict(project_to_dict(original))
    assert p.geometry.member_edge_uses == original.geometry.member_edge_uses
    assert p.geometry.attachments == original.geometry.attachments
    assert len(p.geometry.members) == 44
    assert len(p.edge_sections) == 188
    for member in p.geometry.members.values():
        uses = [p.geometry.member_edge_uses[u] for u in member.edge_use_ids]
        assert uses[0].parent_range.start == 0 and uses[-1].parent_range.end == 1
        assert len(p.geometry.attachments_for_member(member.id)) == 1
