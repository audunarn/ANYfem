"""External prescribed-pressure input; optional Orcina runtime stays in a worker."""
import json
import numpy as np
from PySide6.QtCore import QThread,Signal,QTimer,Qt,QPointF
from PySide6.QtGui import QPainter,QColor,QPolygonF,QPen
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,
    QPushButton,QLabel,QFileDialog,QListWidget,QListWidgetItem,QAbstractItemView,QPlainTextEdit,QWidget,QSlider,QScrollArea)
from ...application.orcawave_pressure import inspect_orcawave_file,prepare_orcawave_job,ApplyPrescribedPressure
from ...io.project_file import project_to_dict


class PressureMapPreview(QWidget):
    """Orthographic source/target overlay; values are inward pressure in Pa."""
    def __init__(self,parent=None):
        super().__init__(parent);self.setMinimumHeight(180);self.source=[];self.polygons=[];self.values=[];self.axes=(0,1)
    def set_data(self,source,polygons,values,axes):
        self.source=source;self.polygons=polygons;self.values=values;self.axes=axes;self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#f6f7f9'))
        if not self.polygons:return
        points=np.concatenate([np.asarray(self.source).reshape(-1,3),*[np.asarray(p) for p in self.polygons]])[:,self.axes]
        lo=points.min(axis=0);span=np.maximum(points.max(axis=0)-lo,1e-6)
        scale=min((self.width()-30)/span[0],(self.height()-30)/span[1])
        def pt(p):
            v=(np.asarray(p)[list(self.axes)]-lo)*scale
            return QPointF(float(v[0]+15),float(self.height()-15-v[1]))
        maximum=max(max(abs(float(v)) for v in self.values),1e-30)
        for poly,value in zip(self.polygons,self.values):
            strength=min(1.,abs(float(value))/maximum);shade=int(245-160*strength)
            painter.setBrush(QColor(235,shade,shade) if value>=0 else QColor(shade,shade,235))
            painter.setPen(QPen(QColor('#303d4b'),1));painter.drawPolygon(QPolygonF([pt(p) for p in poly]))
        painter.setPen(QPen(QColor('#1c805c'),3))
        for p in self.source:painter.drawPoint(pt(p))


class _Job(QThread):
    completed=Signal(object)
    failed=Signal(str)
    def __init__(self,function,kwargs,parent):
        super().__init__(parent);self.function=function;self.kwargs=kwargs;self.tree=None;self.cancelled=False
        from anyfesolver import CancellationToken
        self.token=CancellationToken()
    def run(self):
        from ...job_process import run_in_worker_process
        try:
            result=run_in_worker_process(function=self.function,kwargs=self.kwargs,snapshot=None,project_override={},
                progress=lambda value:None,token=self.token,containment=True,on_tree=lambda p,t:setattr(self,'tree',t))
            if not self.cancelled:self.completed.emit(result)
        except BaseException as e:
            if not self.cancelled:self.failed.emit(str(e))
    def cancel(self):
        self.cancelled=True
        self.token.cancel('OrcaWave preparation cancelled')
        if self.tree is not None:self.tree.terminate()


class OrcaWavePressureDialog(QDialog):
    def __init__(self,panel):
        super().__init__(panel);self.panel=panel;self.app=panel.app;self.source=None;self.prepared=None;self.job=None;self._token=None
        self.setWindowTitle('Saved prescribed pressure');self.resize(780,800)
        layout=QVBoxLayout(self);inputs=QWidget();form=QFormLayout(inputs)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(inputs);layout.addWidget(scroll,2)
        self.path=QLineEdit();form.addRow('Completed .owr or portable pressure bank',self.path)
        browse=QPushButton('Read results…');form.addRow(browse);browse.clicked.connect(self.read_source)
        self.body=QComboBox();self.heading=QComboBox();self.composition=QComboBox();self.frequency=QComboBox()
        for label,w in [('Body',self.body),('Heading (degrees)',self.heading),('Pressure composition',self.composition),('Regular frequency (rad/s)',self.frequency)]:form.addRow(label,w)
        self.mode=QComboBox();self.mode.addItems(['regular','irregular']);form.addRow('Wave',self.mode)
        self.inputs={}
        for key,label,default in [('name','Series name','OrcaWave pressure'),('height','Wave height / significant height (m)','1'),
            ('phase','Regular phase (radians)','0'),('peak_period','Peak period (s)','6'),('gamma','JONSWAP gamma','3.3'),
            ('seed','Seed','0'),('duration','Duration (s)','10'),('dt','Time step (s)','0.1'),
            ('translation','Source origin in project (x,y,z m)','0,0,0'),('rotation','Source roll, pitch, yaw (degrees)','0,0,0'),
            ('gap_m','Maximum projection gap (m)',''),('angle','Maximum normal angle (degrees)',''),
            ('coverage','Uncovered area fraction limit','0.000001'),
            ('convergence','Unresolved panel assignment area fraction limit','0.00001'),
            ('max_depth','Maximum projection refinement depth','10'),('max_triangles','Maximum projection triangles','100000'),
            ('deadline','Preparation time limit (s)','600'),
            ('force_relative_limit','Relative force-error limit',''),('moment_relative_limit','Relative moment-error limit',''),
            ('force_absolute_limit','Absolute force-error limit (N/m)','0'),('moment_absolute_limit','Absolute moment-error limit (N m/m)','0')]:
            edit=QLineEdit(default);self.inputs[key]=edit;form.addRow(label,edit)
        self.surfaces=QListWidget();self.surfaces.setSelectionMode(QAbstractItemView.MultiSelection)
        for s in self.app.project.wet_surfaces:
            item=QListWidgetItem(s.name);item.setData(256,s.id);self.surfaces.addItem(item)
        form.addRow('Target wet surfaces',self.surfaces)
        self.dipole_surfaces=QListWidget();self.dipole_surfaces.setSelectionMode(QAbstractItemView.MultiSelection)
        for s in self.app.project.wet_surfaces:
            item=QListWidgetItem(s.name);item.setData(256,s.id);self.dipole_surfaces.addItem(item)
        form.addRow('Dipole midsurfaces (subset of targets)',self.dipole_surfaces)
        self.status=QLabel('Read completed results, select wet surfaces, and declare mapping limits.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);layout.addWidget(self.preview)
        self.map_view=PressureMapPreview();layout.addWidget(self.map_view)
        self.plane=QComboBox();self.plane.addItems(['XY','XZ','YZ']);layout.addWidget(self.plane)
        self.time_slider=QSlider(Qt.Horizontal);layout.addWidget(self.time_slider)
        self.snapshot_label=QLabel('Green points: source panels. Target colours: inward pressure (red positive, blue negative).');layout.addWidget(self.snapshot_label)
        self.plane.currentIndexChanged.connect(self.update_snapshot);self.time_slider.valueChanged.connect(self.update_snapshot)
        prepare=QPushButton('Prepare and inspect');prepare.clicked.connect(self.prepare);layout.addWidget(prepare)
        self.apply_button=QPushButton('Apply pressure history');self.apply_button.setEnabled(False);self.apply_button.clicked.connect(self.apply);layout.addWidget(self.apply_button)
        cancel=QPushButton('Cancel / close');cancel.clicked.connect(self.reject);layout.addWidget(cancel)
        self.deadline=QTimer(self);self.deadline.setSingleShot(True);self.deadline.timeout.connect(self.cancel_job)
        for edit in self.inputs.values():edit.textChanged.connect(self.invalidate)
        for combo in (self.body,self.heading,self.composition,self.frequency,self.mode):combo.currentIndexChanged.connect(self.invalidate)
        self.path.textChanged.connect(self.invalidate_source);self.surfaces.itemSelectionChanged.connect(self.invalidate)
        self.dipole_surfaces.itemSelectionChanged.connect(self.invalidate)

    def invalidate(self,*_):
        # Retire the job in flight: cancel it, stop its timer; none of its callbacks is accepted afterwards.
        if self.job is not None and self.job.isRunning():self.job.cancel()
        self._token=None;self.deadline.stop()
        self.prepared=None;self.apply_button.setEnabled(False)
    def invalidate_source(self,*_):
        self.source=None;self.invalidate()
    def cancel_job(self):
        if self.job is not None:self.job.cancel()
        self.invalidate();self.status.setText('Pressure preparation cancelled or timed out.')
    def launch(self,function,kwargs,callback):
        if self.job is not None and self.job.isRunning():raise ValueError('a pressure operation is already running')
        self.invalidate();self.job=_Job(function,kwargs,self);job=self.job;token=object();self._token=token
        def complete(result):
            if self._token is token and not job.cancelled:callback(result)
        def finished():
            if self._token is token:self._token=None;self.deadline.stop()
        self.job.completed.connect(complete)
        self.job.failed.connect(lambda s:self.status.setText(s) if self._token is token else None)
        seconds=float(self.inputs['deadline'].text())
        if not np.isfinite(seconds) or not 1<=seconds<=3600:raise ValueError('preparation time limit must be between 1 and 3600 seconds')
        self.job.finished.connect(finished);self.deadline.start(int(seconds*1000));self.job.start()
    def read_source(self):
        path,_=QFileDialog.getOpenFileName(self,'Read OrcaWave results',self.path.text(),'OrcaWave result or portable pressure bank (*.owr *.json);;All files (*)')
        if not path:return
        self.path.setText(path)
        try:
            self.launch(inspect_orcawave_file,{'path':path},self.loaded)
        except Exception as e:self.status.setText(str(e))
    def loaded(self,data):
        from anyfileio.orcawave import OrcaWavePressureDocument
        from anyhydro.orcawave_pressure import ImportedPressureBank,SCHEMA,MIXED_SCHEMA
        if data.get('type') in (SCHEMA,MIXED_SCHEMA):
            bank=ImportedPressureBank.from_dict(data);bank.validate_geometry()
            body_ids=(bank.body_id,);headings=(np.rad2deg(bank.heading),)
            compositions=(bank.composition,);frequencies=bank.omegas
            import json
            provenance=json.loads(bank.provenance_json)
            body_names={bank.body_id:provenance.get('selected_body_id',str(bank.body_id))}
            qualification=provenance.get('qualification_status')
        else:
            doc=OrcaWavePressureDocument.from_dict(data)
            body_ids=sorted(set(doc.body_ids));headings=doc.headings;compositions=doc.compositions
            frequencies=sorted(2*np.pi/doc.periods);body_names={};qualification=None
        self.source=data
        for combo in (self.body,self.heading,self.composition,self.frequency):combo.clear()
        for b in body_ids:
            if b>=0:self.body.addItem(body_names.get(b,str(b)),b)
        for h in headings:self.heading.addItem(str(h),float(h))
        for c in compositions:self.composition.addItem({'fixed_diffraction':'Fixed body: incident + diffraction',
            'total_with_source_motion':'Total: includes source vessel motion'}[c],c)
        for w in frequencies:self.frequency.addItem(f'{w:.12g}',float(w))
        self.status.setText('Results loaded. Select each dipole sheet midsurface once; its signed pressure jump is applied once. '
                           'Unmarked targets receive body pressure. Modes are limited to saved outputs.'
                           +(f' Source qualification: {qualification}.' if qualification else ''))
    def settings(self):
        values={k:e.text() for k,e in self.inputs.items()}
        numeric=('height','phase','peak_period','gamma','duration','dt','force_relative_limit','moment_relative_limit','force_absolute_limit','moment_absolute_limit')
        result={k:float(values[k]) for k in numeric};result.update(name=values['name'],seed=int(values['seed']))
        from scipy.spatial.transform import Rotation
        angles=[float(x) for x in values['rotation'].split(',')]
        if len(angles)!=3:raise ValueError('enter roll,pitch,yaw as three degrees values')
        t=np.eye(4);t[:3,:3]=Rotation.from_euler('xyz',angles,degrees=True).as_matrix()
        t[:3,3]=[float(x) for x in values['translation'].split(',')]
        result.update(source_to_project=t.tolist(),body_id=self.body.currentData(),heading=self.heading.currentData(),
            composition=self.composition.currentData(),omega=self.frequency.currentData(),wave_type=self.mode.currentText(),
            dipole_surfaces=[i.data(256) for i in self.dipole_surfaces.selectedItems()],
            surfaces=[i.data(256) for i in self.surfaces.selectedItems()],limits=dict(gap_m=float(values['gap_m']),
            max_angle=np.deg2rad(float(values['angle'])),coverage_tolerance=float(values['coverage']),
            convergence_tolerance=float(values['convergence']),max_depth=int(values['max_depth']),max_triangles=int(values['max_triangles'])))
        return result
    def prepare(self):
        from anymesher.serialize import mesh_to_dict
        try:
            if self.source is None or self.app.mesh is None:raise ValueError('read results and generate the structural mesh first')
            settings=self.settings();self.pending_settings=settings
            self.launch(prepare_orcawave_job,dict(document=project_to_dict(self.app.project),mesh_data=mesh_to_dict(self.app.mesh),source=self.source,settings=settings),self.ready)
        except Exception as e:self.status.setText(str(e))
    def ready(self,result):
        from anyloads.prescribed_pressure import PrescribedPressureHistory
        if self.settings()!=self.pending_settings:
            self.status.setText('Inputs changed during preparation; prepare again.');return
        h=PrescribedPressureHistory.from_dict(result['history']);b=json.loads(h.binding_json)
        mapping=b['projection'];spectrum=result['spectrum'];errors=b['frequency_evidence']
        lines=[f"Pressure mode: {self.composition.currentText()}",
               f"Dipole midsurfaces: {len(b.get('dipole_surfaces',()))}; signed jump applied once",
               f"Mapped area: {mapping['mapped_area_m2']:.6g} m²",
               f"Maximum projection gap: {mapping['max_gap_m']:.6g} m",
               f"Maximum normal angle: {np.rad2deg(mapping['max_angle_rad']):.6g}°",
               f"Unresolved panel assignment area bound: {mapping['unresolved_area_bound_m2']:.6g} m²",
               f"Maximum force transfer error: {max(e['force']['absolute'] for e in errors):.6g} N/m wave amplitude",
               f"Maximum moment transfer error: {max(e['moment']['absolute'] for e in errors):.6g} N·m/m wave amplitude"]
        provenance=json.loads(json.loads(h.source_payload_json)['provenance_json'])
        if provenance.get('provider')=='ANYhydro':
            lines.extend(['Source: ANYhydro selected-body sheets only (no hull pressure)',
                          f"Source qualification: {provenance.get('qualification_status')}"])
        for kind in sorted({kind for e in errors for kind in e.get('by_kind',{})}):
            lines.append(f"{kind} maximum force/moment error: "
                f"{max(e['by_kind'][kind]['force']['absolute'] for e in errors):.6g} N/m, "
                f"{max(e['by_kind'][kind]['moment']['absolute'] for e in errors):.6g} N·m/m")
        if spectrum['mode']!='regular':lines.extend([
            f"Spectrum energy retained: {100*spectrum['retained_fraction']:.4g}%",
            f"Omitted energy: {100*spectrum['omitted_fraction']:.4g}%",
            f"Achieved significant height: {spectrum['achieved_significant_height']:.6g} m"])
        self.preview.setPlainText('\n'.join(lines))
        self.prepared=result;self.apply_button.setEnabled(True);self.status.setText('Pressure history prepared. Inspect mapping and spectrum diagnostics before applying.')
        self.time_slider.setRange(0,len(h.times)-1);self.update_snapshot()
    def update_snapshot(self,*_):
        if self.prepared is None:return
        from anyloads.prescribed_pressure import PrescribedPressureHistory
        h=PrescribedPressureHistory.from_dict(self.prepared['history']);t=h.times[self.time_slider.value()]
        values=h.preview_pressures(t)
        self.map_view.set_data(self.prepared['source_centers'],self.prepared['target_polygons'],values,[(0,1),(0,2),(1,2)][self.plane.currentIndex()])
        label='body pressure / signed sheet jump (target reference)' if json.loads(h.binding_json).get('dipole_surfaces') else 'inward pressure'
        self.snapshot_label.setText(f't={t:g} s · {label} {values.min():.6g} to {values.max():.6g} Pa · green dots: source panels')
    def apply(self):
        from anyloads.prescribed_pressure import PrescribedPressureHistory
        try:
            r=self.prepared
            if r is None:raise ValueError('prepare the current inputs first')
            case=self.app.run(ApplyPrescribedPressure(PrescribedPressureHistory.from_dict(r['history']),r['name'],r['input_document_hash']))
            self.panel.case.setText(case.name);self.app.set_status('Applied OrcaWave pressure-only series');self.accept()
        except Exception as e:self.status.setText(str(e))
    def reject(self):
        self.cancel_job()
        if self.job is not None:self.job.wait(6000)
        super().reject()
