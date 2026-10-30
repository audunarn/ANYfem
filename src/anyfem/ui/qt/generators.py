"""Structural feature generators with geometry and optional engineering intent."""
import inspect
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import QWidget,QVBoxLayout,QComboBox,QPushButton,QLabel,QFormLayout,QLineEdit
from ... import commands
from ..numeric_input import parse_numeric_sequence
from .forms import OptionForm
from .editors import apply_on_enter
from ...generator_properties import FIELDS

SAME_AS_LONGITUDINAL='__same_as_longitudinal__'

class GeneratorTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app;self.form=None;self._editing_feature=None
        self.layout_=QVBoxLayout(self);self.layout_.setAlignment(Qt.AlignTop);self.kind=QComboBox()
        self.kind.addItems(['Plate','Bulkhead','Frame','Girder','Stiffener','Stiffened panel','Cylinder','Cone']);self.layout_.addWidget(self.kind)
        self.guidance=QLabel();self.guidance.setWordWrap(True);self.layout_.addWidget(self.guidance)
        self.kind.currentTextChanged.connect(self._kind_changed)
        self.engineering=QWidget();self.engineering_form=QFormLayout(self.engineering);self.engineering_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.beam_section=QComboBox();self.longitudinal_beam_section=self.beam_section;self.transverse_beam_section=QComboBox()
        self.plate_section=QComboBox();self.material=QComboBox();self.plate_thickness=QLineEdit();self.mesh_size=QLineEdit()
        for label,control in [('Beam section',self.beam_section),('Transverse beam section',self.transverse_beam_section),('Plate section',self.plate_section),('Plate thickness',self.plate_thickness),('Material',self.material),('Mesh size',self.mesh_size)]:self.engineering_form.addRow(label,control);control.installEventFilter(self)
        self.plate_thickness.setPlaceholderText('Optional; overrides selected plate section')
        self.mesh_size.setPlaceholderText('Optional local target size')
        self.layout_.addWidget(self.engineering)
        self.help=QLabel('Optional engineering properties. Choose Geometry only for an unassigned feature. Create materials and section definitions in Sections when none are available.');self.help.setWordWrap(True);self.layout_.addWidget(self.help)
        self.apply=QPushButton('Create editable feature');self.apply.setProperty('primary',True);self.apply.clicked.connect(app.guarded(self.execute));self.layout_.addWidget(self.apply)
        self.new_button=QPushButton('Cancel edit / New generator');self.new_button.clicked.connect(self.new_feature);self.new_button.hide();self.layout_.addWidget(self.new_button)
        self.kind.installEventFilter(self);self.apply.installEventFilter(self);self.rebuild();self.refresh()

    def new_feature(self):
        self._editing_feature=None
        self.apply.setText('Create editable feature');self.new_button.hide()

    def _kind_changed(self,*_):
        self.new_feature();self.rebuild()

    def load_feature(self,identifier):
        record=self.app.project.geometry.features.get(int(identifier))
        kind=record.kind.removeprefix('generator.').replace('_',' ').capitalize()
        if self.kind.findText(kind)<0:raise ValueError('This feature is not supported by the generator editor')
        self.kind.setCurrentText(kind);self.rebuild()
        from .editors import CommandEditor
        for key,(field,_,_) in self.form.fields.items():
            value=record.name if key=='name' else record.parameters.get(key)
            if value is None:field.clear();continue
            if key in {'length','width','height','radius','radius_start','radius_end','longitudinal_spacing','transverse_spacing','ring_spacing'}:
                text=self.app.project.units.format(value,'length',precision=15)
            elif key=='origin':text=', '.join(self.app.project.units.format(v,'length',precision=15) for v in value)
            else:text=CommandEditor.encoded(value)
            field.setText(text)
        options=self.app.project.generator_properties.get(record.feature_id,{})
        for key in ('beam_section','longitudinal_beam_section','transverse_beam_section','plate_section','material'):
            value=options.get(key,options.get('beam_section') if key=='longitudinal_beam_section' else None)
            if key=='transverse_beam_section' and key not in options and options.get('beam_section') is not None:value=SAME_AS_LONGITUDINAL
            control=getattr(self,key);control.setCurrentIndex(max(0,control.findData(value)))
        for key in ('plate_thickness','mesh_size'):
            value=options.get(key);getattr(self,key).setText('' if value is None else self.app.project.units.format(value,'length',precision=15))
        self._editing_feature=(record.feature_id,self.app.project,self.app.session.revision.id)
        self.apply.setText('Apply generator changes');self.new_button.show()

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.MouseButtonPress,QEvent.KeyPress):self.app._begin_modelling()
        if hasattr(self,'apply') and apply_on_enter(watched,event,self.apply):return True
        return super().eventFilter(watched,event)

    def rebuild(self,*_):
        if self.form is not None:self.layout_.removeWidget(self.form);self.form.deleteLater()
        kind=self.kind.currentText();operation={'Stiffened panel':commands.AddStiffenedPanel,'Cylinder':commands.AddCylinder,'Cone':commands.AddCone}.get(kind)
        if operation is not None:parameters=[(name,p.annotation,p.default) for name,p in inspect.signature(operation).parameters.items() if p.kind not in (p.VAR_KEYWORD,p.VAR_POSITIONAL) and name not in FIELDS]
        else:
            parameters=[('length','float',1.0),('origin','Sequence[float]',(0,0,0))]
            if kind in {'Girder','Stiffener'}:parameters.append(('direction','Sequence[float]',(1,0,0)))
            else:parameters.extend([('width','float',1.0),('u_direction','Sequence[float]',(1,0,0)),('v_direction','Sequence[float]',(0,1,0)),('semantic_group','str',kind.lower())])
        self.form=OptionForm(parameters);self.layout_.insertWidget(2,self.form)
        for field,_,_ in self.form.fields.values():field.installEventFilter(self)
        if hasattr(self,'engineering'):self.refresh()

    def geometry_values(self):
        values={};lengths={'length','width','height','radius','radius_start','radius_end','longitudinal_spacing','transverse_spacing','ring_spacing'}
        for name,(widget,annotation,default) in self.form.fields.items():
            text=widget.text().strip()
            if not text:
                if default is inspect.Parameter.empty:raise ValueError(name.replace('_',' ')+' is required')
                continue
            if name in lengths:values[name]=self.app.project.units.parse(text,'length')
            elif name=='origin':values[name]=parse_numeric_sequence(text,lambda v:self.app.project.units.parse(v,'length'),units=True)
            else:
                from .editors import CommandEditor
                values[name]=CommandEditor.value(self.form,widget,annotation,default)
        return values

    def execute(self):
        kind=self.kind.currentText();values=self.geometry_values();options={}
        line=kind in {'Girder','Stiffener'};beam=kind in {'Girder','Stiffener','Stiffened panel','Cylinder','Cone'}
        for key in ('beam_section','plate_section','material'):
            if key=='beam_section' and kind=='Stiffened panel':continue
            if key=='beam_section' and not beam or key=='plate_section' and line:continue
            value=getattr(self,key).currentData()
            if value is not None:options[key]=value
        if kind=='Stiffened panel':
            for key in ('longitudinal_beam_section','transverse_beam_section'):
                value=getattr(self,key).currentData()
                if value is not None and value!=SAME_AS_LONGITUDINAL:options[key]=value
            if self.transverse_beam_section.currentData()==SAME_AS_LONGITUDINAL and options.get('longitudinal_beam_section') is not None:options['beam_section']=options['longitudinal_beam_section']
        for key in ('plate_thickness','mesh_size'):
            if key=='plate_thickness' and line:continue
            text=getattr(self,key).text().strip()
            if text:options[key]=self.app.project.units.parse(text,'length')
        operation={'Stiffened panel':commands.AddStiffenedPanel,'Cylinder':commands.AddCylinder,'Cone':commands.AddCone}.get(kind)
        if self._editing_feature is not None:
            identifier,project,revision=self._editing_feature
            if self.app.project is not project or self.app.session.revision.id!=revision:raise ValueError('The model changed; reopen the generator editor')
            name=values.pop('name',None)
            command=commands.EditFeature(identifier,name=name,parameters=values,engineering=options)
        else:command=operation(**values,**options) if operation else commands.AddFeature('generator.'+kind.lower(),name=kind,parameters=values,engineering=options or None,label='add '+kind.lower())
        result=self.app.run(command);self.new_feature();return result

    def refresh(self):
        project=self.app.project;kind=self.kind.currentText();line=kind in {'Girder','Stiffener'};beam=kind in {'Girder','Stiffener','Stiffened panel','Cylinder','Cone'}
        self.apply.setEnabled(not self.app.session.read_only and not project.read_only_reason)
        for key,registry in (('beam_section',project.beam_sections),('transverse_beam_section',project.beam_sections),('plate_section',project.plate_sections),('material',project.materials)):
            control=getattr(self,key);current=control.currentData() if control.count() else SAME_AS_LONGITUDINAL if key=='transverse_beam_section' else None
            control.clear()
            if key=='transverse_beam_section':control.addItem('Same as longitudinal',SAME_AS_LONGITUDINAL)
            control.addItem('Use selected section material' if key=='material' else 'Geometry only / no assignment',None)
            for name in sorted(registry):control.addItem(name,name)
            index=control.findData(current);control.setCurrentIndex(max(0,index))
        self.engineering_form.setRowVisible(self.beam_section,beam)
        self.engineering_form.labelForField(self.beam_section).setText('Longitudinal beam section' if kind=='Stiffened panel' else 'Beam section')
        self.engineering_form.setRowVisible(self.transverse_beam_section,kind=='Stiffened panel')
        self.engineering_form.setRowVisible(self.plate_section,not line);self.engineering_form.setRowVisible(self.plate_thickness,not line)
        self.guidance.setText('Dimensions use '+project.units.symbol('length')+'; explicit unit suffixes are accepted. Directions define the local generator frame.')
        for key in ('plate_thickness','mesh_size'):self.engineering_form.labelForField(getattr(self,key)).setText(key.replace('_',' ').title()+' ('+project.units.symbol('length')+')')
