# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Hunter-Sim Multi-Hunter Optimizer v2.0

To build:
1. pip install pyinstaller
2. pyinstaller hunter_sim_gui.spec
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Find the Rust .pyd in site-packages
rust_lib = None
venv_site_packages = Path('.venv/Lib/site-packages/rust_sim')
if venv_site_packages.exists():
    for file in venv_site_packages.glob('*.pyd'):
        rust_lib = str(file)
        break

datas = [
    ('builds/*.yaml', 'builds'),
    # Include hunter-sim module files
    ('hunter-sim/hunters.py', '.'),
    ('hunter-sim/sim.py', '.'),
    ('hunter-sim/units.py', '.'),
    ('hunter-sim/run_optimization.py', '.'),
    ('hunter-sim/sim_worker.py', '.'),
    # Include hunter PNG portraits for battle arena
    ('hunter-sim/assets/borge.png', 'assets'),
    ('hunter-sim/assets/knox.png', 'assets'),
    ('hunter-sim/assets/ozzy.png', 'assets'),
    # IRL Builds are created as blank templates in AppData on first run
]

# Add Rust library if found
binaries = []
if rust_lib:
    binaries.append((rust_lib, 'rust_sim'))
    print(f"Including Rust library: {rust_lib}")
else:
    print("WARNING: Rust library not found - exe will run Python-only mode")

a = Analysis(
    ['hunter-sim/gui_multi.py'],
    pathex=['hunter-sim', '.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'rust_sim',
        'tkinter',
        'tkinter.ttk',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.simpledialog',
        'tkinter.scrolledtext',
        'yaml',
        'json',
        'hunters',
        'sim',
        'gui',
        'units',
        'run_optimization',
        'sim_worker',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        'PIL.ImageDraw',
        'PIL.ImageFont',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'matplotlib', 'scipy', 'pandas'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='HunterSimOptimizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window - GUI only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon path if you have one
)
