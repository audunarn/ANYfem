"""Structural feature generators with typed forms and persistent intent."""
import inspect
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QWidget,QVBoxLayout,QComboBox,QPushButton,QLabel
from ... import commands
from .forms import OptionForm
from .editors import apply_on_enter


class GeneratorTask(QWidget):
    def __init__(self,app):
        super().__init__();self.app=app;self.form=None
        self.layout_=QVBoxLayout(self);self.layout_.setAlignment(Qt.AlignTop);self.kind=QComboBox()
        self.kind.addItems(["Plate","Bulkhead","Frame","Girder","Stiffener","Stiffened panel","Cylinder","Cone"])
        self.layout_.addWidget(self.kind)
        guidance=QLabel("Dimensions are in metres; directions define the local generator frame.")
        guidance.setWordWrap(True);self.layout_.addWidget(guidance)
        self.kind.currentTextChanged.connect(self.rebuild);self.rebuild()
        self.apply=QPushButton("Create editable feature");self.apply.setProperty("primary",True)
        self.apply.clicked.connect(app.guarded(self.execute));self.layout_.addWidget(self.apply)
        self.kind.installEventFilter(self);self.apply.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.MouseButtonPress,QEvent.KeyPress):self.app._begin_modelling()
        if hasattr(self,"apply") and apply_on_enter(watched,event,self.apply):return True
        return super().eventFilter(watched,event)

    def rebuild(self,*_):
        if self.form is not None:self.layout_.removeWidget(self.form);self.form.deleteLater()
        kind=self.kind.currentText();operation={"Stiffened panel":commands.AddStiffenedPanel,"Cylinder":commands.AddCylinder,"Cone":commands.AddCone}.get(kind)
        if operation is not None:
            parameters=[(name,p.annotation,p.default) for name,p in inspect.signature(operation).parameters.items() if p.kind not in (p.VAR_KEYWORD,p.VAR_POSITIONAL)]
        else:
            parameters=[("length","float",1.0),("origin","Sequence[float]",(0,0,0))]
            if kind in {"Girder","Stiffener"}:parameters.append(("direction","Sequence[float]",(1,0,0)))
            else:parameters.extend([("width","float",1.0),("u_direction","Sequence[float]",(1,0,0)),("v_direction","Sequence[float]",(0,1,0)),("semantic_group","str",kind.lower())])
        self.form=OptionForm(parameters);self.layout_.insertWidget(2,self.form)
        for field,_,_ in self.form.fields.values():field.installEventFilter(self)

    def execute(self):
        kind=self.kind.currentText();values=self.form.values()
        operation={"Stiffened panel":commands.AddStiffenedPanel,"Cylinder":commands.AddCylinder,"Cone":commands.AddCone}.get(kind)
        command=operation(**values) if operation is not None else commands.AddFeature("generator."+kind.lower(),name=kind,parameters=values,label="add "+kind.lower())
        return self.app.run(command)

    def refresh(self):pass
