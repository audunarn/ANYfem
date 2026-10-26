"""Selection-driven engineering loads and boundary-condition editor."""
import json
from dataclasses import replace
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,QLineEdit,QCheckBox,QPushButton,QGridLayout
from ... import commands
from ...model import attributes
from ...load_assignment import LoadSelection,ApplyLoadAttributes,AttributeEdit,attribute_targets
from .editors import CommandEditor,reference
from .widgets import Collapsible,ChoiceEdit


class VectorInput(QWidget):
    """Unit-aware components or a direction and physical magnitude."""
    def __init__(self,units,dimension,label):
        super().__init__();self.units=units;self.dimension=dimension
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.mode=QComboBox();self.mode.addItems(['Components','Direction and magnitude']);layout.addWidget(self.mode)
        row=QHBoxLayout();self.components=[]
        for axis in 'XYZ':
            field=QLineEdit('0');field.setAccessibleName(label+' '+axis);field.setPlaceholderText(axis);self.components.append(field);row.addWidget(QLabel(axis));row.addWidget(field)
        layout.addLayout(row)
        self.direction_box=QWidget();direction=QHBoxLayout(self.direction_box);direction.setContentsMargins(0,0,0,0)
        self.direction=[QLineEdit(text) for text in ('0','0','1')]
        for field,axis in zip(self.direction,'XYZ'):field.setAccessibleName(label+' direction '+axis);direction.addWidget(field)
        self.magnitude=QLineEdit('0');self.magnitude.setAccessibleName(label+' magnitude');direction.addWidget(QLabel('Magnitude'));direction.addWidget(self.magnitude)
        layout.addWidget(self.direction_box);self.direction_box.hide()
        self.mode.currentIndexChanged.connect(lambda index:(self.direction_box.setVisible(bool(index)),[field.setEnabled(not index) for field in self.components]))

    def value(self):
        if self.mode.currentIndex()==0:return tuple(self.units.parse(f.text(),self.dimension) for f in self.components)
        direction=np.array([float(f.text()) for f in self.direction]);norm=np.linalg.norm(direction)
        if not np.all(np.isfinite(direction)) or norm==0:raise ValueError('Enter a finite non-zero direction')
        return tuple(direction/norm*self.units.parse(self.magnitude.text(),self.dimension))

    def text(self):return json.dumps(self.value())
    def setText(self,text):
        numbers=json.loads(text) if str(text).strip().startswith('[') else str(text).replace(',',' ').split()
        if len(numbers)!=3:raise ValueError('A vector needs three components')
        self.mode.setCurrentIndex(0)
        for field,value in zip(self.components,numbers):field.setText(str(value))


class LoadTask(CommandEditor):
    PRIMARY={'AddSupport':('Supports',{'vertex','edge','face'}),'AddPointLoad':('Point loads',{'vertex'}),
        'AddLineLoad':('Line loads',{'edge'}),'AddPressure':('Pressure',{'face'}),
        'AddSurfaceTraction':('Surface traction',{'face'}),'AddMass':('Mass',{'vertex','edge','face'})}
    OPERATIONS=['AddSupport','AddPointLoad','AddLineLoad','AddPressure','AddSurfaceTraction','AddMass','AddConnection','AddConnectionLoad','AddWetSurface','AddWetPressure','AddWetPressureTable','SetAcceleration','SetFollowerPressure','AddLoadCase','AddLoadSeries','SetLoadSeriesTimes','AddLoadCaseSeries','AddLoadCasesFromTable','DeleteLoadCase','AddCombination','EditAttribute','DeleteAttribute']

    def __init__(self,app):
        self._editing=None;self._loading_edit=False
        self._interactive_ready=False
        super().__init__(app,self.OPERATIONS)
        header=QFormLayout();self.case=ChoiceEdit([]);header.addRow('Active load case',self.case);self.layout().insertLayout(0,header)
        self.case.currentTextChanged.connect(self.case_changed)
        self.wave_button=QPushButton('Hydrodynamic wave load…')
        self.wave_button.setObjectName('HydrodynamicWaveLoadInput')
        self.wave_button.clicked.connect(self.open_wave_load)
        self.layout().insertWidget(1,self.wave_button)
        self.edit_bar=QWidget();edit_layout=QHBoxLayout(self.edit_bar);edit_layout.setContentsMargins(0,0,0,0)
        self.editing_label=QLabel();self.editing_label.setWordWrap(True);edit_layout.addWidget(self.editing_label)
        self.new_button=QPushButton('Cancel edit / New');self.new_button.clicked.connect(self.cancel_edit);edit_layout.addWidget(self.new_button)
        self.layout().insertWidget(1,self.edit_bar);self.edit_bar.hide()
        for index in range(self.choice.count()):
            key=self.choice.itemData(index);self.choice.setItemText(index,self.PRIMARY[key][0] if key in self.PRIMARY else 'Advanced · '+self.choice.itemText(index))
        self.advanced_form=QCheckBox('Advanced command form');self.advanced_form.setToolTip('Show every original command parameter, including envelopes and explicit references')
        self.layout().insertWidget(self.layout().indexOf(self.choice)+1,self.advanced_form)
        self.advanced_form.toggled.connect(self._advanced_mode_changed)
        self._interactive_ready=True;self.rebuild();self.refresh()
        self.app.selection.add_listener(self.selection_changed)
        self.destroyed.connect(lambda *_:app.selection.remove_listener(self.selection_changed))

    def using_primary(self):
        return self.choice.currentData() in self.PRIMARY and not (hasattr(self,'advanced_form') and self.advanced_form.isChecked())

    def rebuild(self):
        if getattr(self,'_rebuilding_form',False):return
        self._rebuilding_form=True
        try:
            self._rebuild_form()
        finally:
            self._rebuilding_form=False
        if self._interactive_ready:self.selection_changed()

    def _rebuild_form(self):
        key=self.choice.currentData()
        if self._editing is not None and not self._loading_edit and key not in {getattr(self,'_edit_operation',None),'EditAttribute'}:self._clear_edit()
        if not self._interactive_ready or not self.using_primary():
            if hasattr(self,'apply'):self.layout().addWidget(self.apply)
            if hasattr(self,'case'):self.case.setEnabled(True)
            super().rebuild()
            if hasattr(self,'case'):
                self.case_changed(self.case_name());self.apply.setText('Apply advanced command');self.apply.setEnabled(not self.app.session.read_only)
            return
        self.operation=getattr(commands,key);self.fields={};self.record_fields={};self.optional_records={};self.loaded_ids={};self.dimensions={}
        self._draft_project=self.app.project;self._draft_revision=self.app.project.geometry.revision;self._draft_units=self.app.project.units
        container=QWidget();layout=QVBoxLayout(container);layout.setContentsMargins(0,0,0,0)
        title=QLabel(self.PRIMARY[key][0]);title.setObjectName('TaskTitle');layout.addWidget(title)
        self.target_summary=QLabel();self.target_summary.setWordWrap(True);layout.addWidget(self.target_summary)
        picks=QHBoxLayout()
        for label,mode in [('Points','vertex'),('Lines','edge'),('Plate edges','plate_edge'),('Faces','face')]:
            button=QPushButton(label);button.setAccessibleName('Pick '+label.lower());button.clicked.connect(self.app.guarded(lambda checked=False,mode=mode:self.app._quick_select(mode)));picks.addWidget(button)
        layout.addLayout(picks)
        self.layout().removeWidget(self.apply);layout.addWidget(self.apply)
        self.rebind=QPushButton('Use current project and selection');self.rebind.clicked.connect(self.bind_targets);layout.addWidget(self.rebind);self.rebind.hide()
        form=QFormLayout();layout.addLayout(form)
        self.frame=ChoiceEdit(self.coordinate_choices(),'global')
        self.fields['coordinate_system_id']=(self.frame,'str','global')
        self.case.setEnabled(key not in {'AddSupport','AddMass'})
        if key not in {'AddSupport','AddMass'}:self.fields['case']=(self.case,'str','default')
        if key not in {'AddPressure','AddMass'}:form.addRow('Coordinate system',self.frame)
        self.name=QLineEdit('Support' if key=='AddSupport' else 'mass')
        if key in {'AddSupport','AddMass'}:form.addRow('Name',self.name)
        if key=='AddSupport':self.build_support(layout,form)
        elif key in {'AddPointLoad','AddLineLoad','AddSurfaceTraction'}:
            dimension='force' if key=='AddPointLoad' else 'line_load' if key=='AddLineLoad' else 'pressure'
            fieldname='force' if key=='AddPointLoad' else 'force_per_length' if key=='AddLineLoad' else 'traction'
            self.vector=VectorInput(self.app.project.units,dimension,fieldname);form.addRow('Force' if key=='AddPointLoad' else 'Force / length' if key=='AddLineLoad' else 'Traction',self.vector)
            self.fields[fieldname]=(self.vector,'Sequence[float]',(0,0,0))
            if key=='AddPointLoad':
                self.moment=VectorInput(self.app.project.units,'moment','Moment');form.addRow('Moment',self.moment);self.fields['moment']=(self.moment,'Sequence[float]',(0,0,0))
        else:
            self.scalar=QLineEdit('0');form.addRow('Pressure ('+self.app.project.units.symbol('pressure')+')' if key=='AddPressure' else 'Mass ('+self.app.project.units.symbol('mass')+')',self.scalar);self.fields['value']=(self.scalar,'float',0.)
        if key in {'AddPointLoad','AddMass'}:
            self.distribution=ChoiceEdit(self.DISTRIBUTION_CHOICES)
            self.fields['distribution_policy']=(self.distribution,'str','total_distributed' if key=='AddMass' else 'per_target')
            self.distribution.setCurrentIndex(1 if key=='AddMass' else 0);form.addRow('Distribution',self.distribution)
        instructions=QLabel({'AddSupport':'Checked components are restrained to zero; unchecked components remain free.','AddPressure':'Signed pressure follows the existing plate-normal convention.','AddPointLoad':'The full force and moment act on each point unless you choose Total value shared by targets.','AddLineLoad':'Force per unit length acts on every selected line.','AddSurfaceTraction':'Traction per unit area acts on every selected face.','AddMass':'Total value shared by targets divides this mass among the selected geometry entities.'}[key]);instructions.setWordWrap(True);layout.addWidget(instructions)
        advanced=Collapsible('Advanced target and prescribed values');layout.addWidget(advanced)
        self.explicit_ref=QLineEdit();self.explicit_ref.textChanged.connect(lambda *_:self.selection_changed());self.explicit_ref.setPlaceholderText('Optional explicit target, e.g. edge:12');advanced.addWidget(self.explicit_ref);self.fields['ref']=(self.explicit_ref,'EntityRef',None)
        if key=='AddSupport':
            advanced.addWidget(QLabel('Non-zero prescribed displacement / rotation for checked components:'))
            values=QFormLayout();box=QWidget();box.setLayout(values);advanced.addWidget(box)
            for dof in attributes.DOF_NAMES:values.addRow(dof,self.prescribed[dof])
        previous=self.scroll.takeWidget()
        if previous is not None:previous.deleteLater()
        self.scroll.setWidget(container)
        for widget in container.findChildren(QWidget):widget.installEventFilter(self)
        self.feedback.clear();self.guidance.setText('Values use project units; explicit unit suffixes are accepted. Shift-select to add targets.');self.guidance.show()
        self.selection_changed()

    def build_support(self,layout,form):
        self.preset=QComboBox();self.preset.addItems(['custom','fixed','pinned','simply_supported','symmetry','antisymmetry']);form.addRow('Preset',self.preset)
        self.normal=QComboBox();self.normal.addItems(['x','y','z']);self.normal.setCurrentText('z');form.addRow('Symmetry normal',self.normal)
        self.dof=QComboBox();self.dof.addItems(['ux','uy','uz']);self.dof.setCurrentText('uz');form.addRow('Simply supported component',self.dof)
        self.normal_label=form.labelForField(self.normal);self.dof_label=form.labelForField(self.dof)
        box=QWidget();grid=QGridLayout(box);grid.setContentsMargins(0,0,0,0);self.dofs={};self.prescribed={}
        for i,dof in enumerate(attributes.DOF_NAMES):
            check=QCheckBox({'ux':'X translation','uy':'Y translation','uz':'Z translation','rx':'X rotation','ry':'Y rotation','rz':'Z rotation'}[dof]);check.setAccessibleName('Restrain '+dof);grid.addWidget(check,i//3,i%3);self.dofs[dof]=check
            self.prescribed[dof]=QLineEdit('0');self.prescribed[dof].setAccessibleName('Prescribed '+dof)
        layout.addWidget(box)
        self.preset.currentTextChanged.connect(lambda *_:self.fill_preset());self.normal.currentTextChanged.connect(lambda *_:self.fill_preset());self.dof.currentTextChanged.connect(lambda *_:self.fill_preset())
        self.preset.setCurrentText('fixed');self.fill_preset()
        for dof,check in self.dofs.items():
            check.setObjectName('support_'+dof)
            check.toggled.connect(self.support_changed)

    def fill_preset(self):
        kind=self.preset.currentText()
        if kind=='custom':
            for widget in (self.normal,self.normal_label,self.dof,self.dof_label):widget.hide()
            return
        from anygeometry import EntityRef
        options={}
        if kind in {'symmetry','antisymmetry'}:options['normal']=self.normal.currentText()
        elif kind=='simply_supported':options['normal']=self.dof.currentText()
        self._filling_preset=True
        constraints=getattr(attributes,kind)(EntityRef('vertex',1),**options).constraints
        for dof,check in self.dofs.items():check.setChecked(dof in constraints);self.prescribed[dof].setText('0')
        self._filling_preset=False
        self.normal.setVisible(kind in {'symmetry','antisymmetry'});self.normal_label.setVisible(kind in {'symmetry','antisymmetry'});self.dof.setVisible(kind=='simply_supported');self.dof_label.setVisible(kind=='simply_supported')
        if hasattr(self,'explicit_ref'):self.selection_changed()

    def support_changed(self,*_):
        if not getattr(self,'_filling_preset',False):self.preset.setCurrentText('custom')
        self.selection_changed()

    def bind_targets(self):
        if self.app.project.units!=self._draft_units:
            self.rebuild();return
        self._draft_project=self.app.project;self._draft_revision=self.app.project.geometry.revision;self.selection_changed()

    def selection_changed(self):
        # Preset signals can fire before the replacement target field exists.
        # Refresh once the complete form is installed, never through old Qt wrappers.
        if getattr(self,'_rebuilding_form',False):return
        if self._editing is not None:
            self._sync_edit_state();return
        key=self.choice.currentData()
        if not self._interactive_ready or not self.using_primary():return
        if self.app.project is self._draft_project:self._draft_revision=self.app.project.geometry.revision
        refs=self.app.selection.ordered_items
        if hasattr(self,'explicit_ref') and self.explicit_ref.text().strip():
            try:refs=(reference(self.app,self.explicit_ref.text()),)
            except (ValueError,KeyError):refs=()
        captured=LoadSelection.capture(self.app.project,refs,self.PRIMARY[key][1],imported=getattr(self.app,"imported",None),mesh=self.app.mesh)
        ignored=max(0,len(self.app.selection.ordered_items)-len(captured.refs))
        stale=self.app.project is not self._draft_project or self.app.project.units!=self._draft_units
        self.target_summary.setText(('Project or units changed — review targets and values before applying. ' if stale else '')+str(len(captured.refs))+' compatible target(s)'+(' · '+str(ignored)+' other selected item(s) skipped' if ignored else '')+(' — select geometry using the buttons above.' if not captured.refs else ''))
        self.rebind.setVisible(stale)
        has_dofs=key!='AddSupport' or any(check.isChecked() for check in self.dofs.values())
        self.apply.setEnabled(bool(captured.refs) and has_dofs and not stale and not self.app.session.read_only)
        self.apply.setText('Apply to '+str(len(captured.refs))+' target(s)')
        if not has_dofs:self.target_summary.setText(self.target_summary.text()+' — check at least one restrained component.')

    def case_name(self):return self.case.currentText() or 'default'
    def case_changed(self,value):
        if 'case' in self.fields and self.fields['case'][0] is not self.case:self.fields['case'][0].setText(value)

    def refresh(self):
        if not self._interactive_ready:return super().refresh()
        self.case.set_choices([(name,name) for name,case in self.app.project.load_cases.items() if case.parent is None])
        if not self.case.text():self.case.setText('default')
        if self.using_primary():self.frame.set_choices(self.coordinate_choices())
        if not self.using_primary():super().refresh();self.case_changed(self.case_name())
        else:self.selection_changed()
        if self._editing is not None:self._sync_edit_state()

    def open_wave_load(self):
        from .wave_load import WaveLoadDialog
        self._wave_dialog=WaveLoadDialog(self)
        self._wave_dialog.show()

    def support_preset(self):
        self.advanced_form.setChecked(False)
        if self.choice.currentData()!='AddSupport':self.choice.setCurrentIndex(self.choice.findData('AddSupport'))
        self.fill_preset();return self.execute()

    def load_values(self,operation,**values):
        if not self._loading_edit:self._clear_edit()
        if operation not in self.PRIMARY:return super().load_values(operation,**values)
        self.advanced_form.setChecked(False)
        index=self.choice.findData(operation)
        if self.choice.currentIndex()!=index:self.choice.setCurrentIndex(index)
        else:self.rebuild()
        units=self.app.project.units
        for name,value in values.items():
            if name=='support':
                self.name.setText(value.name);self.frame.setText(value.coordinate_system_id)
                self.explicit_ref.setText(self.encoded(value.ref));self.preset.setCurrentText('custom')
                for dof,check in self.dofs.items():
                    check.setChecked(dof in value.constraints)
                    if dof in value.constraints:self.prescribed[dof].setText(self.format_quantity(value.constraints[dof],'length' if dof.startswith('u') else 'angle',units))
            elif name in {'force','force_per_length','traction','moment'}:
                vector=self.fields[name][0];vector.mode.setCurrentIndex(0)
                for field,number in zip(vector.components,value):field.setText(self.format_quantity(number,vector.dimension,units))
            elif name=='value':self.scalar.setText(self.format_quantity(value,'mass' if operation=='AddMass' else 'pressure',units))
            elif name=='name':self.name.setText(value)
            elif name in self.fields:self.fields[name][0].setText(self.encoded(value))
        self.selection_changed()

    def _clear_edit(self):
        self._editing=None
        if hasattr(self,'edit_bar'):self.edit_bar.hide()

    def cancel_edit(self):
        operation=getattr(self,'_edit_operation','AddSupport') or 'AddSupport'
        self._clear_edit();self.advanced_form.setChecked(False)
        self.choice.setCurrentIndex(self.choice.findData(operation));self.rebuild()

    def _advanced_mode_changed(self,checked):
        if self._editing is not None and not self._loading_edit:
            record=self._editing.record
            if checked:
                self._loading_edit=True
                try:super().load_values('EditAttribute',replacement=record)
                finally:self._loading_edit=False
                self._sync_edit_state()
            else:self.edit_existing(record)
        else:self.rebuild()

    def edit_existing(self,record):
        context=AttributeEdit.capture(self.app.project,record)
        operation={attributes.Support:'AddSupport',attributes.PointLoad:'AddPointLoad',attributes.LineLoad:'AddLineLoad',attributes.Pressure:'AddPressure',attributes.SurfaceTraction:'AddSurfaceTraction',attributes.Mass:'AddMass'}.get(type(record))
        self._editing=context;self._edit_operation=operation;self._loading_edit=True
        try:
            if operation is None:
                self.advanced_form.setChecked(True)
                super().load_values('EditAttribute',replacement=record)
            else:
                values={}
                if operation=='AddSupport':values['support']=record
                else:
                    for name in ('force','moment','force_per_length','traction','value','name','coordinate_system_id','distribution_policy'):
                        if hasattr(record,name):values[name]=getattr(record,name)
                self.load_values(operation,**values)
            if context.address[0]=='case':
                case=next(case for case in self.app.project.load_cases.values() if case.id==context.address[1]);self.case.setText(case.name)
        finally:self._loading_edit=False
        self._sync_edit_state()
        return record

    def _sync_edit_state(self):
        context=self._editing
        if context is None or not hasattr(self,'edit_bar'):return
        self.edit_bar.show();self.case.setEnabled(False)
        record=context.record;name=getattr(record,'name',type(record).__name__)
        self.editing_label.setText('Editing '+str(name)+' — existing record')
        try:
            context.validate(self.app.project,read_only=self.app.session.read_only)
            targets=attribute_targets(self.app.project,record)
            if self.using_primary() and self.app.project.units!=self._draft_units:raise ValueError('Project units changed; select this record again to review values')
            valid=not self.using_primary() or self.choice.currentData()!='AddSupport' or any(check.isChecked() for check in self.dofs.values())
            message=str(len(targets))+' existing target(s) · selection does not change this assignment'
        except (ValueError,KeyError,PermissionError) as error:valid=False;message=str(error)
        self.apply.setEnabled(valid);self.apply.setText('Update '+str(name))
        if self.using_primary():self.target_summary.setText(message);self.rebind.hide()

    def _edited_record(self):
        record=self._editing.record;key=self._edit_operation;units=self.app.project.units
        changes={}
        if key=='AddSupport':
            changes['name']=self.name.text().strip() or record.name
            changes['constraints']={dof:units.parse(self.prescribed[dof].text(),'length' if dof.startswith('u') else 'angle') for dof,check in self.dofs.items() if check.isChecked()}
        elif key in {'AddPointLoad','AddLineLoad','AddSurfaceTraction'}:
            field='force' if key=='AddPointLoad' else 'force_per_length' if key=='AddLineLoad' else 'traction';changes[field]=self.vector.value()
            if key=='AddPointLoad':changes['moment']=self.moment.value()
        else:changes['value']=units.parse(self.scalar.text(),'mass' if key=='AddMass' else 'pressure')
        if key in {'AddPointLoad','AddMass'}:changes['distribution_policy']=self.distribution.currentData()
        if key=='AddMass':changes['name']=self.name.text().strip() or record.name
        if hasattr(record,'coordinate_system_id'):
            frame=self.frame.currentData()
            if frame not in self.app.project.coordinate_systems:raise ValueError('Choose an available coordinate system')
            changes['coordinate_system_id']=frame
        return replace(record,**changes)

    def execute(self):
        if self._editing is not None:
            self._editing.validate(self.app.project,read_only=self.app.session.read_only)
            if self.using_primary() and self.app.project.units!=self._draft_units:raise ValueError('Project units changed; select the record again')
            replacement=self._edited_record() if self.using_primary() else self.command_options()['replacement']
            command=self._editing.command(self.app.project,replacement,read_only=self.app.session.read_only)
            result=self.app.run(command);self.edit_existing(result);self.feedback.setText('Updated existing record; Undo restores its previous settings')
            return result
        key=self.choice.currentData()
        if not self.using_primary():
            eligible={'AddWetSurface':{'face'},'AddPointLoad':{'vertex'},'AddLineLoad':{'edge'},'AddPressure':{'face'},'AddSurfaceTraction':{'face'},'AddMass':{'vertex','edge','face'}}
            if key in eligible and not self.fields['ref'][0].text().strip():
                draft=LoadSelection.capture(self.app.project,self.app.selection.ordered_items,eligible[key],imported=getattr(self.app,"imported",None),mesh=self.app.mesh);draft.validate(self.app.project,read_only=self.app.session.read_only,imported=getattr(self.app,"imported",None),mesh=self.app.mesh)
                options=self.command_options({'ref':draft.refs[0]})
                if options.get('distribution_policy')=='total_distributed':
                    for field in ('force','moment'):
                        if field in options:options[field]=tuple(value/len(draft.refs) for value in options[field])
                    if key=='AddMass':options['value']/=len(draft.refs)
                return self.app.run(ApplyLoadAttributes(tuple(self.operation(**dict(options,ref=ref)) for ref in draft.refs)))
            return super().execute()
        if self.app.project is not self._draft_project:raise ValueError('Project changed; confirm current targets before applying')
        if self.app.project.units!=self._draft_units:raise ValueError('Project units changed; reopen the operation to review values')
        refs=(reference(self.app,self.explicit_ref.text()),) if self.explicit_ref.text().strip() else tuple(self.app.selection.ordered_items)
        draft=LoadSelection.capture(self.app.project,refs,self.PRIMARY[key][1],imported=getattr(self.app,"imported",None),mesh=self.app.mesh);draft.validate(self.app.project,read_only=self.app.session.read_only,imported=getattr(self.app,"imported",None),mesh=self.app.mesh)
        options={};case=self.case_name();project=self.app.project;units=project.units
        if key not in {'AddSupport','AddMass'}:
            if (case not in project.load_cases and case!='default') or (case in project.load_cases and project.load_cases[case].is_template):raise ValueError('Choose an existing non-template load case')
            options['case']=case
        frame=self.frame.currentData()
        if key not in {'AddPressure','AddMass'}:
            if frame not in project.coordinate_systems:raise ValueError('Choose an available coordinate system')
            options['coordinate_system_id']=frame
        if key=='AddSupport':
            constraints={dof:units.parse(self.prescribed[dof].text(),'length' if dof.startswith('u') else 'angle') for dof,check in self.dofs.items() if check.isChecked()}
            children=[commands.AddSupport(attributes.Support(self.name.text().strip() or 'Support',ref,constraints,coordinate_system_id=frame)) for ref in draft.refs]
        else:
            if key in {'AddPointLoad','AddLineLoad','AddSurfaceTraction'}:
                field='force' if key=='AddPointLoad' else 'force_per_length' if key=='AddLineLoad' else 'traction';options[field]=self.vector.value()
                if key=='AddPointLoad':options['moment']=self.moment.value()
            else:options['value']=units.parse(self.scalar.text(),'pressure' if key=='AddPressure' else 'mass')
            if key in {'AddPointLoad','AddMass'}:
                options['distribution_policy']=self.distribution.currentData()
                if options['distribution_policy']=='total_distributed':
                    for field in ('force','moment'):
                        if field in options:options[field]=tuple(value/len(draft.refs) for value in options[field])
                    if key=='AddMass':options['value']/=len(draft.refs)
                if key=='AddMass':options['name']=self.name.text().strip() or 'mass'
            children=[self.operation(ref=ref,**options) for ref in draft.refs]
        draft.validate(self.app.project,read_only=self.app.session.read_only,imported=getattr(self.app,"imported",None),mesh=self.app.mesh)
        result=self.app.run(ApplyLoadAttributes(tuple(children),label='apply '+self.PRIMARY[key][0].lower()+' to selection'))
        self.feedback.setText('Applied to '+str(len(draft.refs))+' target(s) in one undoable edit')
        return result
