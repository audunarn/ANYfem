"""Explicitly admitted optional FFmpeg executable; never downloads an encoder."""
from pathlib import Path
import hashlib
import os
import subprocess
import time


class EncoderUnavailable(ValueError):pass
class EncodingCancelled(RuntimeError):pass


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def probe_encoder(path):
    path=Path(path).resolve()
    if not path.is_file():raise EncoderUnavailable('Select an installed FFmpeg executable')
    options=dict(capture_output=True,text=True,timeout=5,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        license_result=subprocess.run([str(path),'-L'],**options)
        encoders=subprocess.run([str(path),'-hide_banner','-encoders'],**options)
    except (OSError,subprocess.TimeoutExpired) as error:raise EncoderUnavailable(f'Encoder probe failed: {error}') from error
    license_text=license_result.stdout+license_result.stderr
    if license_result.returncode or 'ffmpeg' not in license_text.lower() or not any(word in license_text for word in ('LGPL','GPL','GNU Lesser General Public License','GNU General Public License')):
        raise EncoderUnavailable('Executable did not provide a recognized FFmpeg license')
    listing=encoders.stdout+encoders.stderr
    if encoders.returncode or not any(len(line.split())>=2 and line.split()[1]=='mpeg4' for line in listing.splitlines()):
        raise EncoderUnavailable('This FFmpeg build does not provide the built-in mpeg4 encoder')
    return dict(path=str(path),sha256=file_hash(path),license=license_text,codec='mpeg4')


def encode_mp4(directory,temporary,fps,cancel,record):
    if not isinstance(record,dict) or record.get('codec')!='mpeg4':raise EncoderUnavailable('Configure an installed, reviewed FFmpeg encoder first')
    path=Path(record['path'])
    if not path.is_file() or file_hash(path)!=record.get('sha256'):raise EncoderUnavailable('Encoder executable changed; review it again')
    command=[str(path),'-nostdin','-hide_banner','-loglevel','error','-y','-framerate',str(fps),
        '-i',str(Path(directory)/'frame-%06d.png'),'-vf','pad=ceil(iw/2)*2:ceil(ih/2)*2',
        '-c:v','mpeg4','-pix_fmt','yuv420p',str(temporary)]
    # ffmpeg is the sole child. It launches no helper shell or child encoder.
    with tempfile_log(directory) as log:
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+600
            while process.poll() is None:
                if cancel.wait(.05):raise EncodingCancelled('MP4 encoding cancelled')
                if time.monotonic()>deadline:raise EncoderUnavailable('MP4 encoding exceeded its ten-minute deadline')
            if cancel.is_set():raise EncodingCancelled('MP4 encoding cancelled')
            if process.returncode:raise EncoderUnavailable(f'MP4 encoder failed with exit {process.returncode}')
            if not Path(temporary).is_file() or Path(temporary).stat().st_size==0:raise EncoderUnavailable('Encoder produced no MP4 output')
        finally:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            else:process.wait()


def tempfile_log(directory):
    return (Path(directory)/'encoder.log').open('wb')
