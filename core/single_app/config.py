# coding: utf-8
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from config_client import ClientConfig
from config_server import (
    ForceAlignerGGUFArgs,
    FunASRNanoGGUFArgs,
    ModelPaths,
    ParaformerArgs,
    Qwen3ASRGGUFArgs,
    SenseVoiceArgs,
    ServerConfig,
)


CONFIG_VERSION = 1


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION

    client: dict[str, Any] = field(default_factory=dict)
    server: dict[str, Any] = field(default_factory=dict)
    model_paths: dict[str, Any] = field(default_factory=dict)
    paraformer: dict[str, Any] = field(default_factory=dict)
    sensevoice: dict[str, Any] = field(default_factory=dict)
    fun_asr_nano: dict[str, Any] = field(default_factory=dict)
    qwen_asr: dict[str, Any] = field(default_factory=dict)
    force_aligner: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def defaults(cls) -> "AppConfig":
        client = _class_public_values(ClientConfig)
        client.setdefault("input_device", None)
        client.setdefault("auto_start_mic", True)
        client.setdefault("auto_start_worker", True)
        server = _class_public_values(ServerConfig)
        return cls(
            client=client,
            server=server,
            model_paths=_class_public_values(ModelPaths),
            paraformer=_class_public_values(ParaformerArgs),
            sensevoice=_class_public_values(SenseVoiceArgs),
            fun_asr_nano=_class_public_values(FunASRNanoGGUFArgs),
            qwen_asr=_class_public_values(Qwen3ASRGGUFArgs),
            force_aligner=_class_public_values(ForceAlignerGGUFArgs),
        )

    @classmethod
    def load(cls, base_dir: Path | None = None) -> "AppConfig":
        path = config_path(base_dir)
        defaults = cls.defaults()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            defaults.save(path)
            defaults.apply_compat()
            return defaults

        raw = json.loads(path.read_text("utf-8"))
        merged = asdict(defaults)
        _deep_update(merged, raw)
        cfg = cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})
        cfg.version = CONFIG_VERSION
        cfg.save(path)
        cfg.apply_compat()
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_json_ready(asdict(self)), ensure_ascii=False, indent=2), "utf-8")

    def apply_compat(self) -> None:
        _apply_to_class(ClientConfig, self.client)
        _apply_to_class(ServerConfig, self.server)
        _apply_gpu_env(self.server)
        _apply_to_class(ModelPaths, self.model_paths, path_values=True)
        _apply_to_class(ParaformerArgs, self.paraformer)
        _apply_to_class(SenseVoiceArgs, self.sensevoice)
        _apply_to_class(FunASRNanoGGUFArgs, self.fun_asr_nano)
        _apply_to_class(Qwen3ASRGGUFArgs, self.qwen_asr)
        _apply_to_class(ForceAlignerGGUFArgs, self.force_aligner)


def config_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path(__file__).parents[2]
    return root / "config" / "capswriter.json"


def _class_public_values(cls: type) -> dict[str, Any]:
    values = {}
    for name, value in vars(cls).items():
        if name.startswith("_") or callable(value) or isinstance(value, (staticmethod, classmethod)):
            continue
        values[name] = _json_ready(value)
    return values


def _apply_to_class(cls: type, values: dict[str, Any], path_values: bool = False) -> None:
    for name, value in values.items():
        if path_values and isinstance(value, str):
            value = Path(value)
        setattr(cls, name, value)


def _apply_gpu_env(server: dict[str, Any]) -> None:
    import os

    env_flags = {
        "ggml_vk_disable_coopmat": "GGML_VK_DISABLE_COOPMAT",
        "ggml_vk_disable_f16": "GGML_VK_DISABLE_F16",
    }
    for key, env_name in env_flags.items():
        if server.get(key):
            os.environ[env_name] = "1"
        else:
            os.environ.pop(env_name, None)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple):
        return [_json_ready(v) for v in value]
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    return value


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
