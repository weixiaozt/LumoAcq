# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格文件 — LumoAcq Windows onedir 打包。

用法:
    .venv\Scripts\pyinstaller.exe build.spec

输出: dist/LumoAcq/  (整包分发给目标机器)
目标机器要求: 预装 Daheng Galaxy SDK (提供相机驱动 + DLL + gxipy)。
"""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH)

# imagecodecs 大量延迟导入的 _xxx_encode/_decode 子模块, 必须全收.
# 否则写 float32 TIFF 时报 'could not import name floatpred_encode'。
imagecodecs_hidden = collect_submodules('imagecodecs')

a = Analysis(
    ['acquire_app/main.py'],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        # 把整个包结构打进去 (main.py 的 __package__ 要 acquire_app 存在)
    ],
    hiddenimports=[
        # pyqtgraph 会动态 import Qt 绑定
        'pyqtgraph',
        'pyqtgraph.Qt',
        # numpy 子模块, PyInstaller 偶尔漏收
        'numpy.core._methods',
        'numpy.lib.format',
    ] + imagecodecs_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 精简: 测试和打包器自身不需要进产物
        'pytest',
        '_pytest',
        'pyinstaller',
        'PyInstaller',
        # 其他 Qt 绑定不用进
        'PyQt5',
        'PyQt6',
        'PySide2',
        # 不常用的大包
        'tkinter',
        'matplotlib',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LumoAcq',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                   # UPX 压缩可能触发杀软误报
    console=False,               # GUI 应用, 不弹黑窗; 调试时改 True
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',           # 有图标再打开
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='LumoAcq',
)
