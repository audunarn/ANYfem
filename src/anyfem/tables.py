"""Shared, toolkit-neutral adapters for complete worktree category tables."""
from dataclasses import dataclass,replace
import json
from uuid import uuid4
import numpy as np
from anygeometry import EntityRef,feature_entity_owners
from . import commands
from .mesh_sizes import MeshSizeProperty,NamedRefinement,SetMeshSizeProperty,DeleteMeshSizeProperty
from .load_assignment import attribute_targets
from .presentation.points import displayed_geometry_point_ids
from .presentation.sets import is_user_set,set_geometry_references

CATEGORIES=('Beams','Points','Lines','Plates','Construction planes','Sketches','Sets','Wet surfaces','Features','Materials','Plate sections','Beam sections','Load cases','Supports','Masses','Connections','Imperfections','Coordinate systems','Output requests','Meshes','Analyses','Jobs','Mesh sizes','Loads','Results')
ATTRS={'Materials':'materials','Plate sections':'plate_sections','Beam sections':'beam_sections','Load cases':'load_cases','Supports':'supports','Masses':'masses','Connections':'connections','Imperfections':'imperfections','Coordinate systems':'coordinate_systems','Output requests':'output_requests','Meshes':'mesh_records','Analyses':'analyses','Jobs':'jobs','Wet surfaces':'wet_surfaces'}
DOFS=('ux','uy','uz','rx','ry','rz')


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    dimension: str|None = None
    editable: bool = False


@dataclass(frozen=True)
class TableRow:
    key: object
    record: object
    value: object
    case: str = ''


class CategoryAdapter:
    def __init__(self,project,category):
        if category not in CATEGORIES:raise ValueError('Unknown worktree category')
        self.project=project;self.category=category;self._legacy={};self.refresh()

    def refresh(self):
        p=self.project;g=p.geometry;c=self.category;rows=[]
        if c in {'Points','Lines','Plates'}:
            kind={'Points':'vertex','Lines':'edge','Plates':'face'}[c]
            owners=feature_entity_owners(g);owned={ref.id for ref in owners if ref.kind==kind}
            if kind=='edge':owned.update(use.edge_id for use in g.member_edge_uses.values())
            store=g.vertices if kind=='vertex' else g.edges if kind=='edge' else g.faces
            ids=displayed_geometry_point_ids(g) if kind=='vertex' else store
            rows=[TableRow(i,store[i],EntityRef(kind,i)) for i in sorted(ids) if i not in owned]
        elif c=='Beams':rows=[TableRow(i,record,('members',i)) for i,record in g.members.items()]
        elif c in {'Features','Sketches'}:
            rows=[TableRow(record.feature_id,record,('geometry',record.feature_id)) for record in g.features.records if c=='Features' or record.kind in {'geometry.sketch.plane','geometry.sketch.extrude'}]
        elif c=='Sets':rows=[TableRow(record.id,record,('sets',record.id)) for record in p.regions if is_user_set(record)]
        elif c=='Construction planes':rows=[TableRow(key,record,('coordinate_systems',key)) for key,record in p.coordinate_systems.items() if key!='global' and record.kind=='cartesian']
        elif c=='Mesh sizes':
            rows=[TableRow(record.id,record,('mesh_size_properties',record.id)) for record in p.mesh_size_properties.values()]
            for record in p.refinements:
                if isinstance(record,NamedRefinement):continue
                entry=self._legacy.setdefault(id(record),(record,str(uuid4())))
                rows.append(TableRow(entry[1],record,('refinements',id(record))))
        elif c=='Loads':
            for case in p.load_cases.values():
                for name in ('point_loads','pressures','line_loads','surface_tractions','wet_pressures','connection_loads'):
                    rows.extend(TableRow(record.id,record,('load',record.id),case.name) for record in getattr(case,name))
        elif c=='Results':
            seen=set()
            for job in p.jobs.values():
                if job.result_artifact_id and job.result_artifact_id not in seen:
                    seen.add(job.result_artifact_id);rows.append(TableRow(job.result_artifact_id,job,('result',job.result_artifact_id)))
        else:
            attribute=ATTRS[c];values=getattr(p,attribute);pairs=values.items() if hasattr(values,'items') else enumerate(values)
            for key,record in pairs:
                if c=='Materials':key=p.material_ids.get(record.name,record.name)
                else:key=getattr(record,'id',key)
                rows.append(TableRow(key,record,(attribute,key)))
        self.rows=rows;self.by_key={row.key:row for row in rows};self.columns=self._columns()

    def _columns(self):
        c=self.category;columns=[Column('name','Name')]
        if c=='Points':return [Column(axis,axis.upper(),'length',True) for axis in ('x','y','z')]
        if c in {'Features','Sketches'}:return [Column('name','Name',editable=True),Column('kind','Kind'),Column('state','State'),Column('parameters','Parameters',editable=True)]
        if c=='Materials':return columns+[Column('E','Young modulus','pressure',True),Column('nu','Poisson ratio',editable=True),Column('density','Density','density',True),Column('yield_stress','Yield stress','pressure',True),Column('symmetry','Symmetry')]
        if c=='Plate sections':return columns+[Column('thickness','Thickness','length',True),Column('material','Material',editable=True)]
        if c=='Beam sections':return columns+[Column('profile','Profile',editable=True),Column('material','Material',editable=True)]+[Column(key,label,'length',True) for key,label in [('web_height','Web height'),('web_thickness','Web thickness'),('flange_width','Flange width'),('flange_thickness','Flange thickness'),('eccentricity','Eccentricity')]]+[Column('offset_mode','Offset mode',editable=True),Column('rotation_deg','Rotation (deg)',editable=True)]
        if c=='Supports':return [Column('name','Name',editable=True)]+[Column(dof,dof.upper(),'length' if dof.startswith('u') else 'angle',True) for dof in DOFS]+[Column('coordinate_system_id','Coordinate system')]
        if c=='Imperfections':return columns+[Column('kind','Kind'),Column('amplitude','Amplitude','length',True)]
        if c=='Masses':return [Column('name','Name',editable=True),Column('value','Mass','mass',True),Column('distribution_policy','Distribution',editable=True)]
        if c=='Mesh sizes':return [Column('name','Name',editable=True),Column('size','Target size','length',True),Column('radius','Radius','length',True),Column('growth','Growth',editable=True)]
        if c in {'Plates','Lines','Beams'}:return columns+[Column('section','Section',editable=True),Column('material','Material')]+([Column('thickness','Thickness','length')] if c=='Plates' else [])+[Column('edges' if c=='Plates' else 'segments' if c=='Beams' else 'curve','Edges' if c=='Plates' else 'Segments' if c=='Beams' else 'Curve')]+([Column('start','Start point'),Column('end','End point')] if c=='Lines' else [])
        if c=='Loads':return columns+[Column('type','Type'),Column('case','Load case'),Column('value','Pressure','pressure',True)]+[Column(key,key.replace('_',' ').title(),editable=True) for key in ('force_x','force_y','force_z','moment_x','moment_y','moment_z')]+[Column('distribution_policy','Distribution',editable=True)]
        fields={'Lines':('start','end','curve'),'Plates':('edges',),'Beams':('segments',),'Construction planes':('kind','origin'),'Coordinate systems':('kind','origin'),'Sets':('entity_kind','domain','definition'),'Wet surfaces':('fluid_side',),'Connections':('kind','reference','weighting'),'Imperfections':('type',),'Output requests':('quantity_keys','region','location','recovery','reduction','basis'),'Load cases':('template','time'),'Meshes':('kind','status','mesh_hash'),'Analyses':('type','target_kind','target_id'),'Jobs':('status','created_utc','finished_utc'),'Results':('status','finished_utc')}.get(c,())
        return columns+[Column(field,'Set' if field=='region' else field.replace('_',' ').title()) for field in fields]

    def dimension(self,row,column):
        if self.category=='Loads' and column.key.startswith(('force_','moment_')):
            if column.key.startswith('moment_'):return 'moment'
            return 'line_load' if hasattr(row.record,'force_per_length') else 'pressure' if hasattr(row.record,'traction') else 'force'
        return column.dimension

    def get(self,row,column):
        key=column.key;record=row.record;g=self.project.geometry
        if self.category in {'Plates','Lines','Beams'} and key in {'section','material','thickness'}:
            refs=self.targets(row);mapping=self.project.face_sections if self.category=='Plates' else self.project.edge_sections
            names={mapping.get(ref.id) for ref in refs};name=next(iter(names)) if len(names)==1 else 'Mixed'
            if key=='section':return name or 'Unassigned'
            definition=(self.project.plate_sections if self.category=='Plates' else self.project.beam_sections).get(name)
            return getattr(definition,key,None)
        if self.category=='Points':return float(g.vertex_position(row.key)[('x','y','z').index(key)])
        if key in DOFS and self.category=='Supports':return record.constraints.get(key)
        if self.category=='Materials' and key in {'E','nu'}:return record.constants.get({'E':'elastic_modulus','nu':'poisson_ratio'}[key])
        if key=='name':return getattr(record,'name',getattr(record,'label','')) or ('Beam '+str(row.key) if self.category=='Beams' else self.category.rstrip('s'))
        if key=='type':return type(record).__name__
        if key=='case':return row.case
        if self.category=='Loads' and key.startswith(('force_','moment_')):
            field='moment' if key.startswith('moment_') else next((name for name in ('force','force_per_length','traction') if hasattr(record,name)),None)
            return None if field is None or not hasattr(record,field) else float(getattr(record,field)[('x','y','z').index(key[-1])])
        if key=='coordinate_system_id':return getattr(self.project.coordinate_systems.get(getattr(record,key,'')),'name','Unassigned')
        if key=='region':
            scope=self.project.regions[record.region.id]
            return ('Assigned mesh' if scope.domain.value=='mesh' else 'Assigned geometry') if scope.hidden else scope.name
        if key=='edges':return len(record.loop)
        if key=='segments':return len(record.edge_use_ids)
        if key=='definition':return type(record.definition).__name__.removesuffix('Region')
        if key=='template':return record.is_template
        if key=='curve':return type(getattr(record,'curve',None)).__name__
        return getattr(record,key,None)

    def reason(self,row,column):
        if not column.editable:return 'Derived or identity field. Use Edit / source settings when available.'
        if self.category=='Materials' and column.key in {'E','nu'} and {'E':'elastic_modulus','nu':'poisson_ratio'}[column.key] not in row.record.constants:return 'Edit the full material definition for this symmetry.'
        if self.category=='Mesh sizes' and not isinstance(row.record,MeshSizeProperty):return 'Legacy refinement policy; use the Mesh workspace.'
        if self.category=='Loads':
            if column.key=='value' and type(row.record).__name__!='Pressure':return 'Use the original editor for this load representation.'
            if column.key=='distribution_policy' and not hasattr(row.record,'distribution_policy'):return 'Not applicable to this load.'
            if column.key.startswith(('force_','moment_')) and self.get(row,column) is None:return 'Not applicable to this load.'
        return ''

    def edit_command(self,key,field,text):
        row=self.by_key[key];column=next(column for column in self.columns if column.key==field)
        reason=self.reason(row,column)
        if reason:raise ValueError(reason)
        record=row.record;dimension=self.dimension(row,column)
        if self.category in {'Plates','Lines','Beams'} and field=='section':
            definitions=self.project.plate_sections if self.category=='Plates' else self.project.beam_sections
            if str(text) not in definitions:raise ValueError('Choose an existing section name: '+', '.join(definitions))
            from .property_assignment import AtomicPropertyAssignment
            command=commands.AssignPlate if self.category=='Plates' else commands.AssignBeam
            return AtomicPropertyAssignment(tuple(command(ref.id,str(text)) for ref in self.targets(row)),label='assign table section')
        value=self.project.units.parse(str(text),dimension) if dimension and str(text).strip() not in {'','Free','free'} else text
        if self.category=='Points':
            xyz=list(self.project.geometry.vertex_position(key));xyz[('x','y','z').index(field)]=float(value)
            return commands.MovePoint(key,*xyz)
        if self.category in {'Features','Sketches'}:
            return commands.EditFeature(key,**{field:json.loads(text) if field=='parameters' else str(text)})
        if self.category=='Materials':
            if field in {'E','nu'}:updated=replace(record,constants={**record.constants,{'E':'elastic_modulus','nu':'poisson_ratio'}[field]:float(value)})
            else:updated=replace(record,**{field:float(value)})
            updated.build();return commands.AddMaterial(updated)
        if self.category=='Supports' and field in DOFS:
            constraints=dict(record.constraints)
            if str(text).strip() in {'','Free','free'}:constraints.pop(field,None)
            else:constraints[field]=float(value)
            updated=replace(record,constraints=constraints)
        elif self.category=='Loads' and field.startswith(('force_','moment_')):
            name='moment' if field.startswith('moment_') else next(name for name in ('force','force_per_length','traction') if hasattr(record,name));vector=list(getattr(record,name));vector[('x','y','z').index(field[-1])]=float(value);updated=replace(record,**{name:vector})
        else:
            old=self.get(row,column)
            if dimension:value=float(value)
            elif isinstance(old,(float,int)) and not isinstance(old,bool):value=float(text)
            else:value=str(text)
            updated=replace(record,**{field:value})
        if self.category=='Plate sections':return commands.AddPlateSection(updated)
        if self.category=='Beam sections':return commands.AddBeamSection(updated)
        if self.category=='Mesh sizes':return SetMeshSizeProperty(updated)
        return commands.EditAttribute(updated)

    def targets(self,row):
        value=row.value;g=self.project.geometry
        if isinstance(value,EntityRef):return (value,)
        if value[0]=='mesh_size_properties':
            return tuple(dict.fromkeys(ref for item in self.project.refinements if isinstance(item,NamedRefinement) and item.property_id==row.key for source in self.project.resolve_geometry_attachment(item.ref) for ref in g.resolve_ref(source)))
        if value[0]=='output_requests':
            return tuple(self.project.regions.resolve(row.record.region.id,geometry=g,feature_resolver=lambda anchor:g.features.resolve(anchor,g)))
        if value[0]=='members':return tuple(EntityRef('edge',g.member_edge_uses[i].edge_id) for i in row.record.edge_use_ids)
        if value[0]=='geometry':return tuple(ref for ref,owner in feature_entity_owners(g).items() if owner==row.key)
        if value[0]=='sets':
            try:return set_geometry_references(self.project,row.key)
            except (ValueError,KeyError):return ()
        if self.category in {'Supports','Masses','Loads','Wet surfaces','Connections','Imperfections'}:return attribute_targets(self.project,row.record)
        return ()

    def delete_command(self,row):
        value=row.value
        if isinstance(value,EntityRef):return commands.DeleteEntity(value)
        attribute,key=value
        if attribute=='geometry':return commands.DeleteFeature(int(key))
        if attribute in {'supports','masses','load','wet_surfaces','connections','imperfections'}:return commands.DeleteAttribute(str(key))
        if attribute=='load_cases':return commands.DeleteLoadCase(row.record.name)
        if attribute=='mesh_size_properties':return DeleteMeshSizeProperty(str(key))
        if attribute=='output_requests':return commands.DeleteOutputRequest(str(key))
        kind={'materials':'material','plate_sections':'plate_section','beam_sections':'beam_section','coordinate_systems':'coordinate','regions':'region','sets':'region','mesh_records':'mesh','analyses':'analysis','jobs':'job','result':'result'}.get(attribute)
        if kind:return commands.DeleteProjectRecord(kind,row.record.name if attribute in {'materials','plate_sections','beam_sections'} else str(key))
        raise ValueError('Delete this derived geometry through its source feature or canonical Geometry tools.')


    @property
    def creation_fields(self):
        if self.category=='Points':return [Column(axis,axis.upper(),'length',True) for axis in ('x','y','z')]
        if self.category=='Materials':return [Column('name','Name',editable=True),Column('E','Young modulus','pressure',True),Column('nu','Poisson ratio',editable=True),Column('density','Density','density',True),Column('yield_stress','Yield stress','pressure',True)]
        if self.category=='Plate sections':return [Column('name','Name',editable=True),Column('thickness','Thickness','length',True),Column('material','Material',editable=True)]
        if self.category=='Beam sections':return [Column('name','Name',editable=True)]+self.columns[1:]
        if self.category=='Load cases':return [Column('name','Name',editable=True)]
        return []

    def create_command(self,values):
        if self.category=='Points':return commands.AddPoint(**values)
        if self.category=='Materials':
            from .model.materials import MaterialSpec
            if values['name'] in self.project.materials:raise ValueError('A material with this name already exists')
            record=MaterialSpec(name=values['name'],constants={'elastic_modulus':values['E'],'poisson_ratio':values['nu']},density=values['density'],yield_stress=values['yield_stress']);record.build();return commands.AddMaterial(record)
        if self.category=='Plate sections':
            from .model.sections import PlateSection
            if values['name'] in self.project.plate_sections:raise ValueError('A section with this name already exists')
            return commands.AddPlateSection(PlateSection(**values))
        if self.category=='Beam sections':
            from .model.sections import BeamSection
            if values['name'] in self.project.beam_sections:raise ValueError('A section with this name already exists')
            return commands.AddBeamSection(BeamSection(**values))
        if self.category=='Load cases':return commands.AddLoadCase(**values)
        raise ValueError('Use the existing creation workspace for this category')
