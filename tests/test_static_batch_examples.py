"""Execute the self-contained public delivery examples."""
from pathlib import Path
import runpy

import pytest


@pytest.mark.parametrize("name", ["supported", "free_free"])
def test_runnable_selected_envelope_example(name, monkeypatch, tmp_path):
    monkeypatch.setenv("ANYLOADS_TRUSTED_CODE_FILE", str(tmp_path / "trusted-code.json"))
    path = Path(__file__).parents[1] / "examples" / f"static_batch_{name}.py"
    namespace = runpy.run_path(str(path))
    reducer = namespace["main"]()
    assert reducer.complete and reducer.case_count == 3
