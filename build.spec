# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置：单程序 TUI 版。

产物只包含一个用户入口 CapsWriter-Offline-TUI.exe。识别 worker 仍由该程序内部
multiprocessing 拉起，不再提供 start_server/start_client 两个可执行文件。
"""

from os import makedirs, walk
from os.path import basename, dirname, exists, join
from shutil import copyfile, copytree, rmtree

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules


INCLUDE_CUDA_PROVIDER = False

binaries = []
datas = []
hiddenimports = [
    "textual",
    "textual.app",
    "textual.widgets",
    "textual.containers",
    "websockets",
    "websockets.client",
    "websockets.server",
    "rich",
    "rich.console",
    "rich.markdown",
    "rich._unicode_data.unicode17-0-0",
    "keyboard",
    "pyclip",
    "numpy",
    "sounddevice",
    "soundfile",
    "_soundfile_data",
    "pypinyin",
    "watchdog",
    "typer",
    "srt",
    "sherpa_onnx",
    "PIL",
    "PIL.Image",
    "pystray",
]

hiddenimports += collect_submodules("textual")

try:
    sherpa_datas = collect_data_files("sherpa_onnx", include_py_files=False)
    if not INCLUDE_CUDA_PROVIDER:
        sherpa_datas = [
            (src, dest)
            for src, dest in sherpa_datas
            if "providers_cuda" not in basename(src).lower()
        ]
    datas += sherpa_datas
except Exception:
    pass

try:
    datas += collect_data_files("PIL", include_py_files=False)
    binaries += collect_all("PIL")[1]
except Exception:
    pass

try:
    datas += collect_data_files("_soundfile_data", include_py_files=False)
except Exception:
    pass

a = Analysis(
    ["start.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["build_hook.py"],
    excludes=[
        "IPython",
        "PySide6",
        "PySide2",
        "PyQt5",
        "matplotlib",
        "wx",
        "funasr",
        "torch",
    ],
    noarchive=True,
)

filtered_binaries = []
for name, src, type_ in a.binaries:
    src_lower = src.lower() if isinstance(src, str) else ""
    is_system_cuda_dll = (
        "\\nvidia gpu computing toolkit\\cuda\\" in src_lower
        or "\\nvidia\\cudnn\\" in src_lower
        or ("\\cuda\\v" in src_lower and "\\bin\\" in src_lower)
    )
    is_unwanted_onnx_dll = "onnxruntime_providers_cuda.dll" in name.lower()
    if not is_system_cuda_dll and not is_unwanted_onnx_dll:
        filtered_binaries.append((name, src, type_))
a.binaries = filtered_binaries

private_modules = ["core", "config_client", "config_server", "LLM"]
a.pure = [
    item
    for item in a.pure
    if not any(item[0] == module or item[0].startswith(module + ".") for module in private_modules)
]
a.datas = [
    item
    for item in a.datas
    if not any(
        item[0].startswith(module + "/")
        or item[0].startswith(module + "\\")
        or item[0] in (module + ".py", module + ".pyc")
        for module in private_modules
    )
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CapsWriter-Offline-TUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets\\\\icon.ico"],
    contents_directory="internal",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CapsWriter-Offline-TUI",
)

dest_root = join("dist", basename(coll.name))
my_files = [
    "start.py",
    "config_client.py",
    "config_server.py",
    "hot.txt",
    "hot-server.txt",
    "hot-rule.txt",
    "readme.md",
    "LICENSE",
]

for file in my_files:
    if not exists(file):
        continue
    dest_file = join(dest_root, file.replace("\\", "/"))
    makedirs(dirname(dest_file), exist_ok=True)
    copyfile(file, dest_file)

for folder in ["models", "assets", "core", "LLM", "docs", "logs"]:
    if not exists(folder):
        continue
    dest_folder = join(dest_root, folder)
    if exists(dest_folder):
        rmtree(dest_folder)
    copytree(folder, dest_folder)
