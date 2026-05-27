# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 - 猴子看护"""

import os
import imageio_ffmpeg

block_cipher = None

# ffmpeg 二进制路径
ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[
        (ffmpeg_path, 'imageio_ffmpeg/binaries'),
        ('mpg/mpv.exe', 'mpg'),
        ('mpg/d3dcompiler_43.dll', 'mpg'),
    ],
    datas=[],
    hiddenimports=[
        'api_client',
        'config',
        'ezviz_client',
        'recorder',
        'test_login',
        'ui_login',
        'ui_main',
        'video_widget',
        'PyQt5.sip',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'vlc',
        'tkinter',
        'matplotlib',
        'scipy',
        'numpy',
        'PIL',
    ],
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
    name='猴子看护',
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
    icon=None,
)
