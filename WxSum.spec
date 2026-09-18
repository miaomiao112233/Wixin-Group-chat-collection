# -*- coding: utf-8 -*-
"""PyInstaller 打包配置。

构建命令（在项目根目录、venv 激活状态下执行）：
    pyinstaller --noconfirm WxSum.spec

产物：dist\\WxSum\\WxSum.exe（目录模式，启动快、调试方便）
     或 onefile 模式：把 COLLECT 改为 EXE + onefile
"""
from PyInstaller.utils.hooks import collect_submodules

# wechatauto 内部按字符串动态 import，必须显式收齐全部子模块
hiddenimports = collect_submodules("wechatauto") + [
    "psutil", "lxml._elementpath", "zstandard",
    "ctypes.wintypes",
]

# 图片 OCR（RapidOCR + ONNX Runtime）运行时动态加载的模块
try:
    hiddenimports += collect_submodules("rapidocr_onnxruntime")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("onnxruntime")
except Exception:
    pass


a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # 没有外部资源文件（图标程序内生成、prompts 内嵌代码字面量）
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc.data"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WxSum",
    console=False,           # 无控制台窗口（GUI 应用）
    icon=None,               # 用代码内生成的图标，打包后由 Qt 设置
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,               # UPX 压缩会让 PySide6 加载变慢，且易被杀软误报
    name="WxSum",
)
