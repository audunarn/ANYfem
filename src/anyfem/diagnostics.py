"""Compact, copyable diagnostics for desktop and support workflows."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from importlib import metadata, util
import json
import math
from collections import deque
from itertools import islice
from enum import Enum
import numpy as np
import platform
import sys
from typing import Any, Callable, Iterable, Mapping

__all__ = ["ErrorDiagnostic", "build_current_state", "build_diagnostic_report"]


_PACKAGES = (
    ("ANYfem", "anyfem"),
    ("ANYsolver", "anysolver"),
    ("ANYmesher", "anymesher"),
    ("ANYgeometry", "anygeometry"),
    ("ANYmaterial", "anymaterial"),
    ("ANYfileio", "anyfileio"),
    ("ANY3dView", "any3dview"),
    ("ANYtk3D", "anytk3d"),
    ("ANYsection", "anysection"),  # optional: standard beam sections
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_ITEM_LIMIT=30
_REDACTED_KEYS={'code','script','script_code','source_code'}


def _reason(error):return type(error).__name__+': '+str(error)[:500]


def _safe(value: Any, *, depth: int = 0, _budget=None) -> Any:
    """Bound traversal before allocation; never call arbitrary serializers."""
    budget={'nodes':1200,'text':30000} if _budget is None else _budget
    if depth>7:return '<maximum diagnostic depth reached>'
    if budget['nodes']<=0:return '<diagnostic node budget reached>'
    budget['nodes']-=1
    if isinstance(value,Enum):return _safe(value.value,depth=depth+1,_budget=budget)
    if value is None or isinstance(value,bool):return value
    if isinstance(value,int):return value if value.bit_length()<2048 else '<large integer>'
    if isinstance(value,float):return value if math.isfinite(value) else str(value)
    if isinstance(value,str):
        allowed=min(2000,max(0,budget['text']));budget['text']-=min(len(value),allowed)
        return value if len(value)<=allowed else value[:allowed]+'… <text truncated>'
    if isinstance(value,np.ndarray):
        preview=[]
        if value.dtype.kind!='O':
            for item in islice(value.flat,6):preview.append(_safe(item.item(),depth=depth+1,_budget=budget))
        return {'type':'ndarray','shape':list(value.shape),'dtype':str(value.dtype),'size':int(value.size),'preview':preview,'truncated':value.size>len(preview)}
    if isinstance(value,np.generic):return _safe(value.item(),depth=depth+1,_budget=budget)
    if isinstance(value,Mapping):
        result={}
        for key in islice(iter(value),_ITEM_LIMIT):
            name=str(key)[:160] if isinstance(key,(str,int,float,bool)) else '<'+type(key).__name__+' key>'
            try:result[name]='<script code omitted>' if name.lower() in _REDACTED_KEYS else _safe(value[key],depth=depth+1,_budget=budget)
            except Exception as error:result[name]={'error':_reason(error)}
        total=len(value)
        if total>len(result):result['__diagnostic_truncation__']={'total':total,'included':len(result),'truncated':True}
        return result
    if isinstance(value,(list,tuple,set,frozenset,deque)):
        result=[_safe(item,depth=depth+1,_budget=budget) for item in islice(iter(value),_ITEM_LIMIT)]
        if len(value)>len(result):result.append({'__diagnostic_truncation__':{'total':len(value),'included':len(result),'truncated':True}})
        return result
    if is_dataclass(value):
        result={}
        for field in islice(fields(value),_ITEM_LIMIT):
            try:result[field.name]='<script code omitted>' if field.name.lower() in _REDACTED_KEYS else _safe(getattr(value,field.name),depth=depth+1,_budget=budget)
            except Exception as error:result[field.name]={'error':_reason(error)}
        if len(fields(value))>len(result):result['__diagnostic_truncation__']={'total':len(fields(value)),'included':len(result),'truncated':True}
        return result
    return '<'+type(value).__module__+'.'+type(value).__name__+'>'


def _capture(function):
    try:return function()
    except Exception as error:return {'status':'unavailable','error':_reason(error)}


def _record(record,names):
    result={}
    for name in names:
        try:
            if isinstance(record,Mapping):
                if name not in record:continue
                value=record[name]
            else:
                marker=object();value=getattr(record,name,marker)
                if value is marker:continue
            result[name]=_safe(value)
        except Exception as error:result[name]={'error':_reason(error)}
    return result


def _table(values,render=lambda value:_safe(value),*,limit=20):
    total=len(values);source=values.values() if isinstance(values,Mapping) else values;items=[]
    for record in islice(iter(source),limit):items.append(_capture(lambda record=record:render(record)))
    return {'total':total,'included':len(items),'truncated':total>len(items),'items':items}


def _features(geometry):
    history=getattr(geometry,'features',None)
    if history is None:return _table(())
    total=len(history)
    return {'status':'details_omitted' if total else 'empty','total':total,'included':0,'truncated':bool(total),'items':[],'reason':'Public feature records deep-copy history; details omitted to keep diagnosis bounded. Raw geometry input is sampled below.'}


def _geometry_counts(geometry):
    return {label:_capture(lambda name=name:len(getattr(geometry,name,()))) for label,name in (('points','vertices'),('lines','edges'),('plates','faces'),('sheets','sheets'),('members','members'),('features','features'))}


def _mesh_snapshot(mesh):
    if mesh is None:return {'status':'not_loaded'}
    counts={}
    for name,collection in (('num_nodes','nodes'),('num_elements',None),('num_quads','quads'),('num_triangles','tris'),('num_beams','beams'),('num_couplings','couplings')):
        def count(name=name,collection=collection):
            value=getattr(mesh,name,None)
            if value is None and collection:
                value=getattr(mesh,'__dict__',{}).get(collection)
                return len(value) if value is not None else None
            return _safe(value)
        counts[name]=_capture(count)
    return {'status':'loaded','type':type(mesh).__name__,'counts':counts,'metadata':_record(mesh,('diagnostics','hybrid_diagnostics','structural_preparation','quality','metadata','order')),'note':'No node coordinates, connectivity or quality audit were read.'}


def _result_snapshot(solution):
    if solution is None:return {'status':'not_loaded'}
    stored=getattr(solution,'__dict__',{})
    result={'status':'loaded','type':type(solution).__name__,'metadata':_record(solution,('status','converged','success','message','iterations','load_factor','peak_load_factor','time','warnings','outcome','info')),'arrays':{}}
    for name in ('frequencies','buckling_factors','load_factors'):
        if name in stored:result['metadata'][name]=_safe(stored[name])
    if 'shapes' in stored:result['shapes']=_capture(lambda:_table(stored['shapes'],lambda shape:_record(shape,('label','name','value'))))
    if 'steps' in stored:result['steps']=_capture(lambda:_table(stored['steps'],lambda step:_record(step,('step','time','load_factor','converged','iterations','residual_norm','arc_length','message','info'))))
    for name in ('U','u','displacements','reactions','stresses','node_stresses','element_stresses','eigenvectors','modes','times','history'):
        try:
            value=stored.get(name)
            if value is None:continue
            if isinstance(value,np.ndarray):result['arrays'][name]={'shape':list(value.shape),'dtype':str(value.dtype),'size':int(value.size)}
            elif isinstance(value,(Mapping,list,tuple)):result['arrays'][name]={'type':type(value).__name__,'count':len(value),'values':'omitted'}
        except Exception as error:result['arrays'][name]={'error':_reason(error)}
    return result


def _model_state(source,context):
    revision=context.get('revision',{})
    current=revision.get('model_hash') if isinstance(revision,Mapping) else None
    return 'unknown' if not source or not current else 'matches_current_model' if source==current else 'stale'


def build_current_state(project,*,loaded_mesh=None,solution=None,context=None):
    """Read bounded current metadata only; never generate, solve or load artifacts."""
    context=context or {}
    geometry=lambda:{'status':'input_metadata','counts':_geometry_counts(project.geometry),'features':_features(project.geometry),
        'materials':_table(project.materials),'plate_sections':_table(project.plate_sections),'beam_sections':_table(project.beam_sections),
        'section_assignments':{'plates':len(project.face_sections),'beams':len(project.edge_sections),'canonical_records':len(project.section_assignments),'plate_sample':_table(project.face_sections.keys(),lambda key:{'face_id':key,'section':project.face_sections[key]}),'beam_sample':_table(project.edge_sections.keys(),lambda key:{'edge_id':key,'section':project.edge_sections[key]}),'records':_table(project.section_assignments)},
        'vertices':_table(project.geometry.vertices,lambda record:_record(record,('id','position'))),'edges':_table(project.geometry.edges,lambda record:_record(record,('id','start','end','curve'))),'faces':_table(project.geometry.faces,lambda record:_record(record,('id','loop','holes','corners','metadata','surface'))),
        'coordinate_systems':_table(project.coordinate_systems),'regions':{'total':len(project.regions),'definitions':'preserved; not resolved or audited'},'note':'Input descriptions only; geometry was not regenerated or audited.'}
    def mesh_record(record):
        row=_record(record,('id','name','kind','status','source_model_hash','mesh_hash','artifact_id','summary','diagnostics','structural_preparation'))
        row['model_state']=_model_state(getattr(record,'source_model_hash',None),context)
        artifact=getattr(record,'artifact_id',None);row['artifact_availability']={'registered':artifact in project.artifacts if artifact else False,'disk':'not_checked'};return row
    meshing=lambda:{'status':'loaded' if loaded_mesh is not None else 'recorded_not_loaded' if len(project.mesh_records) else 'not_run',
        'global_target_size':getattr(project,'target_size',None),'settings':_safe(getattr(project,'native_mesh_settings',None)),
        'named_size_properties':_table(getattr(project,'mesh_size_properties',{})),'refinements':_table(project.refinements),
        'records':_table(project.mesh_records,mesh_record),'loaded_mesh':_mesh_snapshot(loaded_mesh),'note':'Settings and existing quality metadata only; no mesh generation or quality check was run.'}
    load_types=('point_loads','pressures','line_loads','surface_tractions','wet_pressures','connection_loads')
    def load_case(case):
        row=_record(case,('name','time','is_template','series_id','parameters','acceleration','gravity_coordinate_system_id'))
        row['load_types']={name:_capture(lambda name=name:len(getattr(case,name,()))) for name in load_types}
        row['loads']={name:_capture(lambda name=name:_table(getattr(case,name,()),lambda record:_record(record,('id','name','ref','region','value','force','moment','force_per_length','traction','distribution_policy','coordinate_system_id','envelope')),limit=2)) for name in load_types};return row
    solver_input=lambda:{'status':'configuration_only','compiled_solver_input':{'status':'not_built_by_diagnosis'},
        'load_cases':_table(project.load_cases,load_case,limit=10),'supports':_table(project.supports),'masses':_table(project.masses),'connections':_table(project.connections),'imperfections':_table(project.imperfections),
        'analyses':_table(project.analyses),'output_requests':_table(project.output_requests),'note':'Configured engineering input; no model assembly, solver deck compilation or readiness audit was run.'}
    def job(record):
        row=_record(record,('id','name','analysis_id','status','created_utc','started_utc','finished_utc','partial','model_hash','mesh_hash','analysis_hash','summary','outcome','diagnostics','result_artifact_id','log_artifact_id'))
        row['model_state']=_model_state(getattr(record,'model_hash',None),context);artifact=getattr(record,'result_artifact_id',None)
        row['result_availability']={'registered':artifact in project.artifacts if artifact else False,'artifact_id':artifact,'disk':'not_checked','loaded':'not_inferred_from_record'};return row
    output=lambda:{'status':'loaded' if solution is not None else 'dataset_registered' if isinstance(context.get('result_dataset'),Mapping) and context['result_dataset'].get('status')=='registered' else 'recorded_not_loaded' if len(project.jobs) else 'not_run',
        'jobs':_table(project.jobs,job),'artifacts':_table(project.artifacts),'loaded_result':_result_snapshot(solution),'result_dataset':_safe(context.get('result_dataset',{'status':'not_supplied'})),
        'note':'Job status/outcome and already loaded result metadata only. Artifact files and workers were not opened or polled; model hash matching does not assert solver success or full freshness.'}
    return {name:_capture(builder) for name,builder in (('geometry_input',geometry),('meshing',meshing),('solver_input',solver_input),('solver_output',output))}


@dataclass(frozen=True)
class ErrorDiagnostic:
    """One full error captured when the status bar receives a failure."""

    timestamp_utc: str
    message: str
    details: Any = None
    project_id: str = ""
    view: str = ""

    @classmethod
    def capture(
        cls,
        message: str,
        *,
        details: Any = None,
        project_id: str = "",
        view: str = "",
    ) -> "ErrorDiagnostic":
        return cls(
            _utc_now(),
            str(message),
            _safe(details),
            str(project_id),
            str(view),
        )


def _package_rows(
    version_reader: Callable[[str], str],
    origin_reader: Callable[[str], str | None],
) -> list[dict[str, str]]:
    rows = []
    for distribution, module in _PACKAGES:
        try:
            version = str(version_reader(distribution))
        except Exception:
            version = "metadata unavailable"
        try:
            origin = str(origin_reader(module) or "not importable")
        except Exception:
            origin = "origin unavailable"
        rows.append(
            {
                "distribution": distribution,
                "version": version,
                "module": module,
                "origin": origin,
            }
        )
    return rows


def _default_origin(module: str) -> str | None:
    specification = util.find_spec(module)
    return None if specification is None else specification.origin


def _project_summary(project: Any) -> dict[str, Any]:
    geometry = project.geometry
    load_count = sum(
        len(getattr(case, name, ()))
        for case in islice(project.load_cases.values(),50)
        for name in (
            "point_loads", "pressures", "line_loads", "surface_tractions",
            "connection_loads", "wet_pressures",
        )
    )
    settings = getattr(project, "native_mesh_settings", None)
    return {
        "name": str(getattr(project, "name", "")),
        "document_id": str(getattr(project, "document_id", "")),
        "read_only_reason": getattr(project, "read_only_reason", None),
        "units": _safe(getattr(project, "units", None)),
        "geometry": {
            "revision": int(getattr(geometry, "revision", 0)),
            "points": len(geometry.vertices),
            "lines": len(geometry.edges),
            "plates": len(geometry.faces),
            "sheets": len(getattr(geometry, "sheets", {})),
            "members": len(getattr(geometry, "members", {})),
            "features": len(getattr(geometry, "features", ())),
        },
        "definitions": {
            "materials": len(project.materials),
            "plate_sections": len(project.plate_sections),
            "beam_sections": len(project.beam_sections),
            "regions": len(project.regions),
            "load_cases": len(project.load_cases),
            "loads": load_count if len(project.load_cases)<=50 else None,
            "loads_count_status": "complete" if len(project.load_cases)<=50 else "not_counted_beyond_50_cases",
            "supports": len(project.supports),
            "imperfections": len(project.imperfections),
        },
        "records": {
            "meshes": len(project.mesh_records),
            "analyses": len(project.analyses),
            "jobs": len(project.jobs),
            "artifacts": len(project.artifacts),
        },
        "mesh_settings": None if settings is None else _safe(settings),
    }


def build_diagnostic_report(
    project: Any,
    *,
    errors: Iterable[ErrorDiagnostic] = (),
    context: Mapping[str, Any] | None = None,
    recent_commands: Iterable[str] = (),
    loaded_mesh: Any = None,
    solution: Any = None,
    version_reader: Callable[[str], str] = metadata.version,
    origin_reader: Callable[[str], str | None] = _default_origin,
) -> str:
    """Build one plain-text report suitable for clipboard issue reporting."""

    captured = deque(errors,maxlen=10)
    payload = {
        "report": "ANYfem diagnostic",
        "generated_utc": _utc_now(),
        "errors": [_safe(item) for item in captured],
        "runtime": {
            "python": sys.version.replace("\n", " "),
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "packages": _package_rows(version_reader, origin_reader),
        "project": _capture(lambda:_project_summary(project)),
        "current_state": build_current_state(project,loaded_mesh=loaded_mesh,solution=solution,context=context),
        "application": _safe(context or {}),
        "recent_gui_commands": [_command_text(item) for item in deque(recent_commands,maxlen=100)],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _command_text(value):
    text=str(value)
    return '<script command omitted>' if any(token in text.lower() for token in ('code=','runscript','exec(')) else text[:1000]
