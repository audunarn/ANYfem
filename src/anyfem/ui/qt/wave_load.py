"""Primary regular-wave load input with an isolated, cancellable computation."""
from dataclasses import asdict,fields
from pathlib import Path
from PySide6.QtCore import QThread,Signal,QTimer,Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,QLabel,QPushButton,QHBoxLayout,QCheckBox,QApplication,QWidget,QScrollArea
from ...application.hydro_wave_input import RegularWaveInput,prepare_wave_preview,recover_prepared_wave_input,select_prepared_wave_window,ApplyWavePressure
from ...application.irregular_wave_input import IrregularWaveInput
from ...document import canonical_hash
from ...io.project_file import project_to_dict


class WaveCalculation(QThread):
    completed=Signal(object)
    failed=Signal(str)
    progress_changed=Signal(str)
    def __init__(self,kwargs,parent=None):
        super().__init__(parent);self.kwargs=dict(kwargs);self.stage=self.kwargs.pop('_wave_stage','preview');self.cancelled=False;self.tree=None
        from anysolver import CancellationToken
        self.token=CancellationToken()
    def run(self):
        try:
            from ...job_process import run_in_worker_process
            if self.stage=='floating':
                from ...application.floating_job import prepare_floating_job as function
            elif self.stage=='floating_reopen':
                from ...application.floating_job import reopen_floating_job as function
            else:function=recover_prepared_wave_input if self.stage=='apply' else prepare_wave_preview
            import contextlib
            with contextlib.ExitStack() as stack:
                if self.stage in ('floating','floating_reopen'):
                    # Parent-owned per-job publication workspace: a hard-killed worker tree (cancel -> tree.terminate) cannot leave a
                    # staged temp behind - this directory, and only this one, is removed when the job ends.
                    from ...application.floating_job import owned_publication_workspace
                    self.kwargs['publication_directory']=str(stack.enter_context(owned_publication_workspace(self.kwargs['record_path'])))
                result=run_in_worker_process(function=function,kwargs=self.kwargs,
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
        super().__init__(panel);self.panel=panel;self.app=panel.app;self.runner=None;self.prepared=None
        self._editing_history=None;self._restored_interval=None
        self._prepared_settings=None
        self._retained_force_preview=None
        self._settings_problem=None
        self._job_generation=0;self._active_stage=None;self._apply_window=None;self._apply_preview_hash=None
        self.setWindowTitle('Hydrodynamic wave load');self.setObjectName('HydrodynamicWaveLoadDialog')
        self.resize(1180,680);self.setMinimumSize(980,600)
        layout=QVBoxLayout(self);body=QHBoxLayout();layout.addLayout(body,1)
        inputs_widget=QWidget();left=QVBoxLayout(inputs_widget);form=QFormLayout();self.form=form;left.addLayout(form)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(inputs_widget);scroll.setMinimumWidth(340);scroll.setMaximumWidth(430)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.addWidget(scroll)
        right=QVBoxLayout();body.addLayout(right,1)
        from .wave_preview_plot import WaveResultantPlot
        self.plot=WaveResultantPlot();right.addWidget(self.plot,1)
        self.record_info=QLabel('Full generated record: not prepared');self.record_info.setWordWrap(True)
        self.record_info.setMinimumWidth(0);right.addWidget(self.record_info)
        window_row=QHBoxLayout();right.addLayout(window_row)
        window_row.addWidget(QLabel('Analysis interval (absolute s):'))
        self.window_start=QLineEdit();self.window_start.setObjectName('wave_window_start');self.window_start.setMaximumWidth(105)
        self.window_end=QLineEdit();self.window_end.setObjectName('wave_window_end');self.window_end.setMaximumWidth(105)
        window_row.addWidget(self.window_start);window_row.addWidget(QLabel('to'));window_row.addWidget(self.window_end)
        self.full_window=QPushButton('Full record');window_row.addWidget(self.full_window)
        self.window_info=QLabel('Prepare, then drag blue handles or enter endpoints. Endpoints snap to original samples.')
        self.window_info.setWordWrap(True);right.addWidget(self.window_info)
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
        self._original_owner_name=selected.name if editing else None
        if editing:
            defaults['name']=selected.name
            import json
            history=next(load.history for load in selected.wet_pressures if load.history is not None)
            self._editing_history=history
            self._editing_floating=None
            from anyloads.floating_history import FloatingCoupledLoadHistory
            if hasattr(history,'selected_interval'):self._restored_interval=history.selected_interval
            elif getattr(history,'selected_window',()):self._restored_interval=tuple(history.selected_window)
            if type(history) is FloatingCoupledLoadHistory:
                # The original dialog inputs (type, seed, spectrum, heading ...) were saved beside the authoritative realization.
                from ...application.floating_apply import restore_dialog_settings
                try:
                    self._editing_floating=restore_dialog_settings(history);retained_settings=dict(self._editing_floating['wave'])
                except (ValueError,KeyError) as error:
                    retained_settings={}
                    self._settings_problem=f'Saved floating wave settings cannot be restored: {error}. Start a new record with known inputs; the existing case is unchanged.'
            else:
              try:
                saved=json.loads(history.provenance)
                retained_settings=saved.get('settings',{}) if isinstance(saved,dict) else {}
                if not isinstance(retained_settings,dict):retained_settings={}
              except (ValueError,TypeError):retained_settings={}
            from anyloads.source_pressure_history import SourceAwarePressureHistory
            if isinstance(history,SourceAwarePressureHistory) and not retained_settings:
                if history.force_payload_json:
                    self._settings_problem='Saved v3 wave settings are missing. Restore valid saved settings or recreate explicit wave inputs; no pressure fallback or guessed realization is used.'
                else:
                    from ...application.pressure_transfer import retained_regular_settings
                    try:retained_settings=retained_regular_settings(history)
                    except (ValueError,ImportError) as error:
                        self._settings_problem=f'Saved legacy wave settings cannot be restored: {error}. Start a new wave record with known inputs; the existing case is unchanged.'
            defaults.update(retained_settings);defaults['name']=selected.name
        for key,label in [('name','Load case / time series name'),('height','Wave height H (m); amplitude = H / 2'),
            ('period','Period T (s)'),('heading','Propagation heading (deg;0 = +X)'),('depth','Water depth (m)'),
            ('phase','Phase at t = 0 (deg)'),('significant_height','Significant wave height Hs (m)'),
            ('peak_period','Peak period Tp (s)'),('gamma','JONSWAP gamma'),('seed','Seed (nonnegative integer)'),
            ('component_count','Frequency components (1–200)'),
            ('lower_frequency_ratio','Lower frequency / peak frequency'),
            ('upper_frequency_ratio','Upper frequency / peak frequency'),
            ('duration','Full generated duration (s)'),('dt','Original sample interval (s)')]:
            edit=QLineEdit(str(defaults[key]));edit.setObjectName('wave_'+key);self.inputs[key]=edit;form.addRow(label,edit)
        draft_note=QLabel('Admitted wet geometry: draft 1.5 m. Fixed body; dynamic pressure only unless hydrostatic pressure is included.')
        self._fixed_body_note=draft_note
        draft_note.setWordWrap(True);form.addRow(draft_note)
        self.hydrostatic=QCheckBox('Include hydrostatic pressure');self.hydrostatic.setObjectName('wave_include_hydrostatic')
        self.hydrostatic.setToolTip('Include hydrostatic pressure at the mean water level.')
        self.hydrostatic.setChecked(defaults.get('include_hydrostatic') is True);form.addRow(self.hydrostatic)
        self.follow=QCheckBox('Follow instantaneous wave surface')
        self.follow.setObjectName('wave_follow_surface')
        self.follow.setToolTip('Pressure is zero above the instantaneous surface, -rho g z + dynamic below it, and the '
            'waterline dynamic pressure is extended above the mean level. Components are summed before wetting. '
            'Fixed body: no body motion, inertia relief, self weight or slamming. Overtopping/deck water is not loaded.')
        self._hydrostatic_before=self.hydrostatic.isChecked()
        self.follow.setChecked(defaults.get('follow_surface') is True);form.addRow(self.follow)
        follow_note=QLabel('Wet/dry at the free surface; includes hydrostatic pressure.')
        self._follow_surface_note=follow_note
        follow_note.setWordWrap(True);form.addRow(follow_note)
        self.body_mode=QComboBox();self.body_mode.setObjectName('wave_body_mode')
        self.body_mode.addItem('Fixed body','fixed');self.body_mode.addItem('Floating linear response','floating')
        self.body_mode.setToolTip('Floating: coupled 6-DOF linear response about the automatic mean draft/heel/trim; the load owns '
            'static pressure, weight and rigid inertia on the stationary mesh. It cannot be combined with Follow surface.')
        form.addRow('Body',self.body_mode)
        self.floating_fields={}
        for key,label in (('equilibrium_python','Isolated equilibrium Python (pinned ANYgeometry runtime)'),
                ('owner_wheel','Pinned ANYgeometry owner wheel path'),('stability_source','ANYstability source path (optional)'),
                ('envelope_extra_face_ids','Closing face ids for the displacement envelope (comma separated)'),
                ('panel_size','BEM source panel size (m; 0 = automatic within 600 panels)')):
            edit=QLineEdit('0' if key=='panel_size' else '');edit.setObjectName('floating_'+key);self.floating_fields[key]=edit;form.addRow(label,edit)
        self.floating_dev=QCheckBox('Allow engineering (development) equilibrium');self.floating_dev.setObjectName('floating_allow_development')
        self.floating_native=QCheckBox('Allow native BEM solves in Prepare');self.floating_native.setObjectName('floating_allow_native')
        form.addRow(self.floating_dev);form.addRow(self.floating_native)
        self.inspect_button=QPushButton('Inspect selected time (pressure / acceleration)');self.inspect_button.setObjectName('FloatingInspect')
        self.inspection=QLabel('');self.inspection.setWordWrap(True);self.inspection.setObjectName('FloatingInspection')
        self.inspect_button.clicked.connect(lambda:self.floating_inspect())
        form.addRow(self.inspect_button);form.addRow(self.inspection)
        self.body_mode.currentIndexChanged.connect(self.body_mode_changed)
        for edit in self.floating_fields.values():edit.textChanged.connect(lambda *args:self.invalidate_preview('floating'))
        self.floating_dev.toggled.connect(lambda *args:self.invalidate_preview('floating'));self.floating_native.toggled.connect(lambda *args:self.invalidate_preview('floating'))
        self._floating_widgets=[*self.floating_fields.values(),self.floating_dev,self.floating_native,self.inspect_button,self.inspection]
        for widget in self._floating_widgets:self.form.setRowVisible(widget,False)
        restored=getattr(self,'_editing_floating',None)
        if restored:   # reopening a saved floating series restores its original inputs, not guesses
            saved_floating=restored['floating']
            for key in ('equilibrium_python','owner_wheel','stability_source'):self.floating_fields[key].setText(str(saved_floating.get(key,'')))
            self.floating_fields['envelope_extra_face_ids'].setText(','.join(str(i) for i in saved_floating.get('envelope_extra_face_ids',())))
            self.floating_fields['panel_size'].setText(str(saved_floating.get('panel_size',0)))
            self.floating_dev.setChecked(bool(saved_floating.get('allow_development')))
            self.body_mode.blockSignals(True);self.body_mode.setCurrentIndex(self.body_mode.findData('floating'));self.body_mode.blockSignals(False)
        self.hydrostatic_note=QLabel('Fixed admitted draft 1.5 m, rho 1025 kg/m3, g 9.81 m/s2. '
            'Vessel weight and inertia relief are separate.')
        self.hydrostatic_note.setToolTip('Static pressure rho g max(1.5 m - z, 0), added once as a constant load to the dynamic wave pressure.')
        self.hydrostatic_note.setWordWrap(True);form.addRow(self.hydrostatic_note)
        self.spectrum_note=QLabel('JONSWAP energy is normalized over the declared finite band. '
            'Frequency resolution and band limits require engineering assessment.')
        self.spectrum_note.setWordWrap(True);left.addWidget(self.spectrum_note)
        self.wave_type.setCurrentIndex(max(0,self.wave_type.findData(defaults['wave_type'])))
        self.wave_type.currentIndexChanged.connect(self.update_mode);self.update_mode()
        self.convert=QCheckBox('Replace old wave snapshots')
        self.convert.setToolTip('Replace old wave snapshots with one time series; keep other loads.')
        left.addWidget(self.convert);left.addStretch()
        name=defaults['name']
        legacy=any(c.series==name and c.parent is None and c.times is None and c.wet_pressures for c in self.app.project.load_cases.values())
        if editing:self.convert.setText('Replace the current wave time series')
        self.convert.setChecked(legacy or editing);self.convert.setVisible(legacy or editing)
        self.status=QLabel('Creates one load case for Batch linear static using the current barge and mesh.');self.status.setWordWrap(True);layout.addWidget(self.status)
        actions=QHBoxLayout();self.prepare_button=QPushButton('1. Prepare load preview');self.prepare_button.setObjectName('PrepareWavePreview')
        self.repair_button=QPushButton('Repair / refresh cache (may run BEM)');self.repair_button.setObjectName('RepairWaveCache')
        self.new_record_button=QPushButton('Start a new wave record');self.new_record_button.setVisible(self._settings_problem is not None)
        self.apply_button=QPushButton('2. Apply selected interval');self.apply_button.setObjectName('CreateWavePressureSeries');self.apply_button.setEnabled(False)
        self.cancel_button=QPushButton('Cancel');actions.addWidget(self.prepare_button);actions.addWidget(self.repair_button);actions.addWidget(self.new_record_button);actions.addStretch();actions.addWidget(self.apply_button);actions.addWidget(self.cancel_button);layout.addLayout(actions)
        self.prepare_button.clicked.connect(self.start);self.apply_button.clicked.connect(lambda:self.apply_result());self.cancel_button.clicked.connect(self.reject)
        self.repair_button.clicked.connect(lambda:self.start(repair_cache=True))
        self.new_record_button.clicked.connect(self.start_new_record)
        # Floating wind transient: separate dialog on a saved coarse job input (never runs BEM)
        self.transient_button=QPushButton('Floating wind transient…');self.transient_button.setObjectName('OpenFloatingTransient')
        actions.insertWidget(3,self.transient_button);self.transient_button.clicked.connect(lambda:self.open_floating_transient())
        self.window_start.editingFinished.connect(self.numeric_window);self.window_end.editingFinished.connect(self.numeric_window)
        self.full_window.clicked.connect(self.select_full_window);self.plot.selectionChanged.connect(self.set_window)
        self.deadline=QTimer(self);self.deadline.setSingleShot(True);self.deadline.timeout.connect(self.timeout)
        self._editing_settings=asdict(self.settings()) if editing and self._settings_problem is None else None
        for key,edit in self.inputs.items():edit.textChanged.connect(lambda *args,key=key:self.invalidate_preview(key))
        self.hydrostatic.toggled.connect(lambda *args:self.invalidate_preview('include_hydrostatic'))
        self.follow.toggled.connect(self.follow_toggled)
        self.plot.set_composition(self.hydrostatic.isChecked(),follow=self.follow.isChecked())
        self.update_mode()
        self.wave_type.currentIndexChanged.connect(lambda *args:self.invalidate_preview('wave_type'))
        self._enable_window(False)
        if self._settings_problem:
            self.prepare_button.setEnabled(False);self.repair_button.setEnabled(False);self.status.setText(self._settings_problem)
        if getattr(self,'_editing_floating',None):
            interval=self._restored_interval
            self.body_mode_changed();self._restored_interval=interval

    def floating_selected(self):return self.body_mode.currentData()=='floating'

    def open_floating_transient(self,**services):
        from .floating_transient import FloatingTransientDialog
        self.transient_dialog=FloatingTransientDialog(self.panel,**services);self.transient_dialog.show()
        return self.transient_dialog

    def body_mode_changed(self,*args):
        """Floating owns static + gravity + inertia and cannot combine with the nonlinear Follow surface mode."""
        floating=self.floating_selected()
        for widget in self._floating_widgets:self.form.setRowVisible(widget,floating)
        for widget in (self._fixed_body_note,self._follow_surface_note):self.form.setRowVisible(widget,not floating)
        if floating:
            self._hydrostatic_before_floating=self.hydrostatic.isChecked();self._follow_before_floating=self.follow.isChecked()
            self.follow.setChecked(False);self.hydrostatic.setChecked(True)
        else:   # back to Fixed body: restore the user's own selections exactly
            self.plot.set_floating_caption(None)
            self.plot.set_composition(self.hydrostatic.isChecked(),self.follow.isChecked())
            if getattr(self,'_hydrostatic_before_floating',None) is not None:
                self.hydrostatic.setChecked(self._hydrostatic_before_floating)
                self.follow.setChecked(bool(getattr(self,'_follow_before_floating',False)))
                self._hydrostatic_before_floating=None
        self.follow.setEnabled(not floating);self.hydrostatic.setEnabled(not floating and not self.follow.isChecked())
        self.prepare_button.setText('1. Prepare floating response' if floating else '1. Prepare load preview')
        self.apply_button.setText('2. Apply selected interval')       # floating Apply creates the ONE project load
        self.invalidate_preview('body_mode')
        if floating:self.status.setText('Floating linear response: mean equilibrium from the assembled dry mass, then pressure, weight and '
            'rigid inertia on the stationary mesh. Prepare may run BEM only when explicitly allowed above.')

    def floating_kwargs(self,config):
        """Worker kwargs for the floating Prepare stage (explicit, nothing hardcoded)."""
        raw=self.floating_fields
        faces=tuple(int(x) for x in raw['envelope_extra_face_ids'].text().replace(' ','').split(',') if x)
        floating=dict(equilibrium_python=raw['equilibrium_python'].text().strip(),owner_wheel=raw['owner_wheel'].text().strip(),
            stability_source=raw['stability_source'].text().strip(),envelope_extra_face_ids=faces,
            panel_size=float(raw['panel_size'].text() or 0),allow_development=self.floating_dev.isChecked(),
            allow_native_solve=self.floating_native.isChecked())
        directory=getattr(self.app,'wave_solution_cache_directory',None)
        path=getattr(self.app,'path',None)
        if path is None:raise ValueError('Save the project first: the floating record is stored beside it')
        base=Path(path).resolve().parent         # absolute before any publication path is built (relative project paths)
        return dict(settings={'wave':asdict(config),'floating':floating},
            cache_directory=str(directory if directory is not None else base/'floating-family-cache'),
            record_path=str(base/('floating-'+''.join(c if c.isalnum() or c in '-_' else '_' for c in self.inputs['name'].text())+'.npz')))

    def follow_toggled(self,checked):
        """Following the surface implies and locks hydrostatic inclusion; unticking restores the previous choice."""
        if checked:
            self._hydrostatic_before=self.hydrostatic.isChecked()
            self.hydrostatic.setChecked(True)
        else:self.hydrostatic.setChecked(self._hydrostatic_before)
        self.hydrostatic.setEnabled(not checked)
        self.invalidate_preview('follow_surface')

    def _enable_window(self,enabled):
        for widget in (self.window_start,self.window_end,self.full_window):widget.setEnabled(enabled)
        self.plot.setEnabled(enabled)

    def start_new_record(self):
        if self._settings_problem is None:return
        self._settings_problem=None;self._editing_history=None;self._editing_settings=None;self._original_owner_name=None
        self._retained_force_preview=None;self._restored_interval=None
        values={**asdict(IrregularWaveInput()),**asdict(RegularWaveInput())}
        name='New wave';index=2
        while name in self.app.project.load_cases:name=f'New wave {index}';index+=1
        values['name']=name
        for key,edit in self.inputs.items():edit.setText(str(values[key]))
        self.follow.setChecked(False);self.hydrostatic.setChecked(False)
        self.wave_type.setCurrentIndex(0);self.convert.setChecked(False);self.convert.hide();self.new_record_button.hide()
        self.prepare_button.setEnabled(True);self.repair_button.setEnabled(True)
        self.status.setText('New independent wave record. Enter known inputs before preparing; the existing record is unchanged.')

    def _bind_runner(self,runner,stage):
        self.runner=runner;self._job_generation+=1;generation=self._job_generation;self._active_stage=stage
        def current():return self.runner is runner and self._job_generation==generation and self._active_stage==stage
        def completed(result):
            if not current():return
            if stage=='apply':self.recovered_result(result)
            else:self.prepared_result(result)
        runner.completed.connect(completed)
        runner.failed.connect(lambda message:self.failure(message) if current() else None)
        runner.progress_changed.connect(lambda message:self.status.setText(message) if current() else None)
        runner.finished.connect(lambda:self.finished_run(runner,generation) if current() else None)
        QApplication.instance().aboutToQuit.connect(runner.shutdown)

    def invalidate_preview(self,key=None):
        # The name and the steady hydrostatic composition are not dynamic-source inputs: keep the retained record/interval.
        keep=key in ('name','include_hydrostatic')
        if keep and self.prepared is not None and self.prepared.get('kind')!='floating_linear_response_v1':
            if self.prepared.get('preview_kind')=='force_only_v1':self._retained_force_preview=self.prepared['force_preview']
            else:
                from anyloads.pressure_history import pressure_history_from_dict
                self._editing_history=pressure_history_from_dict(self.prepared['history'])
            self._editing_settings=dict(self._prepared_settings)
            self._restored_interval=self.plot.interval or self._restored_interval
        if key=='name' and self._original_owner_name is not None and self.inputs['name'].text()!=self._original_owner_name:
            self.convert.setChecked(False)
        self.prepared=None;self.apply_button.setEnabled(False);self._enable_window(False)
        self.plot.times=None;self.plot.values=None;self.plot.interval=None;self.plot.update()
        self.record_info.setText('Full generated record: inputs changed; prepare again')
        if not keep:self._restored_interval=None;self._retained_force_preview=None
        self.plot.set_composition(self.hydrostatic.isChecked(),follow=self.follow.isChecked())
        self.status.setText('Inputs changed. Prepare a new preview before applying.')

    def set_window(self,start,end):
        if self._active_stage=='apply':return
        if self.prepared is None:return
        from anyloads.wave_preview import select_sample_window
        try:
            window=select_sample_window(self.prepared['preview_times'],start,end)
            self.window_start.setText(repr(window.interval[0]));self.window_end.setText(repr(window.interval[1]))
            self.plot.set_interval(*window.interval)
            self._restored_interval=window.interval
            self.window_info.setText(f'Selected {len(window.times)} original samples: {window.interval[0]:.9g}–{window.interval[1]:.9g} s. '
                'Absolute time and wave phases are preserved.')
            self.apply_button.setEnabled(True)
        except ValueError as error:
            self.window_info.setText(str(error));self.apply_button.setEnabled(False)

    def numeric_window(self):
        if self._active_stage=='apply':return
        try:self.set_window(float(self.window_start.text()),float(self.window_end.text()))
        except ValueError as error:self.window_info.setText(str(error));self.apply_button.setEnabled(False)

    def select_full_window(self):
        if self.prepared is not None:self.set_window(self.prepared['preview_times'][0],self.prepared['preview_times'][-1])

    def settings(self):
        cls=IrregularWaveInput if self.wave_type.currentData()=='irregular' else RegularWaveInput
        names={f.name for f in fields(cls)}
        values={k:(edit.text() if k=='name' else int(edit.text()) if k in ('seed','component_count')
            else float(edit.text())) for k,edit in self.inputs.items() if k in names}
        values['wave_type']=self.wave_type.currentData()
        values['include_hydrostatic']=self.hydrostatic.isChecked()
        values['follow_surface']=self.follow.isChecked()
        return cls(**values)

    def update_mode(self):
        irregular=self.wave_type.currentData()=='irregular'
        for key in ('height','period','phase'):self.form.setRowVisible(self.inputs[key],not irregular)
        for key in ('significant_height','peak_period','gamma','seed','component_count',
                'lower_frequency_ratio','upper_frequency_ratio'):
            self.form.setRowVisible(self.inputs[key],irregular)
        self.spectrum_note.setVisible(irregular)
        self.hydrostatic.setEnabled(not self.follow.isChecked())   # locked on while following the surface
        if getattr(self,'body_mode',None) is not None and self.floating_selected():
            self.follow.setEnabled(False);self.hydrostatic.setEnabled(False)   # floating owns static+gravity+inertia

    def surface_requirements_text(self):
        """Missing field-capable solution count shown BEFORE preparation (no BEM, no Capytaine import)."""
        from ...application.wave_surface_pressure import field_requirements
        directory=getattr(self.app,'wave_solution_cache_directory',None)
        need=field_requirements(asdict(self.settings()),None if directory is None else str(directory))
        return (f'{need["missing"]} of {need["total"]} field-capable hydrodynamic solutions are missing and will be '
            'computed once by Prepare (BEM); Apply and reopening never run BEM.') if need['missing'] else (
            f'All {need["total"]} field-capable hydrodynamic solutions are available; Prepare needs no BEM.')

    def start(self,repair_cache=False):
        if self._active_stage=='apply' or (self.runner is not None and self.runner.isRunning()):return
        try:
            if self._settings_problem:raise ValueError(self._settings_problem)
            config=self.settings()
            if self.app.mesh is None:raise ValueError('Open/prepare the closed barge and generate its mesh first')
            if self.app.session.read_only:raise ValueError('The current project is read-only')
            from anymesher.serialize import mesh_to_dict
            document=project_to_dict(self.app.project);mesh_data=mesh_to_dict(self.app.mesh)
            self.input_session=self.app.session;self.input_project=self.app.project
            self.input_document_hash=canonical_hash(document);self.input_mesh_hash=canonical_hash(mesh_data)
            self.input_settings_hash=canonical_hash(asdict(config))
            self.input_settings=asdict(config)
            if self.floating_selected():
                # Separate additive mode: the load owns static pressure, weight and rigid inertia; never combined with Follow.
                if config.follow_surface:raise ValueError('Floating linear response cannot be combined with Follow surface')
                kwargs={'_wave_stage':'floating','document':document,'mesh_data':mesh_data,**self.floating_kwargs(config)}
                self.prepared=None;self.apply_button.setEnabled(False);self._enable_window(False)
                self._bind_runner(WaveCalculation(kwargs,self),'floating')
                for edit in (*self.inputs.values(),self.wave_type,self.convert,self.hydrostatic,self.follow,self.body_mode,
                        *self._floating_widgets):edit.setEnabled(False)
                self.prepare_button.setEnabled(False);self.repair_button.setEnabled(False)
                self.status.setText('Preparing floating response: mean equilibrium, wet source, retained families, coupled loads…')
                self.deadline.start(3600000);self.runner.start();return
            path=getattr(self.app,'path',None)
            cache=None if path is None else Path(path).parent/'prepared-source.npz'
            cache=str(cache) if cache is not None and cache.is_file() else None
            # Force preview does not need target cells, mapped history or panel
            # pressure payloads. Keep full snapshots/hashes here for Apply guard.
            context={**document,'load_cases':[],'combinations':[],'analyses':[]}
            kwargs={'document':context,'mesh_data':None,'document_content_hash':self.input_document_hash,
                'mesh_content_hash':self.input_mesh_hash,'preview_context_version':'wave_preview_context_v1',
                'settings':asdict(config),'cache_path':cache,'repair_cache':repair_cache}
            if getattr(self.app,'wave_solution_cache_directory',None) is not None:
                kwargs['source_bank_cache_directory']=str(self.app.wave_solution_cache_directory)
            if config.follow_surface:
                # Separate additive mode: needs the real mesh target; the missing-solution count is shown first.
                kwargs={'document':context,'mesh_data':mesh_data,'settings':asdict(config),
                    'document_content_hash':self.input_document_hash,'mesh_content_hash':self.input_mesh_hash,
                    **{k:v for k,v in kwargs.items() if k=='source_bank_cache_directory'}}
                try:self.status.setText(self.surface_requirements_text())
                except BaseException as error:raise ValueError(f'cannot inspect field-capable solutions: {error}') from error
            from anyloads.source_pressure_history import SourceAwarePressureHistory
            # Name and hydrostatic composition never invalidate the retained dynamic source.
            previous={k:v for k,v in (self._editing_settings or {}).items() if k not in ('name','include_hydrostatic')}
            current={k:v for k,v in asdict(config).items() if k not in ('name','include_hydrostatic')}
            if previous==current and not config.follow_surface:
                if self._retained_force_preview is not None and not repair_cache:
                    kwargs['retained_preview']=self._retained_force_preview;kwargs['cache_path']=None
                elif isinstance(self._editing_history,SourceAwarePressureHistory):
                    if self._editing_history.force_payload_json and not repair_cache:
                        from ...application.wave_force_input import compact_retained_preview
                        kwargs['retained_preview']=compact_retained_preview(self._editing_history,self._editing_settings)
                    else:kwargs['retained_history']=self._editing_history.to_dict()
                    kwargs['cache_path']=None
            self.prepared=None;self.apply_button.setEnabled(False);self._enable_window(False)
            self._bind_runner(WaveCalculation(kwargs,self),'preview')
            for edit in (*self.inputs.values(),self.wave_type,self.convert,self.hydrostatic,self.follow):edit.setEnabled(False)
            self.prepare_button.setEnabled(False);self.repair_button.setEnabled(False)
            if config.follow_surface:
                self.status.setText(self.status.text()+' Preparing same-solution surface fields and applied-resultant preview…')
            else:
                self.status.setText('Explicit cache repair / refresh in progress; bad entries may run BEM…' if repair_cache else 'Preparing compact force responses and full-record resultants; pressures deferred until Apply…')
            self.deadline.start(180000);self.runner.start()
        except BaseException as error:self.failure(str(error))

    def _check_current(self,result,verify_source_file=False):
        from anymesher.serialize import mesh_to_dict
        if verify_source_file and result.get('source_cache_path'):
            import hashlib
            if hashlib.sha256(Path(result['source_cache_path']).read_bytes()).hexdigest()!=result['source_cache_sha256']:
                raise ValueError('Source pressure file changed during preparation; nothing applied')
        if (self.runner is None or self.runner.cancelled or self.app.session is not self.input_session or
                self.app.project is not self.input_project or self.app.session.read_only or self.app.mesh is None or
                canonical_hash(project_to_dict(self.app.project))!=self.input_document_hash or
                canonical_hash(mesh_to_dict(self.app.mesh))!=self.input_mesh_hash or
                canonical_hash(asdict(self.settings()))!=self.input_settings_hash or
                result['input_document_hash']!=self.input_document_hash or result['mesh_hash']!=self.input_mesh_hash or
                result.get('input_settings_hash')!=self.input_settings_hash):
            raise ValueError('Inputs, project or mesh changed during preparation; prepare again before applying')

    def floating_result(self,result):
        """Floating Prepare finished: the immutable record is saved beside the project; the project itself is unchanged."""
        from anymesher.serialize import mesh_to_dict
        if (self.runner is None or self.runner.cancelled or self.app.session is not self.input_session or
                self.app.project is not self.input_project or self.app.mesh is None or
                canonical_hash(project_to_dict(self.app.project))!=self.input_document_hash or
                canonical_hash(mesh_to_dict(self.app.mesh))!=self.input_mesh_hash):
            raise ValueError('Inputs, project or mesh changed during floating preparation; prepare again')
        self.prepared=result
        self.record_info.setText(
            f'Floating linear response: {result["components"]} component(s) · {result["nodes"]} nodes · mass {result["mass"]:.6g} kg · '
            f'rotation envelope {result["rotation_envelope_deg"]:.3g}° · waterline envelope {result["waterline_envelope_m"]:.3g} m '
            f'(clearance {result["clearance_m"]:.3g} m) · source closure {result["source_closure_max"]:.2e} · '
            f'mapped wrench error {result["mapping_wrench_rel_max"]:.2e} · {result["source_panels"]} source panels. '
            'Moments are about the mean centre of gravity in mean body axes.')
        times=result['preview_times']
        import numpy as np
        series=result['resultants']
        origin=series['origin']
        # The same assembled graph widget as the fixed-body mode, fed with the floating dynamic-pressure resultants.
        origin_text=', '.join('0' if abs(x)<1e-10 else f'{x:.5g}' for x in origin)
        self.plot.set_floating_caption(f'APPLIED pressure resultant · mean centre of gravity ({origin_text}) m · mean body axes')
        interval=self._restored_interval or (times[0],times[-1])
        self.plot.set_composition(True)       # the plotted pressure resultant includes the hydrostatic baseline
        self.plot.set_data(np.array(times,dtype=float),np.array(series['pressure'],dtype=float),interval)
        self._enable_window(True)
        self.set_window(*interval)
        self.status.setText('Floating response prepared and validated (self-contained record). Choose the absolute-time interval, '
            'then Apply to create the ONE coupled load series (pressure + weight + rigid inertia).')

    def floating_inspect(self):
        """Pressure / weight / inertia / primary-acceleration inspection of the prepared record at the first selected time."""
        try:
            if self.prepared is None or self.prepared.get('kind')!='floating_linear_response_v1':raise ValueError('Prepare the floating response first')
            from ...application.floating_inspection import inspect_record,format_inspection
            t=float(self.window_start.text())
            view=inspect_record(self.prepared['record_path'],self.app.project,self.app.mesh,t)
            self.inspection.setText(format_inspection(view))
        except BaseException as error:self.inspection.setText(f'Inspection unavailable: {error}')

    def floating_apply(self):
        """Validate the saved record in the worker (stale/cancel guards, no BEM), then apply the ONE project load."""
        from anyloads.wave_preview import select_sample_window
        from anymesher.serialize import mesh_to_dict
        if (self.app.session is not self.input_session or self.app.project is not self.input_project or self.app.mesh is None
                or canonical_hash(project_to_dict(self.app.project))!=self.input_document_hash
                or canonical_hash(mesh_to_dict(self.app.mesh))!=self.input_mesh_hash):
            raise ValueError('Inputs, project or mesh changed after preparation; prepare again before applying')
        window=select_sample_window(self.prepared['preview_times'],float(self.window_start.text()),float(self.window_end.text()))
        self._floating_selection=(tuple(window.times),tuple(window.interval))
        config=self.settings()
        kwargs={'_wave_stage':'floating_reopen','document':project_to_dict(self.app.project),'mesh_data':mesh_to_dict(self.app.mesh),
            **{k:v for k,v in self.floating_kwargs(config).items() if k in ('settings','record_path')}}
        self._bind_runner(WaveCalculation(kwargs,self),'apply')
        for edit in (*self.inputs.values(),self.wave_type,self.convert,self.hydrostatic,self.follow,self.body_mode,
                *self._floating_widgets):edit.setEnabled(False)
        self._enable_window(False);self.apply_button.setEnabled(False);self.prepare_button.setEnabled(False);self.repair_button.setEnabled(False)
        self.status.setText('Validating the saved floating record (stale guards, no BEM)…')
        self.deadline.start(600000);self.runner.start()

    def floating_applied(self,result):
        """The worker revalidated the record: apply it as ONE undoable project load (nothing is applied if anything changed)."""
        from ...application.floating_apply import ApplyFloatingLoad
        from anymesher.serialize import mesh_to_dict
        if (self.runner is None or self.runner.cancelled or self.app.session is not self.input_session
                or self.app.project is not self.input_project or self.app.session.read_only or self.app.mesh is None
                or canonical_hash(project_to_dict(self.app.project))!=self.input_document_hash
                or canonical_hash(mesh_to_dict(self.app.mesh))!=self.input_mesh_hash):
            raise ValueError('Inputs, project or mesh changed during validation; nothing applied')
        if result['array_sha256']!=self.prepared['array_sha256']:
            raise ValueError('The saved floating record changed during validation; prepare again (nothing applied)')
        times,window=self._floating_selection
        name=self.inputs['name'].text()
        parent=self.app.run(ApplyFloatingLoad(dict(name=name,record_path=result['record_path'],array_sha256=result['array_sha256'],
            selected_times=list(times),window=list(window),input_document_hash=self.input_document_hash,
            replace=self.convert.isChecked())))
        self.panel.case.setText(parent.name)
        self.app.set_status(f'Created one floating linear-response load series with {len(parent.times)} steps')
        self.accept()

    def prepared_result(self,result):
        try:
            if result.get('kind')=='floating_linear_response_v1':return self.floating_result(result)
            self._check_current(result)
            self.prepared=result
            if result.get('preview_kind')=='force_only_v1':self._retained_force_preview=result['force_preview']
            self._prepared_settings=dict(self.input_settings)
            interval=self._restored_interval or (result['preview_times'][0],result['preview_times'][-1])
            include=self.input_settings.get('include_hydrostatic') is True
            following=result.get('preview_kind')=='wave_surface_v1'
            self.plot.set_composition(include,follow=following)
            self.plot.set_data(result['preview_times'],result['preview_resultants'],interval)
            self._enable_window(True);self.set_window(*interval)
            self.record_info.setText(f'Full generated record: {result["preview_times"][0]:.9g}–{result["preview_times"][-1]:.9g} s · '
                f'{len(result["preview_times"])} original samples · '
                +('applied hull resultant of wave-following wet/dry frame loads (hydrostatic included; overtopping/deck water '
                    'not loaded); moments about the hydro origin x=0, y=0 on the mean waterline (project z=1.5 m)'
                    if following else 'dynamic + steady hydrostatic buoyancy' if include else 'dynamic-only'))
            self.status.setText('Preview ready. Choose an interval, then Apply. The project is unchanged.')
        except BaseException as error:self.failure(str(error))

    def apply_result(self):
        if self._active_stage=='apply' or (self.runner is not None and self.runner.isRunning()):return
        try:
            if self.prepared is None:raise ValueError('Prepare a load preview before applying')
            if self.prepared.get('kind')=='floating_linear_response_v1':
                self.floating_apply();return
            self._check_current(self.prepared)
            result=select_prepared_wave_window(self.prepared,float(self.window_start.text()),float(self.window_end.text()))
            if result.get('preview_kind')=='force_only_v1':
                self._check_current(result,verify_source_file=True)
                from anymesher.serialize import mesh_to_dict
                kwargs=dict(_wave_stage='apply',document=project_to_dict(self.app.project),mesh_data=mesh_to_dict(self.app.mesh),
                    prepared=result,start=result['selected_window'][0],end=result['selected_window'][1])
                if result['force_preview'].get('source_history_id') is not None:
                    if self._editing_history is None:raise ValueError('retained offline source unavailable for Apply')
                    kwargs['offline_history']=self._editing_history.to_dict()
                self._apply_window=tuple(result['selected_window']);self._apply_preview_hash=result['preview_content_hash']
                self._bind_runner(WaveCalculation(kwargs,self),'apply')
                for edit in (*self.inputs.values(),self.wave_type,self.convert,self.hydrostatic,self.follow):edit.setEnabled(False)
                self._enable_window(False);self.apply_button.setEnabled(False);self.prepare_button.setEnabled(False);self.repair_button.setEnabled(False)
                self.status.setText('Recovering source pressures and mapping selected interval; no new BEM solve…')
                self.deadline.start(180000);self.runner.start();return
            self.recovered_result(result)
        except BaseException as error:self.failure(str(error))

    def recovered_result(self,result):
        try:
            if result.get('kind')=='floating_linear_response_v1':return self.floating_applied(result)
            self._check_current(result,verify_source_file=True)
            if result.get('preview_kind')=='force_only_v1':
                from anyloads.wave_preview import select_sample_window
                current=select_sample_window(result['preview_times'],float(self.window_start.text()),float(self.window_end.text())).interval
                if (tuple(result.get('selected_window',()))!=self._apply_window or current!=self._apply_window or
                        result.get('preview_content_hash')!=self._apply_preview_hash):
                    raise ValueError('selected interval or prepared source changed during pressure recovery; nothing applied')
            parent=self.app.run(ApplyWavePressure(result,replace_legacy=self.convert.isChecked()))
            self.panel.case.setText(parent.name)
            solve=self.app.panels.get('Solve')
            if solve is not None:
                solve.analysis.setCurrentText('Batch linear static');solve.refresh();solve.case.setCurrentText(parent.name)
            self.app.set_status(f'Created one wave pressure time series with {len(parent.times)} steps; use Batch linear static')
            self.accept()
        except BaseException as error:self.failure(str(error))

    def failure(self,message):
        if self.runner is not None and not self.runner.isRunning():
            self.deadline.stop();self._job_generation+=1;self._active_stage=None
            for edit in (*self.inputs.values(),self.wave_type,self.convert,self.hydrostatic,self.follow,self.body_mode,
                    *self._floating_widgets):edit.setEnabled(True)
            self.update_mode()
        self.prepared=None;self._enable_window(False)
        self.hydrostatic.setEnabled(not self.follow.isChecked())     # stays locked on while following the surface
        self.status.setText(message);self.apply_button.setEnabled(False);self.prepare_button.setEnabled(self._settings_problem is None);self.repair_button.setEnabled(self._settings_problem is None)
    def finished_run(self,runner=None,generation=None):
        if runner is not None and (self.runner is not runner or generation!=self._job_generation):return
        self.deadline.stop()
        for edit in (*self.inputs.values(),self.wave_type,self.convert,self.hydrostatic,self.follow,self.body_mode,
                *self._floating_widgets):edit.setEnabled(True)
        self.update_mode()
        if self.runner.cancelled:self.status.setText('Calculation cancelled; project unchanged')
        if self._active_stage=='preview' and self.prepared is not None and not self.runner.cancelled:self._enable_window(True)
        self._active_stage=None
        self.prepare_button.setEnabled(self._settings_problem is None);self.repair_button.setEnabled(self._settings_problem is None)
    def timeout(self):
        if self.runner:self.runner.cancel()
        self.status.setText('Calculation exceeded its time limit ('+('60 min' if self.floating_selected() else '180 s')+') and was cancelled; project unchanged')
    def reject(self):
        if self.runner and self.runner.isRunning():self.runner.cancel()
        self._job_generation+=1
        super().reject()
