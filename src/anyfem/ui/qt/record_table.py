"""One lazy, live table window for every worktree category."""
import json
from PySide6.QtCore import Qt,QAbstractTableModel,QSortFilterProxyModel,QTimer,QModelIndex
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QTableView,QPushButton,QLineEdit,QLabel,QAbstractItemView,QHeaderView,QComboBox,QWidget
from ...tables import CategoryAdapter,DOFS
from ... import commands


class RecordTableModel(QAbstractTableModel):
    def __init__(self,app,category,parent=None):
        super().__init__(parent);self.app=app;self.bound_project=app.project;self.bound_session=app.session;self.adapter=CategoryAdapter(app.project,category);self.error='';self.refresh()

    def refresh(self):
        self.beginResetModel();self.adapter.refresh();self.rows=self.adapter.rows;self.columns=self.adapter.columns;self.revision=self.app.session.revision.id;self.endResetModel()

    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.columns)
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role==Qt.DisplayRole:
            if orientation==Qt.Vertical:return section+1
            column=self.columns[section];return column.header+(' ('+self.bound_project.units.symbol(column.dimension)+')' if column.dimension else '')
        return None

    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return None
        row=self.rows[index.row()];column=self.columns[index.column()]
        if role==Qt.UserRole:return row.key
        if role==Qt.ToolTipRole:return self.adapter.reason(row,column) or str(row.key)
        value=self.adapter.get(row,column)
        if role==Qt.UserRole+1:return value if isinstance(value,(float,int)) else str(getattr(value,'value',value) or '')
        if role==Qt.CheckStateRole and self.adapter.category=='Supports' and column.key in DOFS:return Qt.Unchecked if value is None else Qt.Checked
        if role in {Qt.DisplayRole,Qt.EditRole}:
            if value is None:return 'Free' if self.adapter.category=='Supports' and column.key in DOFS else ''
            dimension=self.adapter.dimension(row,column)
            if dimension:
                return self.bound_project.units.format(value,dimension,precision=17 if role==Qt.EditRole else 6)
            if isinstance(value,(dict,list,tuple)):return json.dumps(value,default=str)
            return str(getattr(value,'value',value))
        return None

    def flags(self,index):
        flags=Qt.ItemIsSelectable|Qt.ItemIsEnabled
        if index.isValid() and self.app.project is self.bound_project and self.app.session is self.bound_session and not self.app.session.read_only:
            row=self.rows[index.row()];column=self.columns[index.column()]
            if not self.adapter.reason(row,column):
                flags|=Qt.ItemIsEditable
                if self.adapter.category=='Supports' and column.key in DOFS:flags|=Qt.ItemIsUserCheckable
        return flags

    def assert_current(self):
        if self.app.project is not self.bound_project or self.app.session is not self.bound_session:raise ValueError('Project changed; open a new table for the current project')
        if self.app.session.read_only:raise PermissionError('This project is read-only')
        if self.app.session.revision.id!=self.revision:raise ValueError('The model changed; wait for the table to refresh and try again')

    def setData(self,index,value,role=Qt.EditRole):
        if not index.isValid() or role not in {Qt.EditRole,Qt.CheckStateRole}:return False
        try:
            self.assert_current();row=self.rows[index.row()];column=self.columns[index.column()]
            if role==Qt.CheckStateRole:value='0' if value in {Qt.Checked,Qt.Checked.value,True} else ''
            command=self.adapter.edit_command(row.key,column.key,value)
            self.app.run(command);self.error='';self.parent().set_feedback('Updated existing row; Undo restores its previous values')
            return True
        except Exception as error:
            self.error=str(error);self.parent().set_feedback(self.error,error=True);return False


class TableCreateDialog(QDialog):
    def __init__(self,table):
        super().__init__(table);self.table=table;self.setWindowTitle('Add '+table.category.rstrip('s'));self.fields={};self.setMinimumWidth(400)
        layout=QVBoxLayout(self);form=QFormLayout();layout.addLayout(form)
        for column in table.model.adapter.creation_fields:
            choices=list(table.app.project.materials) if column.key=='material' else ['Flatbar','T-bar','Angle','L-bulb'] if column.key=='profile' else ['manual','automatic','centerline'] if column.key=='offset_mode' else None
            if choices is not None:field=QComboBox();field.addItems(choices)
            else:field=QLineEdit('0' if column.dimension or column.key in {'nu','rotation_deg'} else '')
            field.setAccessibleName(column.header);self.fields[column.key]=field
            form.addRow(column.header+(' ('+table.app.project.units.symbol(column.dimension)+')' if column.dimension else ''),field)
        self.feedback=QLabel();self.feedback.setWordWrap(True);layout.addWidget(self.feedback)
        row=QHBoxLayout();self.apply=QPushButton('Create');self.apply.setDefault(True);self.apply.setProperty('primary',True);self.cancel=QPushButton('Cancel');row.addWidget(self.apply);row.addWidget(self.cancel);layout.addLayout(row)
        self.apply.clicked.connect(self.save);self.cancel.clicked.connect(self.reject)

    def save(self):
        try:
            self.table.model.assert_current();values={}
            for column in self.table.model.adapter.creation_fields:
                field=self.fields[column.key];text=field.currentText() if isinstance(field,QComboBox) else field.text()
                values[column.key]=self.table.app.project.units.parse(text,column.dimension) if column.dimension else float(text) if column.key in {'nu','rotation_deg'} else text.strip()
            command=self.table.model.adapter.create_command(values);self.table.app.run(command);self.accept()
        except Exception as error:self.feedback.setText(str(error))


class RecordTableDialog(QDialog):
    def __init__(self,app,category):
        super().__init__(app);self.app=app;self.category=category;self.setWindowTitle(category+' — table');self.resize(1050,560);self.setModal(False)
        layout=QVBoxLayout(self);bar=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('Filter rows…');self.search.setClearButtonEnabled(True);bar.addWidget(self.search)
        self.add_button=QPushButton('Add');self.edit_button=QPushButton('Edit / source settings');self.delete_button=QPushButton('Delete selected')
        from .navigation import navigation_icon
        for button,icon in ((self.add_button,'AddPoint'),(self.edit_button,'EditAttribute'),(self.delete_button,'DeleteAttribute')):button.setIcon(navigation_icon(icon));bar.addWidget(button)
        if category=='Sets':
            self.sets_tools_button=QPushButton('Sets tools…');bar.addWidget(self.sets_tools_button)
            self.sets_tools_button.clicked.connect(self.open_sets_tools)
        if category=='Load cases':
            self.visualize_button=QPushButton('Visualize on mesh');self.visualize_button.setObjectName('VisualizeLoadCaseOnMesh');bar.addWidget(self.visualize_button)
            self.visualize_button.setToolTip('Colour the active mesh by the selected case or series step (preview only, no solve). Series definitions use the step chosen in Loads & BC.')
            self.visualize_button.clicked.connect(self.visualize_on_mesh)
            from PySide6.QtWidgets import QSpinBox
            self.visualize_step=QSpinBox();self.visualize_step.setObjectName('LoadCaseStepNumber');self.visualize_step.setAccessibleName('Load case step number');self.visualize_step.setRange(1,1);self.visualize_step.setEnabled(False)
            self.visualize_label=QLabel('Select a load case or series step');self.visualize_label.setObjectName('LoadCaseStepSelection')
            bar.addWidget(QLabel('Step'));bar.addWidget(self.visualize_step);bar.addWidget(self.visualize_label)
            self.visualize_step.valueChanged.connect(lambda *_:self.update_visualize_selector())
        layout.addLayout(bar);self.view=QTableView();self.model=RecordTableModel(app,category,self);self.proxy=QSortFilterProxyModel(self);self.proxy.setSourceModel(self.model);self.proxy.setSortRole(Qt.UserRole+1);self.proxy.setFilterKeyColumn(-1);self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive);self.view.setModel(self.proxy);self.view.setSortingEnabled(True);self.view.setSelectionBehavior(QAbstractItemView.SelectRows);self.view.setSelectionMode(QAbstractItemView.ExtendedSelection);self.view.setAlternatingRowColors(True);self.view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive);self.view.horizontalHeader().setDefaultSectionSize(145);self.view.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.view)
        self.count=QLabel();layout.addWidget(self.count);self.feedback=QLabel('Double-click an input cell to edit. Identity and derived fields are read-only; use source settings.');self.feedback.setWordWrap(True);layout.addWidget(self.feedback)
        self.search.textChanged.connect(self.proxy.setFilterFixedString);self.proxy.rowsInserted.connect(self.update_count);self.proxy.rowsRemoved.connect(self.update_count);self.proxy.modelReset.connect(self.update_count);self.search.textChanged.connect(self.update_count)
        self.add_button.clicked.connect(self.add_record);self.edit_button.clicked.connect(self.edit_record);self.delete_button.clicked.connect(self.delete_records);self.view.selectionModel().selectionChanged.connect(self.highlight)
        if category=='Load cases':self.view.selectionModel().selectionChanged.connect(lambda *_:self.update_visualize_selector(reset=True))
        self._pending=False;self._active=True;self._stack=app.commands;self._workbench=app.workbench;self._stack.add_listener(self.schedule_refresh);self._workbench.add_listener(self.workbench_event);self.update_count()

    def set_feedback(self,message,error=False):self.feedback.setText(str(message));self.feedback.setProperty('error',bool(error))
    def update_count(self,*_):self.count.setText(f'{self.proxy.rowCount()} shown / {self.model.rowCount()} total')
    def selected_rows(self):return [self.model.rows[self.proxy.mapToSource(index).row()] for index in self.view.selectionModel().selectedRows()]

    def schedule_refresh(self,*_):
        if self._pending or not self._active:return
        self._pending=True;QTimer.singleShot(0,self,self.refresh)

    def refresh(self):
        self._pending=False
        if not self._active:return
        if self.app.project is not self.model.bound_project or self.app.session is not self.model.bound_session:
            self.set_feedback('Project changed; reopen this table for the current project',True);self.add_button.setEnabled(False);self.delete_button.setEnabled(False);return
        keys={row.key for row in self.selected_rows()};self.view.selectionModel().blockSignals(True);self.model.refresh();self.update_count()
        from PySide6.QtCore import QItemSelectionModel
        for i,row in enumerate(self.model.rows):
            if row.key in keys:
                index=self.proxy.mapFromSource(self.model.index(i,0));self.view.selectionModel().select(index,QItemSelectionModel.Select|QItemSelectionModel.Rows)

        self.view.selectionModel().blockSignals(False);self.highlight()
        if self.category=='Load cases':self.update_visualize_selector(reset=False)

    def workbench_event(self,event):
        if event.kind=='project_replaced':self.schedule_refresh()

    def highlight(self,*_):
        targets=tuple(dict.fromkeys(ref for row in self.selected_rows() for ref in self.model.adapter.targets(row)))
        self.app.viewport.set_collection_highlight(targets)

    def add_record(self):
        try:
            self.model.assert_current()
            if self.category=='Mesh sizes':
                from .mesh_size_editor import MeshSizeDialog
                return MeshSizeDialog(self.app).exec()
            if self.model.adapter.creation_fields:return TableCreateDialog(self).exec()
            self.open_creation_workspace()
        except Exception as error:self.set_feedback(error,True)

    def open_creation_workspace(self):
        category=self.category
        routes={'Lines':('Geometry','AddLine'),'Plates':('Geometry','AddPlate'),'Beams':('Geometry','AddBeamMember'),'Features':('Geometry','AddFeature'),'Supports':('Loads & BC','AddSupport'),'Masses':('Loads & BC','AddMass'),'Loads':('Loads & BC','AddPointLoad'),'Wet surfaces':('Loads & BC','AddWetSurface'),'Connections':('Loads & BC','AddConnection'),'Imperfections':('Sections','AddImperfection'),'Coordinate systems':('Definitions','AddCoordinateSystem'),'Output requests':('Definitions','AddOutputRequest')}
        if category=='Beams':
            refs=tuple(ref for ref in self.app.selection.items if getattr(ref,'kind',None)=='edge')
            if not refs:raise ValueError('Select the line axes for the new beam, then choose Add. Create axes in Geometry first if needed.')
            return self.app.geometry_actions.assign_beams(refs)
        elif category=='Construction planes':self.app._open_plane_construction()
        elif category=='Sketches':self.app.notebook.select('Construction');self.app.panels['Construction'].start_sketch_workflow()
        elif category=='Features':self.app.notebook.select('Generators')
        elif category=='Sets':return self.app.geometry_actions.new_set(refs=())

        elif category in {'Meshes'}:self.app.notebook.select('Mesh')
        elif category in {'Analyses','Jobs','Results'}:self.app.notebook.select('Solve')
        else:
            page,operation=routes[category];panel=self.app.panels[page]
            if page=='Loads & BC':panel.cancel_edit()
            index=panel.choice.findData(operation)
            if index<0:
                self.app.notebook.select(page);self.app.set_status('Review '+category.lower()+' creation settings in '+page+'. No operation was executed.');self.hide();return
            panel.choice.setCurrentIndex(index);panel.rebuild();self.app.notebook.select(page)
        self.app.set_status(category+': review inputs and Apply in the open workspace. No meshing, solver or job starts automatically.');self.hide()

    def update_visualize_selector(self,reset=False):
        """Own step-number selector: a series definition exposes its steps, a step row selects itself."""
        from ...presentation.mesh_pressure_preview import resolve_preview_selection,MeshPressurePreviewError
        rows=self.selected_rows()
        if len(rows)!=1:
            self.visualize_step.setEnabled(False);self.visualize_label.setText('Select one load case or series step');return
        name=rows[0].record.name;project=self.app.project
        definition=project.load_cases.get(name);count=len(project.series_children(name)) if definition is not None and definition.times is not None else 0
        blocked=self.visualize_step.blockSignals(True)
        self.visualize_step.setRange(1,max(count,1));self.visualize_step.setEnabled(count>1)
        if reset:self.visualize_step.setValue(1)
        self.visualize_step.blockSignals(blocked)
        try:self.visualize_label.setText(resolve_preview_selection(project,name,self.visualize_step.value() if count else None).label())
        except MeshPressurePreviewError as error:self.visualize_label.setText(str(error))

    def visualize_on_mesh(self):
        rows=self.selected_rows()
        if len(rows)!=1:return self.set_feedback('Select one load case or series step row to visualize on the mesh',True)
        try:
            self.model.assert_current();name=rows[0].record.name
            self.update_visualize_selector()
            number=self.visualize_step.value() if self.visualize_step.isEnabled() else None
            selection=self.app.show_pressure_preview(name,number)
            self.set_feedback('Previewing '+selection.label()+' on the mesh (not a result; no solve)')
        except Exception as error:
            self.set_feedback(error,True);self.app.set_status(f'Mesh pressure preview refused: {error}',error=True)

    def open_sets_tools(self):
        self.app.notebook.select('Definitions');self.app.panels['Definitions'].region_name.setFocus()
        self.app.set_status('Sets: create from geometry or mesh selection, or combine existing sets.');self.hide()

    def edit_record(self):
        rows=self.selected_rows()
        if len(rows)!=1:return self.set_feedback('Select one row to inspect or edit',True)
        try:
            self.model.assert_current();row=rows[0]
            from anygeometry import EntityRef
            if isinstance(row.value,EntityRef):
                if row.value.kind=='vertex':
                    xyz=self.app.project.geometry.vertex_position(row.key);self.app.panels['Geometry'].load_values('MovePoint',vertex_id=row.key,x=xyz[0],y=xyz[1],z=xyz[2]);self.app.notebook.select('Geometry')
                else:self.app.geometry_actions.properties((row.value,))
            elif self.category in {'Meshes','Analyses','Jobs','Results'}:self.app.notebook.select('Mesh' if self.category=='Meshes' else 'Solve')
            elif self.category in {'Load cases'}:self.app.notebook.select('Definitions');self.set_feedback('Identity fields are read-only. Use Definitions to create a replacement or manage dependencies.')
            else:self.app.tree.edit_record(row.value)
        except Exception as error:self.set_feedback(error,True)

    def delete_records(self):
        try:
            self.model.assert_current();rows=self.selected_rows()
            if not rows:raise ValueError('Select rows to delete')
            children=tuple(self.model.adapter.delete_command(row) for row in rows)
            self.app.run(commands.CompositeCommand(children,'delete table rows'))
        except Exception as error:self.set_feedback(error,True)

    def done(self,result):
        self._active=False;self._stack.remove_listener(self.schedule_refresh);self._workbench.remove_listener(self.workbench_event)
        windows=getattr(self.app.tree,'_table_windows',{})
        if windows.get(self.category) is self:windows.pop(self.category,None)
        super().done(result);self.deleteLater()
