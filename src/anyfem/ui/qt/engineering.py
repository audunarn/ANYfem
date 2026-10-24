"""Engineering presets using shared material and boundary-condition APIs."""
import importlib.util
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
        # Standard beam sections come from ANYsection, an optional sibling that is imported lazily
        # so ANYfem starts and works without it.
        self.library_button=QPushButton("Browse section library…");self.library_button.clicked.connect(app.guarded(self.browse_library));layout.addWidget(self.library_button)
        if importlib.util.find_spec("anysection") is None:
            self.library_button.setEnabled(False)
            self.library_button.setToolTip("Install ANYsection (pip install ANYsection[gui]) to browse standard beam sections.")

    def browse_library(self):
        """Pick a standard beam section and load it into the beam form for review."""
        try:from anysection.gui import choose_section
        except ImportError as error:raise ValueError(f"The section library needs ANYsection with PySide6: {error}") from error
        choice=choose_section(parent=self.app)
        return None if choice is None else self.load_library_choice(choice)

    def load_library_choice(self,choice):
        """Fill the Add beam section form from a library choice; nothing is applied here.

        The engineer reviews the values, picks the material and applies, which is one undoable command
        like every other edit.  A material is only suggested when the project has exactly one, because
        guessing between several would be a silent decision.  Dimensions arrive in SI and are shown in
        the project unit profile; the name is qualified by series and standard because catalogue
        designations repeat and ANYfem keys beam sections by name.
        """
        index=self.choice.findData("AddBeamSection")
        if index<0:raise ValueError("Add beam section is unavailable in this task")
        self.choice.setCurrentIndex(index);self.rebuild()
        fields=self.record_fields["section"][1]
        for key,value in choice.anyfem_kwargs().items():
            field=fields[key][0]
            field.setText(self.library_quantity(value,self.dimensions[field]) if field in self.dimensions else self.encoded(value))
        section=choice.section;materials=list(self.app.project.materials)
        if len(materials)==1:fields["material"][0].setText(materials[0])
        notes=[f"Loaded {section.qualified_name} from the section library."]
        if len(materials)!=1:notes.append("Choose the material.")
        if section.profile in {"Angle","L-bulb"}:notes.append("Set the web direction: this profile is asymmetric.")
        if section.derived:notes.append("Equivalent angle of a bulb flat: area and strong-axis inertia match the published section.")
        notes.append("Apply creates one undoable edit.")
        self.feedback.setText(" ".join(notes))
        return section

    def library_quantity(self,value,dimension):
        """Shortest project-unit text that reads back as exactly this SI value.

        Stored values are shown with 17 digits so they round-trip; a catalogue dimension such as 92 mm
        would read 0.091999999999999998 m.  This keeps the same exactness (no rounding) and drops the noise.
        """
        profile=self.app.project.units
        shown=abs(float(profile.to_display(value,dimension)))
        for precision in range(max(3,len(str(int(shown)))),18):
            text=profile.format(value,dimension,precision=precision)
            if profile.parse(text,dimension)==value:return text
        return self.format_quantity(value,dimension,profile)

    def material(self):return dnv_steel_material(self.grade.currentText(),float(self.thickness.text()),nonlinear=self.nonlinear.isChecked())
    def create_material(self):return self.app.run(commands.AddMaterial(self.material()))
    def create_plate(self):
        material=self.material();name=self.section_name.text().strip();previous=self.app.project.plate_sections.get(name)
        options={"id":previous.id} if previous is not None else {}
        return self.app.run(commands.AddPlateSection(PlateSection(name,float(self.thickness.text()),material.name,**options),material))


from .load_panel import LoadTask
