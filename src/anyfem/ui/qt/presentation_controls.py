"""Connected presentation controls and independent retained comparison pane."""
from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from queue import SimpleQueue, Empty
import os
import tempfile
import numpy as np
from PySide6.QtCore import Qt,QTimer,QSignalBlocker
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QFormLayout,QHBoxLayout,QPushButton,
    QLineEdit,QComboBox,QCheckBox,QSpinBox,QLabel,QSplitter,QInputDialog,QProgressBar,QSizePolicy)
from ...presentation.workspace import (store_presentation,camera_record,apply_camera,
    section_box_values,stable_sample_ids,annotation_stale)
class PresentationControls(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app;self.reference=None;self._reference_access=None
        self._reference_source=None;self._reference_generation=0;self._reference_closed=False
        self._reference_queue=SimpleQueue();self._reference_requests=None
        self._link_signature=None;self._reloading=False
        from .widgets import Collapsible
        root_layout=QVBoxLayout(self);root_layout.setContentsMargins(0,0,0,0);root_layout.setAlignment(Qt.AlignTop)
        def group(title):
            section=Collapsible(title);section.setSizePolicy(QSizePolicy.Preferred,QSizePolicy.Maximum);root_layout.addWidget(section)
            form=QFormLayout();form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            section.addLayout(form)
            return section.body_layout,form
        layout,form=group('Contours and thresholds')
        def edit(label,text,*,target=None):
            widget=QLineEdit(text);(form if target is None else target).addRow(label,widget);return widget
        def combo(label,choices,*,target=None):
            widget=QComboBox();widget.addItems(choices);(form if target is None else target).addRow(label,widget);return widget
        def check(text,checked=False,*,target=None):
            widget=QCheckBox(text);widget.setChecked(checked);(form if target is None else target).addRow(widget);return widget
        def button(text,callback,*,target=None):
            widget=QPushButton(text);widget.clicked.connect(app.guarded(callback));(layout if target is None else target).addWidget(widget);return widget
        # Saved views share one controller; clipping widgets have their own workspace.
        from .widgets import section_label
        self.sectioning_controls=QWidget()
        section_layout=QVBoxLayout(self.sectioning_controls)
        note=QLabel('Cut away part of the display to inspect inside the model. The engineering geometry is unchanged.')
        note.setWordWrap(True);section_layout.addWidget(note)
        section_layout.addWidget(section_label('Section plane'))
        plane_form=QFormLayout();plane_form.setRowWrapPolicy(QFormLayout.WrapLongRows);section_layout.addLayout(plane_form)
        self.plane=edit('Plane normal','1, 0, 0',target=plane_form)
        self.offset=edit('Plane offset (m)','0',target=plane_form)
        button('Enable section plane',self.apply_plane,target=section_layout)
        button('Clear section plane',self.clear_plane,target=section_layout)
        section_layout.addWidget(section_label('Section box'))
        box_form=QFormLayout();box_form.setRowWrapPolicy(QFormLayout.WrapLongRows);section_layout.addLayout(box_form)
        self.center=edit('Box center (m)','0, 0, 0',target=box_form);self.extents=edit('Box half-extents (m)','1, 1, 1',target=box_form)
        self.orientation=edit('Box quaternion xyzw','0, 0, 0, 1',target=box_form)
        self.box_enabled=check('Enable section box',target=box_form)
        self.box_enabled.toggled.connect(app.guarded(self.apply_box))
        button('Apply section box',self.apply_box,target=section_layout)
        button('Fit box to model',self.fit_box,target=section_layout);button('Fit box to selection',lambda:self.fit_box(selection=True),target=section_layout)
        button('Reset box rotation',self.reset_box,target=section_layout);button('Disable box',lambda:self.box_enabled.setChecked(False),target=section_layout)
        section_layout.addWidget(section_label('Cut surfaces'))
        cap_form=QFormLayout();section_layout.addLayout(cap_form)
        self.caps=check('Closed-volume cut caps',target=cap_form)
        self.caps.setToolTip('Requires an explicitly qualified closed volume. Structural shells are not automatically filled.')
        self.hatch=check('Hatch cut caps',True,target=cap_form)
        button('Apply cut cap settings',self.apply_cut_caps,target=section_layout)
        self.link_clip=check('Link clipping between comparison panes',True,target=cap_form)
        section_layout.addStretch()
        self.contour=combo('Contours',['Continuous','Banded','Isolines'])
        self.levels=edit('Explicit contour levels','');self.zero=check('Show zero contour')
        self.threshold=edit('Threshold value','');self.threshold_direction=combo('Threshold direction',['above','below'])
        button('Apply contours / threshold',self.apply_contours)
        layout,form=group('Selection and rendering')
        self.outline=check('Selection outline',True);self.through=check('Outline through geometry')
        self.shadows=combo('Shadows',['Off','Low','High'])
        self.present=combo('Presentation method',['auto','bitmap','native'])
        button('Apply renderer controls',self.apply_renderer)
        layout,form=group('Saved views')
        self.names=QComboBox();form.addRow('Saved project views',self.names)
        button('Save named view in project',self.save_view);button('Restore named view',self.restore_view)
        button('Delete named view',self.delete_view)
        layout,form=group('Compare results')
        self.compare=check('Two side-by-side result panes');self.compare.toggled.connect(lambda enabled:app.guarded(lambda:self.toggle_reference(enabled))())
        self.link_camera=check('Link cameras',True)
        self.shared_limits=check('Share compatible scalar limits')
        self.job=QComboBox();self.field=QComboBox();self.component=QComboBox()
        form.addRow('Reference job',self.job);form.addRow('Reference field',self.field);form.addRow('Reference component',self.component)
        self.frame=QSpinBox();self.frame.setRange(1,2147483647);form.addRow('Reference frame',self.frame)
        self.scale=edit('Reference deformation multiplier','1')
        button('Refresh reference sources',self.refresh_sources);button('Show reference frame',self.show_reference)
        button('Cancel reference request',self.cancel_reference)
        self.export_controls=QWidget()
        export_layout=QVBoxLayout(self.export_controls)
        export_layout.addWidget(section_label('Images'))
        export_form=QFormLayout();export_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        export_layout.addLayout(export_form)
        self.capture_size=combo('PNG resolution',['Current','4K','Custom'],target=export_form)
        self.width=QSpinBox();self.height=QSpinBox()
        for widget,value in ((self.width,3840),(self.height,2160)):widget.setRange(16,16384);widget.setValue(value)
        export_form.addRow('Custom width',self.width);export_form.addRow('Custom height',self.height)
        button('Capture pane(s) PNG',self.capture,target=export_layout)
        self.capture_status=QLabel();self.capture_status.setWordWrap(True);export_layout.addWidget(self.capture_status)
        export_layout.addWidget(section_label('Animations'))
        interval_note=QLabel('Uses the From frame / To frame interval selected in Results.')
        interval_note.setWordWrap(True);export_layout.addWidget(interval_note)
        animation_form=QFormLayout();export_layout.addLayout(animation_form)
        self.animation_format=combo('Format',['GIF','PNG sequence ZIP','MP4'],target=animation_form)
        self.export_progress=QProgressBar();export_layout.addWidget(self.export_progress)
        self.export_status=QLabel('MP4 requires an installed, reviewed encoder; none is configured.');self.export_status.setWordWrap(True);export_layout.addWidget(self.export_status)
        from .animation_export import AnimationExport
        self.animation_export=AnimationExport(self)
        button('Choose and review installed MP4 encoder',self.configure_encoder,target=export_layout)
        button('Export selected frame interval',self.animation_export.start,target=export_layout)
        button('Cancel animation export',self.animation_export.cancel,target=export_layout)
        export_layout.addStretch()
        layout,form=group('Annotations')
        self.annotation_name=edit('Annotation group','Inspection')
        button('Persist current probe / extrema',self.save_annotation)
        button('Show saved annotations',self.show_annotations)
        self.annotations_visible=check('Show anchored annotation labels',True)
        button('Rename current annotation group',self.rename_annotation_group)
        button('Hide / show current annotation group',self.toggle_annotation_group)
        button('Delete current annotation group',self.delete_annotation_group)
        layout,form=group('Vectors and stress axes')
        self.glyph_count=QSpinBox();self.glyph_count.setRange(1,2000);self.glyph_count.setValue(2000)
        form.addRow('Maximum glyph samples',self.glyph_count)
        self.glyph_scale=edit('Glyph length multiplier','1')
        self.glyph_normalize=check('Normalize glyph lengths',True)
        self.glyph_rank=combo('Principal rank',['All','1','2','3'])
        button('Show displacement vector glyphs',self.vector_glyphs)
        button('Show principal stress axes',self.principal_glyphs)
        button('Clear glyphs',self.clear_glyphs)
        self.status=QLabel();self.status.setWordWrap(True);root_layout.addWidget(self.status)
        self.job.currentIndexChanged.connect(self.refresh_fields);self.field.currentIndexChanged.connect(self.refresh_components)
        self._timer=QTimer(self);self._timer.setInterval(50);self._timer.timeout.connect(self.poll);self._timer.start()
        self.refresh_names();self.refresh_sources()
        exporter=getattr(self.app.viewport.canvas,'export_view_state',None)
        state=exporter() if callable(exporter) else None
        self._seen_clip=None if state is None else (state.section_box,state.section_plane);self._clip_candidate=None
    @staticmethod
    def numbers(text):return [float(value.strip()) for value in text.split(',') if value.strip()]
    def apply_plane(self):
        self.app.viewport.set_section_plane(normal=self.numbers(self.plane.text()),offset=float(self.offset.text()))
        self.save_workspace()
    def clear_plane(self):
        self.app.viewport.clear_section_plane();self.save_workspace()
    def apply_cut_caps(self):
        canvas=self.app.viewport.canvas
        self.invoke(canvas,'set_cut_caps',enabled=self.caps.isChecked(),hatch=self.hatch.isChecked())
        canvas.redraw();self.save_workspace()
    def persist(self,change,label='Presentation settings'):
        store_presentation(self.app.session,change,label)
        self.app._update_window_title()
    def invoke(self,canvas,name,*args,**kwargs):
        method=getattr(canvas,name,None)
        if not callable(method):raise ValueError(f'{name} is unavailable on the active renderer')
        return method(*args,**kwargs)
    def apply_box(self):
        c,h,q=section_box_values(self.numbers(self.center.text()),self.numbers(self.extents.text()),self.numbers(self.orientation.text()))
        self.invoke(self.app.viewport.canvas,'set_section_box',c,h,orientation=q,enabled=self.box_enabled.isChecked())
        self.app.viewport.canvas.redraw()
        self.save_workspace()
    def fit_box(self,selection=False):
        scene=self.app.viewport._scene
        if scene is None:raise ValueError('Display a model before fitting the box')
        if selection:
            refs=set(self.app.selection.items)
            points=[]
            for group in (scene.faces,scene.lines,scene.points):
                for item in group:
                    owners=getattr(item,'owners',()) or (getattr(item,'ref',None),)
                    if not any(owner in refs for owner in owners):continue
                    raw=getattr(item,'polygons',getattr(item,'points',getattr(item,'position',())))
                    for vertex in np.asarray(raw,dtype=float).reshape(-1,3):points.append(vertex)
            if not points:raise ValueError('Selected entities have no visible geometry')
            low,high=np.min(points,axis=0),np.max(points,axis=0)
        else:
            bounds=scene.bounds()
            if bounds is None:raise ValueError('The displayed scene is empty')
            low,high=bounds
        self.center.setText(', '.join(map(str,(low+high)/2)))
        self.extents.setText(', '.join(map(str,np.maximum((high-low)/2,1e-9))))
        self.orientation.setText('0, 0, 0, 1')
        with QSignalBlocker(self.box_enabled):self.box_enabled.setChecked(True)
        self.apply_box()
    def reset_box(self):
        self.orientation.setText('0, 0, 0, 1');self.apply_box()
    def apply_contours(self):
        if self.contour.currentText()=='Isolines':
            scene=getattr(self.app.viewport,'_scene',None)
            field=getattr(scene,'result_field',None)
            if field is None or field.per_element:
                raise ValueError('Isolines require a continuous canonical nodal field; element-constant fields are discontinuous')
            retained=getattr(self.app.viewport,'_retained_result',None)
            if retained is None or len(retained)<5 or not retained[4] or any(ids is None for ids in retained[4]):
                raise ValueError('Isolines require canonical node connectivity on every displayed result primitive')
        threshold=None if not self.threshold.text().strip() else dict(direction=self.threshold_direction.currentText(),value=float(self.threshold.text()))
        self.invoke(self.app.viewport.canvas,'set_contours',mode=self.contour.currentText(),levels=self.numbers(self.levels.text()),zero_contour=self.zero.isChecked(),threshold=threshold)
        self.app.viewport.canvas.redraw();self.save_workspace()
    def apply_renderer(self):
        canvas=self.app.viewport.canvas
        self.invoke(canvas,'set_selection_outline',enabled=self.outline.isChecked(),through_geometry=self.through.isChecked())
        self.app.viewport._presentation_shadows=self.shadows.currentText()
        numerical=getattr(getattr(self.app.viewport,'_scene',None),'result_field',None) is not None
        self.invoke(canvas,'set_shadows',quality='Off' if numerical else self.shadows.currentText(),strength=.5)
        self.invoke(canvas,'set_present_method',self.present.currentText())
        canvas.redraw();self.save_workspace()
        diagnostics=getattr(canvas,'renderer_diagnostics',{})
        if callable(diagnostics):diagnostics=diagnostics()
        self.status.setText(str(diagnostics))
    def record(self):
        canvas=self.app.viewport.canvas;box=getattr(canvas,'section_box',None)
        plane=getattr(canvas,'section_plane',None)
        contours={}
        if callable(getattr(canvas,'set_contours',None)):
            contours=dict(mode=self.contour.currentText(),levels=self.numbers(self.levels.text()),
                zero_contour=self.zero.isChecked(),threshold=None if not self.threshold.text().strip() else
                dict(direction=self.threshold_direction.currentText(),value=float(self.threshold.text())))
        results=getattr(self.app,'panels',{}).get('Results')
        primary=None if results is None else dict(job=self.app.active_job_id,field=results.field_name(),component=results.component.text(),frame=results.frame.value(),scale=results.scale.text())
        return dict(primary=primary,camera=camera_record(canvas),
            box=None if box is None else dict(center=list(box.center.to_tuple()),half_extents=list(box.half_extents.to_tuple()),orientation=list(box.orientation),enabled=box.enabled),
            plane=None if plane is None else dict(normal=[plane.normal.x,plane.normal.y,plane.normal.z],offset=plane.offset,enabled=plane.enabled),
            appearance=asdict(self.app.viewport.visualization),contours=contours,
            renderer=dict(shadows=self.shadows.currentText(),outline=self.outline.isChecked(),through=self.through.isChecked(),caps=self.caps.isChecked(),hatch=self.hatch.isChecked(),present=self.present.currentText()),
            comparison=dict(enabled=self.compare.isChecked(),job=self.job.currentData(),field=self.field.currentText(),component=self.component.currentText(),frame=self.frame.value(),scale=self.scale.text(),camera_link=self.link_camera.isChecked(),clip_link=self.link_clip.isChecked(),shared_limits=self.shared_limits.isChecked()))
    def save_workspace(self):
        if self._reloading:return
        record=self.record();self._saved_clip=(record.get('box'),record.get('plane'))
        current=self.app.viewport.canvas.export_view_state()
        self._seen_clip=(current.section_box,current.section_plane);self._clip_candidate=None
        self.persist(lambda state:state.update(workspace=record))
    def refresh_names(self):
        self.names.clear();self.names.addItems(sorted(getattr(self.app.project,'presentation',{}).get('views',{})))
    def save_view(self):
        name,ok=QInputDialog.getText(self,'Save view','View name')
        if not ok or not name.strip():return
        record=self.record();self.persist(lambda state:state.setdefault('views',{}).__setitem__(name.strip(),record),'Save named view')
        self.refresh_names();self.names.setCurrentText(name.strip())
    def delete_view(self):
        name=self.names.currentText()
        if name:self.persist(lambda state:state.setdefault('views',{}).pop(name,None),'Delete named view')
        self.refresh_names()
    def restore_record(self,record):
        self._reloading=True
        try:
            from ...presentation.visualization import VisualizationStyle
            canvas=self.app.viewport.canvas
            apply_camera(canvas,record['camera'])
            self.app.viewport.set_visualization(VisualizationStyle(**record['appearance']))
            box=record.get('box')
            if box:
                self.invoke(canvas,'set_section_box',box['center'],box['half_extents'],orientation=box['orientation'],enabled=box['enabled'])
                for widget,key in ((self.center,'center'),(self.extents,'half_extents'),(self.orientation,'orientation')):widget.setText(', '.join(map(str,box[key])))
                with QSignalBlocker(self.box_enabled):self.box_enabled.setChecked(box['enabled'])
            else:self.invoke(canvas,'clear_section_box')
            plane=record.get('plane')
            if plane:self.app.viewport.set_section_plane(normal=plane['normal'],offset=plane['offset'],enabled=plane['enabled'])
            else:self.app.viewport.clear_section_plane()
            contours=record.get('contours',{})
            if contours:
                self.invoke(canvas,'set_contours',**contours)
                self.contour.setCurrentText(contours.get('mode','Continuous'))
                self.levels.setText(', '.join(map(str,contours.get('levels',[]))))
                self.zero.setChecked(contours.get('zero_contour',False))
                threshold=contours.get('threshold')
                self.threshold.setText('' if threshold is None else str(threshold['value']))
                if threshold:self.threshold_direction.setCurrentText(threshold['direction'])
            renderer=record.get('renderer',{})
            if renderer:
                self.shadows.setCurrentText(renderer['shadows']);self.outline.setChecked(renderer['outline'])
                self.through.setChecked(renderer['through']);self.caps.setChecked(renderer['caps']);self.hatch.setChecked(renderer['hatch']);self.present.setCurrentText(renderer['present'])
                if callable(getattr(canvas,'set_shadows',None)):self.apply_renderer()
                if callable(getattr(canvas,'set_cut_caps',None)):self.apply_cut_caps()
            primary=record.get('primary')
            if primary and primary.get('job'):
                panel=self.app.panels['Results']
                if primary['job'] in self.app.result_datasets or primary['job'] in self.app.solutions:
                    panel.activate_job(primary['job'])
                    blockers=[QSignalBlocker(control) for control in (panel.field,panel.component,panel.frame,panel.scale)]
                    panel.field.setCurrentText(primary['field']);panel.update_component_choices();panel.component.setText(primary['component'])
                    panel._set_frame_index(primary['frame']);panel.scale.setText(primary['scale']);del blockers
                    panel.show_results()
                else:self.status.setText(f"Saved primary result unavailable / stale: {primary['job']}")
            comparison=record.get('comparison',{})
            if comparison:
                self.link_camera.setChecked(comparison['camera_link']);self.link_clip.setChecked(comparison['clip_link']);self.shared_limits.setChecked(comparison['shared_limits'])
                index=self.job.findData(comparison['job'])
                if index>=0:self.job.setCurrentIndex(index)
                elif comparison['job']:
                    self.job.addItem(f"Unavailable / stale: {comparison['job']}",comparison['job']);self.job.setCurrentIndex(self.job.count()-1)
                    self.status.setText('Saved reference artifact is unavailable / stale')
                    self.field.addItem(comparison['field'])
                self.field.setCurrentText(comparison['field']);self.component.setCurrentText(comparison['component'])
                self.frame.setValue(comparison['frame']);self.scale.setText(comparison['scale']);self.compare.setChecked(comparison['enabled'])
                if comparison['enabled'] and comparison['job'] in self.app.result_datasets:self.show_reference()
            canvas.redraw()
        finally:self._reloading=False
    def restore_view(self):
        name=self.names.currentText()
        if name:self.restore_record(self.app.project.presentation['views'][name])
    def refresh_sources(self):
        old=self.job.currentData();self.job.clear()
        for key,dataset in getattr(self.app,'result_datasets',{}).items():
            job=self.app.project.jobs.get(key);self.job.addItem(getattr(job,'name',key),key)
        index=self.job.findData(old)
        if index>=0:self.job.setCurrentIndex(index)
        self.refresh_fields()
    def refresh_fields(self,*_):
        self.cancel_reference();self.field.clear()
        dataset=getattr(self.app,'result_datasets',{}).get(self.job.currentData())
        if dataset is not None:self.field.addItems(list(dataset.field_keys))
        self.refresh_components()
    def refresh_components(self,*_):
        self.component.clear();self.component.addItem('')
        dataset=getattr(self.app,'result_datasets',{}).get(self.job.currentData())
        if dataset is not None and self.field.currentText():
            self.component.addItems(list(dataset.field(self.field.currentText()).descriptor.components))
    def toggle_reference(self,enabled):
        if enabled and self.reference is None:
            from .viewport import QtViewport
            from ...selection import Selection
            self.reference=QtViewport(self.app,Selection(),backend=self.app._viewer_backend)
            # Reparent both widgets into a single project-owned split workspace.
            self.app.takeCentralWidget()
            self.splitter=QSplitter(Qt.Horizontal,self.app)
            self.splitter.setChildrenCollapsible(False)
            self._primary_minimum_width=self.app.viewport.widget.minimumWidth()
            self.splitter.addWidget(self.app.viewport.widget);self.splitter.addWidget(self.reference.widget)
            self.reference.set_visualization(self.app.viewport.visualization)
            self.app.setCentralWidget(self.splitter)
        if self.reference is not None:
            self.reference.widget.setVisible(enabled)
            # Reserve real canvas space even when dock panels have wide forms.
            # Without this, QMainWindow can allocate the splitter only its handle width.
            self.app.viewport.widget.setMinimumWidth(160 if enabled else self._primary_minimum_width)
            self.reference.widget.setMinimumWidth(160)
            self.splitter.setMinimumWidth(320+self.splitter.handleWidth() if enabled else self._primary_minimum_width)
            if enabled:self.splitter.setSizes([max(160,self.splitter.width()//2)]*2)
        action=getattr(self.app,'split_screen_action',None)
        if action is not None:action.setChecked(enabled)
        if not enabled:
            self.cancel_reference();self.release_reference()
            if self.reference is not None:self.reference.clear()
    def cancel_reference(self):
        self._reference_generation+=1
        if self._reference_requests is not None:self._reference_requests.cancel_channel('reference:display')
    def release_reference(self):
        if self._reference_access is not None:self._reference_access.release_pane_publication('reference')
        self._reference_access=None;self._reference_source=None
    def show_reference(self):
        if not self.compare.isChecked():self.compare.setChecked(True)
        key=self.job.currentData();dataset=self.app.result_datasets.get(key)
        if dataset is None:raise ValueError('Select an available retained reference job')
        if not getattr(dataset,'metadata_ready',True):
            # Readonly preparation is owned by the public workflow, without changing active job.
            if self.app.prepare_result_dataset(dataset,on_ready=self.show_reference):return
        self.cancel_reference();generation=self._reference_generation
        owner=self.app.project;field=self.field.currentText();component=self.component.currentText() or None
        frame=self.frame.value()-1;scale=float(self.scale.text());mesh=self.app.retained_result_mesh(key)
        options=dict(frame=frame,component=component,scale=scale,show_beam_sections=False)
        if self.shared_limits.isChecked():
            main_dataset=self.app.result_datasets.get(self.app.active_job_id)
            main_name=self.app.panels['Results'].field_name()
            descriptor=dataset.field(field).descriptor
            main_descriptor=None if main_dataset is None else main_dataset.field(main_name).descriptor
            if main_descriptor is None or self.quantity_family(main_descriptor)!=self.quantity_family(descriptor):
                raise ValueError('Shared limits require the same canonical quantity family and units; components may differ')
        from ...ui.scene import build_persisted_result_scene
        def load(cancel):
            if cancel.is_set():return None
            return build_persisted_result_scene(owner,mesh,dataset,field,**options)
        def complete(result):
            if result.cancelled or generation!=self._reference_generation or owner is not self.app.project:return
            if result.error is not None:self.app.set_status(str(result.error),error=True);return
            if self.app.result_datasets.get(key) is not dataset:return
            scene=result.value
            access=getattr(self.app,'_live_result_access',None)
            if access is None:
                self.app.set_status('Reference display requires the shared result memory coordinator',error=True);return
            from ...presentation.retained_results import prepared_publication_bytes,scalar_buffer_bytes,prepare
            prepared=prepare(self.reference,scene) if self.reference.active_backend=='gpu' else None
            scalar_bytes=0 if prepared is None else sum(scalar_buffer_bytes(handle) for handle,options in prepared[1])
            try:
                reservation=access.reserve_publication(scene.result_field,prepared_publication_bytes(scene.result_field,scene,scalar_bytes))
                if prepared is not None:scene._prepared_result_batches=prepared
                self.reference.show(scene)
                self.release_reference();access.commit_pane_publication('reference',reservation)
                self._reference_access=access;self._reference_source=(key,id(dataset))
                self._reference_quantity=(field,dataset.field(field).descriptor.unit)
                self._shared_signature=None
                if self.shared_limits.isChecked():self.apply_shared_limits()
                self.status.setText(f'Reference: {key} / {field} / {component or "default"} / frame {frame}')
            except Exception as error:
                access.discard_publication();self.app.set_status(str(error),error=True)
        def begin(_):
            if owner is not self.app.project or generation!=self._reference_generation:return
            self._reference_requests=self.app._result_loader
            self._reference_requests.request(load,complete,channel='reference:display',independent=True)
        # Use the production serial worker and shared retirement fence. The
        # independent channel survives main-pane selection invalidation.
        if getattr(self.app,'_result_loader',None) is not None:begin(None)
        else:self.app._request_result(lambda cancel:None,begin,channel='reference:bootstrap')
    def poll(self):
        if self._reference_access is not None:self._reference_access=self._reference_access.publication_owner()
        while True:
            try:self._reference_queue.get_nowait()()
            except Empty:break
        if self._reference_source:
            key,identity=self._reference_source
            if id(self.app.result_datasets.get(key))!=identity:
                self.cancel_reference();self.release_reference()
                if self.reference is not None:self.reference.clear()
        if hasattr(self.app,'project'):
            try:self.observe_clipping();self.update_annotations();self.maintain_glyphs()
            except Exception as error:self.status.setText(f'Annotation display unavailable: {error}')
        if self.reference is None or not self.compare.isChecked():return
        try:
            main=self.app.viewport.canvas;other=self.reference.canvas
            if self.link_camera.isChecked():
                primary=camera_record(main);reference=camera_record(other)
                previous=self._link_signature
                if previous is not None and primary==previous and reference!=previous:
                    apply_camera(main,reference);self._link_signature=reference
                elif primary!=previous or reference!=primary:
                    apply_camera(other,primary);self._link_signature=primary
            if self.link_clip.isChecked():
                state=main.export_view_state();other_state=other.export_view_state()
                primary_clip=(state.section_box,state.section_plane);reference_clip=(other_state.section_box,other_state.section_plane)
                previous=getattr(self,'_clip_link_signature',None)
                if previous is not None and primary_clip==previous and reference_clip!=previous:
                    source,target=other_state,self.app.viewport;chosen=reference_clip
                else:source,target=state,self.reference;chosen=primary_clip
                target_state=target.canvas.export_view_state()
                if source.section_box!=target_state.section_box:
                    box=source.section_box
                    if box is None:self.invoke(target.canvas,'clear_section_box')
                    else:self.invoke(target.canvas,'set_section_box',box.center,box.half_extents,orientation=box.orientation,enabled=box.enabled)
                if source.section_plane!=target_state.section_plane:
                    plane=source.section_plane
                    if plane is None:target.clear_section_plane()
                    else:target.set_section_plane(normal=(plane.normal.x,plane.normal.y,plane.normal.z),offset=plane.offset,enabled=plane.enabled)
                self._clip_link_signature=chosen
            if self.shared_limits.isChecked():self.apply_shared_limits()
        except Exception as error:
            self.status.setText(f'View linking unavailable: {error}')
    def observe_clipping(self):
        import time
        current=self.app.viewport.canvas.export_view_state();clip=(current.section_box,current.section_plane)
        if clip==getattr(self,'_seen_clip',None):return
        candidate=getattr(self,'_clip_candidate',None)
        if candidate is None or clip!=candidate[0]:
            self._clip_candidate=(clip,time.monotonic());return
        if time.monotonic()-candidate[1]<.25:return
        box=current.section_box
        if box is not None:
            self.center.setText(', '.join(map(str,box.center.to_tuple())))
            self.extents.setText(', '.join(map(str,box.half_extents.to_tuple())))
            self.orientation.setText(', '.join(map(str,box.orientation)))
            with QSignalBlocker(self.box_enabled):self.box_enabled.setChecked(box.enabled)
        else:
            with QSignalBlocker(self.box_enabled):self.box_enabled.setChecked(False)
        if hasattr(getattr(self.app,'session',None),'transaction'):self.save_workspace()
        self._seen_clip=clip;self._clip_candidate=None

    @staticmethod
    def quantity_family(descriptor):
        provenance=descriptor.provenance
        family=provenance.get('quantity_family',provenance.get('source_quantity_id',descriptor.key))
        return (str(family),descriptor.unit,descriptor.location,descriptor.basis)

    def apply_shared_limits(self):
        main=getattr(self.app.viewport,'_scene',None);reference=getattr(self.reference,'_scene',None)
        first=getattr(main,'result_field',None);second=getattr(reference,'result_field',None)
        if first is None or second is None:return
        main_dataset=self.app.result_datasets.get(self.app.active_job_id)
        reference_key=None if self._reference_source is None else self._reference_source[0]
        reference_dataset=self.app.result_datasets.get(reference_key)
        if main_dataset is None or reference_dataset is None:return
        main_descriptor=main_dataset.field(first.name).descriptor;reference_descriptor=reference_dataset.field(second.name).descriptor
        if self.quantity_family(main_descriptor)!=self.quantity_family(reference_descriptor):
            raise ValueError('Displayed scalar quantities have incompatible families or units')
        signature=(id(first),id(second),self.app.panels['Results'].units.currentText())
        if getattr(self,'_shared_signature',None)==signature:return
        values=[float(value) for field in (first,second) for value in field.values.values() if np.isfinite(value)]
        if not values:raise ValueError('No finite scalar values are available for shared limits')
        low,high=min(values),max(values)
        if high<=low:high=low+1
        from ...presentation.result_display import unit_transform
        panel=self.app.panels['Results'];factor,_=unit_transform(first.unit,panel.display_units())
        self.app.viewport.update_result_appearance(limits=(low*factor,high*factor),display_units=panel.display_units(),colormap=panel.colormap())
        self.reference.update_result_appearance(limits=(low,high),display_units='SI (m / Pa)')
        self._shared_signature=signature
    def synchronize_export_view(self):
        """Settle link/clip bookkeeping before freezing an export view."""
        self.pause_glyphs()
        self.poll()
        state=self.app.viewport.canvas.export_view_state()
        if (state.section_box,state.section_plane)!=getattr(self,'_seen_clip',None):self.save_workspace()
        self._seen_clip=(state.section_box,state.section_plane);self._clip_candidate=None
        if self.shared_limits.isChecked():self.apply_shared_limits()

    def export_signature(self):
        """Only actual capture policy; observer bookkeeping is excluded."""
        import json
        def view(canvas):
            state=canvas.export_view_state()
            box=state.section_box;plane=state.section_plane
            return dict(camera=camera_record(canvas),
                box=None if box is None else (box.center.to_tuple(),box.half_extents.to_tuple(),box.orientation,box.enabled),
                plane=None if plane is None else (plane.normal.to_tuple(),plane.offset,plane.enabled))
        reference=None
        if self.compare.isChecked() and self.reference is not None:
            key=self.job.currentData();dataset=self.app.result_datasets.get(key)
            reference=dict(job=key,source=id(dataset),field=self.field.currentText(),component=self.component.currentText(),
                frame=self.frame.value(),scale=self.scale.text(),view=view(self.reference.canvas))
        signature=dict(annotations=self.app.project.presentation.get('annotations',[]),glyph_revision=getattr(self,'_glyph_revision',0),primary=view(self.app.viewport.canvas),reference=reference,comparison=self.compare.isChecked(),
            camera_link=self.link_camera.isChecked(),clip_link=self.link_clip.isChecked(),shared=self.shared_limits.isChecked(),
            resolution=(self.capture_size.currentText(),self.width.value(),self.height.value()),
            appearance=asdict(self.app.viewport.visualization),
            contours=(self.contour.currentText(),self.levels.text(),self.zero.isChecked(),self.threshold.text(),self.threshold_direction.currentText()),
            renderer=(self.shadows.currentText(),self.outline.isChecked(),self.through.isChecked(),self.caps.isChecked(),self.hatch.isChecked(),self.present.currentText()))
        return json.dumps(signature,sort_keys=True,allow_nan=False)

    def capture(self):
        path=self.app.dialogs.save_file(filetypes=[('PNG image','*.png')],defaultextension='.png')
        if not path:return
        result=self.capture_composite()
        destination=Path(path);destination.parent.mkdir(parents=True,exist_ok=True)
        fd,temp=tempfile.mkstemp(prefix='.'+destination.name,suffix='.png',dir=destination.parent);os.close(fd)
        try:result.save(temp,format='PNG');os.replace(temp,destination)
        finally:Path(temp).unlink(missing_ok=True)
        self.capture_status.setText(f'Saved {result.width} × {result.height} PNG')
    def capture_composite(self):
        from PIL import Image
        panes=[self.app.viewport]
        if self.compare.isChecked() and self.reference is not None:panes.append(self.reference)
        size=self.capture_size.currentText();target=None if size=='Current' else (3840,2160) if size=='4K' else (self.width.value(),self.height.value())
        images=[]
        for pane in panes:
            canvas=pane.canvas
            if target is not None:
                # Request native rendering at resolution; reject screenshot upscaling.
                method=getattr(canvas,'capture_image',None)
                try:image=method(width=target[0]//len(panes),height=target[1])
                except TypeError:raise ValueError('Active renderer does not support resolution-controlled capture') from None
                if not isinstance(image,Image.Image):raise ValueError('Renderer did not return a native image')
            else:image=pane.capture_image()
            images.append(image.convert('RGB'))
        result=Image.new('RGB',(sum(image.width for image in images),max(image.height for image in images)))
        x=0
        for image in images:result.paste(image,(x,0));x+=image.width
        return result
    def encoder_record(self):
        import json
        from PySide6.QtCore import QSettings
        raw=QSettings('ANY','ANYfem-Qt').value('presentation/encoder','')
        if not raw:return None
        try:return json.loads(raw)
        except (TypeError,ValueError):return None
    def configure_encoder(self):
        from PySide6.QtWidgets import QFileDialog,QMessageBox
        from PySide6.QtCore import QSettings
        import json
        from .optional_encoder import probe_encoder
        path,_=QFileDialog.getOpenFileName(self,'Select installed FFmpeg encoder','','Executable (*.exe);;All files (*)')
        if not path:return
        record=probe_encoder(path)
        # This optional external executable and its exact license are reviewed
        # concretely before becoming an admitted export tool.
        dialog=QMessageBox(self);dialog.setWindowTitle('Review installed encoder')
        dialog.setText(f"Use this installed FFmpeg for optional MP4 exports?\n{record['path']}\nSHA-256: {record['sha256']}")
        dialog.setDetailedText(record['license']);dialog.setStandardButtons(QMessageBox.Yes|QMessageBox.No)
        if dialog.exec()!=QMessageBox.Yes:return
        QSettings('ANY','ANYfem-Qt').setValue('presentation/encoder',json.dumps(record))
        self.export_status.setText('Installed FFmpeg reviewed; built-in mpeg4 MP4 export available')
    def save_annotation(self):
        panel=self.app.panels['Results'];scene=getattr(self.app.viewport,'_scene',None)
        field=getattr(scene,'result_field',None)
        if field is None:raise ValueError('Display a result before recording an inspection')
        values={int(key):float(value) for key,value in field.values.items() if np.isfinite(value)}
        kind='element' if field.per_element else 'node'
        pins=[dict(kind=ref.kind,id=int(ref.id),role='probe') for ref in self.app.selection.items
              if ref.kind==kind and isinstance(ref.id,int) and ref.id in values]
        extrema=dict(available=bool(values))
        if values:
            minimum=min(values,key=values.get);maximum=max(values,key=values.get)
            extrema.update(minimum=values[minimum],maximum=values[maximum],minimum_id=minimum,maximum_id=maximum)
            pins.extend([dict(kind=kind,id=minimum,role='minimum'),dict(kind=kind,id=maximum,role='maximum')])
        record=dict(group=self.annotation_name.text().strip() or 'Inspection',document_id=self.app.project.document_id,
            mesh_identity=self.mesh_identity(),job=self.app.active_job_id,field=field.name,unit=field.unit,
            component=panel.component.text(),frame=panel.frame.value(),deformation=panel.scale.text(),pins=pins,extrema=extrema)
        self.persist(lambda state:state.setdefault('annotations',[]).append(record),'Save result annotation')
        self._annotation_signature=None;self.update_annotations()
    def mesh_identity(self):
        job=self.app.project.jobs.get(self.app.active_job_id)
        return getattr(job,'mesh_hash',None)
    def rename_annotation_group(self):
        old=self.annotation_name.text().strip() or 'Inspection'
        name,ok=QInputDialog.getText(self,'Rename annotation group','New group label',text=old)
        if not ok or not name.strip() or name.strip()==old:return
        name=name.strip()
        def change(state):
            for record in state.get('annotations',[]):
                if record.get('group')==old:record['group']=name
        self.persist(change,'Rename annotation group');self.annotation_name.setText(name)
        self._annotation_signature=None;self.update_annotations()

    def toggle_annotation_group(self):
        name=self.annotation_name.text().strip() or 'Inspection'
        records=[record for record in self.app.project.presentation.get('annotations',[]) if record.get('group')==name]
        if not records:raise ValueError('This annotation group has no saved inspections')
        hidden=not all(record.get('hidden',False) for record in records)
        def change(state):
            for record in state.get('annotations',[]):
                if record.get('group')==name:record['hidden']=hidden
        self.persist(change,'Set annotation group visibility')
        self._annotation_signature=None;self.update_annotations()

    def delete_annotation_group(self):
        name=self.annotation_name.text().strip() or 'Inspection'
        self.persist(lambda state:state.update(annotations=[record for record in state.get('annotations',[]) if record.get('group')!=name]),'Delete annotation group')
        self._annotation_signature=None;self.update_annotations()
    def show_annotations(self):
        records=self.app.project.presentation.get('annotations',[]);lines=[]
        for record in records:
            stale=annotation_stale(record,self.app.project.document_id,self.mesh_identity())
            pins='; '.join(f"{pin['role']} {pin['kind']} {pin['id']}" for pin in record.get('pins',[]))
            lines.append(f"{record['group']} {'[HIDDEN]' if record.get('hidden',False) else ''} {'[STALE / UNKNOWN MESH]' if stale else ''}: {record['job']} / {record['field']} / frame {record['frame']+1} / deformation {record.get('deformation','unknown')}\n{pins}\n{record.get('extrema',{})}")
        self.app.panels['Results'].report.setPlainText('\n\n'.join(lines) or 'No saved annotations')
        self._annotation_signature=None;self.update_annotations()
    def update_annotations(self):
        scene=getattr(self.app.viewport,'_scene',None);field=getattr(scene,'result_field',None)
        records=getattr(self.app.project,'presentation',{}).get('annotations',[])
        signature=(id(scene),id(self.app.viewport.canvas),self.annotations_visible.isChecked(),repr(records))
        if signature==getattr(self,'_annotation_signature',None):return
        old=getattr(self,'_annotation_group',None)
        if old is not None:old.remove();self._annotation_group=None
        self._annotation_signature=signature
        if field is None or not self.annotations_visible.isChecked():return
        positions={}
        for patch in scene.faces:
            if patch.polygon_node_ids is not None:
                for ids,polygon in zip(patch.polygon_node_ids,patch.polygons):
                    for identifier,point in zip(ids,polygon):positions[('node',int(identifier))]=np.asarray(point)
            for index,polygon in enumerate(patch.polygons):
                from ...presentation.retained_results import element_id
                eid=element_id(patch.owners_for_polygon(index))
                if eid is not None:positions[('element',eid)]=np.mean(polygon,axis=0)
        for line in scene.lines:
            if line.point_node_ids is not None:
                for identifier,point in zip(line.point_node_ids,line.points):positions[('node',int(identifier))]=np.asarray(point)
            from ...presentation.retained_results import element_id
            eid=element_id(line.pick_owners)
            if eid is not None:positions.setdefault(('element',eid),np.mean(line.points,axis=0))
        from any3dview import Point3D
        canvas=self.app.viewport.canvas;group=canvas.annotation_group()
        with group:
            for record_index,record in enumerate(records):
                if record.get('hidden',False):continue
                if annotation_stale(record,self.app.project.document_id,self.mesh_identity()):continue
                if (record.get('field'),record.get('unit'),record.get('component',''))!=(field.name,field.unit,self.app.panels['Results'].component.text()):continue
                pins=record.get('pins',[])
                # Extrema follow the current frame; fixed probes retain identity.
                values={int(key):float(value) for key,value in field.values.items() if np.isfinite(value)}
                for pin_index,pin in enumerate(pins):
                    identifier=pin['id'];role=pin['role']
                    if role=='minimum' and values:identifier=min(values,key=values.get)
                    elif role=='maximum' and values:identifier=max(values,key=values.get)
                    point=positions.get((pin['kind'],identifier))
                    value=values.get(identifier)
                    if point is None or value is None:continue
                    label=f"{record['group']} / {role} {identifier}: {value:.6g} {field.unit} / frame {self.app.panels['Results'].frame.value()+1} / deformation {self.app.panels['Results'].scale.text()}"
                    key=f"{record['document_id']}/{record['group']}/{record['job']}/{record_index}/{pin_index}"
                    canvas.add_text(Point3D(*point),label,color='#233345',callout=True,callout_key=key)
        self._annotation_group=group
    def vector_glyphs(self):
        return self._request_glyphs('vector')

    def principal_glyphs(self):
        return self._request_glyphs('principal')

    def _glyph_context(self):
        panel=self.app.panels['Results'];scene=getattr(self.app.viewport,'_scene',None)
        key=self.app.active_job_id;dataset=self.app.result_datasets.get(key)
        return (id(self.app.session),id(self.app.project),key,id(dataset),id(self.app.viewport.canvas),
            id(getattr(scene,'result_mesh',None)),getattr(self.app,'_result_source_epoch',0),
            panel.frame.value(),panel.field_name(),panel.component.text(),self.glyph_count.value(),
            self.glyph_scale.text(),self.glyph_normalize.isChecked(),self.glyph_rank.currentText())

    def _request_glyphs(self,mode,*,explicit=True):
        scene=getattr(self.app.viewport,'_scene',None);mesh=getattr(scene,'result_mesh',None)
        dataset=self.app.result_datasets.get(self.app.active_job_id)
        if dataset is None or mesh is None:raise ValueError('Display a retained result before requesting glyphs')
        scale=float(self.glyph_scale.text())
        if not np.isfinite(scale) or scale<=0:raise ValueError('Glyph length multiplier must be positive and finite')
        self._glyph_mode=mode
        self.clear_glyphs(disable=False,explicit=explicit)
        generation=self._glyph_generation;context=self._glyph_context()
        self._glyph_pending=True;self._glyph_pending_context=context
        settings=dict(mode=mode,field=self.app.panels['Results'].field_name(),frame=self.app.panels['Results'].frame.value(),
            count=self.glyph_count.value(),scale=scale,normalized=self.glyph_normalize.isChecked(),rank=self.glyph_rank.currentText())
        from ...post.access import WORKING_BYTES
        working_bytes=getattr(getattr(self.app,'_live_result_access',None),'working_bytes',WORKING_BYTES)
        self.status.setText('Preparing glyphs in the result worker…')
        def load(cancel):
            from .glyph_preparation import prepare_glyphs
            return prepare_glyphs(dataset,mesh,scene,settings,cancel,working_bytes)
        def complete(result):
            if generation!=getattr(self,'_glyph_generation',0):return
            self._glyph_pending=False
            if result.cancelled or self.app._closing or context!=self._glyph_context():return
            if result.error is not None:
                self._glyph_mode=None;self.app.set_status(f'Glyph preparation failed: {result.error}',error=True);return
            batches,caption=result.value;canvas=self.app.viewport.canvas
            access=getattr(self.app,'_live_result_access',None)
            if access is None:return
            from ...post.fields import Field
            resident=sum(batch.positions.nbytes+batch.lines.nbytes+batch.triangles.nbytes for batch,color in batches)
            group=canvas.annotation_group()
            try:
                reservation=access.reserve_publication(Field('glyphs'),resident)
                with group:
                    for batch,color in batches:
                        canvas.add_mesh_arrays(batch,color=color,line_color=color,line_width=2,
                            pickable=False,draw_overlay=True,lit=False,layer=45)
                access.commit_pane_publication('glyphs',reservation)
                self._glyph_access=access;self._glyph_group=group;self._glyph_published_context=context
                self.status.setText(caption);canvas.redraw()
            except Exception as error:
                group.remove();access.discard_publication()
                self.app.set_status(f'Glyph publication refused: {error}',error=True)
        def begin(_):
            if generation!=getattr(self,'_glyph_generation',0) or context!=self._glyph_context():return
            self._glyph_requests=self.app._result_loader
            self._glyph_requests.request(load,complete,channel='glyph:display')
        if getattr(self.app,'_result_loader',None) is not None:begin(None)
        else:self.app._request_result(lambda cancel:None,begin,channel='glyph:bootstrap')

    def clear_glyphs(self,*,disable=True,explicit=True):
        if explicit:self._glyph_revision=getattr(self,'_glyph_revision',0)+1
        self._glyph_generation=getattr(self,'_glyph_generation',0)+1
        if disable:self._glyph_mode=None
        requests=getattr(self,'_glyph_requests',None)
        if requests is not None:requests.cancel_channel('glyph:display')
        self._glyph_pending=False;self._glyph_pending_context=None;self._glyph_published_context=None
        group=getattr(self,'_glyph_group',None)
        if group is not None:
            try:group.remove()
            except RuntimeError:
                if getattr(group,'viewer',None) is self.app.viewport.canvas:raise
        self._glyph_group=None
        access=getattr(self,'_glyph_access',None)
        if access is not None:access.release_pane_publication('glyphs')
        self._glyph_access=None;self._glyph_stable=None

    def pause_glyphs(self):
        if getattr(self,'_glyph_group',None) is not None or getattr(self,'_glyph_pending',False):
            self.clear_glyphs(disable=False,explicit=False)

    def maintain_glyphs(self):
        import time
        mode=getattr(self,'_glyph_mode',None)
        if mode is None:return
        playing=getattr(self.app.panels['Results'],'playback_active',False)
        if playing or self.animation_export.state is not None or getattr(self.app.panels['Results'],'_gif_capture',None) is not None:
            self.pause_glyphs();return
        context=self._glyph_context()
        published=getattr(self,'_glyph_published_context',None);pending=getattr(self,'_glyph_pending_context',None)
        if published==context or pending==context:return
        if published is not None or pending is not None:self.clear_glyphs(disable=False,explicit=False)
        stable=getattr(self,'_glyph_stable',None)
        if stable is None or stable[0]!=context:self._glyph_stable=(context,time.monotonic());return
        if time.monotonic()-stable[1]>=.25:
            scene=getattr(self.app.viewport,'_scene',None)
            if self.app.result_datasets.get(self.app.active_job_id) is not None and getattr(scene,'result_mesh',None) is not None:
                self._request_glyphs(mode,explicit=False)

    def _reset_canvas_presentation(self,viewport):
        canvas=viewport.canvas
        for name,args,kwargs in (
            ('clear_section_box',(),{}),('clear_section_plane',(),{}),
            ('set_contours',(),dict(mode='Continuous',levels=(),zero_contour=False,threshold=None)),
            ('set_selection_outline',(),dict(enabled=True,through_geometry=False)),
            ('set_shadows',(),dict(quality='Off',strength=.5)),
            ('set_cut_caps',(),dict(enabled=False,hatch=True)),
            ('clear_cut_cap_provider',(),{}),('set_present_method',('auto',),{})):
            method=getattr(canvas,name,None)
            if callable(method):method(*args,**kwargs)
        viewport._presentation_shadows='Off';viewport._render_quality_key=None

    def reset_project(self,*,restore_workspace=True):
        """Drop old document presentation before optionally restoring this one.

        Global material/theme appearance is preserved. Resetting an empty new
        project never creates presentation metadata or a document revision.
        """
        self.animation_export.cancel()
        self.cancel_reference();self.release_reference();self.clear_glyphs()
        old=getattr(self,'_annotation_group',None)
        if old is not None:old.remove();self._annotation_group=None
        controls=(self.compare,self.box_enabled,self.contour,self.levels,self.zero,
            self.threshold,self.threshold_direction,self.outline,self.through,self.shadows,
            self.caps,self.hatch,self.present,self.link_camera,self.link_clip,self.shared_limits,
            self.center,self.extents,self.orientation,self.job,self.field,self.component,self.frame,
            self.scale,self.annotations_visible)
        previous=self._reloading;self._reloading=True
        blockers=[QSignalBlocker(control) for control in controls]
        try:
            self.compare.setChecked(False);self.toggle_reference(False)
            self._reset_canvas_presentation(self.app.viewport)
            if self.reference is not None:
                self._reset_canvas_presentation(self.reference)
                self.reference.selection.clear();self.reference.visibility.hidden.clear()
            self.box_enabled.setChecked(False);self.center.setText('0, 0, 0')
            self.extents.setText('1, 1, 1');self.orientation.setText('0, 0, 0, 1')
            self.contour.setCurrentText('Continuous');self.levels.clear();self.zero.setChecked(False)
            self.threshold.clear();self.threshold_direction.setCurrentText('above')
            self.outline.setChecked(True);self.through.setChecked(False);self.shadows.setCurrentText('Off')
            self.caps.setChecked(False);self.hatch.setChecked(True);self.present.setCurrentText('auto')
            self.link_camera.setChecked(True);self.link_clip.setChecked(True);self.shared_limits.setChecked(False)
            self.frame.setValue(1);self.scale.setText('1');self.annotations_visible.setChecked(True)
            self.annotation_name.setText('Inspection');self.status.clear()
            visual=getattr(self.app,'panels',{}).get('Visualization')
            if visual is not None:visual.plane.setText('1, 0, 0');visual.offset.setText('0')
            self.refresh_names();self.refresh_sources()
            self._annotation_signature=None;self._link_signature=None;self._clip_link_signature=None
            self._shared_signature=None;self._clip_candidate=None
            state=self.app.viewport.canvas.export_view_state()
            self._seen_clip=(state.section_box,state.section_plane)
        finally:
            del blockers;self._reloading=previous
        workspace=self.app.project.presentation.get('workspace')
        if restore_workspace and workspace:self.app.guarded(lambda:self.restore_record(workspace))()

    def reset_view(self):
        """Restore application display defaults without restoring project view settings."""
        self.app.panels['Results'].stop_playback(clear_prepared=True)
        previous=self._reloading;self._reloading=True
        try:
            self.reset_project(restore_workspace=False)
            self.app.reset_workspace_layout()
            self.annotations_visible.setChecked(False)
            self.app.selection.clear()
            viewport=self.app.viewport
            viewport.visibility.hidden.clear()
            viewport.refresh_visibility()
            self.app.set_model_colour_mode('default')
            visual=self.app.panels['Visualization']
            visual.reset();visual.preset.setCurrentText('Model')
            from dataclasses import replace
            defaults=viewport._default_view_state
            camera_keys=('camera_position','camera_target','camera_world_up','fov','near','far',
                         'axis_indicator','axis_ruler','occlude_lines')
            state=viewport.canvas.export_view_state()
            viewport.canvas.apply_view_state(replace(state,**{key:getattr(defaults,key) for key in camera_keys}),redraw=False)
            viewport.canvas.set_iso_view();viewport.fit()
            self.update_annotations()
        finally:self._reloading=previous
        self.save_workspace()
        self.app._sync_view_mode_buttons()
        self.app.set_status('View reset to application defaults')

    def close(self):
        self.animation_export.close();self.clear_glyphs()
        self._timer.stop();self.cancel_reference();self.release_reference()
        for viewport in (self.app.viewport,self.reference):
            if viewport is not None:
                clear=getattr(viewport.canvas,'clear_cut_cap_provider',None)
                if callable(clear):clear()
        if self._reference_requests is not None:
            self._reference_requests.cancel_channel('reference:display');self._reference_requests=None
        if self.reference is not None:self.reference.close()
