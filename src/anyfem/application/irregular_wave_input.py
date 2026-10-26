"""Bounded JONSWAP settings and fixed-hull frequency-bank preparation."""
from dataclasses import dataclass
import math
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import tempfile
import re
from pathlib import Path
import numpy as np

COMPLETE_BANK_CACHE_POLICY='anyfem_complete_pressure_bank_v1'


class CompletePressureBankCacheError(ValueError):
    """An assembled cache artifact exists but cannot be verified."""


def _canonical_json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def _backend_fingerprint():
    """Inspect installed provider/default code without importing its BEM engine."""
    import anyhydro.pressure_bank as producer
    spec=importlib.util.find_spec('capytaine')
    if spec is None or not spec.origin:raise ValueError('Capytaine provider is unavailable')
    provider=Path(spec.origin).resolve();root=provider.parent
    files=sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.py','.pyd','.dll','.so'))
    files.extend((Path(producer.__file__).resolve(),Path(producer.__file__).with_name('_pressure_body.py').resolve(),
        Path(__file__).resolve()))
    fingerprints={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    return dict(backend='capytaine',version=importlib.metadata.version('capytaine'),path=str(provider),
        code_sha256=fingerprints,numpy_version=importlib.metadata.version('numpy'),
        scipy_version=importlib.metadata.version('scipy'))


def _complete_bank_request(settings,realization,source,fingerprint):
    v,f,labels=source;geometry=hashlib.sha256()
    for array in (np.asarray(v,dtype=np.float64),np.asarray(f,dtype=np.int64)):
        geometry.update(_canonical_json((array.shape,array.dtype.str)).encode());geometry.update(memoryview(array).cast('B'))
    geometry.update(_canonical_json(tuple(labels)).encode())
    return dict(policy=COMPLETE_BANK_CACHE_POLICY,geometry_sha256=geometry.hexdigest(),
        omegas=[c.omega.hex() for c in realization.components],heading=(math.radians(settings.heading)%math.tau).hex(),
        water_depth=float(settings.depth).hex(),rho=float(1025.).hex(),gravity=float(9.81).hex(),
        phase_origin=(0.,0.,0.),conventions=dict(units='Pa/m',phasor='exp(-i omega t)',normals='outward',
            pressure='positive inward',physics='fixed incident+diffraction'),backend=fingerprint)


def _frequency_cache_directory(cache_directory,fingerprint):
    """Isolate native responses by installed numerical backend/producer code.

    Cache-policy helper edits invalidate assembled manifests separately; the
    frequency namespace tracks actual backend/producer/default implementations.
    Legacy top-level entries are never trusted or migrated automatically.
    """
    numeric=dict(fingerprint)
    helper_path=str(Path(__file__).resolve())
    numeric['code_sha256']={path:digest for path,digest in fingerprint['code_sha256'].items()
        if path!=helper_path}
    identity=hashlib.sha256(_canonical_json(numeric).encode()).hexdigest()
    return Path(cache_directory)/'frequency-banks'/identity


def _verify_complete_bank(bank,settings,realization,source,fingerprint,manifest=None):
    bank.validate();v,f,labels=source
    provenance=json.loads(bank.provenance_json)
    if not isinstance(provenance,dict):
        raise CompletePressureBankCacheError('invalid complete pressure bank provenance')
    if (not np.array_equal(bank.vertices,v) or not np.array_equal(bank.faces,f) or bank.surface_ids!=tuple(labels) or
            not np.array_equal(bank.omegas,[c.omega for c in realization.components]) or
            bank.heading!=math.radians(settings.heading)%math.tau or bank.water_depth!=settings.depth or
            bank.rho!=1025. or bank.gravity!=9.81 or bank.phase_origin!=(0.,0.,0.) or
            bank.conventions!=_complete_bank_request(settings,realization,source,fingerprint)['conventions'] or
            provenance.get('backend')!='capytaine' or provenance.get('version')!=fingerprint['version'] or
            provenance.get('path')!=fingerprint['path'] or provenance.get('method')!='indirect' or
            provenance.get('n_threads')!=1 or provenance.get('jobs')!=1 or
            not isinstance(provenance.get('effective_settings'),dict) or
            provenance['effective_settings'].get('method')!='indirect' or
            not provenance.get('green_function') or not provenance.get('engine')):
        raise CompletePressureBankCacheError('complete pressure bank metadata/provider mismatch')
    if manifest is not None and (manifest.get('bank_id')!=bank.bank_id or
            manifest.get('provenance_json')!=bank.provenance_json):
        raise CompletePressureBankCacheError('complete pressure bank provenance/content mismatch')


def _atomic_json(path,data):
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix=path.name+'.',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf8') as stream:
            stream.write(_canonical_json(data));stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def sample_times(duration,dt):
    count=int(math.floor(duration/dt))+1
    times=np.arange(count,dtype=float)*dt
    if duration-times[-1]>1e-10: times=np.r_[times,duration]
    else: times[-1]=duration
    return tuple(times.tolist())


@dataclass(frozen=True)
class IrregularWaveInput:
    name: str='Irregular wave'
    significant_height: float=1.
    peak_period: float=6.
    gamma: float=3.3
    seed: int=0
    component_count: int=32
    lower_frequency_ratio: float=.5
    upper_frequency_ratio: float=3.
    heading: float=0.
    depth: float=200.
    duration: float=10.
    dt: float=.1
    wave_type: str='irregular'

    def __post_init__(self):
        values=(self.significant_height,self.peak_period,self.gamma,self.lower_frequency_ratio,
            self.upper_frequency_ratio,self.heading,self.depth,self.duration,self.dt)
        if (self.wave_type!='irregular' or not self.name.strip() or
                any(isinstance(x,(bool,np.bool_)) or not math.isfinite(x) for x in values) or
                self.significant_height<0 or min(self.peak_period,self.gamma,self.lower_frequency_ratio,
                    self.duration,self.dt)<=0 or self.depth<=1.5 or self.gamma<1 or
                self.upper_frequency_ratio<=self.lower_frequency_ratio or
                type(self.seed) is not int or self.seed<0 or type(self.component_count) is not int or
                not 1<=self.component_count<=200):
            raise ValueError('invalid finite JONSWAP inputs, integer seed/count or depth/draft')
        # Actual endpoint inventory, including a non-grid duration endpoint.
        intervals=int(math.floor(self.duration/self.dt))
        count=intervals+1+(self.duration-intervals*self.dt>1e-10)
        if count>108001: raise ValueError('at most108001 irregular wave samples are admitted')

    @property
    def times(self): return sample_times(self.duration,self.dt)

    def realization(self):
        from anyloads.sea_state import JonswapSpectrum,WaveRealization,DirectionalSpectrum
        wp=math.tau/self.peak_period
        width=wp*(self.upper_frequency_ratio-self.lower_frequency_ratio)/self.component_count
        omegas=tuple(wp*self.lower_frequency_ratio+(i+.5)*width for i in range(self.component_count))
        return WaveRealization.from_jonswap(JonswapSpectrum(self.significant_height,self.peak_period,self.gamma),
            omegas,(width,)*self.component_count,seed=self.seed,
            directions=DirectionalSpectrum((math.radians(self.heading)%math.tau,),(1.,)))


def pressure_bank_for_settings(settings,*,cache_directory=None,cancellation_token=None,progress=None):
    from anyhydro import prepare_pressure_bank,PressureResponseBank
    from .hydro_wave_pressure import _source_mesh
    if cache_directory is None:
        from platformdirs import user_cache_dir
        cache_directory=Path(user_cache_dir('ANYfem','ANY'))/'irregular-pressure-v1'
    realization=settings.realization()
    def cancel():
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled('irregular pressure preparation')
        return False
    def report(state):
        if progress is not None:
            progress(f'Irregular pressure: {len(state.completed)}/{settings.component_count} frequencies; '
                f'{state.cached_count} cached')
    cancel()
    source=_source_mesh();fingerprint=_backend_fingerprint()
    request=_complete_bank_request(settings,realization,source,fingerprint)
    key=hashlib.sha256(_canonical_json(request).encode()).hexdigest()
    folder=Path(cache_directory)/'complete-banks';folder.mkdir(parents=True,exist_ok=True)
    manifest_path=folder/(key+'.json')
    if manifest_path.exists():
        try:
            cancel()
            manifest=json.loads(manifest_path.read_text(encoding='utf8'))
            if (type(manifest) is not dict or set(manifest)!={'request','bank_id','provenance_json'} or
                    type(manifest['bank_id']) is not str or re.fullmatch('[0-9a-f]{64}',manifest['bank_id']) is None):
                raise CompletePressureBankCacheError('invalid complete pressure cache manifest')
            if _canonical_json(manifest.get('request'))!=_canonical_json(request):
                raise CompletePressureBankCacheError('complete pressure cache request identity mismatch')
            # Only derive a content-addressed local filename; never trust a
            # persisted path. The manifest is the sole atomic publication point.
            bank_path=folder/(manifest['bank_id']+'.npz')
            bank=PressureResponseBank.load(bank_path)
            _verify_complete_bank(bank,settings,realization,source,fingerprint,manifest)
        except CompletePressureBankCacheError:raise
        except (ValueError,TypeError,KeyError,OSError) as error:
            raise CompletePressureBankCacheError('corrupt complete pressure bank cache') from error
        cancel()
        if progress is not None:progress(f'Irregular pressure: {settings.component_count}/{settings.component_count} '
            f'frequencies cached; complete bank verified')
        return bank,realization
    if progress is not None:progress('Complete pressure bank cache miss; using verified per-frequency cache')
    bank=prepare_pressure_bank(*source,tuple(c.omega for c in realization.components),
        cache_directory=_frequency_cache_directory(cache_directory,fingerprint),
        heading=math.radians(settings.heading)%math.tau,
        water_depth=settings.depth,max_panels=600,max_missing_solves=settings.component_count,
        max_wall_seconds=120.,cancel=cancel,progress=report)
    cancel()
    _verify_complete_bank(bank,settings,realization,source,fingerprint)
    bank_path=folder/(bank.bank_id+'.npz')
    if bank_path.exists():
        existing=PressureResponseBank.load(bank_path)
        _verify_complete_bank(existing,settings,realization,source,fingerprint)
        if existing.bank_id!=bank.bank_id:
            raise CompletePressureBankCacheError('content-addressed complete pressure payload mismatch')
    else:
        bank.save(bank_path)
    _atomic_json(manifest_path,dict(request=request,bank_id=bank.bank_id,provenance_json=bank.provenance_json))
    cancel()
    return bank,realization
