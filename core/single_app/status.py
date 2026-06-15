# coding: utf-8
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock
from typing import Optional


@dataclass
class AppStatus:
    worker_state: str = "未启动"
    model_state: str = "未加载"
    mic_state: str = "未启动"
    recording_state: str = "空闲"
    last_error: str = ""
    model_error: str = ""
    last_recognition: str = ""
    recent_kind: str = ""
    recent_message: str = ""
    logs: list[str] = field(default_factory=list)

    _lock: Lock = field(default_factory=Lock, repr=False)

    def log(self, message: str, level: Optional[str] = "INFO") -> None:
        if level:
            line = f"{datetime.now():%H:%M:%S} [{level}] {message}"
        else:
            line = f"{datetime.now():%H:%M:%S} {message}"
        with self._lock:
            self.logs.append(line)
            self.logs = self.logs[-300:]

    def set_error(self, message: str) -> None:
        self.last_error = message
        self.recent_kind = "错误"
        self.recent_message = message
        self.log(message, "ERROR")

    def set_recognition(self, message: str) -> None:
        self.last_recognition = message
        self.recent_kind = "识别"
        self.recent_message = message

    def set_model_error(self, message: str) -> None:
        self.model_error = message
        self.model_state = "模型错误"
        self.log("未检测到可用模型，请前往“识别模型”选项卡查看下载说明", "ERROR")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "worker_state": self.worker_state,
                "model_state": self.model_state,
                "mic_state": self.mic_state,
                "recording_state": self.recording_state,
                "last_error": self.last_error,
                "model_error": self.model_error,
                "last_recognition": self.last_recognition,
                "recent_kind": self.recent_kind,
                "recent_message": self.recent_message,
                "logs": list(self.logs),
            }
