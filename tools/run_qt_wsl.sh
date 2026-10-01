#!/usr/bin/env bash
# Run with: wsl -d Ubuntu-24.04 -- bash /mnt/c/Github/ANYfem/tools/run_qt_wsl.sh smoke
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="${ANYFEM_LINUX_PYTHON:-$root/reports/qt/linux-env/bin/python}"
mode="${1:-launch}"
backend="${2:-auto}"
if [[ ! -x "$python" ]]; then
  echo "Linux environment missing: $python. Set ANYFEM_LINUX_PYTHON to a Linux Python with ANYfem, PySide6 and ANY3dView[gpu,qt] installed." >&2
  exit 2
fi
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
# -I deliberately uses installed packages, never mixed sibling/source checkouts.
exec "$python" -I "$root/tools/qt_linux_run.py" --mode "$mode" --backend "$backend"
