# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import time
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from config_client import ClientConfig as Config
from core.client.state import console
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器

    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭

    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """

    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms

    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._last_auto_reopen = 0.0

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数

        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # 只在录音状态时处理数据
        if not self.state.recording:
            return

        import asyncio

        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )

    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return

        logger.info("音频流意外结束，正在尝试重启...")
        self.reopen()

    def start(self) -> Optional[sd.InputStream]:
        """
        启动音频流

        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream

        # 检测音频设备
        device_name = "未知设备"
        try:
            selected_device = getattr(Config, "input_device", None)
            device = sd.query_devices(selected_device, kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            console.print(
                f'使用默认音频设备：[italic]{device_name}，声道数：{self._channels}',
                end='\n\n'
            )
            logger.info(f"找到音频设备: {device_name}, 声道数: {self._channels}")
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except sd.PortAudioError as e:
            message = f"未找到可用麦克风设备: {e}"
            logger.error(message)
            console.print(f"[bold red]{message}[/bold red]")
            if hasattr(self.app, "status"):
                self.app.status.mic_state = "设备不可用"
                self.app.status.set_error(message)
            return None

        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=getattr(Config, "input_device", None),
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()

            self.state.stream = stream
            self._running = True
            if hasattr(self.app, "status"):
                self.app.status.mic_state = f"已打开：{device_name}"
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )
            return stream

        except sd.PortAudioError as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            if hasattr(self.app, "status"):
                self.app.status.mic_state = "打开失败"
                self.app.status.set_error(f"创建音频流失败: {e}")
            if '-9999' in str(e):
                console.print("""
[bold red]检测到麦克风被占用或权限异常（错误码 -9999）[/bold red]
请尝试以下解决方案：

  1. 设置 > 隐私和安全性 > 麦克风，将「允许桌面应用访问麦克风」打开
  2. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「允许应用程序独占控制该设备」
  3. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「增强效果」
""")
            return None
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            if hasattr(self.app, "status"):
                self.app.status.mic_state = "打开失败"
                self.app.status.set_error(f"创建音频流失败: {e}")
            return None

    def stop(self) -> None:
        """停止音频流"""
        if not self._running:
            return

        self._running = False  # 标记为停止
        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已停止")
            except Exception as e:
                logger.debug(f"停止音频流时发生错误: {e}")
            finally:
                self.state.stream = None
                if hasattr(self.app, "status"):
                    self.app.status.mic_state = "已停止"

    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流

        Returns:
            新创建的音频输入流
        """
        logger.info("正在重启音频流...")

        # 停止旧流
        self.stop()

        # 重载 PortAudio，更新设备列表
        try:
            sd._terminate()
            sd._ffi.dlclose(sd._lib)
            sd._lib = sd._ffi.dlopen(sd._libname)
            sd._initialize()
        except Exception as e:
            logger.warning(f"重载 PortAudio 时发生警告: {e}")

        # 等待设备稳定
        time.sleep(0.1)

        # 启动新流
        return self.start()

    def auto_reopen_if_unavailable(self) -> None:
        status = getattr(self.app, "status", None)
        last_error = getattr(status, "last_error", "")
        if "未找到可用麦克风设备" not in last_error:
            return
        now = time.time()
        if now - self._last_auto_reopen < 1.0:
            return
        self._last_auto_reopen = now
        if status:
            status.log("录音结束后检测到麦克风不可用，自动重开麦克风")
        timer = threading.Timer(0.2, self.reopen)
        timer.daemon = True
        timer.start()
