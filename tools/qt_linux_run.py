"""Launch/check the installed Qt candidate on Linux; retain effective runtime evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import import_module, metadata
import json
from pathlib import Path
import platform
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("launch", "smoke"), default="launch")
    parser.add_argument("--backend", choices=("auto", "gpu", "software"), default="auto")
    options = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Use a Linux Python through WSL or on Linux.")
    output = Path(__file__).resolve().parents[1] / "reports" / "qt" / "wsl" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output.mkdir(parents=True)
    report = {"mode": options.mode, "requested_backend": options.backend,
              "platform": platform.platform(), "python": sys.version,
              "physical_linux_gpu_acceptance": False, "packages": {}}
    for distribution, module in (("ANYfem", "anyfem"), ("ANYgeometry", "anygeometry"),
                                 ("ANYmesher", "anymesher"), ("ANYsolver", "anysolver"),
                                 ("ANY3dView", "any3dview"), ("PySide6", "PySide6")):
        report["packages"][distribution] = {"version": metadata.version(distribution),
                                           "origin": import_module(module).__file__}
    try:
        graphics = subprocess.run(["glxinfo", "-B"], capture_output=True, text=True, timeout=15)
        (output / "opengl.txt").write_text(graphics.stdout + graphics.stderr)
        report["glxinfo_exit_code"] = graphics.returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        report["glxinfo_error"] = str(error)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from anyfem.ui.qt.app import QtFemWindow
    app = QApplication([])
    window = QtFemWindow(viewer_backend=options.backend)
    window.resize(1280, 800)
    window.show()

    def ready():
        result = 0
        try:
            report["qt_platform"] = app.platformName()
            report["active_backend"] = window.viewport.active_backend
            report["backend_diagnostics"] = list(window.viewport.backend_diagnostics)
            if options.mode == "smoke":
                screenshot = output / "workbench.png"
                window.viewport.capture_png(screenshot)
                if not screenshot.exists() or screenshot.stat().st_size == 0:
                    raise RuntimeError("Viewport screenshot is empty")
                if options.backend == "gpu" and report["active_backend"] != "gpu":
                    raise RuntimeError("Explicit GPU request did not activate the GPU backend")
                report["screenshot"] = str(screenshot)
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
            result = 1
        finally:
            if options.mode == "smoke":
                window.session.mark_saved()
                window.close()
                report["shutdown_complete"] = window._closing
                if not window._closing:
                    result = 1
                app.exit(result)
            report["startup_ok"] = result == 0
            (output / "runtime.json").write_text(json.dumps(report, indent=2) + "\n")
            print(f"WSL Qt runtime: {output / 'runtime.json'}", flush=True)

    QTimer.singleShot(1500, ready)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
