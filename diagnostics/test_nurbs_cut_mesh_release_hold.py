"""Pure source contract for exactly two owner-held full-cut mesh capabilities."""
import ast
from pathlib import Path

F=Path(__file__).resolve().parents[1]
TARGETS={'test_principal_public_cut_and_default_project_mesh',
         'test_dome_closed_loop_split_default_project_mesh'}
REASON='OWNER-HELD full-cut NURBS meshing; see docs/NURBS_CUT_MESH_RELEASE_HOLD.md'

def _skip_call(node):
    return (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
            and node.func.attr=='skip' and isinstance(node.func.value,ast.Attribute)
            and node.func.value.attr=='mark' and isinstance(node.func.value.value,ast.Name)
            and node.func.value.value.id=='pytest')

def test_exact_two_full_mesh_capabilities_have_unconditional_literal_owner_hold():
    tree=ast.parse((F/'tests/test_nurbs_cut_mesh_integration.py').read_bytes())
    held={}
    for node in tree.body:
        if not isinstance(node,ast.FunctionDef):continue
        marks=[d for d in node.decorator_list if _skip_call(d)]
        if marks:held[node.name]=marks
    assert set(held)==TARGETS
    for marks in held.values():
        assert len(marks)==1 and marks[0].args==[]
        assert len(marks[0].keywords)==1 and marks[0].keywords[0].arg=='reason'
        assert isinstance(marks[0].keywords[0].value,ast.Constant)
        assert marks[0].keywords[0].value.value==REASON


def test_no_module_hold_or_conditional_bypass_and_preview_remains_required():
    tree=ast.parse((F/'tests/test_nurbs_cut_mesh_integration.py').read_bytes())
    assert not any(isinstance(n,ast.Name) and n.id=='pytestmark' and isinstance(n.ctx,ast.Store) for n in ast.walk(tree))
    assert not any(isinstance(n,ast.Attribute) and n.attr in ('skipif','xfail') for n in ast.walk(tree))
    preview=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='test_principal_public_cut_preview_topology_material_and_sheet_uses')
    assert not any(_skip_call(d) for d in preview.decorator_list)
    assert len([n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')])==3


def test_qualification_note_requires_fresh_owner_authority_and_no_consumed_run_reuse():
    note=(F/'docs/NURBS_CUT_MESH_RELEASE_HOLD.md').read_text()
    assert 'Fresh owner-approved qualification' in note
    assert 'Consumed execution authority is not reusable' in note
    assert '200,000' in note and '0.0025' in note
    assert 'unmeasured' in note and 'not failed or qualified' in note
