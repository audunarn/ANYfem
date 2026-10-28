"""Floating wind TRANSIENT dialog: ONE selected saved wind turbine load (``Project.wind_turbine_loads``: thrust curve,
hub, rotor and its owned steady / imported / retained-Kaimal wind, edited in the Loads & BC wind turbine editor) or
"No turbine", mooring attachments and external histories on a saved coarse job input; the canonical operating mean through the isolated
pinned-ANYgeometry runtime when (and only when) the mean-defining loads change; an async cancellable run of the headless
job; plain unverified-hydro diagnostics; reopen (no solve, no BEM) and persistent undoable Apply.

The dialog never runs BEM.  The coarse hydro + structural operators come from a SAVED job input (prepared by
``floating_transient_job.prepare_floating_transient_job``).  A wind realization change with the same declared reference
mean, curve, rotor and attachments keeps the bound operating mean.  Any change of a mean-defining quantity re-solves the
operating mean (``solve_operating_equilibrium_isolated``); if the solved pose differs from the pose the coarse operators
and the cached hydro family were prepared at, the run is refused as stale (re-prepare headless) - nothing stale is run.
Development (unverified) hydrodynamics keep their explicit marker in every label and in the applied project record.
"""
from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from ...model.wind_turbine import realization_record  # noqa: F401 - shared provenance record (re-exported)

DEVELOPMENT_MARKER = 'DEVELOPMENT - UNVERIFIED HYDRODYNAMICS'


def parse_curve(text):
    """'v:T, v:T, ...' -> (speeds, thrusts) strictly increasing speeds."""
    pairs = [p.strip() for p in text.replace(';', ',').split(',') if p.strip()]
    sp, th = [], []
    for p in pairs:
        a, b = p.split(':')
        sp.append(float(a)); th.append(float(b))
    sp, th = np.array(sp), np.array(th)
    if sp.size < 2 or np.any(np.diff(sp) <= 0) or not np.all(np.isfinite(th)):
        raise ValueError('thrust curve needs >= 2 strictly increasing speeds as "v:T, v:T"')
    return sp, th


def parse_vector(text, what):
    v = np.array([float(x) for x in text.replace(',', ' ').split()])
    if v.shape != (3,) or not np.all(np.isfinite(v)):
        raise ValueError(f'{what} needs three finite numbers')
    return v


def parse_fairleads(text):
    """One fairlead per line: 'id; node; px py pz; Fx Fy Fz; kxx kyy kzz' - ABSOLUTE body point, world pretension at
    the hydrostatic reference pose, world stiffness diagonal (the affine law the operating mean evaluates)."""
    out = []
    for line in (ln.strip() for ln in text.splitlines()):
        if not line:
            continue
        parts = [p.strip() for p in line.split(';')]
        if len(parts) not in (5, 6):
            raise ValueError(f'mooring line {line!r}: expected "id; node; px py pz; Fx Fy Fz; K (3 diagonal or 9 '
                             'row-major world values)[; C (3 or 9 values)]"')
        K = _matrix3(parts[4], 'fairlead stiffness')
        C = _matrix3(parts[5], 'fairlead damping') if len(parts) == 6 else np.zeros((3, 3))
        out.append(dict(identity=parts[0], node_id=int(parts[1]), point_body=parse_vector(parts[2], 'fairlead point').tolist(),
                        pretension_world=parse_vector(parts[3], 'pretension').tolist(), stiffness=K.tolist(),
                        damping=C.tolist()))
    return tuple(out)


def _matrix3(text, what):
    """3 values -> diagonal (convenience); 9 values -> the full row-major 3x3 world matrix as supplied."""
    v = np.array([float(x) for x in text.replace(',', ' ').split()])
    if not np.all(np.isfinite(v)) or v.size not in (3, 9):
        raise ValueError(f'{what} needs 3 (diagonal) or 9 (full 3x3) finite values')
    return np.diag(v) if v.size == 3 else v.reshape(3, 3)


def rotor_axis(hub, yaw_deg, tilt_deg):
    """Reuse the ANYloads implementation (TurbineLoadApplicator.from_yaw_tilt) - no local re-expression."""
    from anyloads.wind_turbine import TurbineLoadApplicator
    app = TurbineLoadApplicator.from_yaw_tilt(np.asarray(hub, float), yaw=math.radians(float(yaw_deg)),
                                              tilt=math.radians(float(tilt_deg)))
    return np.asarray(app.rotor_axis_body, float)


def kaimal_parameters(mean_world, ti, length, seed, period, max_frequency):
    U = float(np.linalg.norm(mean_world))
    return dict(mean_speed=U, turbulence_intensity=float(ti), integral_scale=float(length),
                direction=(np.asarray(mean_world, float) / U).tolist(), seed=int(seed), period=float(period),
                max_frequency=float(max_frequency))


def wind_history(mode, times, *, mean_world=None, ti=0.0, length=340.2, seed=0, max_frequency=None, path=None,
                 reference_mean=None, realization=None):
    """ANYloads wind record on the job's accepted time grid.

    'kaimal': ONE retained continuous realization (``KaimalRealization``) is evaluated on the grid.  Its frequency set
    depends only on (mean, TI, L, seed, period, max_frequency) - never on dt - so a changed time step or output window
    re-samples the SAME realization; ``realization`` (when given and with identical parameters) is reused as is.  A grid
    too coarse for the retained max frequency is refused by ANYloads (aliasing), never silently regenerated.
    'steady' -> constant declared mean; 'import' -> CSV 't,u,v,w' covering the record (no extrapolation) with an
    explicit declared reference mean.  The declared reference mean is the physical mean of the operating equilibrium;
    the realization's sample mean is never subtracted.  Returns (times, velocity, reference, provenance, realization)."""
    from anyloads.wind_turbine import WindHistory, generate_kaimal_realization
    t = np.asarray(times, dtype=float)
    if mode == 'kaimal':
        period = float(t[-1])                         # realisation period covers the absolute record [0, t_end]
        fmax = float(max_frequency) if max_frequency else (
            realization.max_frequency if realization is not None else 0.5 / float(np.max(np.diff(t))))
        params = kaimal_parameters(mean_world, ti, length, seed, period, fmax)
        if realization is not None and kaimal_parameters(
                np.asarray(realization.direction) * realization.mean_speed, realization.turbulence_intensity,
                realization.integral_scale, realization.seed, realization.period, realization.max_frequency) == params:
            real = realization                         # same physical parameters: the SAME realization is reused
        else:
            real = generate_kaimal_realization(**{k: (np.asarray(v) if k == 'direction' else v)
                                                  for k, v in params.items()})
        hist = real.to_wind_history(t)
        return hist.times, hist.velocity, np.asarray(mean_world, float), realization_record(real), real
    if mode == 'steady':
        m = np.asarray(mean_world, float)
        return t.copy(), np.tile(m, (t.size, 1)), m, dict(kind='steady', mean_world=m.tolist()), None
    data = np.loadtxt(path, delimiter=',', ndmin=2)
    if data.ndim != 2 or data.shape[1] != 4 or not np.all(np.isfinite(data)) or np.any(np.diff(data[:, 0]) <= 0):
        raise ValueError('imported wind must be a finite CSV "t,u,v,w" with increasing times')
    if data[0, 0] > t[0] or data[-1, 0] < t[-1]:
        raise ValueError('imported wind does not cover the whole accepted record (no extrapolation)')
    if reference_mean is None:
        raise ValueError('an imported wind needs its declared physical reference mean (never the sample mean)')
    hist = WindHistory.from_vectors(data[:, 0], data[:, 1:], reference_mean=reference_mean, source=str(path))
    return (hist.times, hist.velocity, np.asarray(reference_mean, float),
            dict(kind='import', path=str(path), sha256=_file_sha256(path)), None)


def _file_sha256(path):
    import hashlib
    with open(path, 'rb') as stream:
        return hashlib.sha256(stream.read()).hexdigest()


def operating_spec(loads, cg_body):
    """The mean-defining quantities exactly as the canonical operating mean binds them (absolute hub / fairlead body
    points, reference-pose pretensions, curve, reference wind, external channels)."""
    cg = np.asarray(cg_body, float)
    spec = dict(rotor_axis_body=None, hub_body=None, thrust_curve=None, wind_reference_mean_world=None,
                fairleads=[], external_channels=None)
    if loads.get('wind_velocity') is not None:
        spec.update(rotor_axis_body=np.asarray(loads['rotor_axis_body'], float).tolist(),
                    hub_body=(np.asarray(loads['hub_offset_body'], float) + cg).tolist(),
                    thrust_curve=dict(speeds=np.asarray(loads['thrust_speeds'], float).tolist(),
                                      thrusts=np.asarray(loads['thrust_values'], float).tolist()),
                    wind_reference_mean_world=np.asarray(loads['wind_reference_mean_world'], float).tolist())
    # controls are WORLD-frame laws (reference pretension, K, C); the transient body matrices are derived later
    spec['fairleads'] = [dict(identity=f['identity'], point_body=(np.asarray(f['point_body'], float) + cg).tolist(),
                              pretension_world=list(f['reference_pretension_world']), stiffness_world=f['stiffness'],
                              damping_world=f['damping'], frame='world')
                         for f in loads.get('fairleads', ())]
    if loads.get('ext_mean_values') is not None:
        from ...application.floating_transient import external_channels_record
        spec['external_channels'] = external_channels_record(loads['ext_components'], loads['ext_frames'],
                                                             loads['ext_points_body'], loads['ext_node_ids'],
                                                             loads['ext_mean_values'])
    return json.loads(json.dumps(spec))


def bound_spec(record):
    if not record:
        return dict(rotor_axis_body=None, hub_body=None, thrust_curve=None, wind_reference_mean_world=None,
                    fairleads=[], external_channels=None)
    from ...application.floating_transient_record import _canonical       # frozen records: tuples / mappingproxies
    b = dict(record.get('binding') or {})
    return json.loads(_canonical(dict(rotor_axis_body=b.get('rotor_axis_body'), hub_body=b.get('hub_body'),
                                      thrust_curve=b.get('thrust_curve'),
                                      wind_reference_mean_world=b.get('wind_reference_mean_world'),
                                      fairleads=list(b.get('fairleads') or []),
                                      external_channels=b.get('external_channels'))))



class FloatingTransientRun(QThread):
    """Run the headless job in a thread with a public cancellation token."""
    completed = Signal(object)
    failed = Signal(str)
    mean_solved = Signal()

    def __init__(self, function, kwargs, parent=None):
        super().__init__(parent)
        from anysolver import CancellationToken
        self.function, self.kwargs, self.cancelled = function, dict(kwargs), False
        self.token = CancellationToken()

    def run(self):
        try:
            result = self.function(cancellation_token=self.token, **self.kwargs)
            if not self.cancelled:
                self.completed.emit(result)
        except BaseException as error:
            if not self.cancelled:
                self.failed.emit(str(error))

    def cancel(self):
        self.cancelled = True
        self.token.cancel('floating transient cancelled')


class FloatingTransientDialog(QDialog):
    def __init__(self, panel, *, fe_model_provider=None, solve_mean=None, run_job=None, reopen_job=None):
        super().__init__(panel)
        from ...application import floating_transient_job as job
        self.panel, self.app = panel, panel.app
        self.fe_model_provider = fe_model_provider or self._default_fe_model
        self.solve_mean = solve_mean or self.production_mean
        self.run_job = run_job or job.run_floating_transient_job
        self.reopen_job = reopen_job or job.reopen_floating_transient_result
        self.runner, self.summary, self.job_input_path, self.mean_solves = None, None, None, 0
        self.turbine_editor = None
        self.setWindowTitle('Floating wind transient'); self.setObjectName('FloatingTransientDialog')
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form)
        self.fields = {}

        def edit(key, label, text=''):
            w = QLineEdit(text); w.setObjectName('ft_' + key); self.fields[key] = w; form.addRow(label, w)
            return w

        edit('base_input', 'Saved coarse job input (.npz; hydro + structural operators, no BEM)')
        edit('field_kernel', 'Saved field kernel (.npz)')
        edit('modal', 'Verified elastic modal record of THIS model (.json; blank = screening not assessed)')
        edit('result', 'Result file (.npz)')
        # ONE saved wind turbine load definition (Loads & BC -> Wind turbine loads); this dialog owns no wind settings
        turbine_row = QHBoxLayout()
        self.turbine = QComboBox(); self.turbine.setObjectName('ft_turbine')
        self.edit_turbines = QPushButton('Wind turbine loads…'); self.edit_turbines.setObjectName('ft_edit_turbines')
        self.edit_turbines.clicked.connect(lambda: self.open_turbine_editor())
        turbine_row.addWidget(self.turbine, 1); turbine_row.addWidget(self.edit_turbines)
        form.addRow('Wind turbine load', turbine_row)
        self.refresh_turbines()
        edit('external', 'External load history (.npz: ext_times, ext_values (perturbation), ext_mean_values, ...)')
        self.mooring = QPlainTextEdit(); self.mooring.setObjectName('ft_mooring')
        self.mooring.setPlaceholderText('id; node; px py pz (absolute body); Fx Fy Fz (reference pretension); kxx kyy kzz')
        form.addRow('Mooring attachments', self.mooring)
        edit('window_start', 'Structural window start (index)', '0'); edit('window_end', 'Structural window end (index)', '')
        edit('name', 'Result name', 'Floating transient')
        # isolated operating-mean runtime (pinned ANYgeometry); used only when the mean-defining loads change
        edit('equilibrium_python', 'Isolated equilibrium Python (pinned ANYgeometry runtime)')
        edit('owner_wheel', 'Pinned ANYgeometry owner wheel path')
        edit('stability_source', 'ANYstability source path (optional)')
        edit('envelope_faces', 'Closing face ids for the displacement envelope (comma separated)')
        self.mean_development = QCheckBox('Allow engineering (development) operating mean'); form.addRow(self.mean_development)
        self.development = QCheckBox('Allow and attach development (unverified) hydrodynamics')
        self.development.setObjectName('ft_development')
        form.addRow(self.development)
        self.diagnostics = QLabel(''); self.diagnostics.setWordWrap(True); self.diagnostics.setObjectName('ft_diagnostics')
        layout.addWidget(self.diagnostics)
        self.status = QLabel('Uses a saved coarse input; no BEM is run here. Stresses are quasistatic.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        row = QHBoxLayout(); layout.addLayout(row)
        self.run_button = QPushButton('Run transient'); self.run_button.setObjectName('ft_run')
        self.cancel_run = QPushButton('Cancel run'); self.cancel_run.setObjectName('ft_cancel'); self.cancel_run.setEnabled(False)
        self.reopen_button = QPushButton('Reopen result (no solve)'); self.reopen_button.setObjectName('ft_reopen')
        self.apply_button = QPushButton('Apply to project'); self.apply_button.setObjectName('ft_apply'); self.apply_button.setEnabled(False)
        self.close_button = QPushButton('Close')
        for b in (self.run_button, self.cancel_run, self.reopen_button, self.apply_button, self.close_button):
            row.addWidget(b)
        inspect_row = QHBoxLayout(); layout.addLayout(inspect_row)
        self.inspect_time = QLineEdit(); self.inspect_time.setObjectName('ft_inspect_time')
        self.inspect_time.setPlaceholderText('saved frame time (s)')
        self.inspect_button = QPushButton('Inspect applied frame (no solve)'); self.inspect_button.setObjectName('ft_inspect')
        self.inspection = QLabel(''); self.inspection.setWordWrap(True); self.inspection.setObjectName('ft_inspection')
        inspect_row.addWidget(self.inspect_time); inspect_row.addWidget(self.inspect_button)
        layout.addWidget(self.inspection)
        self.inspect_button.clicked.connect(lambda: self.inspect_applied())
        self.last_inspection = None
        self.run_button.clicked.connect(lambda: self.start())
        self.cancel_run.clicked.connect(lambda: self.cancel())
        self.reopen_button.clicked.connect(lambda: self.reopen())
        self.apply_button.clicked.connect(lambda: self.apply())
        self.close_button.clicked.connect(self.reject)

    # ---------------------------------------------------------------- services
    def _default_fe_model(self):
        from ...solve.build import build_fe_model
        return build_fe_model(self.app.project, self.app.mesh, require_loads=False, require_supports=False).fe_model

    def runtime_settings(self, base):
        faces = tuple(int(x) for x in self.fields['envelope_faces'].text().replace(',', ' ').split())
        return SimpleNamespace(equilibrium_python=self.fields['equilibrium_python'].text().strip(),
                               owner_wheel=self.fields['owner_wheel'].text().strip(),
                               stability_source=self.fields['stability_source'].text().strip(),
                               allow_development=self.mean_development.isChecked(), envelope_extra_face_ids=faces,
                               rho=float((base.identities.get('coarse_prepare') or {}).get('rho', 1025.0)),
                               gravity=float(base.gravity), datum_xy=(0.0, 0.0), equilibrium_controls=None,
                               equilibrium_timeout_s=600.0)

    def production_mean(self, spec, base, cancel=None):
        """Marker for the production service; the actual call is frozen by ``freeze_mean_call`` on the GUI thread."""
        raise RuntimeError('production_mean is executed only through freeze_mean_call')

    def freeze_mean_call(self, spec, base, fe_snapshot):
        """GUI thread: freeze runtime settings (plain values), the owner envelope, dry mass of the SAME FE snapshot and
        the neutral operating loads; returns a worker callable ``call(cancel)`` that reads no widget / live project.
        External mean channels (world AND body frame) are evaluated per trial pose by the canonical solve."""
        from ...application.floating_equilibrium_process import prepare_operating_spec, run_operating_spec
        from ...application.floating_mass import dry_mass_properties
        if self.solve_mean != self.production_mean:
            injected = self.solve_mean
            return lambda cancel: injected(spec, base, cancel)
        settings = self.runtime_settings(base)
        frozen_settings = SimpleNamespace(**vars(settings))
        operating = dict(length_scale=float(base.length_scale), fairleads=spec['fairleads'],
                         external_channels=spec['external_channels'])
        if spec['thrust_curve'] is not None:
            operating.update(wind_mean_world=spec['wind_reference_mean_world'], thrust_curve=spec['thrust_curve'],
                             rotor_axis_body=spec['rotor_axis_body'], hub_body=spec['hub_body'])
        frozen_spec = prepare_operating_spec(self.app.project, frozen_settings, dry_mass_properties(fe_snapshot),
                                             operating)
        return lambda cancel: run_operating_spec(frozen_settings, frozen_spec, cancel=cancel)['mean_equilibrium']

    # ---------------------------------------------------------------- turbine selection
    def refresh_turbines(self):
        """'No turbine' plus every saved turbine load of the live project; keeps the current selection by ID."""
        current = self.turbine.currentData() if self.turbine.count() else None
        self.turbine.blockSignals(True)
        try:
            self.turbine.clear()
            self.turbine.addItem('No turbine', None)
            loads = getattr(self.app.project, 'wind_turbine_loads', {}) or {}
            for load in sorted(loads.values(), key=lambda item: item.name):
                self.turbine.addItem(f'{load.name} ({load.wind_mode} wind, hub node {load.hub_node_id})', load.id)
            index = self.turbine.findData(current) if current is not None else 0
            self.turbine.setCurrentIndex(max(index, 0))
        finally:
            self.turbine.blockSignals(False)

    def select_turbine(self, key):
        """Select a saved turbine load by name or ID (None = no turbine)."""
        self.refresh_turbines()
        if key is None:
            self.turbine.setCurrentIndex(0)
            return None
        load = self.app.project.wind_turbine_load(key)
        self.turbine.setCurrentIndex(self.turbine.findData(load.id))
        return load

    def selected_turbine(self):
        """The selected immutable record, read from the live project by ID (never a cached copy)."""
        load_id = self.turbine.currentData()
        if load_id is None:
            return None
        load = (getattr(self.app.project, 'wind_turbine_loads', {}) or {}).get(load_id)
        if load is None:
            raise ValueError('the selected wind turbine load no longer exists; choose another or "No turbine"')
        return load

    def open_turbine_editor(self):
        from .wind_turbine import WindTurbineLoadEditor
        self.turbine_editor = WindTurbineLoadEditor(self.panel, parent=self)
        self.turbine_editor.finished.connect(lambda *_: self.refresh_turbines())
        self.turbine_editor.show()
        return self.turbine_editor

    # ---------------------------------------------------------------- inputs
    def load_fields(self, base):
        """Selected turbine load / mooring / external fields on the base input's accepted time grid."""
        out, wind_prov = {}, None
        cg = np.asarray(base.cg_body, float)
        turbine = self.selected_turbine()
        if turbine is None:
            out.update(wind_times=None, wind_velocity=None, thrust_speeds=None, thrust_values=None, hub_offset_body=None,
                       rotor_axis_body=None, hub_node_id=None, mean_thrust=None, wind_reference_mean_world=None)
        else:
            hub_abs = np.asarray(turbine.hub_body, float)
            # the record's OWNED wind: steady mean, imported samples or the retained Kaimal realization re-sampled on
            # this grid (full coverage enforced, never regenerated)
            wt, wv, ref, wind_prov, _ = turbine.wind_history(base.times)
            out.update(wind_times=wt, wind_velocity=wv, wind_reference_mean_world=ref,
                       thrust_speeds=np.asarray(turbine.thrust_speeds, float),
                       thrust_values=np.asarray(turbine.thrust_values, float),
                       hub_offset_body=hub_abs - cg,          # converted ONCE: transient is CG-relative
                       rotor_axis_body=turbine.rotor_axis_body(), hub_node_id=int(turbine.hub_node_id))
        fairleads = []
        for f in parse_fairleads(self.mooring.toPlainText()):
            g = dict(f, point_body=(np.asarray(f['point_body']) - cg).tolist(),
                     reference_pretension_world=f['pretension_world'])
            fairleads.append(g)
        out['fairleads'] = tuple(fairleads)
        ext = self.fields['external'].text().strip()
        if ext:
            with np.load(ext, allow_pickle=False) as d:
                out.update(ext_times=np.array(d['ext_times']), ext_values=np.array(d['ext_values']),
                           ext_components=tuple(str(x) for x in d['ext_components']),
                           ext_frames=tuple(str(x) for x in d['ext_frames']),
                           ext_points_body=np.array(d['ext_points_body']),
                           ext_node_ids=tuple(int(x) for x in d['ext_node_ids']),
                           ext_mean_values=np.array(d['ext_mean_values']))
        return out, wind_prov

    def plan_input(self, fe_snapshot=None):
        """Fast synchronous validation + plan (no mean solve here): saved coarse input, control loads, window and
        whether the mean-defining quantities changed.  The mean solve / input / job run in the cancellable worker."""
        from ...application.floating_transient import load_floating_transient_input
        base = load_floating_transient_input(self.fields['base_input'].text())
        if base.hydrodynamic_verification == 'unverified' and not self.development.isChecked():
            raise ValueError('the saved coarse input has UNVERIFIED hydrodynamics (gates: '
                             + ', '.join(base.unpassed_hydro_gates) + '); enable development explicitly to run it')
        loads, wind_prov = self.load_fields(base)
        n_t = base.times.size
        start = int(self.fields['window_start'].text() or 0)
        end = int(self.fields['window_end'].text() or n_t - 1)
        frames = np.array([i for i in base.frame_indices if start <= i <= end], dtype=np.int64)
        if frames.size == 0:
            raise ValueError('the selected window contains no structural frame of the saved input')
        spec = operating_spec(loads, base.cg_body)
        record = None if base.mean_equilibrium is None else dict(base.mean_equilibrium)
        need_solve = spec != bound_spec(record)
        mean_call = None
        if need_solve:
            # GUI thread: validate + freeze everything the mean worker needs (no fallback mean, no live reads later)
            from ...application.floating_equilibrium_process import FloatingEquilibriumError, _check_runtime
            try:
                if self.solve_mean == self.production_mean:
                    _check_runtime(self.runtime_settings(base))
                mean_call = self.freeze_mean_call(spec, base, fe_snapshot)
            except (FloatingEquilibriumError, ValueError) as error:
                raise ValueError(f'operating mean preparation refused: {error}') from error
        result_path = self.fields['result'].text()
        return dict(base=base, loads=loads, wind_prov=wind_prov, frames=frames, spec=spec, record=record,
                    need_solve=need_solve, mean_call=mean_call, result_path=result_path,
                    input_path=str(Path(result_path).with_suffix('.input.npz')),
                    field_kernel_path=self.fields['field_kernel'].text())

    @staticmethod
    def check_prepared_pose(base, record):
        """The solved mean must be the pose the coarse operators / cached hydro family were prepared at: rotation AND
        translation (CG world).  A moved mean needs a newly prepared matching family; it is never run on old ones."""
        Q = np.asarray(record['rotation_body_to_world'], float)
        t = np.asarray(record.get('translation_world', [np.nan] * 3), float)
        prep = dict((base.identities or {}).get('coarse_prepare') or {})
        if prep.get('pose_t') is None:
            raise ValueError('the saved coarse input does not record its prepared pose translation; no matching '
                             'prepared source can be shown for a re-solved mean (refused)')
        t0 = np.asarray(prep['pose_t'], float)
        scale = max(1.0, float(np.max(np.abs(t0))))
        if not float(np.max(np.abs(Q - base.Q0))) <= 1e-9 or not float(np.max(np.abs(t - t0))) <= 1e-9 * scale:
            raise ValueError('the operating mean moved the pose (rotation or translation); the coarse operators and the '
                             'cached hydro family are stale for it - prepare a matching family/operators headless '
                             '(nothing stale is run)')

    def finalize_input(self, plan, record):
        base, loads, wind_prov, frames = plan['base'], dict(plan['loads']), plan['wind_prov'], plan['frames']
        forces = ((record or {}).get('binding') or {}).get('mooring_mean_forces_world') or {}
        fls = []
        for f in loads.pop('fairleads'):
            g = {k: v for k, v in f.items() if k != 'reference_pretension_world'}
            if f['identity'] not in forces:
                raise ValueError(f"fairlead {f['identity']} has no evaluated operating-mean force")
            g['pretension_world'] = list(forces[f['identity']])       # relinearised about the operating pose
            Q = np.asarray(record['rotation_body_to_world'], float)     # world law -> MEAN-BODY FairleadSpec matrices
            g['stiffness'] = (Q.T @ np.asarray(f['stiffness'], float) @ Q).tolist()
            g['damping'] = (Q.T @ np.asarray(f['damping'], float) @ Q).tolist()
            fls.append(g)
        over = dict(loads, fairleads=tuple(fls), frame_indices=frames, mean_equilibrium=record)
        if loads.get('wind_velocity') is not None:
            over['mean_thrust'] = float(record['mean_thrust'])
        ident = dict(base.identities)
        if wind_prov is not None:
            ident['wind_realization'] = wind_prov
        over['identities'] = ident
        inp = dataclasses.replace(base, **over)
        path = plan['input_path']                     # frozen on the GUI thread (never a widget read here)
        digest = inp.save(path)
        return path, digest, inp

    # ---------------------------------------------------------------- run / reopen / apply
    def start(self):
        try:
            # ---- GUI thread: ONE FE snapshot, all controls, paths, modal record and the mean call are frozen here
            fe_model = self.fe_model_provider()
            plan = self.plan_input(fe_snapshot=fe_model)
            from ...application.floating_transient import ElasticModalRecord
            modal = self.fields['modal'].text().strip()
            kwargs = dict(field_kernel_path=plan['field_kernel_path'], result_path=plan['result_path'],
                          fe_model=fe_model, screening=ElasticModalRecord.load_json(modal) if modal else None)
            inp0 = plan['base']
            label = DEVELOPMENT_MARKER if inp0.hydrodynamic_verification == 'unverified' else 'accepted hydrodynamics'
            run_job, finalize, check_pose = self.run_job, self.finalize_input, self.check_prepared_pose
            holder = {}
            self.job_input_path = plan['input_path']

            def work(*, cancellation_token, **kw):
                # worker thread: uses ONLY the frozen plan (no widget, no live project / provider); the mean runs in
                # the isolated process tree (finite timeout, killed on cancel); UI state changes go through signals
                cancel = (lambda: bool(cancellation_token.is_cancelled))
                record = plan['record']
                if plan['need_solve']:
                    try:
                        record = dict(plan['mean_call'](cancel))
                    except Exception as error:
                        if cancel():
                            return dict(status='cancelled', published=False, message='cancelled during the mean solve')
                        raise ValueError(f'operating mean preparation refused: {error}') from error
                    holder['runner'].mean_solved.emit()
                    check_pose(plan['base'], record)
                if cancel():
                    return dict(status='cancelled', published=False, message='cancelled before the transient')
                path, digest, _ = finalize(plan, record)
                return run_job(input_path=path, expected_input_hash=digest, cancellation_token=cancellation_token, **kw)

            self.runner = FloatingTransientRun(work, kwargs, self)
            holder['runner'] = self.runner
            self.runner.mean_solved.connect(self._count_mean_solve)
            self.runner.completed.connect(self.completed)
            self.runner.failed.connect(self.failed)
            self.runner.finished.connect(self.finished_run)
            self.run_button.setEnabled(False); self.cancel_run.setEnabled(True); self.apply_button.setEnabled(False)
            self.status.setText(f'Running the floating transient ({label}); no BEM is run.')
            self.runner.start()
        except BaseException as error:
            self.failed(str(error))

    def cancel(self):
        if self.runner is not None and self.runner.isRunning():
            self.runner.cancel()
            self.status.setText('Run cancelled; nothing published, project unchanged')

    def _count_mean_solve(self):
        self.mean_solves += 1                         # GUI thread (queued signal)

    def finished_run(self):
        self.run_button.setEnabled(True); self.cancel_run.setEnabled(False)

    def completed(self, summary):
        self.summary = dict(summary)
        self.show_summary(self.summary)

    def failed(self, message):
        self.summary = None
        self.apply_button.setEnabled(False); self.run_button.setEnabled(True); self.cancel_run.setEnabled(False)
        self.status.setText('Floating transient refused: ' + message)

    def show_summary(self, s):
        if not s.get('published'):
            self.diagnostics.setText(f"{s.get('status')}: {s.get('message', '')} - nothing published")
            self.apply_button.setEnabled(False)
            return
        if s.get('hydrodynamic_verification') == 'unverified':
            text = (f"{DEVELOPMENT_MARKER}. Status: {s['status']}. Unpassed hydrodynamic gates: "
                    + ', '.join(s.get('unpassed_gates', ())) + '. Not an accepted structural result. '
                    'Stresses are quasistatic.')
        else:
            text = (f"Status: {s['status']} (accepted hydrodynamics; unqualified development workflow). "
                    'Stresses are quasistatic (residual-relieved effective frame loads).')
        self.diagnostics.setText(text)
        ok = s['status'] == 'complete' or (s['status'] == 'completed_unverified_hydrodynamics'
                                           and self.development.isChecked())
        self.apply_button.setEnabled(ok)
        self.status.setText(f"{s.get('n_samples', 0)} samples, {s.get('n_frames', 0)} structural frames; result {s['result_hash'][:12]}")

    def reopen(self):
        try:
            s = self.reopen_job(self.fields['result'].text())
            s['published'] = True
            self.summary = s
            self.show_summary(s)
        except BaseException as error:
            self.failed(str(error))

    def apply(self):
        from ...application.floating_transient_job import ApplyFloatingTransientResult, result_reference
        try:
            if not self.summary or not self.summary.get('published'):
                raise ValueError('no published result to apply')
            name = self.fields['name'].text().strip() or 'Floating transient'
            ref = result_reference(name, self.summary['result_path'], expected_hash=self.summary['result_hash'],
                                   development_attached=self.development.isChecked())
            self.app.run(ApplyFloatingTransientResult(ref))
            self.app.set_status(f'Applied floating transient result {name!r}'
                                + (f' ({DEVELOPMENT_MARKER})' if ref.marker else ''))
            self.apply_button.setEnabled(False)
        except BaseException as error:
            self.failed(str(error))

    def inspect_applied(self, time_value=None):
        """Selected saved frame of the APPLIED project result: status, nodal displacements, stress values + layout
        (verified artifact, current mass + stiffness identity; never a solve or BEM)."""
        from ...application.floating_transient_job import applied_floating_transient_frames, applied_frame_at
        try:
            name = self.fields['name'].text().strip() or 'Floating transient'
            frames = applied_floating_transient_frames(self.app.project, self.fe_model_provider(), name,
                                                       allow_development=self.development.isChecked())
            t = float(self.inspect_time.text()) if time_value is None else float(time_value)
            frame = applied_frame_at(frames, t)
            disp = np.asarray(frame['displacements'], float)
            n_stress = sum(len(v) for v in frame['stresses'].values())
            label = DEVELOPMENT_MARKER if frame['marker'] else 'accepted'
            self.inspection.setText(
                f"t = {frame['time']:.6g} s (frame {frame['frame_index']}), status {frame['status']} ({label}); "
                f"max |u| = {float(np.max(np.abs(disp), initial=0.0)):.6g} over {disp.size} DOFs; "
                f"{n_stress} stress components in {len(frame['stresses'])} elements - {frame['stress_label']}")
            self.last_inspection = frame
            return frame
        except BaseException as error:
            self.last_inspection = None
            self.inspection.setText('Inspection refused: ' + str(error))
            return None

    def reject(self):
        self.cancel()
        if self.runner is not None:
            self.runner.wait(6000)
        super().reject()
