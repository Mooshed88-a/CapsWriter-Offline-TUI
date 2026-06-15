# CapsWriter-Offline-TUI 打包指南

## 产物结构

PyInstaller 构建后会生成单程序 TUI 发行目录：

```text
dist/CapsWriter-Offline-TUI/
├── CapsWriter-Offline-TUI.exe
├── internal/
├── assets/
├── config_client.py
├── config_server.py
├── core/
├── docs/
├── hot.txt
├── hot-rule.txt
├── hot-server.txt
├── LLM/
├── logs/
├── models/
├── readme.md
└── start.py
```

发布到 GitHub Release 的压缩包固定为：

```text
release/CapsWriter-Offline-TUI.zip
```

## 首次准备环境

```powershell
py -3.8 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果 `py -3.8` 不可用，可以改用系统里可用的 Python 3.8 路径创建虚拟环境。

## 构建 EXE

如果要让 GGUF 引擎内置 llama.cpp 运行库，先下载：

```text
https://github.com/ggml-org/llama.cpp/releases/download/b7798/llama-b7798-bin-win-vulkan-x64.zip
```

解压后把其中所有 `.dll` 放到：

```text
core/server/engines/llama/bin/
```

这些 DLL 会被打进发行包，但默认受 `.gitignore` 忽略，不会进入源码提交。

```powershell
.\.venv\Scripts\python.exe -m compileall core start.py start_client.py start_server.py config_client.py config_server.py
git diff --check
.\.venv\Scripts\pyinstaller.exe --noconfirm --clean build.spec
```

构建完成后，入口文件是：

```text
dist/CapsWriter-Offline-TUI/CapsWriter-Offline-TUI.exe
```

## 生成 Release ZIP

优先使用项目脚本：

```powershell
.\.venv\Scripts\python.exe .\zip_release.py
```

如果机器没有 7-Zip，也可以用 PowerShell 直接压缩：

```powershell
Remove-Item .\release\CapsWriter-Offline-TUI.zip -ErrorAction SilentlyContinue
Compress-Archive -Path .\dist\CapsWriter-Offline-TUI -DestinationPath .\release\CapsWriter-Offline-TUI.zip -CompressionLevel Optimal
```

## 发布前检查

```powershell
git status --short
```

确认没有误加入 `.venv/`、`build/`、`dist/`、`release/` 等本地构建产物。
