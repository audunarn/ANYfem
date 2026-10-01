"""Engineering presets using shared material and boundary-condition APIs."""
from PySide6.QtWidgets import QFormLayout,QComboBox,QLineEdit,QCheckBox,QPushButton
from anymaterial import available_grades
from ... import commands
from ...model import attributes
from ...model.materials import dnv_steel_material
from ...model.sections import PlateSection
from .editors import CommandEditor


class SectionTask(CommandEditor):
    def __init__(self,app):
        super().__init__(app,["AddMaterial","AddPlateSection","AddBeamSection","AssignPlate","AssignBeam","AddImperfection","AddCoordinateSystem","AddRegion"])
        layout=self.layout();form=QFormLayout();layout.addLayout(form)
        self.grade=QComboBox();self.grade.addItems(list(available_grades()));self.grade.setCurrentText("S355")
        self.thickness=QLineEdit("0.01");self.section_name=QLineEdit("plate")
        self.nonlinear=QCheckBox("DNV RP-C208 nonlinear hardening");self.nonlinear.setChecked(True)
        form.addRow("DNV grade",self.grade);form.addRow("DNV plate thickness (m)",self.thickness);form.addRow("Plate section name",self.section_name);layout.addWidget(self.nonlinear)
        for label,callback in [("Create DNV material",self.create_material),("Create plate section with DNV material",self.create_plate)]:
            button=QPushButton(label);button.clicked.connect(app.guarded(callback));layout.addWidget(button)

    def material(self):return dnv_steel_material(self.grade.currentText(),float(self.thickness.text()),nonlinear=self.nonlinear.isChecked())
    def create_material(self):return self.app.run(commands.AddMaterial(self.material()))
    def create_plate(self):
        material=self.material();name=self.section_name.text().strip();previous=self.app.project.plate_sections.get(name)
        options={"id":previous.id} if previous is not None else {}
        return self.app.run(commands.AddPlateSection(PlateSection(name,float(self.thickness.text()),material.name,**options),material))


class LoadTask(CommandEditor):
    SELECTION_KINDS={commands.AddPointLoad:"vertex",commands.AddPressure:"face",
        commands.AddLineLoad:"edge",commands.AddSurfaceTraction:"face",commands.AddMass:None}
    def __init__(self,app):
        super().__init__(app,["AddPointLoad","AddPressure","AddSupport","AddLineLoad","AddSurfaceTraction","AddMass","SetAcceleration","SetFollowerPressure","AddLoadCase","DeleteLoadCase","AddCombination","EditAttribute","DeleteAttribute"])
        form=QFormLayout();self.layout().addLayout(form)
        self.case=QComboBox();form.addRow("Active load case",self.case)
        self.case.currentTextChanged.connect(self.case_changed)
        self.preset=QComboBox();self.preset.addItems(["fixed","pinned","simply_supported","symmetry","antisymmetry"]);form.addRow("Support preset",self.preset)
        self.normal=QLineEdit("z");form.addRow("Symmetry normal (x/y/z)",self.normal)
        self.dof=QComboBox();self.dof.addItems(["ux","uy","uz"]);form.addRow("Simply supported component",self.dof)
        button=QPushButton("Apply support preset to selection");button.clicked.connect(app.guarded(self.support_preset));self.layout().addWidget(button)

    def support_preset(self):
        refs=list(self.app.selection.ordered_items)
        if not refs:raise ValueError("Select geometry for the support preset")
        kind=self.preset.currentText();options={}
        if kind in {"symmetry","antisymmetry"}:options["normal"]=self.normal.text()
        elif kind=="simply_supported":options["normal"]=self.dof.currentText()
        self.app.run_many(commands.AddSupport(getattr(attributes,kind)(ref,**options)) for ref in refs)

    def execute(self):
        refs=tuple(self.app.selection.ordered_items)
        if (self.operation not in self.SELECTION_KINDS or len(refs)<2
                or self.fields["ref"][0].text().strip()):
            return super().execute()
        kind=self.SELECTION_KINDS[self.operation]
        if self.app.selection.domain.value!="geometry" or (kind is not None and any(ref.kind!=kind for ref in refs)):
            raise ValueError(f"Select {kind or 'geometry'} entities for this load")
        options=self.command_options({"ref":refs[0]})
        if options.get("distribution_policy")=="total_distributed":
            if self.operation is commands.AddPointLoad:
                for key in ("force","moment"):options[key]=tuple(value/len(refs) for value in options[key])
            elif self.operation is commands.AddMass:options["value"]/=len(refs)
        result=self.app.run_many(self.operation(**dict(options,ref=ref)) for ref in refs)
        self.feedback.setText(f"Applied to {len(refs)} selected entities")
        return result

    def refresh(self):
        super().refresh()
        current=self.case.currentText();choices=list(self.app.project.load_cases)
        if choices!=[self.case.itemText(i) for i in range(self.case.count())]:
            self.case.clear();self.case.addItems(choices);self.case.setCurrentText(current if current in choices else "default")

    def case_name(self):return self.case.currentText() or "default"

    def case_changed(self,value):
        if "case" in self.fields:self.fields["case"][0].setText(value)

    def rebuild(self):
        super().rebuild()
        if hasattr(self,"case"):self.case_changed(self.case_name())
