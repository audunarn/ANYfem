"""Optional mean-water-level hydrostatic pressure for the admitted fixed-hull wave load.

One constant ordinary ``WetPressure`` is composed with the dynamic mapped history of a wave series; it is
never part of a spectral response bank. Pressure physics is the standard ANYloads expression evaluation and
the force-preview baseline is ANYhydro's exact public buoyancy wrench; nothing is re-derived here.
"""
from uuid import NAMESPACE_URL,uuid5
import json
import numpy as np

RHO=1025.
GRAVITY=9.81
DRAFT=1.5
# Project frame: waterplane at z=1.5 m (hydro z=0). Elements above the waterline evaluate to zero.
EXPRESSION='1025.0*9.81*max(1.5-z,0.0)'
NAME='Hydrostatic pressure (mean water level)'
BOTTOM_PRESSURE=RHO*GRAVITY*DRAFT
_NAMESPACE='anyfem:wave-hydrostatic:v1:'


def static_pressure_id(dynamic_id):
    """Deterministic identity of the companion owned by one dynamic wave pressure."""
    return str(uuid5(NAMESPACE_URL,_NAMESPACE+str(dynamic_id)))


def make_static_pressure(dynamic):
    from ..model import WetPressure
    return WetPressure(expression=EXPRESSION,surfaces=tuple(dynamic.surfaces),scale=1.,name=NAME,
        id=static_pressure_id(dynamic.id))


def is_owned_static(load,dynamic):
    """True only for the exact canonical companion of this dynamic history pressure."""
    from ..model import WetPressure
    return (type(load) is WetPressure and type(dynamic) is WetPressure and dynamic.history is not None
        and load.id==static_pressure_id(dynamic.id) and load.expression==EXPRESSION and load.value is None
        and load.mapping is None and load.code is None and load.table is None and load.history is None
        and load.table_case is None and not load.code_vectorized and load.mesh_content_sha256 is None
        and load.envelope is None and type(load.scale) is float and load.scale==1. and load.name==NAME
        and load.mapping_key=='face' and tuple(load.surfaces)==tuple(dynamic.surfaces))


def owned_pair(case):
    """(dynamic, static) when the case holds exactly the app-owned pair in generated order, else None."""
    loads=case.wet_pressures
    if len(loads)==2 and getattr(loads[0],'history',None) is not None and is_owned_static(loads[1],loads[0]):
        return loads[0],loads[1]
    return None


def wave_loads_without_companion(case):
    """Wet pressures of a case with a validated owned companion set aside; unvalidated look-alikes stay."""
    pair=owned_pair(case)
    return [pair[0]] if pair is not None else list(case.wet_pressures)


def setting_flag(settings):
    value=(settings or {}).get('include_hydrostatic',False)
    if type(value) is not bool:raise ValueError('include_hydrostatic must be a boolean')
    return value


def history_includes_hydrostatic(history):
    """Intent persisted in the history provenance settings; missing means False (legacy data)."""
    try:saved=json.loads(history.provenance)
    except (ValueError,TypeError,AttributeError):return False
    settings=saved.get('settings') if isinstance(saved,dict) else None
    return setting_flag(settings) if isinstance(settings,dict) else False


def source_baseline():
    """Steady buoyancy wrench (Fx,Fy,Fz,Mx,My,Mz) of the admitted source hull about WORLD0, from ANYhydro."""
    from anyhydro.hydrostatics_exact import buoyancy_baseline_wrench
    from .hydro_wave_pressure import _source_mesh
    v,f,_=_source_mesh()
    return tuple(float(x) for x in buoyancy_baseline_wrench(v,f,(0.,0.,0.),RHO,GRAVITY))


def composition(include):
    return 'dynamic+hydrostatic' if include else 'dynamic-only'


def total_resultants(dynamic_values,include):
    """Dynamic (n,6) resultants plus the steady baseline exactly once per sample when enabled."""
    values=np.asarray(dynamic_values,dtype=float)
    return values+np.asarray(source_baseline(),dtype=float) if include else values
