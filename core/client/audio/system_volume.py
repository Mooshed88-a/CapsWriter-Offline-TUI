# coding: utf-8
"""
Windows system playback volume helper.

Used to temporarily lower speaker volume while CapsLock push-to-talk is active.
"""

from __future__ import annotations

import ctypes
import platform
import threading
import uuid
from dataclasses import dataclass
from typing import Optional

from . import logger


S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850
COINIT_MULTITHREADED = 0
CLSCTX_ALL = 0x17
ERENDER = 0
EMULTIMEDIA = 1


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_uint8 * 8),
    ]


CLSID_MMDeviceEnumerator = GUID.from_buffer_copy(
    uuid.UUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}").bytes_le
)
IID_IMMDeviceEnumerator = GUID.from_buffer_copy(
    uuid.UUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}").bytes_le
)
IID_IAudioEndpointVolume = GUID.from_buffer_copy(
    uuid.UUID("{5CDF2C82-841E-4546-9722-0CF74078229A}").bytes_le
)


@dataclass
class VolumeSnapshot:
    level: float
    muted: bool


class WindowsEndpointVolume:
    """Minimal ctypes wrapper around IAudioEndpointVolume."""

    def __init__(self):
        if platform.system() != "Windows":
            raise RuntimeError("system volume control is only supported on Windows")
        self._ole32 = ctypes.WinDLL("ole32")
        self._ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self._ole32.CoInitializeEx.restype = ctypes.c_long
        self._ole32.CoUninitialize.argtypes = []
        self._ole32.CoUninitialize.restype = None
        self._ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ole32.CoCreateInstance.restype = ctypes.c_long

    def get_state(self) -> VolumeSnapshot:
        return self._with_endpoint(self._read_state)

    def set_state(self, snapshot: VolumeSnapshot) -> None:
        def apply(endpoint):
            self._set_level(endpoint, snapshot.level)
            self._set_mute(endpoint, snapshot.muted)

        self._with_endpoint(apply)

    def set_level(self, level: float) -> None:
        self._with_endpoint(lambda endpoint: self._set_level(endpoint, level))

    def _with_endpoint(self, action):
        initialized = self._initialize_com()
        endpoint = None
        try:
            endpoint = self._activate_endpoint_volume()
            return action(endpoint)
        finally:
            if endpoint:
                self._release(endpoint)
            if initialized:
                self._ole32.CoUninitialize()

    def _initialize_com(self) -> bool:
        hr = self._ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        if hr in (S_OK, S_FALSE):
            return True
        if hr == RPC_E_CHANGED_MODE:
            return False
        self._raise_for_hr(hr, "CoInitializeEx")
        return False

    def _activate_endpoint_volume(self):
        enumerator = ctypes.c_void_p()
        hr = self._ole32.CoCreateInstance(
            ctypes.byref(CLSID_MMDeviceEnumerator),
            None,
            CLSCTX_ALL,
            ctypes.byref(IID_IMMDeviceEnumerator),
            ctypes.byref(enumerator),
        )
        self._raise_for_hr(hr, "CoCreateInstance(IMMDeviceEnumerator)")

        device = ctypes.c_void_p()
        try:
            get_default_audio_endpoint = self._method(
                enumerator, 4, ctypes.c_long, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
            )
            hr = get_default_audio_endpoint(enumerator, ERENDER, EMULTIMEDIA, ctypes.byref(device))
            self._raise_for_hr(hr, "IMMDeviceEnumerator.GetDefaultAudioEndpoint")

            endpoint = ctypes.c_void_p()
            activate = self._method(
                device,
                3,
                ctypes.c_long,
                ctypes.POINTER(GUID),
                ctypes.c_ulong,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            hr = activate(
                device,
                ctypes.byref(IID_IAudioEndpointVolume),
                CLSCTX_ALL,
                None,
                ctypes.byref(endpoint),
            )
            self._raise_for_hr(hr, "IMMDevice.Activate(IAudioEndpointVolume)")
            return endpoint
        finally:
            if device:
                self._release(device)
            self._release(enumerator)

    def _read_state(self, endpoint) -> VolumeSnapshot:
        level = ctypes.c_float()
        get_level = self._method(endpoint, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_float))
        hr = get_level(endpoint, ctypes.byref(level))
        self._raise_for_hr(hr, "IAudioEndpointVolume.GetMasterVolumeLevelScalar")

        muted = ctypes.c_int()
        get_mute = self._method(endpoint, 15, ctypes.c_long, ctypes.POINTER(ctypes.c_int))
        hr = get_mute(endpoint, ctypes.byref(muted))
        self._raise_for_hr(hr, "IAudioEndpointVolume.GetMute")

        return VolumeSnapshot(level=float(level.value), muted=bool(muted.value))

    def _set_level(self, endpoint, level: float) -> None:
        set_level = self._method(endpoint, 7, ctypes.c_long, ctypes.c_float, ctypes.c_void_p)
        hr = set_level(endpoint, ctypes.c_float(self._clamp_level(level)), None)
        self._raise_for_hr(hr, "IAudioEndpointVolume.SetMasterVolumeLevelScalar")

    def _set_mute(self, endpoint, muted: bool) -> None:
        set_mute = self._method(endpoint, 14, ctypes.c_long, ctypes.c_int, ctypes.c_void_p)
        hr = set_mute(endpoint, int(muted), None)
        self._raise_for_hr(hr, "IAudioEndpointVolume.SetMute")

    def _release(self, interface) -> None:
        release = self._method(interface, 2, ctypes.c_ulong)
        release(interface)

    def _method(self, interface, index, restype, *argtypes):
        vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

    @staticmethod
    def _clamp_level(level: float) -> float:
        return max(0.0, min(1.0, float(level)))

    @staticmethod
    def _raise_for_hr(hr: int, context: str) -> None:
        if hr < 0:
            raise OSError(f"{context} failed with HRESULT 0x{hr & 0xFFFFFFFF:08X}")


class SystemVolumeManager:
    """Temporarily sets system playback volume and restores it afterwards."""

    def __init__(self, target_level: float = 0.20):
        self.target_level = max(0.0, min(1.0, float(target_level)))
        self._lock = threading.Lock()
        self._endpoint: Optional[WindowsEndpointVolume] = None
        self._saved: Optional[VolumeSnapshot] = None
        self._active_count = 0
        self._warned_unavailable = False

    def lower_for_recording(self) -> None:
        with self._lock:
            if self._active_count > 0:
                self._active_count += 1
                return

            try:
                endpoint = self._get_endpoint()
                self._saved = endpoint.get_state()
                endpoint.set_level(self.target_level)
                self._active_count = 1
                logger.debug(
                    f"CapsLock 录音期间系统播放音量调整为 {int(self.target_level * 100)}%"
                )
            except Exception as e:
                self._saved = None
                self._active_count = 0
                self._warn_once(f"调整系统播放音量失败，已跳过: {e}")

    def restore_after_recording(self) -> None:
        with self._lock:
            if self._active_count <= 0:
                return

            self._active_count -= 1
            if self._active_count > 0:
                return

            self._restore_locked()

    def restore_now(self) -> None:
        with self._lock:
            self._active_count = 0
            self._restore_locked()

    def _restore_locked(self) -> None:
        if not self._saved:
            return

        saved = self._saved
        self._saved = None
        try:
            self._get_endpoint().set_state(saved)
            logger.debug("已恢复 CapsLock 录音前的系统播放音量")
        except Exception as e:
            self._warn_once(f"恢复系统播放音量失败: {e}")

    def _get_endpoint(self) -> WindowsEndpointVolume:
        if self._endpoint is None:
            self._endpoint = WindowsEndpointVolume()
        return self._endpoint

    def _warn_once(self, message: str) -> None:
        if not self._warned_unavailable:
            logger.warning(message)
            self._warned_unavailable = True
