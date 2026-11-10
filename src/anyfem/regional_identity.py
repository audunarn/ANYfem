"""Identify the code that produced a saved regional-meshing baseline."""

from functools import lru_cache
from hashlib import sha256
from importlib import import_module
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path


@lru_cache(maxsize=3)
def build_identity(package):
    module = import_module(package)
    root = Path(module.__file__).resolve().parent
    files = sorted(path for path in root.rglob("*") if path.is_file()
                   and (path.suffix == ".py" or path.name.endswith(tuple(EXTENSION_SUFFIXES))))
    if not files:
        raise ValueError(f"cannot identify the installed {package} build for regional meshing")
    digest = sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        with path.open("rb") as content:
            file_hash = sha256()
            for chunk in iter(lambda: content.read(1024 * 1024), b""):
                file_hash.update(chunk)
            digest.update(file_hash.digest())
    return f"{getattr(module, '__version__', '')}:sha256:{digest.hexdigest()}"
