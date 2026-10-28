"""Typed option forms backed by the public engineering API defaults."""
import dataclasses
import inspect
from PySide6.QtWidgets import QWidget,QFormLayout,QLineEdit,QCheckBox,QComboBox
from .editors import CommandEditor,title


class OptionForm(QWidget):
    def __init__(self,parameters,parent=None):
        super().__init__(parent);layout=QFormLayout(self);self.fields={}
        parameters=tuple(parameters)
        relief_form=any(name=="inertia_relief" for name,_,_ in parameters)
        layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for name,annotation,default in parameters:
            if name == "constraint_mode" and "StaticConstraintMode" in str(annotation):
                widget=QComboBox()
                for label,value in (("Supported model (transformation)","transformation"),
                                    ("Free-floating (nullspace)","nullspace"),
                                    ("Automatic (supports required)","auto")):
                    widget.addItem(label,value)
                widget.setCurrentIndex(widget.findData(default))
                widget.setToolTip("Nullspace fixes the rigid-motion gauge for static equilibrium. Balanced loads are required unless generalized load balancing is enabled separately.")
            elif str(annotation).startswith("bool") or isinstance(default,bool):
                widget=QCheckBox();widget.setChecked(bool(default))
            else:widget=CommandEditor.make_field(default,str(annotation),name)
            if name == "allow_unbalanced_free_free":
                widget.setToolTip("Applies only to nullspace. Opt in to generalized load balancing: returns gauged displacements and generalized balancing reactions. This is not mass-based inertia relief.")
            elif name == "inertia_relief":
                widget.setToolTip("Use model mass and inertia to calculate rigid-body accelerations and balancing inertial loads. For a connected free-free linear beam/shell model. Selects nullspace; generalized load balancing is disabled.")
            elif name == "reference_point" and relief_form:
                widget.setToolTip("Inertia-relief acceleration reporting point in global coordinates, metres. Empty uses the centre of mass.")
                widget.setPlaceholderText("Centre of mass (default), or x, y, z in metres")
            self.fields[name]=(widget,str(annotation),default)
            label = "Allow generalized load balancing" if name=="allow_unbalanced_free_free" else "Mass-based inertia relief" if name=="inertia_relief" else title(name)
            layout.addRow(label,widget)
        if "inertia_relief" in self.fields:
            self.fields["inertia_relief"][0].toggled.connect(self._inertia_relief_toggled)
            self._sync_inertia_relief()

    def _inertia_relief_toggled(self,enabled):
        if "constraint_mode" in self.fields:
            mode=self.fields["constraint_mode"][0]
            if enabled:self._previous_constraint_mode=mode.currentData()
            elif hasattr(self,"_previous_constraint_mode"):
                mode.setCurrentIndex(mode.findData(self._previous_constraint_mode))
        self._sync_inertia_relief()

    def _sync_inertia_relief(self,*_):
        enabled=self.fields["inertia_relief"][0].isChecked()
        if "constraint_mode" in self.fields:
            mode=self.fields["constraint_mode"][0]
            if enabled:mode.setCurrentIndex(mode.findData("nullspace"))
            mode.setEnabled(not enabled)
        if "allow_unbalanced_free_free" in self.fields:
            balance=self.fields["allow_unbalanced_free_free"][0]
            if enabled:balance.setChecked(False)
            balance.setEnabled(not enabled)
        if "reference_point" in self.fields:self.fields["reference_point"][0].setEnabled(enabled)

    @classmethod
    def record(cls,record,values=None,exclude=()):
        parameters=[]
        for field in dataclasses.fields(record):
            if not field.init or field.name in exclude:continue
            default=(values or {}).get(field.name,field.default)
            if default is dataclasses.MISSING and field.default_factory is not dataclasses.MISSING:default=field.default_factory()
            parameters.append((field.name,field.type,default))
        return cls(parameters)

    def values(self):
        result={}
        for name,(widget,annotation,default) in self.fields.items():
            if name=="reference_point" and "inertia_relief" in self.fields and not self.fields["inertia_relief"][0].isChecked():
                result[name]=None;continue
            if isinstance(widget,QComboBox):result[name]=widget.currentData()
            elif isinstance(widget,QCheckBox):result[name]=widget.isChecked()
            elif widget.text().strip():result[name]=CommandEditor.value(self,widget,annotation,default)
            elif default is inspect.Parameter.empty or default is dataclasses.MISSING:raise ValueError(f"{title(name)} is required")
        return result

    def set_values(self,values):
        if "inertia_relief" in self.fields:
            if "inertia_relief" in values and type(values["inertia_relief"]) is not bool:
                raise ValueError("Inertia relief must be a boolean")
            enabled=values.get("inertia_relief",self.fields["inertia_relief"][0].isChecked())
            if enabled and (values.get("allow_unbalanced_free_free",False) or values.get("constraint_mode")=="transformation"):
                raise ValueError("Inertia relief requires nullspace and disables generalized load balancing")
            if values.get("reference_point") is not None and not enabled:
                raise ValueError("Reference point requires inertia relief")
        items=list(values.items())
        if "inertia_relief" in self.fields and "inertia_relief" in values:
            items.sort(key=lambda item:item[0]!="inertia_relief")
        for name,value in items:
            if name not in self.fields:continue
            widget=self.fields[name][0]
            if isinstance(widget,QComboBox):
                index=widget.findData(value)
                if index < 0:raise ValueError(f"Invalid {title(name)}: {value!r}")
                widget.setCurrentIndex(index)
            elif isinstance(widget,QCheckBox):widget.setChecked(bool(value))
            else:widget.setText("" if value is None else CommandEditor.encoded(value))
        if "inertia_relief" in self.fields:self._sync_inertia_relief()
