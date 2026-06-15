# coding: utf-8
from __future__ import annotations

import time
from base64 import b64decode

from config_server import ServerConfig as ServerConfig
from core.constants import AudioFormat
from core.protocol import AudioMessage
from core.server.schema import Task


LOCAL_SOCKET_ID = "local-tui"


class AudioCache:
    def __init__(self):
        self.chunks: bytes = b""
        self.offset: float = 0.0
        self.byte_count: int = 0

    @property
    def duration(self) -> float:
        return AudioFormat.bytes_to_seconds(len(self.chunks))

    @property
    def total_duration(self) -> float:
        return AudioFormat.bytes_to_seconds(self.byte_count)

    def reset(self) -> None:
        self.chunks = b""
        self.offset = 0.0
        self.byte_count = 0


class LocalAudioDispatcher:
    """Converts AudioMessage objects into worker Task objects without WebSocket."""

    def __init__(self, worker_manager, status):
        self.worker_manager = worker_manager
        self.status = status
        self._caches: dict[str, AudioCache] = {}

    def submit(self, msg: AudioMessage) -> bool:
        if not self.worker_manager.is_ready:
            self.status.set_error("识别 worker 尚未就绪，音频未发送")
            return False

        cache = self._caches.setdefault(msg.task_id, AudioCache())
        is_start = not bool(cache.chunks)

        if is_start and msg.source == "mic" and ServerConfig.gpu_boost_enabled:
            self.worker_manager.queue_in.put(Task(
                type="cmd",
                task_id="gpu_boost",
                data=b"",
                offset=0,
                overlap=0,
                socket_id=LOCAL_SOCKET_ID,
                is_final=False,
                time_start=0,
                time_submit=0,
                command="gpu_boost",
            ))

        try:
            data = b64decode(msg.data)
            cache.chunks += data
            cache.byte_count += len(data)
            seg_threshold = msg.seg_duration + msg.seg_overlap * 2

            if not msg.is_final:
                segment_bytes = AudioFormat.seconds_to_bytes(msg.seg_duration + msg.seg_overlap)
                stride_bytes = AudioFormat.seconds_to_bytes(msg.seg_duration)
                while cache.duration >= seg_threshold:
                    self._put_task(msg, cache.chunks[:segment_bytes], cache.offset, False)
                    cache.chunks = cache.chunks[stride_bytes:]
                    cache.offset += msg.seg_duration
                return True

            self._put_task(msg, cache.chunks, cache.offset, True)
            cache.reset()
            self._caches.pop(msg.task_id, None)
            return True
        except Exception as exc:
            self.status.set_error(f"本地音频分发失败: {exc}")
            return False

    def _put_task(self, msg: AudioMessage, data: bytes, offset: float, is_final: bool) -> None:
        self.worker_manager.queue_in.put(Task(
            type=msg.source,
            data=data,
            offset=offset,
            task_id=msg.task_id,
            socket_id=LOCAL_SOCKET_ID,
            overlap=msg.seg_overlap,
            is_final=is_final,
            time_start=msg.time_start,
            time_submit=time.time(),
            context=msg.context,
            language=msg.language,
        ))
