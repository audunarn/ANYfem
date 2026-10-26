"""Cancellation-safe animation export from the production result publication route."""
from pathlib import Path
from threading import Event
import os
import tempfile
import zipfile
from PIL import Image


def encode_frames(directory,destination,kind,fps,cancel,encoder=None):
    """Prepare a complete output privately and publish only if still authorized."""
    destination=Path(destination);directory=Path(directory)
    frames=sorted(directory.glob('frame-*.png'))
    if not frames:raise ValueError('No captured frames to export')
    fd,temp=tempfile.mkstemp(prefix='.'+destination.name,suffix=destination.suffix,dir=destination.parent)
    os.close(fd)
    try:
        if kind=='PNG sequence ZIP':
            with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_STORED) as archive:
                for frame in frames:
                    if cancel.is_set():return None
                    archive.write(frame,frame.name)
        elif kind=='GIF':
            # A bounded decoded working set; do not load arbitrarily long movies.
            sizes=[]
            for frame in frames:
                with Image.open(frame) as image:sizes.append(image.width*image.height*4)
            if sum(sizes)>128*1024*1024:raise ValueError('GIF frames exceed the 128 MiB export working limit; export a PNG sequence or shorter interval')
            images=[]
            try:
                for frame in frames:
                    if cancel.is_set():return None
                    with Image.open(frame) as image:images.append(image.convert('RGBA'))
                images[0].save(temp,format='GIF',save_all=True,append_images=images[1:],duration=max(1,round(1000/fps)),loop=0,disposal=2,optimize=False)
            finally:
                for image in images:image.close()
        elif kind=='MP4':
            from .optional_encoder import encode_mp4,EncodingCancelled
            try:encode_mp4(directory,temp,fps,cancel,encoder)
            except EncodingCancelled:return None
        else:raise ValueError('Unsupported animation export format')
        if cancel.is_set():return None
        os.replace(temp,destination)
        return destination
    finally:Path(temp).unlink(missing_ok=True)


class AnimationExport:
    def __init__(self,controls):
        self.controls=controls;self.app=controls.app;self.state=None

    def context(self):
        panel=self.app.panels['Results']
        signature=self.controls.export_signature()
        return (signature,id(self.app.session),id(self.app.project),self.app.active_job_id,
            id(self.app.result_datasets.get(self.app.active_job_id)),id(self.app.solution),
            getattr(self.app,'_result_source_epoch',0),panel.field_name(),panel.component.text(),
            panel.scale.text(),panel.colors.currentText(),panel.minimum.text(),panel.maximum.text(),panel.units.currentText())

    def start(self):
        if self.state is not None:raise ValueError('An animation export is already running')
        panel=self.app.panels['Results'];frames=panel.animation_frames()
        if panel.envelope.isChecked():raise ValueError('Envelope results are static; disable the envelope before exporting animation')
        kind=self.controls.animation_format.currentText();suffix='.gif' if kind=='GIF' else '.mp4' if kind=='MP4' else '.zip'
        path=self.app.dialogs.save_file(filetypes=[(kind,'*'+suffix)],defaultextension=suffix)
        if not path:return
        encoder=self.controls.encoder_record() if kind=='MP4' else None
        if kind=='MP4' and encoder is None:raise ValueError('Configure and review an installed FFmpeg encoder first')
        panel.stop_playback()
        self.controls.synchronize_export_view()
        destination=Path(path);destination.parent.mkdir(parents=True,exist_ok=True)
        directory=tempfile.TemporaryDirectory(prefix='.anyfem-animation-',dir=destination.parent)
        self.state=dict(context=self.context(),original=panel.frame.value(),frames=frames,index=0,
            directory=directory,destination=destination,kind=kind,fps=panel.playback_fps(),cancel=Event(),future=None,encoder=encoder,expected_frame=panel.frame.value())
        self.controls.export_progress.setRange(0,len(frames));self.controls.export_progress.setValue(0)
        self.controls.export_status.setText('Capturing animation frames')
        self.app.scheduler.call_later(0,self.step)

    def restore(self,state):
        if (state['context']==self.context() and not self.app._closing
                and self.app.panels['Results'].frame.value()==state['expected_frame']):
            panel=self.app.panels['Results'];state['expected_frame']=state['original'];panel._set_frame_index(state['original'])
            self.app.guarded(panel.show_results)()

    def cancel(self):
        state=self.state
        if state is None:return
        state['cancel'].set();self.restore(state)
        self.controls.export_status.setText('Export cancelled; previous output preserved')
        if state['future'] is None:self.finish(state)

    def finish(self,state):
        state['directory'].cleanup()
        if self.state is state:self.state=None

    def step(self):
        state=self.state
        if state is None:return
        if state['context']!=self.context() or self.app._closing or self.app.panels['Results'].frame.value()!=state['expected_frame']:
            state['cancel'].set();self.finish(state);return
        try:
            if state['index']<len(state['frames']):
                panel=self.app.panels['Results'];state['expected_frame']=state['frames'][state['index']];panel._set_frame_index(state['expected_frame'])
                def capture():
                    if self.state is not state or state['cancel'].is_set():return
                    if state['context']!=self.context() or panel.frame.value()!=state['expected_frame']:self.cancel();return
                    image=self.controls.capture_composite()
                    image.save(Path(state['directory'].name)/f"frame-{state['index']:06d}.png")
                    state['index']+=1
                    self.controls.export_progress.setValue(state['index'])
                    self.controls.export_status.setText(f"Captured {state['index']} / {len(state['frames'])} frames")
                    self.app.scheduler.call_later(0,self.step)
                panel.show_results(on_ready=capture,on_cancel=self.cancel)
                return
            self.restore(state)
            self.controls.export_status.setText('Encoding animation')
            state['future']=self.app._artifact_executor.submit(encode_frames,state['directory'].name,
                Path(state['directory'].name)/('encoded'+state['destination'].suffix),state['kind'],state['fps'],state['cancel'],state['encoder'])
            self.app.scheduler.call_later(50,self.poll)
        except Exception:
            self.restore(state);self.finish(state);raise

    def poll(self):
        state=self.state
        if state is None:return
        if state['context']!=self.context() or self.app._closing or self.app.panels['Results'].frame.value()!=state['expected_frame']:state['cancel'].set()
        if not state['future'].done():self.app.scheduler.call_later(50,self.poll);return
        try:
            result=state['future'].result()
            if state['cancel'].is_set():result=None
            if result is not None:
                os.replace(result,state['destination'])
                result=state['destination']
            self.controls.export_status.setText('Export cancelled' if result is None else f'Saved {result}')
        except Exception as error:self.controls.export_status.setText(f'Export failed: {error}')
        finally:self.finish(state)

    def close(self):
        state=self.state
        self.cancel()
        if state is not None and state['future'] is not None:
            state['future'].add_done_callback(lambda future:state['directory'].cleanup())
            self.state=None
