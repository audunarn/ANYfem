# Qt on WSL

Use Ubuntu-24.04 with WSLg. This launcher uses installed packages in the existing
`reports/qt/linux-env` Linux environment, independently of dirty source checkouts.
Set `ANYFEM_LINUX_PYTHON` inside WSL to use another prepared Linux environment.

From PowerShell, start the workbench:

```powershell
wsl -d Ubuntu-24.04 -- bash /mnt/c/Github/ANYfem/tools/run_qt_wsl.sh launch
```

Check OpenGL startup, viewport capture and shutdown:

```powershell
wsl -d Ubuntu-24.04 -- bash /mnt/c/Github/ANYfem/tools/run_qt_wsl.sh smoke gpu
```

Check the independent Qt software viewer:

```powershell
wsl -d Ubuntu-24.04 -- bash /mnt/c/Github/ANYfem/tools/run_qt_wsl.sh smoke software
```

Each run writes package versions/origins, effective Qt platform, backend diagnostics
and `glxinfo -B` output to a new `reports/qt/wsl` directory. Smoke runs also retain
a viewport PNG and report shutdown. `gpu` means the OpenGL viewer path; inspect
the recorded renderer to distinguish hardware acceleration from Mesa llvmpipe.
These checks establish WSL startup, not physical Linux GPU acceptance or scientific
qualification. Published geometry packages may still lack the batch API needed for
geometry meshing; launch/imported-model work can run while owner publication is pending.
