from dataclasses import replace
import numpy as np
import pytest
from anyfem import commands
from anyfem.application.controller import default_project
from anyfem.presentation.colour_modes import colour_scene,UNASSIGNED
from anyfem.ui.scene import build_geometry_scene,build_mesh_scene,Scene,FacePatch,Polyline
from anyfem.model.regions import EntityRef
from anyfem.selection import MeshEntityRef
from anyfem.model.units import unit_profile
from anymesher.mesh import Mesh


def model():
    p=default_project();commands.AddStiffenedPanel(4,3,1,beam_section='stiffener',plate_thickness=.02,material='S355').do(p)
    return p


def test_exact_collapsed_faces_thickness_identity_units_and_immutable_cache():
    p=model();faces=sorted(p.geometry.faces);base=p.plate_sections[p.face_sections[faces[0]]]
    p.plate_sections['other']=replace(base,name='Other same thickness',id='other')
    p.face_sections[faces[1]]='other'
    p.plate_sections['thick']=replace(base,name='Thick',thickness=.04,id='thick');p.face_sections[faces[2]]='thick'
    p.units=unit_profile('SI-mm-N-MPa');scene=build_geometry_scene(p)
    original=[tuple(f.colors) for f in scene.faces]
    result=colour_scene(scene,p,'thickness')
    assert [tuple(f.colors) for f in scene.faces]==original
    assert all(a is not b for a,b in zip(scene.faces,result.faces))
    colors={}
    for patch in result.faces:
        for i,c in enumerate(patch.colors):
            owners=patch.polygon_visibility_owners[i] if patch.polygon_visibility_owners else patch.owners_for_polygon(i)
            for r in owners:
                if r.kind=='face':colors[r.id]=c
    assert colors[faces[0]]==colors[faces[1]]!=colors[faces[2]]
    assert {label for label,_ in result.legend['entries']}=={'20 mm','40 mm','Unassigned'}
    assert [f.colors for f in colour_scene(scene,p).faces]==[f.colors for f in scene.faces]


def test_sections_colour_rendered_profiles_and_axes_consistently():
    p=model();scene=build_geometry_scene(p);result=colour_scene(scene,p,'section')
    edge=next(iter(p.edge_sections));key=EntityRef('edge',edge)
    axes=[line.color for line in result.lines if line.ref==key]
    patches=[c for f in result.faces if f.ref==key for c in f.colors]
    assert axes and patches and set(axes+patches)=={axes[0]}
    assert any(label=='stiffener' for label,_ in result.legend['entries'])
    assert any(label=='Unassigned' for label,_ in result.legend['entries'])


def test_target_public_field_local_refinement_and_unset():
    from anyfem.mesh.refinement import Refinement
    p=model();p.target_size=2
    p.refinements.append(Refinement(size=.25,center=(0,0,0),radius=1,growth=1.2))
    scene=Scene(lines=[Polyline(EntityRef('edge',next(iter(p.geometry.edges))),np.array([[0,0,0],[0.1,0,0]]))])
    result=colour_scene(scene,p,'density');assert result.legend['entries'][0][0]=='0.25 m'
    p.target_size=None;result=colour_scene(scene,p,'density')
    assert result.lines[0].color==UNASSIGNED and 'not set' in result.legend['note']


def test_invalid_attachment_is_explicit_and_does_not_mutate_project(monkeypatch):
    from anyfem.mesh.refinement import Refinement
    p=model();p.target_size=1;p.refinements.append(Refinement(size=.2,ref=EntityRef('face',next(iter(p.geometry.faces)))))
    monkeypatch.setattr(type(p),'resolve_geometry_attachment',lambda *a:(_ for _ in ()).throw(RuntimeError('unresolved face')))
    result=colour_scene(build_geometry_scene(p),p,'density')
    assert 'unresolved face' in result.legend['note']
    monkeypatch.setattr(type(p),'resolve_geometry_attachment',lambda *a:())
    result=colour_scene(build_geometry_scene(p),p,'density')
    assert 'no resolved geometry' in result.legend['note']


def test_imported_mesh_actual_density_and_available_synthetic_sections():
    p=model();p.geometry=type(p.geometry)();p.mesh_only=True
    p.edge_sections={90:'stiffener'};p.face_sections={91:next(name for name,section in p.plate_sections.items() if section.thickness==.02)}
    mesh=Mesh(nodes={1:np.array([0.,0.,0.]),2:np.array([2.,0.,0.]),3:np.array([2.,2.,0.]),4:np.array([0.,2.,0.]),5:np.array([1.,0.,0.])},quads={11:(1,2,3,4)},beams={12:(1,5,2)},elements_of_face={91:[11]},elements_of_edge={90:[12]})
    scene=build_mesh_scene(p,mesh,show_beam_sections=False)
    result=colour_scene(scene,p,'density',mesh=mesh)
    assert result.legend['entries']==[('2 m',result.legend['entries'][0][1])]
    assert 'Actual element size' in result.legend['title']
    assert any(label=='stiffener' for label,_ in colour_scene(scene,p,'section',mesh=mesh).legend['entries'])
    assert any(label=='0.02 m' for label,_ in colour_scene(scene,p,'thickness',mesh=mesh).legend['entries'])


def test_results_are_never_recoloured():
    p=model();scene=build_geometry_scene(p);scene.result_field=object();scene.legend={'title':'Stress','levels':[1.]}
    assert colour_scene(scene,p,'thickness').legend==scene.legend
    assert colour_scene(scene,p,'section').faces==scene.faces
    with pytest.raises(ValueError):colour_scene(scene,p,'missing')


def test_density_legend_spans_range_despite_many_nearly_equal_elements():
    p=default_project();mesh=Mesh();scene=Scene()
    for i,size in enumerate([.2+1e-8*j for j in range(20)]+[.5]):
        mesh.nodes[2*i+1]=np.array([0.,i,0.]);mesh.nodes[2*i+2]=np.array([size,i,0.])
        mesh.beams[i+1]=(2*i+1,2*i+2)
        scene.lines.append(Polyline(None,np.array([mesh.nodes[2*i+1],mesh.nodes[2*i+2]]),owners=(MeshEntityRef('element',i+1),)))
    result=colour_scene(scene,p,'density',mesh=mesh)
    labels=[text for text,_ in result.legend['entries']]
    assert labels==['0.2 m','0.25 m','0.3 m','0.35 m','0.4 m','0.45 m','0.5 m']
    assert result.legend['title']=='Actual element size'
    assert result.legend['note']=='Shell √area; beam length'
    # Range levels only affect the key; real near-equal sizes keep real colours.
    assert result.lines[0].color==result.legend['entries'][0][1]
    assert result.lines[-1].color==result.legend['entries'][-1][1]


def test_density_precision_consistent_uniform_jitter_uses_one_colour():
    p=default_project();mesh=Mesh();scene=Scene()
    for i in range(15):
        size=.2+1e-8*i
        mesh.nodes[2*i+1]=np.array([0.,i,0.]);mesh.nodes[2*i+2]=np.array([size,i,0.])
        mesh.beams[i+1]=(2*i+1,2*i+2)
        scene.lines.append(Polyline(None,np.array([mesh.nodes[2*i+1],mesh.nodes[2*i+2]]),owners=(MeshEntityRef('element',i+1),)))
    before={key:value.copy() for key,value in mesh.nodes.items()}
    result=colour_scene(scene,p,'density',mesh=mesh)
    assert len(result.legend['entries'])==1 and result.legend['entries'][0][0]=='0.2 m'
    assert len({line.color for line in result.lines})==1
    assert all(np.array_equal(mesh.nodes[k],v) for k,v in before.items())
