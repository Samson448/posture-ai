# -*- mode: python ; coding: utf-8 -*-
# PyInstaller recipe for Posture AI.
# Build with build_exe.bat (or:  python -m PyInstaller --noconfirm --clean PostureAI.spec)
# Result: dist\PostureAI\PostureAI.exe  (a folder that runs without Python installed)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("app.ico", ".")]          # the window icon, found at runtime by resource_path()
binaries = []
hiddenimports = []

# MediaPipe loads its model files and native libraries by path, so take the whole package.
for package in ("mediapipe",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Modules that are only loaded by name at runtime (voice, tray icon, charts window).
hiddenimports += collect_submodules("pyttsx3")
hiddenimports += [
    "pyttsx3.drivers", "pyttsx3.drivers.sapi5",
    "comtypes", "comtypes.client", "win32com", "win32com.client", "pythoncom", "pywintypes",
    "pystray._win32",
    "PIL._tkinter_finder",
    "matplotlib.backends.backend_tkagg",
    "posture_gui", "posture_monitor", "posture_report", "posture_common",
]

a = Analysis(
    ["posture_app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["IPython", "pytest", "tensorflow", "torch"],  # not used; keeps the build smaller
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PostureAI",
    console=False,          # no black window; messages go to posture_monitor.log
    icon="app.ico",
    upx=False,              # UPX-packed files are flagged by antivirus more often
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="PostureAI",
)
