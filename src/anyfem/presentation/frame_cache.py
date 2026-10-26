"""Owned temporary, disk-backed prepared frames; never runs a solver or a GUI."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace,MethodType
import gzip,json,pickle,shutil,sys,tempfile,threading,traceback


def new_directory():
    return Path(tempfile.mkdtemp(prefix='anyfem-playback-')).resolve()


def remove_directory(directory):
    directory=Path(directory).resolve();root=Path(tempfile.gettempdir()).resolve()
    if directory.parent!=root or not directory.name.startswith('anyfem-playback-'):
        raise ValueError('refusing cleanup outside owned playback cache')
    shutil.rmtree(directory,ignore_errors=True)


def _dump(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    with gzip.open(temporary,'wb',compresslevel=1) as stream:pickle.dump(value,stream,protocol=5)
    temporary.replace(path)


def _load(path):
    # Files are created only by this application's own temporary worker.
    with gzip.open(path,'rb') as stream:return pickle.load(stream)


def _progress(directory,**values):
    path=directory/'progress.json';temporary=directory/'progress.tmp'
    temporary.write_text(json.dumps(values),encoding='utf-8');temporary.replace(path)


def write_request(directory,project,mesh,dataset,policy,options,frames,cancel):
    from ..io.project_file import project_to_dict
    directory=Path(directory)
    try:
        if cancel.is_set():return None
        payload=dict(project=project_to_dict(project),mesh=mesh,path=str(dataset.path),identity=dataset.identity,
                     policy=policy,options=options,frames=tuple(frames))
        _dump(directory/'request.pkl.gz',payload)
        return str(directory/'request.pkl.gz')
    finally:
        if cancel.is_set():remove_directory(directory)


def prepare(request_path):
    """Child process entry: canonical scene construction and retained preparation."""
    from ..io.project_file import project_from_dict
    from ..io.artifacts import LazyResultDataset
    from ..post.access import LiveResultAccess,result_access_scope
    from ..ui.scene import build_persisted_result_scene,build_attribute_overlay
    from .viewport import SceneViewport
    from .frame_updates import prepare_frame
    from any3dview.selection import PickOwner,PickBinding
    request_path=Path(request_path);directory=request_path.parent
    data=_load(request_path);dataset=None;access=None
    try:
        dataset=LazyResultDataset(data['path']);dataset.validate();dataset.prepare_metadata()
        if dataset.identity!=data['identity']:raise ValueError('retained source changed before preprocessing')
        project=project_from_dict(data['project']);mesh=data['mesh'];options=data['options']
        view=SimpleNamespace(_visualization=data['policy']['visualization'],
            visualization=data['policy']['visualization'],_commercial_selection=data['policy']['commercial'],
            _selection_api={'PickOwner':PickOwner,'PickBinding':PickBinding})
        view._face_style=MethodType(SceneViewport._face_style,view)
        view._pick_binding=MethodType(SceneViewport._pick_binding,view)
        layout=None;written=set();total=len(data['frames']);access=LiveResultAccess()
        with result_access_scope(access):
            for completed,index in enumerate(data['frames'],1):
                scene=build_persisted_result_scene(project,mesh,dataset,options['field'],frame=index,
                    component=options['component'],scale=options['scale'],limits=options['limits'],
                    colormap=options['colormap'],display_units=options['units'],
                    show_nodes=options['show_nodes'],show_beam_sections=options['show_beam_sections'])
                if options['attributes']:
                    scene.merge(build_attribute_overlay(project,case_name=options['case'],mesh=mesh,**options['attribute_options']))
                prepared=prepare_frame(view,scene,layout)
                if prepared is None:raise ValueError('this result does not provide retained GPU geometry')
                layout=scene._retained_frame_layout;key=layout.topology.hex()
                if key not in written:
                    _dump(directory/f'layout-{key}.pkl.gz',(prepared[1],prepared[3],prepared[4]))
                    written.add(key)
                positions=[batch.positions for batch,_ in prepared[1]]
                scalars=[(batch.element_scalars,batch.node_scalars) for batch,_ in prepared[1]]
                # Keep the canonical scene for picking, appearance and later
                # renderer changes, but store the mesh/topology only once.
                scene.result_mesh=None
                scene.__dict__.pop('_prepared_result_batches',None)
                scene.__dict__.pop('_retained_frame_layout',None)
                payload=(scene,key,prepared[0],positions,scalars,prepared[2])
                if completed==1:
                    estimate=len(pickle.dumps(payload,protocol=5))*total
                    if shutil.disk_usage(directory).free<estimate:
                        raise ValueError('Not enough temporary disk space; choose a shorter frame interval')
                _dump(directory/f'frame-{index}.pkl.gz',payload)
                _progress(directory,completed=completed,total=total,ready=False)
        _progress(directory,completed=total,total=total,ready=True,frames=data['frames'])
    except BaseException as error:
        _progress(directory,error=f'{type(error).__name__}: {error}',ready=False)
        traceback.print_exc();return 1
    finally:
        if dataset is not None:dataset.close()
        if access is not None:access.close()
    return 0


class PreparedFrames:
    def __init__(self,directory,mesh,context,frames):
        self.directory=Path(directory);self.mesh=mesh;self.context=context;self.frames=tuple(frames)
        self._layout=None;self._base=None;self.lock=threading.Lock()
    def load(self,index,cancel):
        with self.lock:return self._load_frame(index,cancel)
    def _load_frame(self,index,cancel):
        if index not in self.frames:raise ValueError('frame outside prepared interval')
        if cancel.is_set():
            from anypost.errors import RequestCancelled
            raise RequestCancelled()
        scene,key,digest,positions,scalars,remaining=_load(self.directory/f'frame-{index}.pkl.gz')
        if key!=self._layout:
            self._base=_load(self.directory/f'layout-{key}.pkl.gz');self._layout=key
        bases,edge_bases,node_maps=self._base;batches=[];edges=[];edge_index=0
        for (base,options),points,values in zip(bases,positions,scalars):
            batches.append((replace(base,positions=points,element_scalars=values[0],node_scalars=values[1]),
                            dict(options,scalar_range=scene.result_scalar_range,colormap=scene.result_colormap)))
            if 'opacity' in options:
                edges.append(replace(edge_bases[edge_index],positions=points));edge_index+=1
        scene.result_mesh=self.mesh
        scene._prepared_result_batches=(digest,batches,remaining,edges,node_maps)
        scene._retained_topology_key=(str(self.directory),key)
        return scene


if __name__=='__main__':
    raise SystemExit(prepare(sys.argv[1]))
