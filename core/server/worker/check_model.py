# coding: utf-8
"""
模型检查模块

检查配置的语音模型文件是否存在，如果不存在则提供下载链接。
"""

from pathlib import Path

from config_server import ServerConfig as Config
from config_server import ModelPaths, ModelDownloadLinks
from core.server.state import console
from . import logger


class ModelCheckError(RuntimeError):
    """模型配置或文件缺失时抛出的可恢复错误。"""


def _llama_runtime_files():
    if Config.model_type.lower() in {"fun_asr_nano", "qwen_asr"}:
        return ["ggml.dll", "ggml-base.dll", "llama.dll"]
    return []


def _check_llama_runtime(engine_name: str) -> None:
    required_files = _llama_runtime_files()
    if not required_files:
        return

    candidate_dirs = [
        Path("core") / "server" / "engines" / engine_name / "inference" / "bin",
        Path("core") / "server" / "engines" / "llama" / "bin",
    ]
    for folder in candidate_dirs:
        if all((folder / file_name).exists() for file_name in required_files):
            return

    detail = (
        "\n[bold red]缺少 llama.cpp 运行库[/bold red]\n\n"
        f"当前配置的模型类型：[bold yellow]{Config.model_type.lower()}[/bold yellow]\n\n"
        "GGUF 引擎需要以下 DLL：\n"
        + "".join(f"- {file_name}\n" for file_name in required_files)
        + "\n请下载 llama.cpp Windows 发行包，并将 DLL 解压到以下任一目录：\n"
        + "".join(f"[cyan]{folder}[/cyan]\n" for folder in candidate_dirs)
        + "\n下载地址：\n"
        "[cyan]https://github.com/ggml-org/llama.cpp/releases/download/b7798/llama-b7798-bin-win-vulkan-x64.zip[/cyan]\n"
    )
    logger.error(detail)
    raise ModelCheckError(detail)


def check_model() -> None:
    """
    根据配置的模型类型检查所需的模型文件是否存在
    
    如果模型文件不存在，显示错误信息和下载链接后退出程序。
    
    Raises:
        SystemExit: 当模型类型不支持或模型文件缺失时退出
    """
    model_type = Config.model_type.lower()
    logger.debug(f"检查模型文件, 类型: {model_type}")

    # 根据模型类型确定需要检查的文件
    if model_type == 'fun_asr_nano':
        model_dir = ModelPaths.fun_asr_nano_gguf_dir
        required_files = [
            ModelPaths.fun_asr_nano_gguf_encoder_adaptor,
            ModelPaths.fun_asr_nano_gguf_ctc,
            ModelPaths.fun_asr_nano_gguf_llm_decode,
            ModelPaths.fun_asr_nano_gguf_token,
        ]
        runtime_engine = "fun_asr_gguf"
    elif model_type == 'sensevoice':
        model_dir = ModelPaths.sensevoice_dir
        required_files = [
            ModelPaths.sensevoice_encoder,
            ModelPaths.sensevoice_decoder,
            ModelPaths.sensevoice_tokenizer,
        ]
        runtime_engine = ""
    elif model_type == 'paraformer':
        model_dir = ModelPaths.paraformer_dir
        required_files = [
            ModelPaths.paraformer_model,
            ModelPaths.paraformer_tokens,
        ]
        runtime_engine = ""
    elif model_type == 'qwen_asr':
        model_dir = ModelPaths.qwen3_asr_gguf_dir
        required_files = [
            ModelPaths.qwen3_asr_gguf_encoder_frontend,
            ModelPaths.qwen3_asr_gguf_encoder_backend,
            ModelPaths.qwen3_asr_gguf_llm_decode,
        ]
        runtime_engine = "qwen_asr_gguf"
    else:
        error_msg = f"不支持的模型类型: {Config.model_type}"
        logger.error(error_msg)
        detail = f'''
    [bold red]不支持的模型类型：{Config.model_type}[/bold red]

    请在 config_server.py 中将 ServerConfig.model_type 设置为：
    - 'fun_asr_nano'
    - 'sensevoice'
    - 'paraformer'
    - 'qwen_asr'

        '''
        console.print(detail, style='bright_red')
        raise ModelCheckError(detail)

    # 检查所有必需的文件
    missing_files = []
    for file_path in required_files:
        if not file_path.exists():
            missing_files.append(file_path)
            logger.warning(f"模型文件缺失: [bold yellow]{file_path}[/bold yellow]")

    # 如果有缺失的文件，显示错误信息并提供下载链接
    if missing_files:
        logger.error(f"模型文件检查失败，共 {len(missing_files)} 个文件缺失")
        error_msg = f'\n[bold red]未能找到模型文件[/bold red]\n\n'
        error_msg += f'当前配置的模型类型：[bold yellow]{model_type}[/bold yellow]\n\n'
        for file_path in missing_files:
            error_msg += f'未找到：[bold yellow]{file_path.name}[/bold yellow]\n'

        # 检查是否有文件被错放到上级目录
        for parent in model_dir.parents:
            if any((parent / fp.name).exists() for fp in missing_files):
                error_msg += f'\n模型文件似乎错放到了上级目录，请确保放到：[bold yellow]{model_dir}[/bold yellow]\n'
                break

        # 提供统一下载页面链接
        error_msg += f'\n模型发布页面：\n'
        error_msg += f'[cyan]{ModelDownloadLinks.models_page}[/cyan]\n\n'

        error_msg += f'请根据发布页说明，将模型解压到 [cyan]{ModelPaths.model_dir}[/cyan] 下的正确目录\n'
        error_msg += '\n'
        
        logger.error(error_msg)
        raise ModelCheckError(error_msg)

    if runtime_engine:
        _check_llama_runtime(runtime_engine)

    # 所有必需文件检查通过
    logger.info(f"模型文件检查通过 ({model_type})")
    console.print(f'[green4]模型文件检查通过 ({model_type})', end='\n\n')
