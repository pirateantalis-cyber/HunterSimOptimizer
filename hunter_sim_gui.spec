# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Hunter-Sim Multi-Hunter Optimizer

To build:
1. pip install pyinstaller
2. pyinstaller hunter_sim_gui.spec
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Find the Rust library
rust_lib = None
for name in ['hunter_sim_lib.pyd', 'hunter_sim_lib.dll', 'libhunter_sim_lib.so', 'libhunter_sim_lib.dylib']:
    # Check hunter-sim directory first (where we copied it)
    lib_path = Path('hunter-sim') / name
    if lib_path.exists():
        rust_lib = str(lib_path)
        break
    # Then check rust target directory
    lib_path = Path('hunter-sim-rs/target/release') / name
    if lib_path.exists():
        rust_lib = str(lib_path)
        break

datas = [
    ('builds/*.yaml', 'builds'),
    # Include hunter-sim module files
    ('hunter-sim/hunters.py', '.'),
    ('hunter-sim/sim.py', '.'),
    ('hunter-sim/gui.py', '.'),
    ('hunter-sim/units.py', '.'),
    # Include rust_sim.py from root
    ('rust_sim.py', '.'),
    # Do NOT include IRL Builds - let the app create it fresh with zeros
]

# Add Rust library if found
binaries = []
if rust_lib:
    binaries.append((rust_lib, '.'))
    print(f"Including Rust library: {rust_lib}")

a = Analysis(
    ['hunter-sim/gui_multi.py'],
    pathex=['hunter-sim', '.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'hunter_sim_lib',
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
        'rust_sim',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'PIL', 'Pillow', 'matplotlib', 'scipy', 'pandas'],
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
