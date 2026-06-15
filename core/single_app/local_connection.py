# coding: utf-8
from __future__ import annotations

from typing import Optional

from core.protocol import AudioMessage, RecognitionMessage


class LocalConnectionManager:
    """Drop-in replacement for the old WebSocketManager in the single app."""

    def __init__(self, worker_manager, dispatcher):
        self.worker_manager = worker_manager
        self.dispatcher = dispatcher

    @property
    def is_connected(self) -> bool:
        return self.worker_manager.is_ready

    async def connect(self) -> bool:
        return self.worker_manager.is_ready

    async def send(self, message: AudioMessage) -> bool:
        return self.dispatcher.submit(message)

    async def receive(self) -> Optional[RecognitionMessage]:
        return await self.worker_manager.result_queue.get()

    async def close(self) -> None:
        return None

    def close_sync(self) -> None:
        return None
