# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['Data-Intake-PyQt5-CLEAN.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'IPython', 'jupyter', 'notebook', 'qtpy', 'PySide2', 'PySide6', 'PyQt6', 'numpy', 'mkl', 'blas', 'lapack', 'openblas', 'intel_openmp', 'tbb', 'mkl_rt', 'mkl_service', 'mkl_fft', 'mkl_random'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DataIntake',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['Intake-icon.ico'],
)
