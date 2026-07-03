# coding: utf-8
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import webbrowser
from pathlib import Path
from typing import Any

import sounddevice as sd
from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
)

from config_client import ClientConfig as ClientConfig
from config_client import __version__
from config_server import ServerConfig as ServerConfig
from core.client.audio.system_volume import SystemVolumeManager
from core.client.audio.stream import AudioStreamManager
from core.client.diary.diary_writer import DiaryWriter
from core.client.hotword.manager import HotwordManager
from core.client.llm.llm_handler import LLMHandler
from core.client.output import ResultProcessor
from core.client.output.text_output import TextOutput
from core.client.shortcut.shortcut_config import Shortcut
from core.client.shortcut.shortcut_manager import ShortcutManager
from core.client.state import ClientState
from core.client.transcribe import FileTranscriber, SrtAdjuster
from core.client.udp.udp_control import UDPController
from core.tools.empty_working_set import empty_current_working_set

from .config import AppConfig, config_path
from .local_connection import LocalConnectionManager
from .local_dispatcher import LocalAudioDispatcher
from .status import AppStatus
from .tray import TrayController
from .worker_manager import WorkerManager


MODEL_HELP_URL = "https://github.com/Mooshed88-a/CapsWriter-Offline-TUI/blob/master/docs/%E6%A8%A1%E5%9E%8B%E4%B8%8B%E8%BD%BD%E7%9A%84%E8%8B%A5%E5%B9%B2%E9%97%AE%E9%A2%98.md"
MODEL_RELEASE_URL = "https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models"
LOG_LINE_RE = re.compile(r"^(?P<time>\d\d:\d\d:\d\d)(?: \[(?P<level>[A-Z]+)\])? (?P<message>.*)$")


class CapsWriterSingleApp:
    def __init__(self, files: list[Path] | None = None):
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
        self.files = files or []
        self.config = AppConfig.load(self.base_dir)
        self.status = AppStatus()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.state = ClientState(app=self)

        self.worker = WorkerManager(self.status)
        self.dispatcher = LocalAudioDispatcher(self.worker, self.status)
        self.ws = LocalConnectionManager(self.worker, self.dispatcher)

        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=ClientConfig.hot_thresh,
            similar_threshold=ClientConfig.hot_similar,
        )
        self.llm = LLMHandler(app=self)
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=self.base_dir)
        self.stream = AudioStreamManager(self)
        self.system_volume = SystemVolumeManager(target_level=0.20)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in ClientConfig.shortcuts])
        self.udp = UDPController(self.shortcut)
        self.tray = TrayController(self)
        self.tui = None
        self.result_processor: ResultProcessor | None = None
        self.result_task: asyncio.Task | None = None

        empty_current_working_set()

    def run(self) -> None:
        self.tui = CapsWriterTUI(self)
        self.tui.run()

    async def start_runtime(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.status.log(f"CapsWriter-Offline-TUI {__version__} 启动")
        self.tray.start()

        if ClientConfig.auto_start_worker:
            self.worker.start(self.loop)

        self.hotword.start()
        self.llm.start()

        if ClientConfig.auto_start_mic:
            self.reopen_microphone()

        self.shortcut.start()
        if ClientConfig.udp_control:
            self.udp.start()

        self.result_processor = ResultProcessor(self)
        self.result_task = asyncio.create_task(self.result_processor.start())

        if self.files:
            asyncio.create_task(self.transcribe_files(self.files))

    async def stop_runtime(self) -> None:
        if self.result_processor:
            self.result_processor.request_exit()
        if self.result_task:
            self.result_task.cancel()
        self.udp.stop()
        self.shortcut.stop()
        self.system_volume.restore_now()
        self.stream.stop()
        self.hotword.stop()
        self.llm.stop()
        self.worker.stop()
        self.tray.stop()
        try:
            self.state.reset()
        except Exception:
            pass

    def save_config(self) -> None:
        self.config.save(config_path(self.base_dir))
        self.config.apply_compat()
        self.status.log(f"配置已保存：{config_path(self.base_dir)}")

    def reopen_microphone(self) -> None:
        self.stream.reopen()

    def restart_worker(self) -> None:
        if not self.loop:
            return
        self.worker.restart(self.loop)

    def reload_shortcuts(self) -> None:
        self.shortcut.stop()
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in ClientConfig.shortcuts])
        self.shortcut.start()
        self.udp.manager = self.shortcut
        self.status.log("快捷键监听器已按新配置重载")

    def minimize_to_tray(self) -> None:
        self.tray.minimize()

    def open_model_folder(self) -> None:
        folder = self.base_dir / "models"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)
            self.status.log(f"已打开模型文件夹：{folder}")
        except Exception as exc:
            self.status.set_error(f"打开模型文件夹失败: {exc}")

    async def transcribe_files(self, files: list[Path]) -> None:
        for file in files:
            try:
                if file.suffix.lower() in [".txt", ".json", ".srt", ".vtt"]:
                    SrtAdjuster().adjust(file)
                    self.status.log(f"时间轴调整完成：{file}")
                    continue
                transcriber = FileTranscriber(self, file)
                if await transcriber.check():
                    await transcriber.send()
                    await transcriber.receive()
                    await transcriber.close()
                    self.status.log(f"文件转录完成：{file}")
            except Exception as exc:
                self.status.set_error(f"文件处理失败 {file}: {exc}")


class CapsWriterTUI(App):
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; }
    .row { height: auto; margin: 0 0 1 0; }
    .status-actions { height: auto; margin: 1 0 1 0; }
    .panel { padding: 1; height: 1fr; }
    .status-line { height: auto; }
    Input, Select { width: 45; }
    Button { margin-right: 1; }
    #logs { height: 1fr; }
    #shortcut_table { height: 10; }
    #advanced_json { height: 1fr; }
    """

    BINDINGS = [
        ("q", "quit", "退出"),
        ("r", "refresh_devices", "刷新设备"),
        ("w", "restart_worker", "重启 worker"),
    ]

    def __init__(self, runtime: CapsWriterSingleApp):
        super().__init__()
        self.runtime = runtime
        self._last_log_count = 0
        self._selected_shortcut_index = 0

    def compose(self) -> ComposeResult:
        with TabbedContent():
            with TabPane("状态总览", id="tab_status"):
                with Vertical(classes="panel"):
                    yield Static("", id="status_summary", classes="status-line")
                    with Horizontal(classes="status-actions"):
                        yield Button("重启 worker", id="restart_worker", variant="primary")
                        yield Button("重开麦克风", id="reopen_mic")
                        yield Button("刷新设备", id="refresh_devices")
                        yield Button("最小化到托盘", id="minimize_to_tray")
                    yield RichLog(id="logs", markup=True, highlight=False, wrap=True, max_lines=300)
            with TabPane("麦克风设备", id="tab_mic"):
                with Vertical(classes="panel"):
                    yield Label("输入设备")
                    yield Select([], id="input_device")
                    with Horizontal(classes="row"):
                        yield Button("保存并重开麦克风", id="save_mic", variant="primary")
                        yield Button("仅刷新列表", id="refresh_devices_mic")
                    yield DataTable(id="device_table")
            with TabPane("快捷键", id="tab_shortcuts"):
                with VerticalScroll(classes="panel"):
                    yield DataTable(id="shortcut_table")
                    yield Label("快捷键触发阈值（秒）")
                    yield Input(value=str(ClientConfig.threshold), id="shortcut_threshold")
                    yield Label("按键")
                    yield Input(value="", id="shortcut_key")
                    yield Label("输入类型")
                    yield Select([("键盘", "keyboard"), ("鼠标", "mouse")], value="keyboard", id="shortcut_type")
                    yield Label("模式")
                    yield Select(
                        [
                            ("按住说话，松开结束", "hold"),
                            ("单击开始，再次单击结束", "click"),
                            ("CapsLock 按住说话且不切换大小写", "caps_no_toggle"),
                        ],
                        value="hold",
                        id="shortcut_mode",
                    )
                    yield Label("阻塞原按键")
                    yield Switch(value=True, id="shortcut_suppress")
                    yield Label("启用")
                    yield Switch(value=True, id="shortcut_enabled")
                    with Horizontal(classes="row"):
                        yield Button("应用到选中项", id="apply_shortcut", variant="primary")
                        yield Button("新增快捷键", id="add_shortcut")
                        yield Button("删除选中项", id="delete_shortcut")
                        yield Button("添加 CapsLock 免切换模式", id="add_caps_no_toggle")
                        yield Button("保存并重载快捷键", id="save_shortcuts", variant="success")
            with TabPane("识别模型", id="tab_model"):
                with VerticalScroll(classes="panel"):
                    yield Static("", id="model_error")
                    with Horizontal(classes="row"):
                        yield Button("打开模型下载说明", id="open_model_help", variant="primary")
                        yield Button("打开模型 Release 下载页", id="open_model_release")
                        yield Button("打开模型文件夹", id="open_model_folder")
                    yield Static(f"说明：{MODEL_HELP_URL}\n下载：{MODEL_RELEASE_URL}", id="model_links")
                    yield Label("模型类型")
                    yield Select(
                        [
                            ("qwen_asr", "qwen_asr"),
                            ("fun_asr_nano", "fun_asr_nano"),
                            ("sensevoice", "sensevoice"),
                            ("paraformer", "paraformer"),
                        ],
                        value=ServerConfig.model_type,
                        id="model_type",
                    )
                    yield Label("识别语言")
                    yield Input(value=str(ClientConfig.language), id="language")
                    yield Label("上下文提示词")
                    yield Input(value=str(ClientConfig.context), id="context")
                    yield Button("保存模型配置", id="save_model", variant="primary")
            with TabPane("输出行为", id="tab_output"):
                with VerticalScroll(classes="panel"):
                    yield Label("粘贴输出")
                    yield Switch(value=bool(ClientConfig.paste), id="paste")
                    yield Label("恢复剪贴板")
                    yield Switch(value=bool(ClientConfig.restore_clip), id="restore_clip")
                    yield Label("强制粘贴应用 JSON 数组")
                    yield TextArea(json.dumps(ClientConfig.paste_apps, ensure_ascii=False, indent=2), id="paste_apps")
                    yield Label("输出后自动回车应用 JSON 数组，如 [[\"app.exe\", 0.5]]")
                    yield TextArea(json.dumps(ClientConfig.enter_apps, ensure_ascii=False, indent=2), id="enter_apps")
                    yield Label("保存录音")
                    yield Switch(value=bool(ClientConfig.save_audio), id="save_audio")
                    yield Label("录音文件名识别文本长度")
                    yield Input(value=str(ClientConfig.audio_name_len), id="audio_name_len")
                    yield Label("删除自定义末尾符号集合")
                    yield Input(value=str(ClientConfig.trash_punc), id="trash_punc")
                    yield Label("删除任意末尾标点/符号")
                    yield Switch(value=bool(getattr(ClientConfig, "trash_punc_any", False)), id="trash_punc_any")
                    yield Label("低于多少语义单元时删除末尾符号；0 表示总是按规则删除")
                    yield Input(value=str(ClientConfig.trash_punc_thresh), id="trash_punc_thresh")
                    yield Label("强制删除末尾符号应用 JSON 数组")
                    yield TextArea(json.dumps(ClientConfig.trash_punc_apps, ensure_ascii=False, indent=2), id="trash_punc_apps")
                    yield Label("转换为繁体中文")
                    yield Switch(value=bool(ClientConfig.traditional_convert), id="traditional_convert")
                    yield Label("繁体地区")
                    yield Select(
                        [("标准繁体", "zh-hant"), ("台湾繁体", "zh-tw"), ("香港繁体", "zh-hk")],
                        value=str(ClientConfig.traditional_locale),
                        id="traditional_locale",
                    )
                    yield Button("保存输出配置", id="save_output", variant="primary")
            with TabPane("热词/规则", id="tab_hotword"):
                with VerticalScroll(classes="panel"):
                    yield Label("启用热词")
                    yield Switch(value=bool(ClientConfig.hot), id="hot")
                    yield Label("启用规则替换")
                    yield Switch(value=bool(ClientConfig.hot_rule), id="hot_rule")
                    yield Label("热词阈值")
                    yield Input(value=str(ClientConfig.hot_thresh), id="hot_thresh")
                    yield Button("保存热词配置", id="save_hotword", variant="primary")
            with TabPane("LLM", id="tab_llm"):
                with VerticalScroll(classes="panel"):
                    yield Label("启用 LLM")
                    yield Switch(value=bool(ClientConfig.llm_enabled), id="llm_enabled")
                    yield Label("停止键")
                    yield Input(value=str(ClientConfig.llm_stop_key), id="llm_stop_key")
                    yield Button("保存 LLM 配置", id="save_llm", variant="primary")
            with TabPane("文件转录", id="tab_files"):
                with VerticalScroll(classes="panel"):
                    yield Label("文件分段长度（秒）")
                    yield Input(value=str(ClientConfig.file_seg_duration), id="file_seg_duration")
                    yield Label("文件分段重叠（秒）")
                    yield Input(value=str(ClientConfig.file_seg_overlap), id="file_seg_overlap")
                    yield Label("保存 SRT")
                    yield Switch(value=bool(ClientConfig.file_save_srt), id="file_save_srt")
                    yield Label("保存 TXT")
                    yield Switch(value=bool(ClientConfig.file_save_txt), id="file_save_txt")
                    yield Label("保存 JSON")
                    yield Switch(value=bool(ClientConfig.file_save_json), id="file_save_json")
                    yield Label("保存 merge.txt")
                    yield Switch(value=bool(ClientConfig.file_save_merge), id="file_save_merge")
                    yield Button("保存文件转录配置", id="save_file_config")
                    yield Label("文件路径，一行一个")
                    yield TextArea(id="files_text")
                    yield Button("开始转录", id="start_transcribe", variant="primary")
            with TabPane("UDP/GPU", id="tab_udp_gpu"):
                with VerticalScroll(classes="panel"):
                    yield Label("启用 UDP 广播")
                    yield Switch(value=bool(ClientConfig.udp_broadcast), id="udp_broadcast")
                    yield Label("启用 UDP 控制")
                    yield Switch(value=bool(ClientConfig.udp_control), id="udp_control")
                    yield Label("UDP 广播目标 JSON 数组，如 [[\"127.255.255.255\", 6017]]")
                    yield TextArea(json.dumps(ClientConfig.udp_broadcast_targets, ensure_ascii=False, indent=2), id="udp_broadcast_targets")
                    yield Label("UDP 控制监听地址")
                    yield Input(value=str(ClientConfig.udp_control_addr), id="udp_control_addr")
                    yield Label("UDP 控制监听端口")
                    yield Input(value=str(ClientConfig.udp_control_port), id="udp_control_port")
                    yield Label("ONNX Provider: SenseVoice")
                    yield Select(self._provider_options(), value=str(self.runtime.config.sensevoice.get("onnx_provider", "CPU")), id="sensevoice_onnx_provider")
                    yield Label("ONNX Provider: Fun-ASR-Nano")
                    yield Select(self._provider_options(), value=str(self.runtime.config.fun_asr_nano.get("onnx_provider", "CPU")), id="fun_onnx_provider")
                    yield Label("ONNX Provider: Qwen3-ASR")
                    yield Select(self._provider_options(), value=str(self.runtime.config.qwen_asr.get("onnx_provider", "CPU")), id="qwen_onnx_provider")
                    yield Label("DML pad 秒数")
                    yield Input(value=str(self.runtime.config.qwen_asr.get("dml_pad_to", 30)), id="dml_pad_to")
                    yield Label("Fun-ASR-Nano GGUF 使用 GPU")
                    yield Switch(value=bool(self.runtime.config.fun_asr_nano.get("llm_use_gpu", True)), id="fun_llm_use_gpu")
                    yield Label("Qwen3-ASR GGUF 使用 GPU")
                    yield Switch(value=bool(self.runtime.config.qwen_asr.get("llm_use_gpu", True)), id="qwen_llm_use_gpu")
                    yield Label("ForceAligner GGUF 使用 GPU")
                    yield Switch(value=bool(self.runtime.config.force_aligner.get("llm_use_gpu", False)), id="aligner_llm_use_gpu")
                    yield Label("Vulkan 兼容：禁用 coopmat")
                    yield Switch(value=bool(self.runtime.config.server.get("ggml_vk_disable_coopmat", False)), id="ggml_vk_disable_coopmat")
                    yield Label("Vulkan 兼容：禁用 f16")
                    yield Switch(value=bool(self.runtime.config.server.get("ggml_vk_disable_f16", False)), id="ggml_vk_disable_f16")
                    yield Label("启用 NVIDIA GPU 预加速")
                    yield Switch(value=bool(ServerConfig.gpu_boost_enabled), id="gpu_boost_enabled")
                    yield Label("预加速命令")
                    yield Input(value=str(ServerConfig.gpu_boost_cmd), id="gpu_boost_cmd")
                    yield Label("取消预加速命令")
                    yield Input(value=str(ServerConfig.gpu_unboost_cmd), id="gpu_unboost_cmd")
                    yield Label("取消预加速闲置秒数")
                    yield Input(value=str(ServerConfig.gpu_unboost_timeout), id="gpu_unboost_timeout")
                    yield Button("保存 UDP/GPU 配置", id="save_udp_gpu", variant="primary")
            with TabPane("高级配置", id="tab_advanced"):
                with VerticalScroll(classes="panel"):
                    yield Label("完整配置 JSON")
                    yield TextArea.code_editor(json.dumps(self.runtime.config.__dict__, ensure_ascii=False, indent=2, default=str), language="json", id="advanced_json")
                    yield Button("保存完整配置", id="save_advanced", variant="primary")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "CapsWriter-Offline-TUI"
        self.sub_title = "单程序 TUI"
        self.refresh_devices()
        self.refresh_shortcuts()
        await self.runtime.start_runtime()
        self.set_interval(0.5, self.refresh_status)

    async def on_unmount(self) -> None:
        await self.runtime.stop_runtime()

    def refresh_status(self) -> None:
        snap = self.runtime.status.snapshot()
        self.runtime.status.recording_state = "录音中" if self.runtime.state.recording else "空闲"
        if snap["recent_message"]:
            color = "green" if snap["recent_kind"] == "识别" else "red"
            recent_summary = f"[{color}]{snap['recent_kind']}：{snap['recent_message']}[/{color}]"
        else:
            recent_summary = "无"
        summary = (
            f"Worker: [bold]{snap['worker_state']}[/bold]\n"
            f"模型: [bold]{snap['model_state']}[/bold]\n"
            f"模型提示: [yellow]{'未检测到模型，请前往“识别模型”选项卡查看下载说明' if snap['model_error'] else '无'}[/yellow]\n"
            f"麦克风: [bold]{snap['mic_state']}[/bold]\n"
            f"录音: [bold]{'录音中' if self.runtime.state.recording else '空闲'}[/bold]\n"
            f"最近: {recent_summary}"
        )
        self.query_one("#status_summary", Static).update(summary)
        self.query_one("#model_error", Static).update(
            f"[bold red]未检测到模型[/bold red]\n{snap['model_error']}"
            if snap["model_error"]
            else "[green]当前没有模型错误。[/green]"
        )
        logs = snap["logs"]
        log_widget = self.query_one("#logs", RichLog)
        for line in logs[self._last_log_count:]:
            log_widget.write(self._format_log_line(line))
        self._last_log_count = len(logs)

    def _format_log_line(self, line: str) -> str:
        match = LOG_LINE_RE.match(line)
        if not match:
            return escape(line)
        time_text = f"[dim]{escape(match.group('time'))}[/dim]"
        level = match.group("level")
        message = escape(match.group("message"))
        if level == "ERROR":
            return f"{time_text} [red]{message}[/red]"
        if level == "WARNING":
            return f"{time_text} [yellow]{message}[/yellow]"
        if message.startswith("模型输出："):
            return f"{time_text} 模型输出：[cyan]{message[len('模型输出：'):]}[/cyan]"
        if message.startswith("片段拼接："):
            return f"{time_text} 片段拼接：[purple]{message[len('片段拼接：'):]}[/purple]"
        if message.startswith("格式化后："):
            return f"{time_text} 格式化后：[green]{message[len('格式化后：'):]}[/green]"
        return f"{time_text} {message}"

    def refresh_devices(self) -> None:
        options, rows = self._device_options()
        select = self.query_one("#input_device", Select)
        select.set_options(options)
        current = ClientConfig.input_device
        select.value = "__default__" if current is None else str(current)

        table = self.query_one("#device_table", DataTable)
        table.clear(columns=True)
        table.add_columns("ID", "名称", "输入声道", "默认采样率")
        for row in rows:
            table.add_row(*row)

    def _device_options(self) -> tuple[list[tuple[str, str]], list[tuple[str, str, str, str]]]:
        options = [("系统默认", "__default__")]
        rows = []
        try:
            devices = sd.query_devices()
            for index, device in enumerate(devices):
                channels = int(device.get("max_input_channels", 0))
                if channels <= 0:
                    continue
                name = str(device.get("name", f"Device {index}"))
                options.append((f"{index}: {name}", str(index)))
                rows.append((str(index), name, str(channels), str(device.get("default_samplerate", ""))))
        except Exception as exc:
            self.runtime.status.set_error(f"刷新麦克风设备失败: {exc}")
        return options, rows

    def _provider_options(self) -> list[tuple[str, str]]:
        return [
            ("CPU", "CPU"),
            ("DML / DirectML", "DML"),
            ("CUDA", "CUDA"),
            ("TensorRT", "TensorRT"),
        ]

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "restart_worker":
            self.runtime.restart_worker()
        elif button_id == "reopen_mic":
            self.runtime.reopen_microphone()
        elif button_id in {"refresh_devices", "refresh_devices_mic"}:
            self.refresh_devices()
        elif button_id == "minimize_to_tray":
            self.runtime.minimize_to_tray()
        elif button_id == "save_mic":
            self._save_mic()
        elif button_id == "apply_shortcut":
            self._apply_shortcut()
        elif button_id == "add_shortcut":
            self._add_shortcut()
        elif button_id == "delete_shortcut":
            self._delete_shortcut()
        elif button_id == "add_caps_no_toggle":
            self._add_caps_no_toggle()
        elif button_id == "save_shortcuts":
            self._save_shortcuts()
        elif button_id == "save_model":
            self._save_model()
        elif button_id == "open_model_help":
            webbrowser.open(MODEL_HELP_URL)
        elif button_id == "open_model_release":
            webbrowser.open(MODEL_RELEASE_URL)
        elif button_id == "open_model_folder":
            self.runtime.open_model_folder()
        elif button_id == "save_output":
            self._save_output()
        elif button_id == "save_hotword":
            self._save_hotword()
        elif button_id == "save_llm":
            self._save_llm()
        elif button_id == "start_transcribe":
            await self._start_transcribe()
        elif button_id == "save_file_config":
            self._save_file_config()
        elif button_id == "save_udp_gpu":
            self._save_udp_gpu()
        elif button_id == "save_advanced":
            self._save_advanced()

    def action_refresh_devices(self) -> None:
        self.refresh_devices()

    def action_restart_worker(self) -> None:
        self.runtime.restart_worker()

    def _save_mic(self) -> None:
        value = self.query_one("#input_device", Select).value
        device = None if value in (None, "__default__") else int(str(value))
        self.runtime.config.client["input_device"] = device
        self.runtime.save_config()
        self.runtime.reopen_microphone()

    def refresh_shortcuts(self) -> None:
        table = self.query_one("#shortcut_table", DataTable)
        table.clear(columns=True)
        table.add_columns("#", "按键", "类型", "模式", "阻塞", "启用")
        for index, shortcut in enumerate(self.runtime.config.client.get("shortcuts", [])):
            table.add_row(
                str(index),
                str(shortcut.get("key", "")),
                str(shortcut.get("type", "keyboard")),
                self._shortcut_mode_label(shortcut),
                "是" if shortcut.get("suppress", False) else "否",
                "是" if shortcut.get("enabled", True) else "否",
            )
        if self.runtime.config.client.get("shortcuts"):
            self._load_shortcut_form(min(self._selected_shortcut_index, len(self.runtime.config.client["shortcuts"]) - 1))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "shortcut_table":
            return
        self._load_shortcut_form(event.cursor_row)

    def _load_shortcut_form(self, index: int) -> None:
        shortcuts = self.runtime.config.client.get("shortcuts", [])
        if not shortcuts:
            return
        self._selected_shortcut_index = max(0, min(index, len(shortcuts) - 1))
        shortcut = shortcuts[self._selected_shortcut_index]
        self.query_one("#shortcut_key", Input).value = str(shortcut.get("key", ""))
        self.query_one("#shortcut_type", Select).value = str(shortcut.get("type", "keyboard"))
        self.query_one("#shortcut_mode", Select).value = self._shortcut_mode_value(shortcut)
        self.query_one("#shortcut_suppress", Switch).value = bool(shortcut.get("suppress", False))
        self.query_one("#shortcut_enabled", Switch).value = bool(shortcut.get("enabled", True))

    def _shortcut_from_form(self) -> dict[str, Any]:
        mode = str(self.query_one("#shortcut_mode", Select).value)
        key = self.query_one("#shortcut_key", Input).value.strip() or "caps_lock"
        shortcut = {
            "key": key,
            "type": str(self.query_one("#shortcut_type", Select).value),
            "suppress": bool(self.query_one("#shortcut_suppress", Switch).value),
            "hold_mode": mode != "click",
            "no_toggle": mode == "caps_no_toggle",
            "enabled": bool(self.query_one("#shortcut_enabled", Switch).value),
        }
        if mode == "caps_no_toggle":
            shortcut.update({"key": "caps_lock", "type": "keyboard", "suppress": True, "hold_mode": True, "no_toggle": True})
        return shortcut

    def _shortcut_mode_value(self, shortcut: dict[str, Any]) -> str:
        if shortcut.get("key") == "caps_lock" and shortcut.get("no_toggle"):
            return "caps_no_toggle"
        return "hold" if shortcut.get("hold_mode", True) else "click"

    def _shortcut_mode_label(self, shortcut: dict[str, Any]) -> str:
        value = self._shortcut_mode_value(shortcut)
        return {
            "caps_no_toggle": "CapsLock 免切换长按",
            "hold": "长按",
            "click": "单击",
        }[value]

    def _apply_shortcut(self) -> None:
        shortcuts = self.runtime.config.client.setdefault("shortcuts", [])
        if not shortcuts:
            shortcuts.append(self._shortcut_from_form())
        else:
            shortcuts[self._selected_shortcut_index] = self._shortcut_from_form()
        self.refresh_shortcuts()

    def _add_shortcut(self) -> None:
        self.runtime.config.client.setdefault("shortcuts", []).append(self._shortcut_from_form())
        self._selected_shortcut_index = len(self.runtime.config.client["shortcuts"]) - 1
        self.refresh_shortcuts()

    def _delete_shortcut(self) -> None:
        shortcuts = self.runtime.config.client.setdefault("shortcuts", [])
        if shortcuts:
            shortcuts.pop(self._selected_shortcut_index)
            self._selected_shortcut_index = max(0, self._selected_shortcut_index - 1)
        self.refresh_shortcuts()

    def _add_caps_no_toggle(self) -> None:
        self.runtime.config.client.setdefault("shortcuts", []).append({
            "key": "caps_lock",
            "type": "keyboard",
            "suppress": True,
            "hold_mode": True,
            "no_toggle": True,
            "enabled": True,
        })
        self._selected_shortcut_index = len(self.runtime.config.client["shortcuts"]) - 1
        self.refresh_shortcuts()

    def _save_shortcuts(self) -> None:
        self._apply_shortcut()
        self.runtime.config.client["threshold"] = float(self.query_one("#shortcut_threshold", Input).value)
        self.runtime.save_config()
        self.runtime.reload_shortcuts()

    def _save_model(self) -> None:
        self.runtime.config.server["model_type"] = str(self.query_one("#model_type", Select).value)
        self.runtime.config.client["language"] = self.query_one("#language", Input).value
        self.runtime.config.client["context"] = self.query_one("#context", Input).value
        self.runtime.save_config()
        self.runtime.status.log("模型配置已保存；模型类型需重启 worker 后生效")

    def _save_output(self) -> None:
        self.runtime.config.client["paste"] = self.query_one("#paste", Switch).value
        self.runtime.config.client["restore_clip"] = self.query_one("#restore_clip", Switch).value
        self.runtime.config.client["paste_apps"] = json.loads(self.query_one("#paste_apps", TextArea).text)
        self.runtime.config.client["enter_apps"] = json.loads(self.query_one("#enter_apps", TextArea).text)
        self.runtime.config.client["save_audio"] = self.query_one("#save_audio", Switch).value
        self.runtime.config.client["audio_name_len"] = int(self.query_one("#audio_name_len", Input).value)
        self.runtime.config.client["trash_punc"] = self.query_one("#trash_punc", Input).value
        self.runtime.config.client["trash_punc_any"] = self.query_one("#trash_punc_any", Switch).value
        self.runtime.config.client["trash_punc_thresh"] = int(self.query_one("#trash_punc_thresh", Input).value)
        self.runtime.config.client["trash_punc_apps"] = json.loads(self.query_one("#trash_punc_apps", TextArea).text)
        self.runtime.config.client["traditional_convert"] = self.query_one("#traditional_convert", Switch).value
        self.runtime.config.client["traditional_locale"] = str(self.query_one("#traditional_locale", Select).value)
        self.runtime.save_config()

    def _save_hotword(self) -> None:
        self.runtime.config.client["hot"] = self.query_one("#hot", Switch).value
        self.runtime.config.client["hot_rule"] = self.query_one("#hot_rule", Switch).value
        self.runtime.config.client["hot_thresh"] = float(self.query_one("#hot_thresh", Input).value)
        self.runtime.save_config()

    def _save_llm(self) -> None:
        self.runtime.config.client["llm_enabled"] = self.query_one("#llm_enabled", Switch).value
        self.runtime.config.client["llm_stop_key"] = self.query_one("#llm_stop_key", Input).value
        self.runtime.save_config()

    async def _start_transcribe(self) -> None:
        self._save_file_config()
        text = self.query_one("#files_text", TextArea).text
        files = [Path(line.strip()) for line in text.splitlines() if line.strip()]
        asyncio.create_task(self.runtime.transcribe_files(files))

    def _save_file_config(self) -> None:
        self.runtime.config.client["file_seg_duration"] = float(self.query_one("#file_seg_duration", Input).value)
        self.runtime.config.client["file_seg_overlap"] = float(self.query_one("#file_seg_overlap", Input).value)
        self.runtime.config.client["file_save_srt"] = self.query_one("#file_save_srt", Switch).value
        self.runtime.config.client["file_save_txt"] = self.query_one("#file_save_txt", Switch).value
        self.runtime.config.client["file_save_json"] = self.query_one("#file_save_json", Switch).value
        self.runtime.config.client["file_save_merge"] = self.query_one("#file_save_merge", Switch).value
        self.runtime.save_config()

    def _save_udp_gpu(self) -> None:
        self.runtime.config.client["udp_broadcast"] = self.query_one("#udp_broadcast", Switch).value
        self.runtime.config.client["udp_control"] = self.query_one("#udp_control", Switch).value
        self.runtime.config.client["udp_broadcast_targets"] = json.loads(self.query_one("#udp_broadcast_targets", TextArea).text)
        self.runtime.config.client["udp_control_addr"] = self.query_one("#udp_control_addr", Input).value
        self.runtime.config.client["udp_control_port"] = int(self.query_one("#udp_control_port", Input).value)
        dml_pad_to = int(float(self.query_one("#dml_pad_to", Input).value))
        sensevoice_provider = str(self.query_one("#sensevoice_onnx_provider", Select).value)
        fun_provider = str(self.query_one("#fun_onnx_provider", Select).value)
        qwen_provider = str(self.query_one("#qwen_onnx_provider", Select).value)
        self.runtime.config.sensevoice["onnx_provider"] = sensevoice_provider
        self.runtime.config.sensevoice["dml_pad_to"] = dml_pad_to
        self.runtime.config.fun_asr_nano["onnx_provider"] = fun_provider
        self.runtime.config.fun_asr_nano["dml_pad_to"] = dml_pad_to
        self.runtime.config.fun_asr_nano["llm_use_gpu"] = self.query_one("#fun_llm_use_gpu", Switch).value
        self.runtime.config.qwen_asr["onnx_provider"] = qwen_provider
        self.runtime.config.qwen_asr["dml_pad_to"] = dml_pad_to
        self.runtime.config.qwen_asr["llm_use_gpu"] = self.query_one("#qwen_llm_use_gpu", Switch).value
        self.runtime.config.force_aligner["onnx_provider"] = qwen_provider
        self.runtime.config.force_aligner["dml_pad_to"] = dml_pad_to
        self.runtime.config.force_aligner["llm_use_gpu"] = self.query_one("#aligner_llm_use_gpu", Switch).value
        self.runtime.config.server["ggml_vk_disable_coopmat"] = self.query_one("#ggml_vk_disable_coopmat", Switch).value
        self.runtime.config.server["ggml_vk_disable_f16"] = self.query_one("#ggml_vk_disable_f16", Switch).value
        self.runtime.config.server["gpu_boost_enabled"] = self.query_one("#gpu_boost_enabled", Switch).value
        self.runtime.config.server["gpu_boost_cmd"] = self.query_one("#gpu_boost_cmd", Input).value
        self.runtime.config.server["gpu_unboost_cmd"] = self.query_one("#gpu_unboost_cmd", Input).value
        self.runtime.config.server["gpu_unboost_timeout"] = float(self.query_one("#gpu_unboost_timeout", Input).value)
        self.runtime.save_config()
        self.runtime.status.log("UDP/GPU 配置已保存；Provider、GGUF 和 Vulkan 设置需重启 worker 后生效")

    def _save_advanced(self) -> None:
        raw = json.loads(self.query_one("#advanced_json", TextArea).text)
        self.runtime.config = AppConfig(**raw)
        self.runtime.save_config()


def main() -> None:
    files = [Path(arg) for arg in sys.argv[1:] if Path(arg).exists()]
    CapsWriterSingleApp(files=files).run()
