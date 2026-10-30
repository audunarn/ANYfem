"""Command-backed Qt editors. Model changes always enter the shared command stack."""
from __future__ import annotations

import dataclasses
import inspect
import json
import re

from PySide6.QtCore import Qt, QEvent, QSignalBlocker
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QComboBox, QFormLayout, QLabel, QLineEdit, QCheckBox,
    QApplication, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ... import commands
from anyloads import Envelope
from ...model.attributes import PressureTable, Support
from ...model.materials import MaterialSpec
from ...model.sections import PlateSection, BeamSection
from ...model.coordinates import CoordinateSystem
from ...model.imperfections import Imperfection
from ...model.records import OutputRequest
from ...model.regions import region_from_dict, RegionRef
from ...model.units import UnitProfile
from ..numeric_input import numeric_sequence_annotation, parse_numeric_sequence
from anygeometry import SketchDefinition


from .widgets import ChoiceEdit


class UserSetChoiceEdit(ChoiceEdit):
    """Offer visible sets while retaining an existing internal assignment."""
    def __init__(self,choices,default=None):
        self._preserved=None
        super().__init__(choices,default)

    def setText(self,value):
        value=str(value)
        index=self.findData(value)
        if index<0 and value:
            self._preserved=value
            index=self.findData(None)
            if index<0:self.addItem('Existing assignment (unchanged)',None);index=self.count()-1
            self.setCurrentIndex(index)
        else:
            self.setCurrentIndex(index)

    def text(self):
        return self._preserved or '' if self.currentData() is None else super().text()


class PressureHistoryEdit(QLineEdit):
    """Opaque immutable history, never a truncated editable JSON payload."""
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setToolTip('Retained pressure history is preserved unchanged. Use Hydrodynamic wave load to edit its record/window.')
        self.set_history(None)

    def set_history(self, value):
        from anyloads.pressure_history import MappedPressureHistory, pressure_history_from_dict
        from anyloads.spectral_pressure_history import SpectralMappedPressureHistory
        from anyloads.source_pressure_history import SourceAwarePressureHistory
        if isinstance(value,dict):value=pressure_history_from_dict(value)
        from anyloads.wave_surface_pressure import WaveSurfacePressureHistory
        if value is not None and not isinstance(value,(MappedPressureHistory,SpectralMappedPressureHistory,SourceAwarePressureHistory,
                WaveSurfacePressureHistory)):
            raise ValueError('pressure history must be a supported immutable history record')
        self.history=value
        if value is None:summary='No retained pressure history'
        elif isinstance(value,WaveSurfacePressureHistory):
            summary=(f'Wave-following pressure (hydrostatic included): {len(value.times)} samples, '
                f'{len(value.element_ids)} elements, {len(value.omegas)} frequencies; {value.content_sha256[:16]}')
        else:
            times=value.times
            identity=getattr(value,'content_sha256','')
            summary=f'{type(value).__name__}: {len(times)} samples, {len(value.element_ids)} elements; {identity[:16]}'
        super().setText(summary)

    def setText(self,value):
        raise ValueError('retained pressure history cannot be replaced by text; use set_history with a typed record')


class CodeEdit(QPlainTextEdit):
    """A monospace multi-line field that reads and writes like a QLineEdit."""

    def __init__(self):
        super().__init__()
        self.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.setTabChangesFocus(False)
        self.setMinimumHeight(150)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def text(self):return self.toPlainText()
    def setText(self,value):self.setPlainText(value)


PRESSURE_CODE_HINT="\n".join((
    "def pressure(case, x, y, z):",
    "    # case: load case number; x, y, z: element centre in metres.",
    "    # Optional arguments: t (case time), name, parameters.",
    "    return 1025 * 9.81 * max(0.0, 2.0 - z)",
))


def apply_on_enter(watched, event, button):
    """Send an unmodified Enter through the operation's normal button route."""
    if (event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and event.modifiers() in (Qt.NoModifier, Qt.KeypadModifier)
            and QApplication.activePopupWidget() is None
            and (watched is button or isinstance(watched, (QLineEdit, QCheckBox, QComboBox)))):
        if not event.isAutoRepeat():button.click()
        event.accept()
        return True
    return False


def title(name):
    if name == "frame_indices":return "Frame indices (zero based)"
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ").capitalize()


def reference(app, text):
    """Resolve explicit or selected project-bound topology; never guess coordinates."""
    if not text.strip():
        refs = app.selection.ordered_items
        if len(refs) != 1:
            raise ValueError("Select one entity or enter its type and ID, for example vertex:1")
        return refs[0]
    kind, identifier = text.strip().split(":", 1)
    if kind=="group":
        if app.imported is None:raise ValueError("Group references require an imported model")
        return app.imported.groups[identifier]
    if kind in {"node","element","element_face"}:
        from ...selection import MeshEntityRef
        value=parse_numeric_sequence(identifier,int) if kind=="element_face" else int(identifier)
        return MeshEntityRef(kind,value)
    return app.project.geometry.entity_ref(kind, int(identifier))


class CommandEditor(QWidget):
    """Readable forms for public commands, including nested engineering records."""
    RECORD_TYPES = {"MaterialSpec": MaterialSpec, "PlateSection": PlateSection,
                    "BeamSection": BeamSection, "Support": Support,
                    "CoordinateSystem": CoordinateSystem, "Imperfection": Imperfection}

    def __init__(self, app, names, parent=None):
        super().__init__(parent)
        self.app = app
        layout = QVBoxLayout(self)
        self.choice = QComboBox()
        for name in names:
            if hasattr(commands,name):
                self.choice.addItem(title(name), name)
        layout.addWidget(self.choice)
        from .widgets import FormHost
        self.scroll = FormHost()
        layout.addWidget(self.scroll)
        self.guidance=QLabel()
        self.guidance.setObjectName("Hint");self.guidance.setWordWrap(True)
        self.guidance.setToolTip(self.GUIDANCE)
        layout.addWidget(self.guidance)
        self.feedback = QLabel()
        self.feedback.setObjectName("Feedback")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.apply = QPushButton("Apply")
        self.apply.setProperty("primary",True)
        self.apply.clicked.connect(app.guarded(self.execute))
        layout.addWidget(self.apply)
        self.choice.installEventFilter(self)
        self.apply.installEventFilter(self)
        self.choice.currentIndexChanged.connect(self.rebuild)
        self.rebuild()

    def eventFilter(self, watched, event):
        if apply_on_enter(watched, event, self.apply):
            return True
        return super().eventFilter(watched, event)

    def rebuild(self):
        self.loaded_ids={};self.optional_records={}
        self.dimensions={}
        self.fields = {}
        self.record_fields = {}
        self.operation = getattr(commands, self.choice.currentData())
        container = QWidget()
        form = QFormLayout(container)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.signature = inspect.signature(self.operation)
        for name, parameter in self.signature.parameters.items():
            if name in {"label","id"} or parameter.kind in {parameter.VAR_POSITIONAL,parameter.VAR_KEYWORD}:
                continue
            annotation = str(parameter.annotation).strip("'")
            record = next((t for key,t in self.RECORD_TYPES.items() if annotation.startswith(key)),None)
            if name=="replacement":record=getattr(self,"replacement_type",None)
            if record:
                form.addRow(QLabel(title(name)))
                if "None" in annotation:
                    enabled=QCheckBox("Include "+title(name));form.addRow(enabled)
                    self.optional_records[name]=enabled
                fields={}
                for f in dataclasses.fields(record):
                    if not f.init or f.name == "id":
                        continue
                    default = f.default
                    if default is dataclasses.MISSING and f.default_factory is not dataclasses.MISSING:
                        default=f.default_factory()
                    field=self.choice_field(f.name,default,str(f.type)) or self.make_field(default, str(f.type), f.name,record.__name__)
                    self.unit_field(field,default,f.name,record.__name__)
                    form.addRow("Set" if "RegionRef" in str(f.type) else title(f.name),field)
                    fields[f.name]=(field,str(f.type),default)
                self.record_fields[name]=(record,fields)
            else:
                field=self.choice_field(name,parameter.default,annotation) or self.make_field(parameter.default,annotation,name,self.operation.__name__)
                self.unit_field(field,parameter.default,name,self.operation.__name__)
                form.addRow(self.FIELD_LABELS.get(name,title(name)),field)
                self.fields[name]=(field,annotation,parameter.default)
        previous=self.scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        self.scroll.setWidget(container)
        for field in container.findChildren(QWidget):
            field.installEventFilter(self)
        self.feedback.clear()
        self.update_guidance()

    GUIDANCE=("Engineering quantities use the project unit profile; explicit unit suffixes are accepted. "
              "Numeric lists accept commas or spaces and optional brackets. Entity IDs follow the model tree.")
    FIELD_LABELS={"ref":"Target","refs":"Targets","coordinate_system_id":"Coordinate system","distribution_policy":"Distribution",
                  "gravity_coordinate_system_id":"Gravity coordinate system"}
    DISTRIBUTION_CHOICES=(("per_target","Full value on each target"),("total_distributed","Total value shared by targets"))

    def choice_field(self,name,default,annotation=""):
        """Readable drop-downs for closed or project-defined values."""
        if "RegionRef" in annotation:
            return UserSetChoiceEdit(self.set_choices(),default.id if isinstance(default,RegionRef) else None)
        from .widgets import ChoiceEdit
        if name=="distribution_policy":
            return ChoiceEdit(self.DISTRIBUTION_CHOICES,default if isinstance(default,str) else None)
        if name.endswith("coordinate_system_id"):
            return ChoiceEdit(self.coordinate_choices(),default if isinstance(default,str) else None)
        return None

    def set_choices(self):
        return [(region.id,region.name) for region in self.app.project.regions if not region.hidden]

    def coordinate_choices(self):
        systems=getattr(self.app.project,"coordinate_systems",{})
        return [("global","Global")]+[(key,str(system.name)) for key,system in systems.items() if key!="global"]

    def update_guidance(self):
        """One short unit line, shown only when the form has engineering quantities."""
        if not self.dimensions:
            self.guidance.hide();return
        units=self.app.project.units
        symbols={}
        for dimension in ("length","force","pressure"):
            try:symbols[dimension]=units.symbol(dimension)
            except Exception:pass
        example="250 mm" if symbols.get("length")!="mm" else "0.25 m"
        symbols=list(symbols.values())
        self.guidance.setText(f"Units: {', '.join(symbols)}. Type a unit to override, e.g. {example}.")
        self.guidance.show()

    @staticmethod
    def make_field(default, annotation, name, owner=None):
        if name=='history' and ('PressureHistory' in annotation or owner in ('WetPressure','AddWetPressure')):
            field=PressureHistoryEdit()
            if default is not None and default is not inspect.Parameter.empty and default is not dataclasses.MISSING and repr(default)!="<factory>":
                field.set_history(default)
            return field
        field=CodeEdit() if name=="code" else QLineEdit()
        missing=default is inspect.Parameter.empty or default is dataclasses.MISSING or repr(default)=="<factory>"
        if not missing and default is not None:
            field.setText(default if isinstance(default,str) else json.dumps(default,default=lambda value:value.tolist() if hasattr(value,"tolist") else str(value)))
        if name=="code":
            field.setPlaceholderText(PRESSURE_CODE_HINT)
        elif "Envelope" in annotation:
            field.setPlaceholderText('When it acts, e.g. {"start": 20, "stop": 300, "by": "number", "expression": "sin(omega * t)"}; empty = always')
        elif "EntityRef" in annotation:
            field.setPlaceholderText("Selection, vertex:1 / edge:1 / face:1, or group:name")
        elif numeric_sequence_annotation(annotation):
            field.setPlaceholderText("Numbers separated by commas or spaces; brackets are optional")
        elif "Sequence" in annotation:
            field.setPlaceholderText("Comma-separated values")
        elif "Mapping" in annotation or "Dict" in annotation or "dict" in annotation:
            field.setPlaceholderText('For example {"ux": 0, "uy": 0, "uz": 0}' if name=="constraints"
                                     else 'JSON object, for example {"name": value}')
        elif missing:
            field.setPlaceholderText("Required")
        return field

    def value(self, field, annotation, default):
        if isinstance(field,PressureHistoryEdit):
            if field.history is not None:return field.history
            if default is None or 'None' in annotation or 'Optional' in annotation:return None
            raise ValueError('A required pressure history is empty')
        text=field.text().strip()
        if text=="null" and ("None" in annotation or "Optional" in annotation):return None
        if annotation.startswith("RegionRef"):
            return RegionRef(text.removeprefix("region:"))
        if text.startswith("region:") and "RegionRef" in annotation:return RegionRef(text.split(":",1)[1])
        if "EntityRef" in annotation and any(word in annotation for word in ("Sequence", "List", "list", "tuple")):
            if not text:return tuple(self.app.selection.ordered_items)
            values=json.loads(text) if text.startswith("[") else text.split(",")
            return tuple(reference(self.app,value) for value in values)
        if "EntityRef" in annotation:
            return reference(self.app,text)
        if not text:
            if default is None or "None" in annotation:
                return None
            if default is not inspect.Parameter.empty and default is not dataclasses.MISSING and repr(default)!="<factory>":
                return default
            raise ValueError("A required field is empty")
        dimension=self.dimensions.get(field) if hasattr(self,"dimensions") else None
        if dimension is not None:
            return self.parse_quantity(text,annotation,dimension,self.app.project.units)
        if annotation.startswith("str") or annotation.startswith("Literal") or annotation=="<class 'str'>" or annotation.startswith("Optional[str]"):
            return text
        if annotation.startswith("UnitProfile") and not text.startswith("{"):
            return text
        if annotation.startswith("float") or annotation=="<class 'float'>":
            return float(text)
        if annotation.startswith("int") or annotation=="<class 'int'>":
            return int(text)
        if annotation.startswith("bool"):
            if text.casefold() not in {"true","false","1","0"}:
                raise ValueError("Boolean fields accept true or false")
            return text.casefold() in {"true","1"}
        numeric_type=numeric_sequence_annotation(annotation)
        if numeric_type:
            return parse_numeric_sequence(text, int if numeric_type=="int" else float)
        if any(word in annotation for word in ("Sequence","Tuple","tuple[","list[","ndarray")):
            if "str" in annotation and not text.startswith("["):
                return tuple(value.strip() for value in text.split(",") if value.strip())
            if not text.startswith("[") and not text.startswith("("):
                text="["+text+"]"
        try:
            value=json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"Enter a valid value: {text}") from None
        converters={"SketchDefinition":SketchDefinition.from_parameters,
                    "Region":region_from_dict,"OutputRequest":OutputRequest.from_dict,
                    "UnitProfile":UnitProfile.from_dict,
                    "PressureTable":lambda data:PressureTable(**data),
                    "Envelope":Envelope.from_dict}
        converter=converters.get(annotation.split(" | ")[0])
        return converter(value) if converter is not None else value

    @staticmethod
    def dimension(name,owner):
        if name in {"plane_point", "axis_point", "spacing"}:return "length"
        if name=="vector" and owner=="Extrude":return "length"
        if name in {"x","y","z","origin","centre","center","position","translation","reference_point","offset","eccentricity","radius","radius_start","radius_end","height","length","width","thickness","web_height","web_thickness","flange_width","flange_thickness","longitudinal_spacing","ring_spacing","transverse_spacing","extrusion","amplitude","target_size"}:return "length"
        if name in {"angle","angle_step"}:return "angle"
        if name=="rotation_deg":return "angle_degrees"
        if name=="force":return "force"
        if name=="moment":return "moment"
        if name=="force_per_length":return "line_load"
        if name in {"traction","force_per_area"}:return "pressure"
        if name=="constraints":return "constraints"
        if name=="value":return {"AddPressure":"pressure","Pressure":"pressure","AddWetPressure":"pressure","AddMass":"mass","Mass":"mass"}.get(owner)
        if name=="acceleration":return "acceleration"
        if name=="density":return "density"
        if name=="yield_stress":return "pressure"
        return None

    @staticmethod
    def format_quantity(value,dimension,profile):
        import numpy as np
        if dimension=="constraints":return json.dumps({key:profile.format(number,"length" if key.startswith("u") else "angle",precision=17) for key,number in value.items()})
        if dimension=="angle_degrees":value=np.deg2rad(value);dimension="angle"
        if isinstance(value,(list,tuple,np.ndarray)):return ", ".join(profile.format(float(number),dimension,precision=17) for number in value)
        return profile.format(float(value),dimension,precision=17)

    @staticmethod
    def parse_quantity(text,annotation,dimension,profile):
        import numpy as np
        if dimension=="constraints":return {key:profile.parse(value,"length" if key.startswith("u") else "angle") for key,value in json.loads(text).items()}
        actual="angle" if dimension=="angle_degrees" else dimension
        if numeric_sequence_annotation(annotation) or "ndarray" in annotation:
            result=parse_numeric_sequence(text,lambda value:profile.parse(value,actual),units=True)
        else:result=profile.parse(text,actual)
        return float(np.rad2deg(result)) if dimension=="angle_degrees" else result

    def unit_field(self,field,value,name,owner):
        dimension=self.dimension(name,owner)
        if dimension is None:return
        self.dimensions[field]=dimension
        missing=value is inspect.Parameter.empty or value is dataclasses.MISSING or repr(value)=="<factory>"
        if not missing and value is not None:field.setText(self.format_quantity(value,dimension,self.app.project.units))

    def command_options(self,overrides=None):
        overrides=overrides or {}
        options={}
        for name,(field,annotation,default) in self.fields.items():
            if name in overrides:
                options[name]=overrides[name];continue
            if (not field.text().strip() or isinstance(field,PressureHistoryEdit) and field.history is None) and repr(default)=="<factory>":
                continue
            options[name]=self.value(field,annotation,default)
        if self.operation in {commands.AddFeature,commands.EditFeature} and options.get("inputs") is not None:
            from anygeometry import FeatureOutputRef
            def input_ref(value):
                if isinstance(value,str):return reference(self.app,value)
                if isinstance(value,dict):
                    if "entity" in value:return reference(self.app,":".join(map(str,value["entity"])))
                    if "feature" in value:return FeatureOutputRef(value["feature"],value["output"],value["kind"])
                    if "feature_id" in value:return FeatureOutputRef(**value)
                    return reference(self.app,f"{value['kind']}:{value['id']}")
                return value
            options["inputs"]={key:tuple(input_ref(value) for value in refs) for key,refs in options["inputs"].items()}
        for name,(record,fields) in self.record_fields.items():
            if name in self.optional_records and not self.optional_records[name].isChecked():
                options[name]=None;continue
            values={key:self.value(field,annotation,default) for key,(field,annotation,default) in fields.items()}
            if name in self.loaded_ids:values["id"]=self.loaded_ids[name]
            options[name]=record(**values)
        return options

    def execute(self):
        if self.operation is commands.MeasureGeometry:
            self.app._select_workspace(list(self.app.panels).index("Measure"))
            return
        options=self.command_options()
        command=self.operation(**options)
        result=self.app.commands.query(command) if self.operation is commands.MeasureGeometry else self.app.run(command)
        self.feedback.setText(f"Applied {title(self.operation.__name__)}"+(f": {result}" if isinstance(result,(int,str)) else ""))
        return result

    @staticmethod
    def encoded(value):
        if isinstance(value,RegionRef):return value.id
        if hasattr(value,"kind") and hasattr(value,"id"):return f"{value.kind}:{value.id}"
        if isinstance(value,str):return value
        if hasattr(value,"to_dict"):value=value.to_dict()
        return json.dumps(value,default=lambda item:item.tolist() if hasattr(item,"tolist") else dataclasses.asdict(item) if dataclasses.is_dataclass(item) else str(item))

    def load_values(self,operation,**values):
        index=self.choice.findData(operation)
        if index<0:raise ValueError(f"{operation} is unavailable in this task")
        replacement=values.get("replacement")
        self.replacement_type=type(replacement) if replacement is not None else None
        self.choice.setCurrentIndex(index);self.rebuild()
        for name,value in values.items():
            if name in self.record_fields:
                if name in self.optional_records:self.optional_records[name].setChecked(value is not None)
                if value is None:continue
                if hasattr(value,"id"):self.loaded_ids[name]=value.id
                for key,(field,*_) in self.record_fields[name][1].items():
                    number=getattr(value,key)
                    if isinstance(field,PressureHistoryEdit):
                        field.set_history(number);continue
                    field.setText(self.format_quantity(number,self.dimensions[field],self.app.project.units) if field in self.dimensions and number is not None else self.encoded(number))
                    if key=="name":field.setReadOnly(True)
            elif name in self.fields:
                field=self.fields[name][0]
                if isinstance(field,PressureHistoryEdit):
                    field.set_history(value);continue
                field.setText(self.format_quantity(value,self.dimensions[field],self.app.project.units) if field in self.dimensions and value is not None else self.encoded(value))
        self.feedback.setText("Loaded existing values. Apply creates one undoable edit.")

    def refresh(self):
        self.apply.setEnabled((self.operation is commands.MeasureGeometry or not self.app.session.read_only) and not self.app._closing)
        from .widgets import ChoiceEdit
        for name,(field,_,_) in self.fields.items():
            if name.endswith("coordinate_system_id") and isinstance(field,ChoiceEdit):
                field.set_choices(self.coordinate_choices())
        for _,fields in self.record_fields.values():
            for field,annotation,_ in fields.values():
                if isinstance(field,UserSetChoiceEdit):field.set_choices(self.set_choices())
        for field,annotation,_ in self.fields.values():
            if isinstance(field,UserSetChoiceEdit):field.set_choices(self.set_choices())
        self.update_guidance()

    def case_name(self):
        field=self.fields.get("case")
        return field[0].text() if field else "default"


class GeometryTask(CommandEditor):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.MouseButtonPress, QEvent.KeyPress) and self.operation is not commands.MeasureGeometry:
            self.app._begin_modelling()
        return super().eventFilter(watched, event)

    def __init__(self,app,names):
        super().__init__(app,names)
        labels={
            "AddLine":"Add line / beam axis",
            "AddPlate":"Add plate from points",
            "AddFace":"Add face from boundary edges",
            "AddPolyline":"Add polyline",
            "AddCylinder":"Add cylinder",
            "AddCone":"Add cone",
            "AddStiffenedPanel":"Add stiffened panel",
            "AddConstructionPlane":"Add construction plane",
            "MeasureGeometry":"Measure selected geometry",
            "Loft":"Loft between profiles",
        }
        for operation,label in labels.items():
            index=self.choice.findData(operation)
            if index>=0:self.choice.setItemText(index,label)
        self.operation_heading=QLabel("Choose an operation, then complete the fields or follow its canvas instructions.")
        self.operation_heading.setWordWrap(True);self.operation_heading.setObjectName("Hint")
        self.layout().insertWidget(0,self.operation_heading)
        app.selection.add_listener(self.populate_selection)
        self.populate_selection()
        self.construct_plate = QPushButton("Construct plate by clicking points")
        self.construct_plate.setObjectName("ConstructPlateByPoints")
        self.construct_plate.setToolTip("Click the boundary vertices in order, then click the first vertex again to finish.")
        self.construct_plate.clicked.connect(app.guarded(lambda: app.geometry_actions.start_plate()))
        self.layout().insertWidget(self.layout().indexOf(self.apply), self.construct_plate)
        self.construct_plate.setVisible(self.choice.currentData() == "AddPlate")
        from .widgets import Collapsible
        checks=Collapsible("Checks",hint="Geometry checks that do not change the model.")
        button=QPushButton("Check selected plates for mapped meshing")
        button.clicked.connect(app.guarded(self.check_mappable));checks.addWidget(button)
        self.layout().addWidget(checks);self.checks=checks

    def rebuild(self):
        super().rebuild()
        if self.operation is commands.MeasureGeometry:
            previous = self.scroll.takeWidget()
            previous.deleteLater()
            self.fields = {}; self.record_fields = {}
            self.scroll.setWidget(QWidget())
            self.apply.setText("Open Measure workspace")
            self.apply.setEnabled(True)
            self.feedback.setText("Measure selected geometry in the dedicated Measure workspace.")
            if hasattr(self, "construct_plate"):
                self.construct_plate.setVisible(False)
            return
        if self.operation is commands.Loft:
            previous = self.scroll.takeWidget()
            previous.deleteLater()
            self.fields = {}; self.record_fields = {}
            self.scroll.setWidget(QWidget())
            self.apply.setText("Construct")
            self.feedback.setText("Select exactly two lines, profile faces or sketches, then Construct. Matching is automatic.")
            if hasattr(self, "construct_plate"):
                self.construct_plate.setVisible(False)
            return
        self.apply.setText("Start sketch" if self.operation is commands.AddSketch else "Apply")
        if self.operation is commands.AddSketch:
            # Sketch intent is drawn in the canvas, never entered as a JSON record.
            previous=self.scroll.takeWidget()
            previous.deleteLater()
            self.fields={};self.record_fields={}
            container=QWidget();form=QFormLayout(container)
            self.sketch_support=QComboBox()
            self.sketch_support.addItem("Construction plane", "plane")
            self.sketch_support.addItem("Selected flat face", "face")
            refs=self.app.selection.ordered_items
            if len(refs)==1 and refs[0].kind=="face":self.sketch_support.setCurrentIndex(1)
            form.addRow("Sketch on",self.sketch_support)
            self.sketch_plane=QComboBox()
            self.sketch_plane.addItem("XY (Global)","global")
            self.sketch_plane.addItem("XZ","sketch-xz");self.sketch_plane.addItem("YZ","sketch-yz")
            for identifier,system in self.app.project.coordinate_systems.items():
                if identifier!="global":self.sketch_plane.addItem(system.name,identifier)
            construction=getattr(self.app,"panels",{}).get("Construction")
            if construction is not None:
                selected=self.sketch_plane.findData(construction.system.currentData())
                if selected>=0:self.sketch_plane.setCurrentIndex(selected)
            preferred=getattr(self,"_created_plane",None)
            if preferred in self.app.project.coordinate_systems:
                self.sketch_plane.setCurrentIndex(self.sketch_plane.findData(preferred))
                self.sketch_support.setCurrentIndex(0)
            form.addRow("Plane",self.sketch_plane)
            self.sketch_offset=QLineEdit("0");form.addRow(f"Plane offset ({self.app.project.units.symbol('length')})",self.sketch_offset)
            def support_changed():
                enabled=self.sketch_support.currentData()=="plane"
                self.sketch_plane.setEnabled(enabled);self.sketch_offset.setEnabled(enabled)
            self.sketch_support.currentIndexChanged.connect(support_changed);support_changed()
            guidance=QLabel("Start sketch opens the sketcher on this support and turns the view toward the plane. Click profile points, then Finish sketch. Custom planes can be created in Construction.")
            guidance.setWordWrap(True);form.addRow(guidance)
            for control in (self.sketch_support,self.sketch_plane,self.sketch_offset):control.installEventFilter(self)
            self.scroll.setWidget(container)
        if self.operation is not commands.AddLine:
            self.app.viewport.cancel_line_construction()
        if self.operation in {commands.AddPoint, commands.MovePoint}:
            form = self.scroll.widget().layout()
            self.point_input_mode = QCheckBox("Enter X, Y, Z together")
            self.point_input_mode.setObjectName("CombinedPointInputMode")
            self.point_coordinates = QLineEdit()
            self.point_coordinates.setObjectName("CombinedPointCoordinates")
            self.point_coordinates.setPlaceholderText("For example: 1 2 3 or (1, 2, 3)")
            self.point_coordinates.setToolTip("Enter three coordinates. Each may have an explicit length unit.")
            values = [self.fields[name][0].text() for name in ("x", "y", "z")]
            self.point_coordinates.setText(", ".join(values) if all(values[:2]) else "")
            form.insertRow(0, self.point_input_mode)
            form.insertRow(1, "Coordinates (X, Y, Z)", self.point_coordinates)
            self.point_input_mode.setChecked(getattr(self, "_point_combined_mode", False))
            self._show_point_input_mode(self.point_input_mode.isChecked())
            self.point_input_mode.toggled.connect(self._switch_point_input_mode)
            self.point_input_mode.installEventFilter(self)
            self.point_coordinates.installEventFilter(self)
        self._selection_signature = None
        self.populate_selection()
        help_text = {
            "AddPoint": "Enter coordinates and apply to add one point.",
            "AddLine": "Click an existing start point and then an end point in the canvas to create one line (beam axis). You can also enter point IDs below.",
            "AddFace": "Closed boundary edge IDs create a geometry face. Assign material and thickness separately.",
            "AddPlate": "Boundary-ordered vertex IDs create boundary lines and a geometry face. Review the order; assign material and thickness separately.",
            "AddSketch": "Choose a construction plane or one selected flat face, then start the interactive sketcher.",
            "AddConstructionPlane": "Create a named plane from an origin, normal and in-plane X direction. Origin uses project length units; directions are unitless. The plane is shown in the canvas and becomes available to Add sketch.",
        }.get(self.choice.currentData(), "IDs are filled from matching selected geometry. Explicit edits remain until selection changes.")
        self.feedback.setText(help_text)
        if self.operation is commands.AddSketch:
            self.apply.setToolTip("Start an interactive sketch on the chosen support. Finish sketch saves one undoable model edit; Escape cancels.")
        else:self.apply.setToolTip(f"Apply {self.choice.currentText()} using the entered values as an undoable model edit. {help_text}")
        if hasattr(self, "construct_plate"):
            self.construct_plate.setVisible(self.choice.currentData() == "AddPlate")

    def execute(self):
        if self.operation is commands.Loft:
            from .loft import construct_selected_loft
            return construct_selected_loft(self.app)
        if self.operation is commands.AddConstructionPlane:
            if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
            system=super().execute()
            self.app.panels["Construction"].select_plane(system)
            self._created_plane=system.id
            self.app.set_status(f"Created construction plane {system.name}. Choose Add sketch to draw on it.")
            return system
        if self.operation is commands.AddSketch:
            panel=self.app.panels["Construction"]
            if self.app.viewport.construction_active:raise ValueError("Finish or cancel the current construction first")
            panel.support_choice.setCurrentIndex(panel.support_choice.findData(self.sketch_support.currentData()))
            panel.refresh()
            panel.system.setCurrentIndex(panel.system.findData(self.sketch_plane.currentData()))
            panel.offset.setText(self.sketch_offset.text())
            # Selecting a face must be validated before changing workspace.
            panel.start_sketch_workflow()
            self.app.notebook.select("Construction")
            return
        return super().execute()

    def _show_point_input_mode(self, combined):
        form = self.scroll.widget().layout()
        form.setRowVisible(self.point_coordinates, combined)
        for name in ("x", "y", "z"):
            form.setRowVisible(self.fields[name][0], not combined)

    def _combined_point_values(self):
        values = parse_numeric_sequence(
            self.point_coordinates.text(),
            lambda value: self.app.project.units.parse(value, "length"),
            units=True,
        )
        if len(values) != 3:
            raise ValueError("Enter exactly three coordinates (X, Y, Z)")
        return values

    def _switch_point_input_mode(self, combined):
        if combined:
            values = [self.fields[name][0].text().strip() for name in ("x", "y", "z")]
            self.point_coordinates.setText(", ".join(values) if any(values[:2]) else "")
        elif self.point_coordinates.text().strip():
            try:
                values = self._combined_point_values()
            except ValueError as error:
                with QSignalBlocker(self.point_input_mode):
                    self.point_input_mode.setChecked(True)
                self.feedback.setText(f"Cannot switch input mode: {error}")
                self.point_coordinates.setFocus()
                return
            for name, value in zip(("x", "y", "z"), values):
                self.fields[name][0].setText(self.format_quantity(value, "length", self.app.project.units))
        self._point_combined_mode = combined
        self._show_point_input_mode(combined)
        self.feedback.clear()

    def command_options(self, overrides=None):
        if self.operation in {commands.AddPoint, commands.MovePoint} and self.point_input_mode.isChecked():
            coordinates = dict(zip(("x", "y", "z"), self._combined_point_values()))
            coordinates.update(overrides or {})
            overrides = coordinates
        return super().command_options(overrides)

    def populate_selection(self):
        refs = tuple(self.app.selection.ordered_items)
        signature = tuple((ref.kind, ref.id) for ref in refs)
        if signature == getattr(self, "_selection_signature", None):return
        self._selection_signature = signature
        from anygeometry.entities import EntityRef
        for name, (field, _, _) in self.fields.items():
            kind = {"vertex_ids":"vertex", "edge_ids":"edge", "face_ids":"face"}.get(name)
            if kind is None:continue
            ids = [ref.id for ref in refs if isinstance(ref, EntityRef) and ref.kind == kind]
            field.setText(", ".join(map(str, ids)))
        vertex_ids = [ref.id for ref in refs if isinstance(ref, EntityRef) and ref.kind == "vertex"]
        names = {"AddLine": ("start", "end"), "AddArc": ("start", "via", "end")}.get(self.choice.currentData(), ())
        for index, name in enumerate(names):
            self.fields[name][0].setText(str(vertex_ids[index]) if index < len(vertex_ids) else "")
        for name, (field, _, _) in self.fields.items():
            kind = {"vertex_id":"vertex", "edge_id":"edge", "face_id":"face"}.get(name)
            if kind:
                ids = [ref.id for ref in refs if isinstance(ref, EntityRef) and ref.kind == kind]
                field.setText(str(ids[0]) if len(ids) == 1 else "")

    def check_mappable(self):
        from anymesher.decomposition import check_mappable
        refs=self.app.selection.ordered_items
        if not refs or any(ref.kind!="face" for ref in refs):raise ValueError("Select plates to check")
        reports=[check_mappable(self.app.project.geometry,ref.id) for ref in refs]
        bad=[report for report in reports if not report.ok]
        self.feedback.setText(str(bad[0]) if bad else f"{len(reports)} plate(s): all mappable")
        return reports
