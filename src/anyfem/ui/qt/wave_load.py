"""Primary regular-wave load input with an isolated, cancellable computation."""
from dataclasses import asdict,fields
from pathlib import Path
from PySide6.QtCore import QThread,Signal,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,QLabel,QPushButton,QHBoxLayout,QCheckBox,QApplication
from ...application.hydro_wave_input import RegularWaveInput,compute_wave_input,ApplyWavePressure
from ...application.irregular_wave_input import IrregularWaveInput
from ...document import canonical_hash
from ...io.project_file import project_to_dict


class WaveCalculation(QThread):
    completed=Signal(object)
    failed=Signal(str)
    progress_changed=Signal(str)
    def __init__(self,kwargs,parent=None):
        super().__init__(parent);self.kwargs=kwargs;self.cancelled=False;self.tree=None
        from anysolver import CancellationToken
        self.token=CancellationToken()
    def run(self):
        try:
            from ...job_process import run_in_worker_process
            result=run_in_worker_process(function=compute_wave_input,kwargs=self.kwargs,
                snapshot=None,project_override={},progress=lambda value:self.progress_changed.emit(str(value)),token=self.token,
                containment=True,on_tree=lambda process,tree:setattr(self,'tree',tree))
            if not self.cancelled:self.completed.emit(result)
        except BaseException as error:
            if not self.cancelled:self.failed.emit(str(error))
    def cancel(self):
        self.cancelled=True;self.token.cancel('wave input cancelled')
        if self.tree is not None:self.tree.terminate()
    def shutdown(self):
        self.cancel();self.wait(6000)


class WaveLoadDialog(QDialog):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.app=panel.app;self.runner=None
        self.setWindowTitle('Hydrodynamic wave load');self.setObjectName('HydrodynamicWaveLoadDialog')
        layout=QVBoxLayout(self);form=QFormLayout();self.form=form;layout.addLayout(form)
        self.wave_type=QComboBox();self.wave_type.addItem('Regular wave','regular');form.addRow('Wave type',self.wave_type)
        self.wave_type.addItem('Irregular wave (JONSWAP)','irregular')
        self.inputs={}
        defaults={**asdict(IrregularWaveInput()),**asdict(RegularWaveInput())}
        selected=self.app.project.load_cases.get(panel.case_name())
        if selected is None:
            owners=[case for case in self.app.project.load_cases.values() if case.times is not None
                and any(load.history is not None for load in case.wet_pressures)]
            selected=next((case for case in owners if case.name==defaults['name']),None)
            if selected is None and len(owners)==1:selected=owners[0]
        editing=selected is not None and selected.times is not None and any(load.history is not None for load in selected.wet_pressures)
        if editing:
            defaults['name']=selected.name
            import json
            history=next(load.history for load in selected.wet_pressures if load.history is not None)
            try:
                saved=json.loads(history.provenance)
                retained_settings=saved.get('settings',{}) if isinstance(saved,dict) else {}
                if not isinstance(retained_settings,dict):retained_settings={}
            except (ValueError,TypeError):retained_settings={}
            from anyloads.source_pressure_history import SourceAwarePressureHistory
            if isinstance(history,SourceAwarePressureHistory) and not retained_settings:
                from ...application.pressure_transfer import retained_regular_settings
                retained_settings=retained_regular_settings(history)
            defaults.update(retained_settings);defaults['name']=selected.name
        for key,label in [('name','Load case / time series name'),('height','Wave height H (m); amplitude = H / 2'),
            ('period','Period T (s)'),('heading','Propagation heading (deg;0 = +X)'),('depth','Water depth (m)'),
            ('phase','Phase at t = 0 (deg)'),('significant_height','Significant wave height Hs (m)'),
            ('peak_period','Peak period Tp (s)'),('gamma','JONSWAP gamma'),('seed','Seed (nonnegative integer)'),
            ('component_count','Frequency components (1–200)'),
            ('lower_frequency_ratio','Lower frequency / peak frequency'),
            ('upper_frequency_ratio','Upper frequency / peak frequency'),
            ('duration','Duration (s)'),('dt','Sample interval (s)')]:
            edit=QLineEdit(str(defaults[key]));edit.setObjectName('wave_'+key);self.inputs[key]=edit;form.addRow(label,edit)
        form.addRow('Draft from admitted wetted geometry',QLabel('1.5 m — fixed body, dynamic pressure only'))
        self.spectrum_note=QLabel('JONSWAP energy is normalized over the declared finite band. '
            'Frequency resolution and band limits require engineering assessment.')
        self.spectrum_note.setWordWrap(True);layout.addWidget(self.spectrum_note)
        self.wave_type.setCurrentIndex(max(0,self.wave_type.findData(defaults['wave_type'])))
        self.wave_type.currentIndexChanged.connect(self.update_mode);self.update_mode()
        self.convert=QCheckBox('Replace old wave snapshots with one time series (keep other loads)')
        layout.addWidget(self.convert)
        name=defaults['name']
        legacy=any(c.series==name and c.parent is None and c.times is None and c.wet_pressures for c in self.app.project.load_cases.values())
        if editing:self.convert.setText('Replace the current wave time series (keep other loads)')
        self.convert.setChecked(legacy or editing);self.convert.setVisible(legacy or editing)
        self.status=QLabel('Creates one load case for Batch linear static using the current barge and mesh.');self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QHBoxLayout();self.apply_button=QPushButton('Create wave pressure time series');self.apply_button.setObjectName('CreateWavePressureSeries')
        self.cancel_button=QPushButton('Cancel');actions.addWidget(self.apply_button);actions.addWidget(self.cancel_button);layout.addLayout(actions)
        self.apply_button.clicked.connect(self.start);self.cancel_button.clicked.connect(self.reject)
        self.deadline=QTimer(self);self.deadline.setSingleShot(True);self.deadline.timeout.connect(self.timeout)

    def settings(self):
        cls=IrregularWaveInput if self.wave_type.currentData()=='irregular' else RegularWaveInput
        names={f.name for f in fields(cls)}
        values={k:(edit.text() if k=='name' else int(edit.text()) if k in ('seed','component_count')
            else float(edit.text())) for k,edit in self.inputs.items() if k in names}
        values['wave_type']=self.wave_type.currentData()
        return cls(**values)

    def update_mode(self):
        irregular=self.wave_type.currentData()=='irregular'
        for key in ('height','period','phase'):self.form.setRowVisible(self.inputs[key],not irregular)
        for key in ('significant_height','peak_period','gamma','seed','component_count',
                'lower_frequency_ratio','upper_frequency_ratio'):
            self.form.setRowVisible(self.inputs[key],irregular)
        self.spectrum_note.setVisible(irregular)

    def start(self):
        if self.runner is not None and self.runner.isRunning():return
        try:
            config=self.settings()
            if self.app.mesh is None:raise ValueError('Open/prepare the closed barge and generate its mesh first')
            if self.app.session.read_only:raise ValueError('The current project is read-only')
            from anymesher.serialize import mesh_to_dict
            document=project_to_dict(self.app.project);mesh_data=mesh_to_dict(self.app.mesh)
            self.input_session=self.app.session;self.input_project=self.app.project
            self.input_document_hash=canonical_hash(document);self.input_mesh_hash=canonical_hash(mesh_data)
            path=getattr(self.app,'path',None)
            cache=None if path is None else Path(path).parent/'prepared-source.npz'
            cache=str(cache) if cache is not None and cache.is_file() else None
            self.runner=WaveCalculation({'document':document,'mesh_data':mesh_data,'settings':asdict(config),'cache_path':cache},self)
            self.runner.completed.connect(self.apply_result);self.runner.failed.connect(self.failure)
            self.runner.progress_changed.connect(self.status.setText)
            self.runner.finished.connect(self.finished_run)
            QApplication.instance().aboutToQuit.connect(self.runner.shutdown)
            for edit in (*self.inputs.values(),self.wave_type,self.convert):edit.setEnabled(False)
            self.apply_button.setEnabled(False);self.status.setText('Calculating wave pressure and mapping…')
            self.deadline.start(180000);self.runner.start()
        except BaseException as error:self.failure(str(error))

    def apply_result(self,result):
        try:
            from anymesher.serialize import mesh_to_dict
            if result.get('source_cache_path'):
                import hashlib
                if hashlib.sha256(Path(result['source_cache_path']).read_bytes()).hexdigest()!=result['source_cache_sha256']:
                    raise ValueError('Source pressure file changed during calculation; nothing applied')
            if (self.runner.cancelled or self.app.session is not self.input_session or self.app.project is not self.input_project
                    or self.app.session.read_only or self.app.mesh is None
                    or canonical_hash(project_to_dict(self.app.project))!=self.input_document_hash
                    or canonical_hash(mesh_to_dict(self.app.mesh))!=self.input_mesh_hash):
                raise ValueError('Project or mesh changed during calculation; no wave load applied')
            parent=self.app.run(ApplyWavePressure(result,replace_legacy=self.convert.isChecked()))
            self.panel.case.setText(parent.name)
            solve=self.app.panels.get('Solve')
            if solve is not None:
                solve.analysis.setCurrentText('Batch linear static');solve.refresh();solve.case.setCurrentText(parent.name)
            self.app.set_status(f'Created one wave pressure time series with {len(parent.times)} steps; use Batch linear static')
            self.accept()
        except BaseException as error:self.failure(str(error))

    def failure(self,message):
        self.status.setText(message);self.apply_button.setEnabled(True)
    def finished_run(self):
        self.deadline.stop()
        for edit in (*self.inputs.values(),self.wave_type,self.convert):edit.setEnabled(True)
        self.update_mode()
        if self.runner.cancelled:self.status.setText('Calculation cancelled; project unchanged')
        self.apply_button.setEnabled(True)
    def timeout(self):
        if self.runner:self.runner.cancel()
        self.status.setText('Calculation exceeded180 s and was cancelled; project unchanged')
    def reject(self):
        if self.runner and self.runner.isRunning():self.runner.cancel()
        super().reject()
