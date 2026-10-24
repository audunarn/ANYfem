"""One opt-in engineering editor for single or multiple geometry targets."""
import math
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QCheckBox,QComboBox,QLineEdit,QPushButton,QGroupBox,QScrollArea,QWidget
from ...geometry_edit import GeometryEditDraft,MIXED
from ...model.sections import PROFILES,OFFSET_MODES,ATTACHMENT_SIDES
from ...ui.numeric_input import parse_numeric_sequence

LABELS={'section':'Base section','material':'Material','thickness':'Thickness','profile':'Profile','web_height':'Web height','web_thickness':'Web thickness','flange_width':'Flange / bar width','flange_thickness':'Flange / bar thickness','rotation_deg':'Rotation relative to plate (deg)','attachment_side':'Attachment side','offset_mode':'Offset mode','eccentricity':'Manual eccentricity','web_direction':'Web direction (X, Y, Z)','mesh_size_id':'Named mesh size','mesh_size':'Numeric mesh target'}
LENGTHS={'thickness','web_height','web_thickness','flange_width','flange_thickness','eccentricity','mesh_size'}


class GeometryEditDialog(QDialog):
    def __init__(self,app,references):
        super().__init__(app);self.app=app;self.draft=GeometryEditDraft.capture(app.project,references);self.session=app.session;self.revision=app.session.revision.id
        self.setObjectName('GeometryEditDialog');self.setWindowTitle('Edit selected beams and plates');self.setAttribute(Qt.WA_DeleteOnClose);self.resize(580,650)
        layout=QVBoxLayout(self);counts=self.draft.counts
        self.summary=QLabel(f'{counts["beam"]} beam / line axes · {counts["plate"]} plates · {counts["skipped"]} incompatible point(s) skipped');self.summary.setWordWrap(True);layout.addWidget(self.summary)
        note=QLabel('Check only the inputs you want to change. Unchecked inputs remain unchanged. Shared definitions are preserved; changes affect the selected targets only.');note.setWordWrap(True);layout.addWidget(note)
        scroll=QScrollArea();scroll.setWidgetResizable(True);body=QWidget();body_layout=QVBoxLayout(body);scroll.setWidget(body);layout.addWidget(scroll)
        self.beam_fields={};self.beam_checks={};self.plate_fields={};self.plate_checks={};self._original_text={}
        for kind,refs,fields,checks,keys in (
            ('beam',self.draft.beam_refs,self.beam_fields,self.beam_checks,('section','material','rotation_deg','attachment_side','offset_mode','eccentricity','profile','web_height','web_thickness','flange_width','flange_thickness','web_direction','mesh_size_id','mesh_size')),
            ('plate',self.draft.plate_refs,self.plate_fields,self.plate_checks,('section','material','thickness','mesh_size_id','mesh_size'))):
            if not refs:continue
            group=QGroupBox(('Beams / lines' if kind=='beam' else 'Plates')+f' ({len(refs)})');form=QFormLayout(group);body_layout.addWidget(group)
            for key in keys:
                value=self.draft.shared(kind,key);label=LABELS[key]+(' ('+app.project.units.symbol('length')+')' if key in LENGTHS else '')
                check=QCheckBox(label);check.setAccessibleName('Change '+label);check.setObjectName(kind+'_change_'+key)
                if key in {'section','material','profile','attachment_side','offset_mode','mesh_size_id'}:
                    field=QComboBox()
                    choices=(app.project.beam_sections if kind=='beam' else app.project.plate_sections) if key=='section' else app.project.materials if key=='material' else PROFILES if key=='profile' else ATTACHMENT_SIDES if key=='attachment_side' else OFFSET_MODES if key=='offset_mode' else {item.id:item.name for item in app.project.mesh_size_properties.values()}
                    field.addItem(str(MIXED) if value is MIXED else 'Unassigned / choose value','')
                    for identifier in choices:field.addItem(str(choices[identifier]) if key=='mesh_size_id' else str(identifier),identifier)
                    index=field.findData(value);field.setCurrentIndex(max(0,index))
                else:
                    field=QLineEdit();field.setPlaceholderText(str(MIXED) if value is MIXED else 'Unassigned' if value is None else '')
                    if value not in (None,MIXED):
                        field.setText(app.project.units.format(value,'length',precision=15) if key in LENGTHS else ', '.join(str(x) for x in value) if key=='web_direction' else str(value))
                if isinstance(field,QLineEdit):self._original_text[field]=(field.text(),value)
                field.setAccessibleName(label);field.setObjectName(kind+'_'+key);field.setEnabled(False);check.toggled.connect(field.setEnabled);form.addRow(check,field);fields[key]=field;checks[key]=check
            help_text=QLabel('Manual eccentricity requires Offset mode = manual. Bare rotation numbers are degrees. Clear an enabled web direction to use the attached plate normal.' if kind=='beam' else 'Thickness and material require an existing assigned or explicitly selected base section.')
            help_text.setWordWrap(True);form.addRow(help_text)
        body_layout.addStretch();hint=QLabel('Mesh target: use a named control OR a numeric target. The local target must not exceed the global target. Geometry and adjoining controls may adjust element sizes. Existing legacy/generator controls must be edited in Mesh; numeric edits retain a named control’s radius and growth.');hint.setWordWrap(True);layout.addWidget(hint)
        self.feedback=QLabel();self.feedback.setWordWrap(True);layout.addWidget(self.feedback)
        row=QHBoxLayout();self.apply=QPushButton('Apply to selected');self.apply.setDefault(True);self.apply.setProperty('primary',True);self.apply.setEnabled(not app.session.read_only);self.cancel=QPushButton('Cancel');row.addWidget(self.apply);row.addWidget(self.cancel);layout.addLayout(row)
        self.apply.clicked.connect(self.save);self.cancel.clicked.connect(self.reject)

    def patch(self,fields,checks):
        result={}
        for key,check in checks.items():
            if not check.isChecked():continue
            field=fields[key]
            if isinstance(field,QComboBox):
                value=field.currentData()
                if not value:raise ValueError('Choose '+LABELS[key].lower()+'.')
            else:
                text=field.text().strip()
                original_text,original_value=self._original_text[field]
                if text==original_text and original_value is not None and original_value is not MIXED:
                    result[key]=original_value;continue
                if key in LENGTHS:value=self.app.project.units.parse(text,'length')
                elif key=='rotation_deg':
                    try:value=float(text)
                    except ValueError:value=math.degrees(self.app.project.units.parse(text,'angle'))
                elif key=='web_direction':value=parse_numeric_sequence(text) if text else None
                else:value=text
            result[key]=value
        return result

    def save(self):
        try:
            if self.app.session is not self.session or self.app.project is not self.draft.project or self.app.session.revision.id!=self.revision:raise ValueError('The model changed; reopen Edit for the current selection.')
            command=self.draft.command(beam=self.patch(self.beam_fields,self.beam_checks),plate=self.patch(self.plate_fields,self.plate_checks),current_project=self.app.project,read_only=self.app.session.read_only)
            if command is not None:self.app.run(command)
            else:self.app.set_status('Selected properties are unchanged.')
            self.accept()
        except Exception as error:self.feedback.setText(str(error))
