import os
import json
import platform
from pathlib import Path

# 版本信息
__version__ = '2.4'

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _parse_bool(value, default: bool) -> bool:
    """解析布尔配置值，兼容字符串/数字。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _load_local_overrides() -> dict:
    """
    读取本地覆盖配置文件（可选）。

    默认文件名：config_server.local.json
    可通过环境变量 CAPSWRITER_SERVER_CONFIG 指定路径。
    """
    path = os.getenv("CAPSWRITER_SERVER_CONFIG", "config_server.local.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


_OVERRIDES = _load_local_overrides()


def _get_override(*keys, default=None):
    """从覆盖配置中读取多层键。"""
    current = _OVERRIDES
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _cfg(env_name: str, *keys, default=None):
    """优先读环境变量，其次读本地覆盖文件，最后使用默认值。"""
    value = os.getenv(env_name)
    if value is not None:
        return value
    return _get_override(*keys, default=default)


def _cfg_bool(env_name: str, *keys, default: bool = False) -> bool:
    return _parse_bool(_cfg(env_name, *keys, default=default), default)


def _cfg_int(env_name: str, *keys, default: int) -> int:
    value = _cfg(env_name, *keys, default=default)
    try:
        return int(value)
    except Exception:
        return default


# 服务端配置
class ServerConfig:
    addr = str(_cfg("CAPSWRITER_SERVER_ADDR", "server", "addr", default='0.0.0.0'))
    port = str(_cfg("CAPSWRITER_SERVER_PORT", "server", "port", default='6016'))

    # 语音模型选择：'fun_asr_nano', 'sensevoice', 'paraformer'
    model_type = str(_cfg("CAPSWRITER_MODEL_TYPE", "server", "model_type", default='fun_asr_nano'))

    format_num = True       # 输出时是否将中文数字转为阿拉伯数字
    format_spell = True     # 输出时是否调整中英之间的空格

    enable_tray = _cfg_bool(
        "CAPSWRITER_SERVER_ENABLE_TRAY",
        "server", "enable_tray",
        default=(platform.system() == 'Windows')
    )

    # 日志配置
    log_level = 'INFO'        # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'





class ModelDownloadLinks:
    """模型下载链接配置"""
    # 统一导向 GitHub Release 模型页面
    models_page = "https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models"


class ModelPaths:
    """模型文件路径配置"""

    # 基础目录
    model_dir = Path() / 'models'

    # Paraformer 模型路径
    paraformer_dir = model_dir / 'Paraformer' / "speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx"
    paraformer_model = paraformer_dir / 'model.onnx'
    paraformer_tokens = paraformer_dir / 'tokens.txt'

    # 标点模型路径
    punc_model_dir = model_dir / 'Punct-CT-Transformer' / 'sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12' / 'model.onnx'

    # SenseVoice 模型路径，自带标点
    sensevoice_dir = model_dir / 'SenseVoice-Small' / 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17'
    sensevoice_model = sensevoice_dir / 'model.onnx'
    sensevoice_tokens = sensevoice_dir / 'tokens.txt'

    # Fun-ASR-Nano 模型路径，自带标点
    # 默认启用了 DML 对 Encoder 和 CTC 进行加速，显卡用 fp16 模型会更快
    # 但若禁用了 DML，则建议把 Encoder 和 CTC 的 fp16 改为 int8，让可以 CPU 运行更快
    fun_asr_nano_gguf_dir = model_dir / 'Fun-ASR-Nano' / 'Fun-ASR-Nano-GGUF'
    fun_asr_nano_gguf_encoder_adaptor = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx'
    fun_asr_nano_gguf_ctc = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-CTC.fp16.onnx'
    fun_asr_nano_gguf_llm_decode = fun_asr_nano_gguf_dir / 'Fun-ASR-Nano-Decoder.q8_0.gguf'
    fun_asr_nano_gguf_token = fun_asr_nano_gguf_dir / 'tokens.txt'
    fun_asr_nano_gguf_hotwords = Path() / 'hot-server.txt'



class ParaformerArgs:
    """Paraformer 模型参数配置"""

    paraformer = ModelPaths.paraformer_model.as_posix()
    tokens = ModelPaths.paraformer_tokens.as_posix()
    num_threads = 4
    sample_rate = 16000
    feature_dim = 80
    decoding_method = 'greedy_search'
    provider = 'cpu'
    debug = False


class SenseVoiceArgs:
    """SenseVoice 模型参数配置"""

    model = ModelPaths.sensevoice_model.as_posix()
    tokens = ModelPaths.sensevoice_tokens.as_posix()
    use_itn = True
    language = 'zh'
    num_threads = _cfg_int(
        "CAPSWRITER_SENSEVOICE_NUM_THREADS",
        "sensevoice", "num_threads",
        default=4
    )

    # 设备策略：auto/gpu/cpu
    device_mode = str(
        _cfg(
            "CAPSWRITER_SENSEVOICE_DEVICE_MODE",
            "sensevoice", "device_mode",
            default=("gpu" if platform.system() == "Darwin" else "auto")
        )
    ).lower()

    # provider 可选值示例：cpu/coreml/cuda
    provider = str(
        _cfg(
            "CAPSWRITER_SENSEVOICE_PROVIDER",
            "sensevoice", "provider",
            default=("coreml" if platform.system() == "Darwin" else "cpu")
        )
    ).lower()

    if device_mode == "cpu":
        provider = "cpu"
    elif device_mode == "gpu":
        if platform.system() == "Darwin":
            provider = "coreml"
        elif platform.system() == "Windows" and provider == "cpu":
            provider = "cuda"

    debug = False


class FunASRNanoGGUFArgs:
    """Fun-ASR-Nano-GGUF 模型参数配置"""

    # 模型路径
    encoder_onnx_path = ModelPaths.fun_asr_nano_gguf_encoder_adaptor.as_posix()
    ctc_onnx_path = ModelPaths.fun_asr_nano_gguf_ctc.as_posix()
    decoder_gguf_path = ModelPaths.fun_asr_nano_gguf_llm_decode.as_posix()
    tokens_path = ModelPaths.fun_asr_nano_gguf_token.as_posix()
    hotwords_path = ModelPaths.fun_asr_nano_gguf_hotwords.as_posix()

    # 设备策略：auto/gpu/cpu（用于快速切换）
    device_mode = str(
        _cfg(
            "CAPSWRITER_DEVICE_MODE",
            "fun_asr_nano", "device_mode",
            default=("gpu" if platform.system() == "Darwin" else "auto")
        )
    ).lower()

    # 显卡加速
    dml_enable = _cfg_bool(
        "CAPSWRITER_DML_ENABLE",
        "fun_asr_nano", "dml_enable",
        default=False
    )
    coreml_enable = _cfg_bool(
        "CAPSWRITER_COREML_ENABLE",
        "fun_asr_nano", "coreml_enable",
        default=(platform.system() == 'Darwin')
    )
    metal_enable = _cfg_bool(
        "CAPSWRITER_METAL_ENABLE",
        "fun_asr_nano", "metal_enable",
        default=(platform.system() == 'Darwin')
    )
    vulkan_enable = _cfg_bool(
        "CAPSWRITER_VULKAN_ENABLE",
        "fun_asr_nano", "vulkan_enable",
        default=(platform.system() == 'Windows')
    )
    vulkan_force_fp32 = False   # 是否强制 FP32 计算（如果 GPU 是 Intel 集显且出现精度溢出，可设为 True）
    
    # 模型细节
    enable_ctc = True           # 是否启用 CTC 热词检索
    n_predict = 512             # LLM 最大生成 token 数
    n_threads = None            # 线程数，None 表示自动
    similar_threshold = 0.6     # 热词相似度阈值
    max_hotwords = 20           # 每次替换的最大热词数
    verbose = False

    # ONNX 音频 padding 策略：
    # auto / fixed30 / fixed5 / dynamic
    onnx_padding_mode = str(
        _cfg(
            "CAPSWRITER_ONNX_PADDING_MODE",
            "fun_asr_nano", "onnx_padding_mode",
            default="auto"
        )
    ).lower()
    onnx_padding_secs = _cfg_int(
        "CAPSWRITER_ONNX_PADDING_SECS",
        "fun_asr_nano", "onnx_padding_secs",
        default=30
    )

    # 设备模式兜底：允许一键切 CPU-only 或 GPU-preferred
    if device_mode == "cpu":
        dml_enable = False
        coreml_enable = False
        metal_enable = False
        vulkan_enable = False
    elif device_mode == "gpu":
        if platform.system() == "Darwin":
            coreml_enable = True
            metal_enable = True
            vulkan_enable = False
            dml_enable = False
        elif platform.system() == "Windows":
            vulkan_enable = True
