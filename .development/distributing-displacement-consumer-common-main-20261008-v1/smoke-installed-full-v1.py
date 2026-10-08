"""Run the complete affected consumer test file from pinned installed sites."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
source = HERE / "smoke-installed-v1.py"
spec = importlib.util.spec_from_file_location("installed_smoke", source)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
tree = ast.parse(module.TEST_FILE.read_text(encoding="utf-8"))
module.CASES = tuple(
    node.name for node in tree.body
    if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
)
assert module.CASES
module.ATTEMPT = HERE / "installed-smoke-full-v1-a1"
module.main()
receipt_path = module.ATTEMPT / "receipt.json"
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
receipt["wrapper_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
receipt["function_inventory"] = list(module.CASES)
receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
