"""Qt model/view representation of project records and persistent entity references."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QTreeView,QHeaderView,QMenu,QLineEdit,QVBoxLayout,QWidget
from anygeometry import feature_entity_owners
from ...presentation.tree import bounded_entity_ids, bounded_unowned_entity_ids


class QtModelTree(QTreeView):
    def __init__(self, app):
        super().__init__(app)
        self.app=app
        self.project=app.project
        self.exploded_feature_ids=frozenset()
        self.query=""
        self._model=QStandardItemModel()
        self._model.setHorizontalHeaderLabels(["Model", "State"])
        self.setModel(self._model)
        self.header().setSectionResizeMode(0,QHeaderView.Stretch)
        self.header().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.setSelectionMode(self.SelectionMode.ExtendedSelection)
        self.selectionModel().selectionChanged.connect(self._picked)
        self.doubleClicked.connect(self._activated)
        self._refreshing=False
        self._groups={}
        self._rows={}
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

    def generated_entity_owners(self):
        return feature_entity_owners(self.project.geometry)

    def reset_feature_exposure(self):
        self.exploded_feature_ids=frozenset()

    def search(self,text):
        self.query=text
        self.refresh()

    def panel(self):
        panel=QWidget(self.app);layout=QVBoxLayout(panel)
        search=QLineEdit(panel);search.setPlaceholderText("Find topology by entity ID")
        search.textChanged.connect(self.search)
        layout.addWidget(search);layout.addWidget(self)
        return panel

    def refresh(self):
        self._refreshing=True
        try:
            self.project=self.app.project
            geometry=self.project.geometry
            owners=self.generated_entity_owners()
            owned={kind:set() for kind in ("vertex","edge","face")}
            generated={}
            for ref,feature_id in owners.items():
                owned[ref.kind].add(ref.id)
                generated.setdefault(feature_id,{}).setdefault(ref.kind,[]).append(ref.id)
            for label,kind,items in [("Points","vertex",geometry.vertices),("Lines","edge",geometry.edges),("Plates","face",geometry.faces)]:
                self._sync_group(label,[(identifier,f"{kind.capitalize()} {identifier}","",geometry.entity_ref(kind,identifier)) for identifier in bounded_unowned_entity_ids(items,owned[kind],self.query)])
            for label,attribute in [("Features","geometry"),("Materials","materials"),("Plate sections","plate_sections"),("Beam sections","beam_sections"),("Load cases","load_cases"),("Supports","supports"),("Masses","masses"),("Imperfections","imperfections"),("Coordinate systems","coordinate_systems"),("Regions","regions"),("Output requests","output_requests"),("Meshes","mesh_records"),("Analyses","analyses"),("Jobs","jobs")]:
                values=getattr(self.project,attribute,{})
                if attribute=="geometry":
                    history=getattr(geometry,"features",None)
                    values={} if history is None else {record.feature_id:record for record in history.records}
                pairs=values.items() if hasattr(values,"items") else enumerate(values)
                rows=[]
                for key,value in pairs:
                    key=getattr(value,"id",key)
                    if attribute=="materials":key=self.project.material_ids.get(value.name,key)
                    state=getattr(value,"status","")
                    status=str(getattr(state,"value",state))
                    if attribute=="jobs" and self.app._job_is_stale(value):
                        status += " (stale)"
                    rows.append((key,str(getattr(value,"name",key)),status,(attribute,key)))
                self._sync_group(label,rows)
                if attribute=="geometry":
                    for feature_id,(item,_) in self._rows[label].items():
                        item.removeRows(0,item.rowCount())
                        if feature_id not in self.exploded_feature_ids:continue
                        for kind,identifiers in generated.get(feature_id,{}).items():
                            branch=QStandardItem(f"Generated {kind}s ({len(identifiers)})");branch.setEditable(False)
                            item.appendRow(branch)
                            for identifier in bounded_entity_ids(sorted(identifiers),self.query):
                                child=QStandardItem(f"{kind.capitalize()} {identifier}");child.setEditable(False)
                                child.setData(geometry.entity_ref(kind,identifier),Qt.UserRole);branch.appendRow(child)
            loads=[]
            for case in self.project.load_cases.values():
                for collection in ("point_loads","pressures","line_loads","surface_tractions"):
                    for value in getattr(case,collection,()):loads.append((value.id,f"{case.name}: {type(value).__name__}","",("load",value.id)))
            self._sync_group("Loads",loads)
            self._sync_group("Results",[(job.result_artifact_id,job.name,"stale" if self.app._job_is_stale(job) else "",("result",job.result_artifact_id)) for job in self.project.jobs.values() if job.result_artifact_id])
            self.sync_from_selection()
        finally:
            self._refreshing=False

    def _sync_group(self,label,rows):
        parent=self._groups.get(label)
        if parent is None:
            parent=QStandardItem(label);parent.setEditable(False)
            self._model.appendRow(parent);self._groups[label]=parent
            self._rows[label]={}
        existing=self._rows[label];wanted={key for key,*_ in rows}
        for key in tuple(existing):
            if key not in wanted:
                parent.removeRow(existing[key][0].row());existing.pop(key)
        for key,text,state,data in rows:
            pair=existing.get(key)
            if pair is None:
                item=QStandardItem(text);item.setEditable(False)
                badge=QStandardItem(state);badge.setEditable(False)
                item.setData(data,Qt.UserRole);parent.appendRow([item,badge])
                existing[key]=(item,badge)
            else:
                item,badge=pair
                if item.text()!=text:item.setText(text)
                if badge.text()!=state:badge.setText(state)
                if item.data(Qt.UserRole)!=data:item.setData(data,Qt.UserRole)

    def _picked(self,*_):
        if self._refreshing:
            return
        refs=[index.data(Qt.UserRole) for index in self.selectionModel().selectedRows()]
        refs=[ref for ref in refs if hasattr(ref,"kind") and hasattr(ref,"id")]
        self._tree_driven=True
        try:
            if refs and len({ref.kind for ref in refs})==1:self.app.selection.set_mode(refs[0].kind)
            self.app.selection.clear()
            for ref in refs:
                if ref.kind==self.app.selection.mode:self.app.selection.select(ref,extend=True)
        finally:self._tree_driven=False

    def _activated(self,index):
        value=index.data(Qt.UserRole)
        if isinstance(value,(tuple,list)) and value[0]=="jobs":
            self.app.guarded(lambda:self.app.panels["Results"].activate_job(value[1]))()
        elif isinstance(value,(tuple,list)) and value[0]=="result":
            job=next(job for job in self.project.jobs.values() if job.result_artifact_id==value[1])
            self.app.guarded(lambda:self.app.panels["Results"].activate_job(job.id))()
        else:self.app.guarded(lambda:self.edit_record(value))()

    @staticmethod
    def item_key(value):
        if hasattr(value,"kind"):return f"{value.kind}:{value.id}"
        if not isinstance(value,(tuple,list)):return None
        attribute,key=value
        prefix={"geometry":"feature","materials":"material","plate_sections":"plate_section","beam_sections":"beam_section","load_cases":"case","supports":"support","masses":"mass","imperfections":"imperfection","coordinate_systems":"coordinate","regions":"region","mesh_records":"mesh","analyses":"analysis","jobs":"job","output_requests":"output_request"}.get(attribute,attribute)
        return f"{prefix}:{key}"

    def _context_menu(self,position):
        index=self.indexAt(position)
        if not index.isValid():return
        if not self.selectionModel().isSelected(index):self.setCurrentIndex(index)
        values=[item.data(Qt.UserRole) for item in self.selectionModel().selectedRows()]
        keys=tuple(key for value in values if (key:=self.item_key(value)))
        if not keys:return
        menu=QMenu(self)
        menu.addAction("Edit / inspect",lambda:self.app.guarded(lambda:self.edit_record(index.siblingAtColumn(0).data(Qt.UserRole)))())
        menu.addAction("Delete",lambda:self.app.guarded(lambda:self.app._delete_tree_items(keys))())
        if all(key.startswith("feature:") for key in keys):
            for label,action in [("Expose / collapse topology","explode"),("Suppress / resume","suppress"),("Rename","rename"),("Dependencies","dependencies")]:
                menu.addAction(label,lambda checked=False,action=action:self.app.guarded(lambda:self.app._tree_action(action,keys))())
        menu.exec(self.viewport().mapToGlobal(position))

    def toggle_feature_topology(self,identifiers):
        identifiers=set(identifiers)
        for identifier in identifiers:self.project.geometry.features.get(identifier)
        exposing=not identifiers.issubset(self.exploded_feature_ids)
        self.exploded_feature_ids=frozenset(set(self.exploded_feature_ids)|identifiers if exposing else set(self.exploded_feature_ids)-identifiers)
        self.refresh()
        return exposing

    def edit_record(self,value):
        if not isinstance(value,(tuple,list)):return
        attribute,key=value
        if attribute=="geometry":
            if self.app.panels["Construction"].edit_sketch(int(key)):return
            record=self.project.geometry.features.get(int(key));panel=self.app.panels["Geometry"]
            panel.load_values("EditFeature",feature_id=record.feature_id,name=record.name,parameters=record.parameters,inputs=record.inputs,dependencies=record.dependencies)
            page="Geometry"
        elif attribute in {"materials","plate_sections","beam_sections"}:
            values=getattr(self.project,attribute)
            record=next(record for name,record in values.items() if getattr(record,"id",self.project.material_ids.get(name,name))==key)
            operation={"materials":"AddMaterial","plate_sections":"AddPlateSection","beam_sections":"AddBeamSection"}[attribute]
            self.app.panels["Sections"].load_values(operation,**{"material" if attribute=="materials" else "section":record});page="Sections"
        elif attribute in {"supports","masses","imperfections","load"}:
            from ...commands import _attribute_container
            record=_attribute_container(self.project,str(key))[3]
            self.app.panels["Loads & BC"].load_values("EditAttribute",replacement=record);page="Loads & BC"
        elif attribute=="output_requests":
            self.app.panels["Definitions"].load_values("EditOutputRequest",request_id=key,replacement=self.project.output_requests[key]);page="Definitions"
        elif attribute=="analyses":
            record=self.project.analyses[key];panel=self.app.panels["Solve"]
            import json
            display={"linear_static":"Linear static","batch_linear_static":"Batch linear static","modal":"Modal","buckling":"Buckling","nonlinear_static":"Nonlinear static","arc_length":"Arc length","transient":"Transient","impact":"Impact","capacity":"Capacity"}.get(record.type,record.type)
            if panel.analysis.findText(display)<0:raise ValueError(f"Analysis type {record.type} is unavailable")
            panel.analysis.setCurrentText(display);panel.options.setPlainText(json.dumps(record.settings));page="Solve"
        else:
            self.app.set_status(f"{attribute.replace('_',' ')}: {key}");return
        self.app.notebook.select(page)

    def sync_from_selection(self):
        if getattr(self,"_tree_driven",False):return
        from PySide6.QtCore import QItemSelectionModel
        prior=self._refreshing;self._refreshing=True
        try:
            records=[index for index in self.selectionModel().selectedRows() if isinstance(index.data(Qt.UserRole),(tuple,list))]
            self.clearSelection()
            for index in records:self.selectionModel().select(index,QItemSelectionModel.Select|QItemSelectionModel.Rows)
            refs=set(self.app.selection.items)
            def descendants(parent):
                for child in range(parent.rowCount()):
                    item=parent.child(child)
                    yield item
                    yield from descendants(item)
            for row in range(self._model.rowCount()):
                for item in descendants(self._model.item(row)):
                    ref=item.data(Qt.UserRole)
                    if hasattr(ref,"kind") and ref in refs:
                        self.selectionModel().select(item.index(),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        finally:
            self._refreshing=prior

    def refresh_job_states(self):
        self.refresh()

    def refresh_mesh_states(self):
        self.refresh()
