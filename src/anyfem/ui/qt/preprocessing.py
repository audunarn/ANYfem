"""One isolated preprocessing child, with owner-thread progress and cancellation."""
import json,os,shutil,subprocess,sys,threading
from pathlib import Path
from ...presentation.frame_cache import new_directory,remove_directory,PreparedFrames


def _retire(process,directory,cache=None):
    def cleanup():
        if process is not None:
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=2)
        if cache is not None:
            with cache.lock:remove_directory(directory)
        else:remove_directory(directory)
    threading.Thread(target=cleanup,daemon=True).start()


class FramePreparation:
    def __init__(self,scheduler,context,changed,ready,failed,progress=None):
        self.scheduler=scheduler;self.context=context;self.changed=changed;self.ready=ready;self.failed=failed
        self.active=False;self.generation=0;self.timer=None;self.process=None
        self.directory=None;self.expected=None;self.cache=None
        self.progress=progress or (lambda completed,total:None)
    def begin(self):
        self.stop(clear=True);self.generation+=1;self.active=True
        self.expected=self.context();self.directory=new_directory();self.changed('Preparing snapshot')
        self.progress(0,0)
        return self.generation,self.directory
    def current(self,token):
        if not self.active or token!=self.generation:return False
        try:return self.context()==self.expected
        except (ValueError,RuntimeError,AttributeError):return False
    def launch(self,token,request,mesh,frames):
        if not self.current(token):
            if self.active and token==self.generation:self.stop(clear=True)
            return
        environment=dict(os.environ)
        environment.update(PYTHONPATH=os.pathsep.join(path for path in sys.path if path),
            OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        with (self.directory/'worker.log').open('wb') as log:
            self.process=subprocess.Popen([sys.executable,'-B','-m','anyfem.presentation.frame_cache',request],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=environment,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.progress(0,len(frames))
        def poll():
            self.timer=None
            if not self.current(token):self.stop(clear=True);return
            status={};path=self.directory/'progress.json'
            if path.exists():status=json.loads(path.read_text(encoding='utf-8'))
            if status.get('error'):
                self._save_diagnostic_log()
                message=status['error'];self.stop(clear=True);self.failed(RuntimeError(message));return
            code=self.process.poll()
            if code is not None:
                self._save_diagnostic_log()
                if code!=0 or not status.get('ready'):
                    self.stop(clear=True);self.failed(RuntimeError(f'frame preprocessing exited {code}'));return
                self.process.wait();self.process=None;self.active=False
                self.cache=PreparedFrames(self.directory,mesh,self.expected,frames)
                self.progress(len(frames),len(frames))
                self.changed('Frames prepared');self.ready(self.cache);return
            self.progress(status.get('completed',0),len(frames))
            self.changed(f"Preparing frame {status.get('completed',0)} / {len(frames)}")
            self.timer=self.scheduler.call_later(50,poll)
        self.timer=self.scheduler.call_later(50,poll)
    def _save_diagnostic_log(self):
        destination=os.environ.get('ANY3DVIEW_CHECK_DIR')
        if destination and self.directory is not None:
            path=self.directory/'worker.log'
            if path.exists():shutil.copyfile(path,Path(destination)/'preprocess-worker.log')
    def stop(self,clear=False):
        was_active=self.active;self.generation+=1;self.active=False
        if self.timer is not None:self.scheduler.cancel_call(self.timer);self.timer=None
        process,self.process=self.process,None
        if process is not None and process.poll() is None:process.terminate()
        if self.directory is not None and (was_active or clear):
            _retire(process,self.directory,self.cache);self.directory=None;self.cache=None
        if was_active:self.changed('Preprocessing stopped')
