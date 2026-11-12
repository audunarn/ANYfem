"""Find a user-selected, installed or portable OpenCAD Studio executable."""
import os
from pathlib import Path
import re
import shutil
import sys


def find_studio(configured=""):
    candidates = [configured,shutil.which("OpenCADStudio"),shutil.which("opencadstudio")]
    if sys.platform=="win32":
        import winreg
        key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OpenCADStudio.exe"
        for root in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root,key) as entry:
                    candidates.append(winreg.QueryValue(entry,None).strip('"'))
            except OSError:
                pass
        for env in ("ProgramFiles","ProgramW6432","ProgramFiles(x86)","LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                for sub in ("Open CAD Studio","OpenCADStudio","Programs/Open CAD Studio"):
                    candidates.append(Path(base)/sub/"OpenCADStudio.exe")
        for filetype in ("DWG","DXF"):
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,rf"OpenCADStudio.{filetype}\shell\open\command") as entry:
                    command = winreg.QueryValue(entry,None)
                match = re.match(r'^"([^"]+)"',command)
                if match:
                    candidates.append(match.group(1))
            except OSError:
                pass
    elif sys.platform=="darwin":
        candidates.append("/Applications/OpenCADStudio.app/Contents/MacOS/OpenCADStudio")
    for candidate in candidates:
        if (candidate and re.fullmatch(r"opencadstudio(?:[-_].+)?",Path(candidate).stem.replace(" ",""),re.I)
                and Path(candidate).is_file() and os.access(candidate,os.X_OK)):
            return Path(candidate).resolve()
    return None
