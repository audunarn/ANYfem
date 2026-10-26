"""Optional PySide6 frontend; importing ANYfem itself never loads Qt."""
def main(*, viewer_backend="auto"):
    from .app import main as launch
    return launch() if viewer_backend == "auto" else launch(viewer_backend=viewer_backend)

def __getattr__(name):
    if name == "QtFemWindow":
        from .app import QtFemWindow
        return QtFemWindow
    raise AttributeError(name)
