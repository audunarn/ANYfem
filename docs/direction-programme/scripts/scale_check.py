"""1e12 translation: probe residual vs fast-route frame accuracy, legacy Q8."""
import warnings, numpy as np
from anyfesolver.fe_core import FEMesh, Material
from anyfesolver.elements import ShellElement
import anyfem.mesh_orientation as mo
base = np.array([[0,0,0],[1,0,0],[1,1,.05],[0,1,0],[.5,0,0],[1,.5,.02],[.5,1,.03],[0,.5,0]],float)
steel = Material(name="steel", elastic_modulus=210e9, poisson_ratio=.3)
def element_at(shift):
    m = FEMesh()
    for k,p in enumerate(base+shift): m.add_node(k+1,*map(float,p))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore"); e = ShellElement(1, list(range(1,9)), "steel", thickness=.01)
    m.add_element(1,e); return e,m
ref_e, ref_m = element_at(np.zeros(3)); ref = mo._legacy_recovery_frames(ref_e, ref_m, steel)
def angle(a,b): return max(np.degrees(np.arccos(min(1,abs(float(f.x_axis@g.x_axis))))) for f,g in zip(a,b)), max(np.degrees(np.arccos(min(1,float(f.normal@g.normal)))) for f,g in zip(a,b))
for t in (0, 1e3, 1e6, 1e9, 1e12):
    e, m = element_at(np.array([t, -t, t]))
    fast = mo._legacy_recovery_frames(e, m, steel)
    ax, an = angle(fast, ref)
    try: mo.element_recovery_frames(e, m); probe="accepted"
    except ValueError as x: probe=str(x).split("(")[-1].rstrip(")")
    print(f"shift {t:7.0e}: solver frame error vs unshifted x {ax:.2e} deg, normal {an:.2e} deg | probe: {probe}")
