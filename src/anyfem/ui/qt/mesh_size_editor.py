"""Unit-aware modal editor for reusable local mesh-size properties."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QLineEdit,QLabel,QPushButton,QHBoxLayout
from ...mesh_sizes import MeshSizeProperty,SetMeshSizeProperty


class MeshSizeDialog(QDialog):
    def __init__(self,app,definition=None):
        super().__init__(app);self.app=app;self.definition=definition;self.project=app.project;self.session=app.session;self.units=app.project.units
        self.setWindowTitle('Edit mesh size' if definition else 'New mesh size');self.setMinimumWidth(380)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        self.name=QLineEdit(definition.name if definition else 'Mesh size')
        self.size=QLineEdit(self.units.format(definition.size,'length',precision=17) if definition else '')
        self.radius=QLineEdit(self.units.format(definition.radius if definition else 0.,'length',precision=17))
        self.growth=QLineEdit(str(definition.growth if definition else 1.5))
        for label,field in [('Name',self.name),('Target element size ('+self.units.symbol('length')+')',self.size),('Radius ('+self.units.symbol('length')+')',self.radius),('Growth',self.growth)]:field.setAccessibleName(label);form.addRow(label,field)
        self.size.setAccessibleName('Mesh size');self.name.setAccessibleName('Mesh size name')
        help_=QLabel('Local target must not exceed the global target when one is set. The mesher may adjust element sizes for geometry and adjoining controls. Creating an unused property does not change the mesh.');help_.setWordWrap(True);layout.addWidget(help_)
        row=QHBoxLayout();self.apply=QPushButton('Save changes' if definition else 'Create');self.primary=self.apply;self.apply.setProperty('primary',True);self.apply.setDefault(True);self.cancel=QPushButton('Cancel');row.addWidget(self.apply);row.addWidget(self.cancel);layout.addLayout(row)
        self.cancel.clicked.connect(self.reject);self.apply.clicked.connect(app.guarded(self.save));self.apply.setEnabled(not app.session.read_only)

    def save(self):
        if self.app.session is not self.session or self.app.project is not self.project or self.app.project.units!=self.units:raise ValueError('The project or units changed; reopen the mesh size editor')
        if self.session.read_only:raise PermissionError('This project is read-only')
        if self.definition is not None and self.project.mesh_size_properties.get(self.definition.id) is not self.definition:raise ValueError('This mesh size changed; reopen its editor')
        options={} if self.definition is None else {'id':self.definition.id}
        definition=MeshSizeProperty(self.name.text(),self.units.parse(self.size.text(),'length'),self.units.parse(self.radius.text(),'length'),float(self.growth.text()),**options)
        self.app.run(SetMeshSizeProperty(definition));self.accept();return definition
