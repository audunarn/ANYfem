"""Installed-artifact checks and a separate, opt-in Metal clear/readback probe."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from importlib import import_module, metadata
from importlib.util import find_spec
import io
import json
from pathlib import Path
import platform
import sys
import tempfile


def installed_report(forbidden=(), scratch=None):
    import h5py
    import numpy as np
    import scipy
    import anygeometry
    from anymesher import native_cpp
    from anyfem.io.project_file import load_project, save_project
    from anyfem.model.project import Project

    modules = {name: import_module(name) for name in (
        "anyfem", "anygeometry", "anymesher", "anysolver", "anypost", "anyfileio",
        "anyloads", "anymaterial", "anysolver_results", "any3dview", "PySide6", "wgpu")}
    if sys.platform == "darwin":
        modules["psutil"] = import_module("psutil")
    origins = {name: str(Path(module.__file__).resolve()) for name, module in modules.items()}
    for name, origin in origins.items():
        if any(Path(root).resolve() == Path(origin) or Path(root).resolve() in Path(origin).parents for root in forbidden):
            raise RuntimeError(f"{name} resolved inside source tree: {origin}")
    launchers = {entry.name: entry for entry in metadata.entry_points(group="gui_scripts")
                 if entry.name in ("anyfem", "anyfem-qt")}
    if forbidden:  # an installed-artifact claim also requires installed launchers
        if set(launchers) != {"anyfem", "anyfem-qt"} or not all(callable(entry.load()) for entry in launchers.values()):
            raise RuntimeError("installed ANYfem Qt launchers are missing or unloadable")
    if find_spec("anyfem.geometry_cleaner") is not None:
        if find_spec("anygeometry.cleaner") is None:
            raise RuntimeError("This ANYfem candidate includes geometry cleaning, but the pinned ANYgeometry artifact "
                               "does not provide anygeometry.cleaner. Commit the owner feature and update the "
                               "coordinated ecosystem pin before claiming a clean Mac installation.")
        import_module("anyfem.geometry_cleaner")
    if not native_cpp.NATIVE_CPP_AVAILABLE:
        raise RuntimeError("ANYmesher compiled extension is not active")
    if forbidden and anygeometry.backend_info().get("backend") != "python":
        raise RuntimeError("bootstrap did not select ANYgeometry's default Python build")
    if native_cpp.orient2d((0., 0.), (1., 0.), (0., 1.)) <= 0:
        raise RuntimeError("native predicate operation failed")
    # Exercise the installed project codec with a non-ASCII, spaced pathname.
    with tempfile.TemporaryDirectory(prefix="anyfem mac ", dir=scratch) as directory:
        directory = Path(directory)
        path = directory / "modèle café.anyfem"
        project = Project("Mac installation smoke")
        save_project(project, path)
        reopened = load_project(path)
        if reopened.geometry.model_id != project.geometry.model_id:
            raise RuntimeError("project identity changed during installed round-trip")
        with h5py.File(directory / "résultats.h5", "w") as archive:
            archive["values"] = np.array([1., 2., 3.])
        with h5py.File(directory / "résultats.h5", "r") as archive:
            np.testing.assert_array_equal(archive["values"][:], [1., 2., 3.])
    configuration = io.StringIO()
    with redirect_stdout(configuration):
        np.show_config()
        scipy.show_config()
    return {"status": "passed", "platform": platform.platform(), "machine": platform.machine(),
            "python": sys.version, "executable": sys.executable, "module_origins": origins,
            "launchers": {name: entry.value for name, entry in launchers.items()},
            "geometry_backend": dict(anygeometry.backend_info()),
            "native_mesher": native_cpp.NATIVE_CPP_AVAILABLE,
            "native_extension": str(native_cpp._compiled.__file__),
            "distributions": {dist.metadata["Name"]: dist.version for dist in metadata.distributions()},
            "blas_configuration": configuration.getvalue()}


def metal_report():
    import wgpu

    adapters = wgpu.gpu.enumerate_adapters_sync()
    report = {"status": "not_verified", "adapters": [dict(adapter.info) for adapter in adapters]}
    adapter = next((item for item in adapters if item.info.get("backend_type") == "Metal"
                    and item.info.get("adapter_type") != "CPU"), None)
    if adapter is None:
        report["reason"] = "No hardware Metal adapter exposed by this runner"
        return report
    device = adapter.request_device_sync()
    texture = device.create_texture(size=(4, 4, 1), format="rgba8unorm",
                                   usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC)
    encoder = device.create_command_encoder()
    render = encoder.begin_render_pass(color_attachments=[{
        "view": texture.create_view(), "resolve_target": None,
        "clear_value": (1., 0., 0., 1.), "load_op": "clear", "store_op": "store"}])
    render.end()
    device.queue.submit([encoder.finish()])
    pixels = bytes(device.queue.read_texture({"texture": texture, "mip_level": 0, "origin": (0, 0, 0)},
                                            {"offset": 0, "bytes_per_row": 256, "rows_per_image": 4}, (4, 4, 1)))
    for row in range(4):
        if pixels[row * 256:row * 256 + 16] != bytes([255, 0, 0, 255]) * 4:
            raise RuntimeError("Metal clear/readback returned unexpected pixels")
    texture.destroy()
    device.destroy()
    report.update(status="passed", selected=dict(adapter.info), operation="offscreen clear/readback")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--metal-only", action="store_true")
    parser.add_argument("--forbid-source-root", action="append", default=[])
    options = parser.parse_args(argv)
    options.out.parent.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed"}
    try:
        report = metal_report() if options.metal_only else installed_report(options.forbid_source_root, options.out.parent)
        return 0
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        options.out.parent.mkdir(parents=True, exist_ok=True)
        options.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
