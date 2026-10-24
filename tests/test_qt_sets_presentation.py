"""Visible set selectors preserve hidden existing assignments."""
import os
import pytest
if os.environ.get('ANYFEM_RUN_QT_TESTS')!='1':pytest.skip('Qt opt-in',allow_module_level=True)
from test_qt_geometry_actions import qapp,window
from anyfem import commands
from anyfem.model.attributes import Support
from anyfem.model.records import OutputRequest
from anyfem.model.regions import Region,ManualRegion,MeshEntityRef
from anyfem.io.project_file import project_to_dict
from anyfem.tables import CategoryAdapter


def test_output_set_picker_excludes_hidden_and_preserves_existing_assignment(window,qapp):
    v=commands.AddPoint(0,0).do(window.project);ref=window.project.geometry.entity_ref('vertex',v)
    support=commands.AddSupport(Support('Support',ref,{'ux':0.})).do(window.project)
    visible=Region('User set','geometry','vertex',ManualRegion((ref,)));commands.AddRegion(visible).do(window.project)
    record=OutputRequest(('displacement',),support.region,'node',label='Output');commands.AddOutputRequest(record).do(window.project)
    before=project_to_dict(window.project)
    panel=window.panels['Definitions'];panel.load_values('EditOutputRequest',request_id=record.id,replacement=record)
    field=panel.record_fields['replacement'][1]['region'][0]
    hidden={region.id for region in window.project.regions if region.hidden}
    assert not hidden & {field.itemData(i) for i in range(field.count())}
    assert field.text()==support.region.id
    panel.refresh();assert field.text()==support.region.id
    assert panel.command_options()['replacement'].region==record.region
    assert project_to_dict(window.project)==before
    adapter=CategoryAdapter(window.project,'Output requests');column=next(c for c in adapter.columns if c.key=='region')
    assert adapter.get(adapter.rows[0],column)=='Assigned geometry'


def test_mesh_set_inspection_is_safe_and_not_geometry_editable(window,qapp):
    mesh=Region('Nodes','mesh','node',ManualRegion((MeshEntityRef('mesh1','node',1),)));window.run(commands.AddRegion(mesh));qapp.processEvents()
    before=project_to_dict(window.project)
    assert mesh.id in window.tree._rows['Sets']
    dialog=window.tree.edit_record(('sets',mesh.id));qapp.processEvents()
    assert dialog.windowTitle()=='Set: Nodes'
    with pytest.raises(ValueError):window.geometry_actions.rename_set(mesh.id)
    assert project_to_dict(window.project)==before
    dialog.close()
