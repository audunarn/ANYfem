"""The file inspector must refuse formats it cannot summarise with a typed error.

ANYfileio versions after 0.3.4 register geometry/model readers (``.sat``,
``.xml``, ``.anygeometry.json[.gz]``) whose results have no ``summary()``.
Reaching them through an "All files" dialog must give ``FileFormatError``,
never ``AttributeError`` or a foreign reader error.
"""
import pytest

from anyfileio.diagnostics import FileFormatError

from anyfem.presentation.file_inspection import inspect_file


@pytest.mark.parametrize("name", ["model.sat", "model.SAT", "model.xml", "model.anygeometry.json", "model.anygeometry.json.gz"])
def test_inspect_file_refuses_unsupported_geometry_suffixes(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"not a real file\n")
    with pytest.raises(FileFormatError):
        inspect_file(path)
