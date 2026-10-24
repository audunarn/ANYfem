"""Set creation cancellation and immutable source menus."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from test_qt_geometry_actions import qapp,window,geometry
from PySide6.QtWidgets import QInputDialog
from anyfem import commands
from anyfem.model.regions import Region,ManualRegion,BooleanRegion


def test_empty_create_cancel_invalid_duplicate_and_readonly(window,qapp,monkeypatch):
    before=window.commands.history();monkeypatch.setattr(QInputDialog,'getText',lambda *a,**k:('',False))
    assert window.geometry_actions.new_set(refs=()) is None and window.commands.history()==before
    with pytest.raises(ValueError):window.geometry_actions.new_set(refs=(),name=' ')
    region=window.geometry_actions.new_set(refs=(),name='Empty');assert not region.definition.anchors
    with pytest.raises(ValueError):window.geometry_actions.new_set(refs=(),name='Empty')
    window.session.read_only=True
    with pytest.raises(PermissionError):window.geometry_actions.new_set(refs=(),name='Readonly')


def test_source_membership_actions_disabled_with_explanation(window,qapp):
    _,refs=geometry(window);one=window.geometry_actions.new_set(refs=(),name='One');two=window.geometry_actions.new_set(refs=(),name='Two')
    combined=Region('Union','geometry','mixed',BooleanRegion('union',(one.id,two.id)));window.run(commands.AddRegion(combined));qapp.processEvents()
    window.selection.set_mode('face');window.selection.restore((refs[2],))
    menu=window.tree.set_menu(combined.id)
    actions={action.text():action for action in menu.actions()}
    for name in ('Add to set','Remove from set'):
        assert not actions[name].isEnabled() and 'manual geometry' in actions[name].toolTip()
    menu.deleteLater()
