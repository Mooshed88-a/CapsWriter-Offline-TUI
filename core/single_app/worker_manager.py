# coding: utf-8
from __future__ import annotations

import asyncio
import queue
import re
import threading
import time
from multiprocessing import Manager, Process, Queue
from typing import Optional

from core.protocol import RecognitionMessage
from core.server.schema import Result
from core.server.worker import start_worker
from core.server.worker.check_model import ModelCheckError, check_model

from .local_dispatcher import LOCAL_SOCKET_ID


_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RICH_TAG_RE = re.compile(
    r"\[(?:/|/?(?:bold|italic|underline|red|green|green4|cyan|purple|yellow|blue|white|bright_red)[^\]]*)\]"
)


class WorkerManager:
    def __init__(self, status):
        self.status = status
        self.queue_in = Queue()
        self.queue_out = Queue()
        self.result_queue: asyncio.Queue[RecognitionMessage] = asyncio.Queue()
        self._manager = None
        self.sockets_id = None
        self.process: Optional[Process] = None
        self.is_ready = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pump_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stopping = False

    def start(self, loop: asyncio.AbstractEventLoop) -> bool:
        if self.process and self.process.is_alive():
            return True

        self._loop = loop
        self._stopping = False
        self.is_ready = False
        self.status.worker_state = "启动中"
        self.status.model_state = "检查模型"
        self.status.log("正在检查模型文件")

        try:
            check_model()
        except ModelCheckError as exc:
            self.status.worker_state = "未启动"
            self.status.set_model_error(str(exc))
            return False

        self._manager = Manager()
        self.sockets_id = self._manager.list([LOCAL_SOCKET_ID])
        self.process = Process(
            target=start_worker,
            args=(self.queue_in, self.queue_out, self.sockets_id, None),
            daemon=True,
        )
        self.process.start()
        self.status.worker_state = f"运行中 PID {self.process.pid}"
        self.status.model_state = "加载中"
        self.status.log(f"识别 worker 已启动 PID={self.process.pid}")

        self._pump_thread = threading.Thread(target=self._pump_results, daemon=True)
        self._pump_thread.start()
        self._monitor_thread = threading.Thread(target=self._monitor_process, daemon=True)
        self._monitor_thread.start()
        return True

    def stop(self) -> None:
        self._stopping = True
        self.is_ready = False
        try:
            self.queue_in.put(None)
        except Exception:
            pass
        if self.process and self.process.is_alive():
            self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.terminate()
        if self._manager:
            try:
                self._manager.shutdown()
            except Exception:
                pass
        self.status.worker_state = "已停止"
        self.status.model_state = "未加载"

    def restart(self, loop: asyncio.AbstractEventLoop) -> bool:
        self.stop()
        return self.start(loop)

    def _pump_results(self) -> None:
        while not self._stopping:
            try:
                item = self.queue_out.get(timeout=0.2)
            except queue.Empty:
                continue
            except (EOFError, OSError):
                return

            if item is True:
                self.is_ready = True
                self.status.model_state = "已加载"
                self.status.worker_state = "就绪"
                self.status.log("模型加载完成，worker 就绪")
                continue
            if item is None:
                return
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "worker_log":
                    for line in self._clean_worker_log(item.get("text", "")):
                        self.status.log(line, None)
                elif item_type == "worker_error":
                    self.is_ready = False
                    self.status.worker_state = "异常退出"
                    self.status.model_state = "未加载"
                    message = item.get("message", "未知错误")
                    self.status.set_error(f"识别 worker 异常退出：{message}")
                    traceback_text = item.get("traceback", "")
                    for line in traceback_text.splitlines():
                        self.status.log(f"worker traceback: {line}", "ERROR")
                continue
            if isinstance(item, Result):
                msg = RecognitionMessage(
                    task_id=item.task_id,
                    is_final=item.is_final,
                    duration=item.duration,
                    time_start=item.time_start,
                    time_submit=item.time_submit,
                    time_complete=item.time_complete,
                    text=item.text,
                    text_accu=item.text_accu,
                    tokens=item.tokens,
                    timestamps=item.timestamps,
                )
                if msg.is_final:
                    self.status.set_recognition(msg.text)
                if self._loop and not self._loop.is_closed():
                    asyncio.run_coroutine_threadsafe(self.result_queue.put(msg), self._loop)

    def _clean_worker_log(self, text: str) -> list[str]:
        clean = _ANSI_RE.sub("", str(text)).replace("\r", "")
        clean = _RICH_TAG_RE.sub("", clean)
        lines = []
        for line in clean.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(("模型输出", "片段拼接", "格式化后")):
                lines.append(line)
            else:
                lines.append(line[:240])
        return lines

    def _monitor_process(self) -> None:
        while not self._stopping and self.process:
            if not self.process.is_alive():
                code = self.process.exitcode
                if code not in (0, None):
                    self.is_ready = False
                    self.status.worker_state = f"异常退出 {code}"
                    self.status.model_state = "未加载"
                    self.status.set_error(f"识别 worker 异常退出，退出码：{code}")
                return
            time.sleep(0.5)
