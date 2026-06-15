# coding: utf-8
from __future__ import annotations

from pathlib import Path


class TrayController:
    """Single-program wrapper around the shared tray implementation."""

    def __init__(self, app):
        self.app = app
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            from core.ui.tray import enable_min_to_tray

            icon_path = Path(self.app.base_dir) / "assets" / "icon.ico"
            enable_min_to_tray(
                "CapsWriter-Offline-TUI",
                str(icon_path),
                exit_callback=self.quit,
                more_options=[
                    ("重开麦克风", self.reopen_microphone),
                    ("重启 worker", self.restart_worker),
                ],
                start_hidden=False,
            )
            self._started = True
            self.app.status.log("托盘图标已启用")
        except Exception as exc:
            self.app.status.set_error(f"启用托盘失败: {exc}")

    def capture_window(self) -> None:
        self.start()

    def minimize(self) -> None:
        self.start()
        try:
            from core.ui.tray import hide_to_tray

            if hide_to_tray():
                self.app.status.log("已最小化到托盘")
            else:
                self.app.status.set_error("无法定位当前终端窗口，最小化到托盘失败")
        except Exception as exc:
            self.app.status.set_error(f"最小化到托盘失败: {exc}")

    def restore(self) -> None:
        try:
            from core.ui.tray import restore_from_tray

            if restore_from_tray():
                self.app.status.log("已从托盘恢复")
            else:
                self.app.status.set_error("无法定位当前终端窗口，无法从托盘恢复")
        except Exception as exc:
            self.app.status.set_error(f"从托盘恢复失败: {exc}")

    def stop(self) -> None:
        try:
            from core.ui.tray import stop_tray

            stop_tray()
            self._started = False
        except Exception:
            pass

    def quit(self, icon=None, item=None) -> None:
        tui = getattr(self.app, "tui", None)
        if tui:
            tui.call_from_thread(tui.exit)

    def reopen_microphone(self, icon=None, item=None) -> None:
        self.app.reopen_microphone()

    def restart_worker(self, icon=None, item=None) -> None:
        self.app.restart_worker()
