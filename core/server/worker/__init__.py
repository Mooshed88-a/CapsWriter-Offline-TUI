# coding: utf-8
"""
识别子进程工作包 (Worker Package)

包含模型加载、任务处理和 Worker 门面类。
"""

import io
import traceback
from multiprocessing import Queue
from multiprocessing.managers import ListProxy
from .. import logger
from .worker import RecognizerWorker


class _QueueTextWriter(io.TextIOBase):
    """Line-buffer worker stdout/stderr into the parent queue."""

    def __init__(self, queue_out: Queue, stream_name: str):
        self.queue_out = queue_out
        self.stream_name = stream_name
        self._buffer = ""

    def writable(self):
        return True

    def write(self, text):
        if not text:
            return 0
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line.rstrip())
        return len(text)

    def flush(self):
        if self._buffer.strip():
            self._emit(self._buffer.rstrip())
        self._buffer = ""

    def _emit(self, text):
        if text:
            try:
                self.queue_out.put({
                    "type": "worker_log",
                    "stream": self.stream_name,
                    "text": text,
                })
            except Exception:
                pass


def start_worker(queue_in: Queue, queue_out: Queue, sockets_id: ListProxy, stdin_fn: int):
    """识别子进程启动入口"""
    import sys

    sys.stdout = _QueueTextWriter(queue_out, "stdout")
    sys.stderr = _QueueTextWriter(queue_out, "stderr")
    try:
        from core.single_app.config import AppConfig

        AppConfig.load()
    except Exception as exc:
        logger.warning(f"Worker 加载单程序配置失败，使用源码默认配置: {exc}")
    try:
        worker = RecognizerWorker(queue_in, queue_out, sockets_id, stdin_fn)
        worker.run()
    except Exception as exc:
        tb = traceback.format_exc()
        try:
            queue_out.put({
                "type": "worker_error",
                "message": str(exc),
                "traceback": tb,
            })
        except Exception:
            pass
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

__all__ = ['RecognizerWorker', 'start_worker']
