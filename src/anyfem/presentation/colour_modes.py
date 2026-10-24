"""Transient engineering colours for model scenes; no model or cache mutation."""
from __future__ import annotations
from dataclasses import replace
import colorsys
import hashlib
import numpy as np
from ..model.regions import EntityRef
from ..selection import MeshEntityRef

UNASSIGNED = '#9b9fa5'
MODES = {'default', 'thickness', 'section', 'density'}


def _category_colour(key):
    hue = int(hashlib.sha256(str(key).encode('utf8')).hexdigest()[:8],16)/0xffffffff
    return '#%02x%02x%02x' % tuple(round(v*255) for v in colorsys.hsv_to_rgb(hue,.63,.83))


def _target_field(project):
    """Use the mesher's own size field and the project's attachment resolver."""
    from anymesher.refinement import SizeField
    settings = project.native_mesh_settings
    size = settings.get('target_size') if isinstance(settings,dict) else getattr(settings,'target_size',None)
    size = size if size is not None else project.target_size
    if size is None:
        return None, 'Global target is not set'
    try:
        zones=[]
        for zone in project.refinements:
            if zone.ref is None:zones.append(zone)
            else:
                references=tuple(project.resolve_geometry_attachment(zone.ref))
                if not references:raise ValueError(f"Refinement {zone.name!r} has no resolved geometry")
                zones.extend(replace(zone,ref=ref) for ref in references)
        return SizeField(project.geometry,float(size),zones), ''
    except Exception as error:
        # An invalid document is inspectable; never fabricate an alternate field.
        return None, 'Target unavailable: '+str(error)


def colour_scene(scene, project, mode='default', *, mesh=None):
    """Return independent coloured draw records, preserving every pick binding.

    Geometry target sizes are sampled at displayed face/line centres. FE sizes
    use corner polygons (sqrt area) and beam spans, never a display section face.
    """
    if mode not in MODES:raise ValueError('unknown model colour mode')
    result=scene.copy()
    if mode=='default' or scene.result_field is not None:return result
    field,note=_target_field(project) if mode=='density' and mesh is None else (None,'')
    values={}
    element_faces={} if mesh is None else {eid:fid for fid,ids in mesh.elements_of_face.items() for eid in ids}
    element_edges={} if mesh is None else {eid:edge for edge,ids in mesh.elements_of_edge.items() for eid in ids}
    actual_sizes={}
    def register(key,label):
        values[key]=label
        return key
    def classify(owners,points):
        geom=next((x for x in owners if isinstance(x,EntityRef) and x.kind in ('face','edge')),None)
        element=next((x.id for x in owners if isinstance(x,MeshEntityRef) and x.kind=='element'),None)
        face_id=geom.id if geom is not None and geom.kind=='face' else element_faces.get(element)
        edge_id=geom.id if geom is not None and geom.kind=='edge' else element_edges.get(element)
        if mode=='thickness':
            thickness=None
            if face_id is not None:
                key=project.face_sections.get(face_id);section=project.plate_sections.get(key)
                thickness=None if section is None else section.thickness
            if thickness is None and mesh is not None and element is not None:
                thickness=mesh.thickness_of_face.get(face_id)
            return register(float(thickness),project.units.format(thickness,'length')) if thickness is not None else register(None,'Unassigned')
        if mode=='section':
            section=None
            if edge_id is not None:section=project.beam_sections.get(project.edge_sections.get(edge_id))
            return register(section.id,section.name) if section is not None else register(None,'Unassigned')
        if mesh is None:
            size=None if field is None else float(field.size_at(np.mean(points,axis=0))[0])
        elif element in actual_sizes:size=actual_sizes[element]
        elif element in mesh.beams:
            nodes=mesh.beams[element];size=float(np.linalg.norm(np.asarray(mesh.nodes[nodes[-1]])-mesh.nodes[nodes[0]]))
        elif element is not None:
            polygon=np.asarray([mesh.nodes[n] for n in mesh.corners_of(element)],dtype=float)
            area=.5*np.linalg.norm(np.sum(np.cross(polygon,np.roll(polygon,-1,axis=0)),axis=0))
            size=float(np.sqrt(area))
        else:size=None
        if mesh is not None and element is not None:actual_sizes[element]=size
        return register(size,project.units.format(size,'length')) if size is not None else register(None,'Unassigned / unavailable')
    face_keys=[]
    for patch in scene.faces:
        keys=[]
        for i,polygon in enumerate(patch.polygons):
            owners=patch.polygon_visibility_owners[i] if patch.polygon_visibility_owners is not None else patch.owners_for_polygon(i)
            # Mesh owners remain essential alongside exact geometry owners.
            if mesh is not None:owners=patch.owners_for_polygon(i)
            keys.append(classify(owners,polygon))
        face_keys.append(keys)
    line_keys=[]
    for line in scene.lines:
        owners=line.owners or (() if line.ref is None else (line.ref,))
        # Keep annotations and topology outlines canonical in section/thickness views.
        applicable=not line.annotation and (mode=='density' or (mode=='section' and any(getattr(x,'kind',None)=='edge' or (isinstance(x,MeshEntityRef) and x.kind=='element' and mesh is not None and x.id in mesh.beams) for x in owners)))
        line_keys.append(classify(owners,line.points) if applicable else 'unchanged')
    numeric=sorted(k for k in values if k is not None) if mode!='section' else []
    uniform_density=(mode=='density' and bool(numeric) and project.units.format(numeric[0],'length',precision=4)==project.units.format(numeric[-1],'length',precision=4))
    def color(key):
        if key is None:return UNASSIGNED
        if mode!='density':return _category_colour(key)
        lo,hi=(numeric[0],numeric[-1]) if numeric else (0,0)
        fraction=0.5 if hi==lo or uniform_density else (key-lo)/(hi-lo)
        return '#%02x%02x%02x' % tuple(round(v*255) for v in colorsys.hsv_to_rgb((1-fraction)*.65,.7,.86))
    result.faces=[replace(patch,colors=[color(key) for key in keys]) for patch,keys in zip(scene.faces,face_keys)]
    result.lines=[line if key=='unchanged' else replace(line,color=color(key)) for line,key in zip(scene.lines,line_keys)]
    keys=sorted(values,key=lambda k:(k is None,str(k))) if mode=='section' else numeric+([None] if None in values else [])
    if mode=='density' and len(numeric)>9:
        keys=list(np.linspace(numeric[0],numeric[-1],7))+([None] if None in values else [])
    if uniform_density:keys=[numeric[0]]+([None] if None in values else [])
    title={'thickness':'Plate thickness','section':'Beam section','density':'Actual element size' if mesh is not None else 'Target mesh size'}[mode]
    if mode=='density':
        explanation='Shell √area; beam length' if mesh is not None else 'Sampled at face/line centres'
        note=explanation+(' — '+note if note else '')
    entries=[(project.units.format(k,'length',precision=4) if mode=='density' and k is not None else values[k],color(k)) for k in keys]
    result.legend={'title':title,'entries':entries,'note':note}
    return result
