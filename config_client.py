import os
import sys
import json
import platform
from collections.abc import Iterable
from pathlib import Path

# 版本信息
__version__ = '2.4'

_IS_WINDOWS = platform.system() == 'Windows'


def _looks_like_client_root(path: Path) -> bool:
    markers = (
        "config_client.py",
        "assets",
        "LLM",
        "config_client.local.json",
        "hot.txt",
        "hot-rule.txt",
        "hot-rectify.txt",
        "start_client.command",
    )
    return any((path / marker).exists() for marker in markers)


def _resolve_base_dir() -> str:
    """解析运行基目录，兼容 macOS .app 与源码运行。"""
    if not getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(__file__))

    exe_path = Path(sys.executable)

    # 优先尝试 .app 同级目录（把 .app 放在项目根目录时可命中）
    for parent in [exe_path, *exe_path.parents]:
        if parent.suffix == ".app":
            app_parent = parent.parent
            if _looks_like_client_root(app_parent):
                return app_parent.as_posix()
            break

    # 其次尝试当前工作目录
    cwd = Path.cwd()
    if _looks_like_client_root(cwd):
        return cwd.as_posix()

    # 兜底：可执行文件目录
    return exe_path.parent.as_posix()


# 项目根目录
BASE_DIR = _resolve_base_dir()


def _load_local_overrides() -> dict:
    """
    读取客户端本地覆盖配置（可选）。

    默认文件名：config_client.local.json
    可通过 CAPSWRITER_CLIENT_CONFIG 指定路径。
    """
    path_str = os.getenv("CAPSWRITER_CLIENT_CONFIG", "config_client.local.json")
    path = Path(path_str)
    if not path.is_absolute():
        cwd_path = Path.cwd() / path
        base_path = Path(BASE_DIR) / path
        if cwd_path.exists():
            path = cwd_path
        else:
            path = base_path

    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


_OVERRIDES = _load_local_overrides()


def _get_override(*keys, default=None):
    current = _OVERRIDES
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _cfg(env_name: str, *keys, default=None):
    value = os.getenv(env_name)
    if value is not None:
        return value
    return _get_override(*keys, default=default)


def _cfg_bool(env_name: str, *keys, default: bool = False) -> bool:
    value = _cfg(env_name, *keys, default=default)
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


def _cfg_int(env_name: str, *keys, default: int) -> int:
    value = _cfg(env_name, *keys, default=default)
    try:
        return int(value)
    except Exception:
        return default


def _cfg_float(env_name: str, *keys, default: float) -> float:
    value = _cfg(env_name, *keys, default=default)
    try:
        return float(value)
    except Exception:
        return default


def _normalize_partial_input_mode(value) -> str:
    """
    统一解析 partial_input_enabled：
    - true/1/yes/on  => "true"  （启用 partial）
    - force          => "force" （强制原始模式）
    - 其他            => "false"
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return "true" if bool(value) else "false"
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return "true"
    if text == "force":
        return "force"
    return "false"


def _normalize_ws_uri(uri: str) -> str:
    """规范化服务端地址为 ws/wss URI。"""
    text = str(uri or "").strip()
    if not text:
        return ""

    lower = text.lower()
    if lower.startswith("ws://") or lower.startswith("wss://"):
        return text
    if lower.startswith("http://"):
        return "ws://" + text[len("http://"):]
    if lower.startswith("https://"):
        return "wss://" + text[len("https://"):]
    return f"ws://{text}"


def _legacy_server_uri() -> str:
    """
    兼容旧版 addr/port 配置，自动转换为 URI。

    优先级：
    1) 环境变量 CAPSWRITER_CLIENT_ADDR / CAPSWRITER_CLIENT_PORT
    2) 本地配置 server.addr / server.port
    """
    env_addr = os.getenv("CAPSWRITER_CLIENT_ADDR")
    env_port = os.getenv("CAPSWRITER_CLIENT_PORT")

    if env_addr or env_port:
        addr = (env_addr or "127.0.0.1").strip()
        port = (env_port or "6016").strip()
        return f"ws://{addr}:{port}"

    cfg_addr = _get_override("server", "addr", default=None)
    cfg_port = _get_override("server", "port", default=None)
    if cfg_addr or cfg_port:
        addr = str(cfg_addr or "127.0.0.1").strip()
        port = str(cfg_port or "6016").strip()
        return f"ws://{addr}:{port}"

    return ""


# 客户端配置
class ClientConfig:
    # 唯一服务端地址配置（支持 ws:// / wss:// / http:// / https:// / host:port）
    server_uri = _normalize_ws_uri(
        str(_cfg("CAPSWRITER_CLIENT_SERVER_URI", "server", "uri", default="")).strip()
    ) or _legacy_server_uri() or "ws://127.0.0.1:6016"
    # 连接密钥：需与服务端一致
    secret = str(_cfg("CAPSWRITER_CLIENT_SECRET", "server", "secret", default="")).strip()

    @classmethod
    def websocket_url(cls) -> str:
        """返回最终 WebSocket 连接地址。"""
        return cls.server_uri

    @classmethod
    def server_display(cls) -> str:
        """返回用于 UI 显示的服务端地址。"""
        return cls.server_uri

    @classmethod
    def reload_runtime_settings(cls) -> None:
        """
        重新加载运行时可热更新配置（主要用于托盘“重启音频”）。

        说明：
        - 仅刷新录音/输出相关的关键字段，避免影响当前会话稳定性。
        - server_uri / secret 等连接参数仍建议重启客户端后生效。
        """
        global _OVERRIDES
        _OVERRIDES = _load_local_overrides()

        cls.threshold = _cfg_float("CAPSWRITER_THRESHOLD", "recording", "threshold", default=cls.threshold)
        cls.release_tail_enabled = _cfg_bool(
            "CAPSWRITER_RELEASE_TAIL_ENABLED",
            "recording", "release_tail_enabled",
            default=cls.release_tail_enabled
        )
        cls.release_tail_adaptive = _cfg_bool(
            "CAPSWRITER_RELEASE_TAIL_ADAPTIVE",
            "recording", "release_tail_adaptive",
            default=cls.release_tail_adaptive
        )
        cls.release_tail_ms = _cfg_int(
            "CAPSWRITER_RELEASE_TAIL_MS",
            "recording", "release_tail_ms",
            default=cls.release_tail_ms
        )
        cls.release_tail_max_ms = _cfg_int(
            "CAPSWRITER_RELEASE_TAIL_MAX_MS",
            "recording", "release_tail_max_ms",
            default=cls.release_tail_max_ms
        )
        cls.release_tail_silence_ms = _cfg_int(
            "CAPSWRITER_RELEASE_TAIL_SILENCE_MS",
            "recording", "release_tail_silence_ms",
            default=cls.release_tail_silence_ms
        )
        cls.release_tail_vad_threshold = _cfg_float(
            "CAPSWRITER_RELEASE_TAIL_VAD_THRESHOLD",
            "recording", "release_tail_vad_threshold",
            default=cls.release_tail_vad_threshold
        )

        cls.partial_input_char_interval_ms = _cfg_int(
            "CAPSWRITER_PARTIAL_INPUT_CHAR_INTERVAL_MS",
            "output", "partial_input_char_interval_ms",
            default=cls.partial_input_char_interval_ms
        )
        cls.partial_input_mode = _normalize_partial_input_mode(
            _cfg("CAPSWRITER_PARTIAL_INPUT_ENABLED", "output", "partial_input_enabled", default=cls.partial_input_mode)
        )
        cls.partial_input_enabled = cls.partial_input_mode == "true"
        cls.partial_input_force_legacy = cls.partial_input_mode == "force"
        cls.partial_input_seg_duration = _cfg_int(
            "CAPSWRITER_PARTIAL_INPUT_SEG_DURATION",
            "output", "partial_input_seg_duration",
            default=cls.partial_input_seg_duration
        )
        cls.partial_input_seg_overlap = _cfg_int(
            "CAPSWRITER_PARTIAL_INPUT_SEG_OVERLAP",
            "output", "partial_input_seg_overlap",
            default=cls.partial_input_seg_overlap
        )

        cls.mic_seg_duration = _cfg_int(
            "CAPSWRITER_MIC_SEG_DURATION",
            "recording", "mic_seg_duration",
            default=cls.mic_seg_duration
        )
        cls.mic_seg_overlap = _cfg_int(
            "CAPSWRITER_MIC_SEG_OVERLAP",
            "recording", "mic_seg_overlap",
            default=cls.mic_seg_overlap
        )

        cls.audio_device_auto_refresh = _cfg_bool(
            "CAPSWRITER_AUDIO_DEVICE_AUTO_REFRESH",
            "audio", "device_auto_refresh",
            default=cls.audio_device_auto_refresh
        )
        cls.audio_device_poll_interval_secs = _cfg_float(
            "CAPSWRITER_AUDIO_DEVICE_POLL_INTERVAL_SECS",
            "audio", "device_poll_interval_secs",
            default=cls.audio_device_poll_interval_secs
        )

    # 快捷键配置列表
    shortcuts = (
        [
            {
                'key': 'caps_lock',     # 监听大写锁定键
                'type': 'keyboard',     # 是键盘快捷键
                'suppress': True,       # 阻塞按键（短按会补发）
                'hold_mode': True,      # 长按模式
                'enabled': True         # 启用此快捷键
            },
            {
                'key': 'ctrl_r',        # 右 Ctrl 长按说话
                'type': 'keyboard',
                'suppress': False,
                'hold_mode': True,
                'enabled': True
            },
        ]
        if _IS_WINDOWS
        else [
            {
                'key': 'ctrl+cmd',      # macOS 默认快捷键：Control + Command
                'type': 'keyboard',
                'suppress': False,
                'hold_mode': True,
                'enabled': True
            },
            {
                'key': 'caps_lock',     # 保留原始按键支持
                'type': 'keyboard',
                'suppress': False,
                'hold_mode': True,
                'enabled': True
            },
            {
                'key': 'ctrl_r',        # 右 Ctrl 长按说话
                'type': 'keyboard',
                'suppress': False,
                'hold_mode': True,
                'enabled': True
            },
        ]
    )

    threshold    = 0.3          # 快捷键触发阈值（秒）
    # 松开按键后的尾留音（避免尾字被截断）
    release_tail_enabled = _cfg_bool(
        "CAPSWRITER_RELEASE_TAIL_ENABLED",
        "recording", "release_tail_enabled",
        default=True
    )
    release_tail_adaptive = _cfg_bool(
        "CAPSWRITER_RELEASE_TAIL_ADAPTIVE",
        "recording", "release_tail_adaptive",
        default=True
    )
    release_tail_ms = _cfg_int(
        "CAPSWRITER_RELEASE_TAIL_MS",
        "recording", "release_tail_ms",
        default=350
    )
    release_tail_max_ms = _cfg_int(
        "CAPSWRITER_RELEASE_TAIL_MAX_MS",
        "recording", "release_tail_max_ms",
        default=1000
    )
    release_tail_silence_ms = _cfg_int(
        "CAPSWRITER_RELEASE_TAIL_SILENCE_MS",
        "recording", "release_tail_silence_ms",
        default=180
    )
    release_tail_vad_threshold = _cfg_float(
        "CAPSWRITER_RELEASE_TAIL_VAD_THRESHOLD",
        "recording", "release_tail_vad_threshold",
        default=0.02
    )

    # Partial 输入模式（流式边说边上屏）
    partial_input_char_interval_ms = _cfg_int(
        "CAPSWRITER_PARTIAL_INPUT_CHAR_INTERVAL_MS",
        "output", "partial_input_char_interval_ms",
        default=10
    )
    partial_input_mode = _normalize_partial_input_mode(
        _cfg("CAPSWRITER_PARTIAL_INPUT_ENABLED", "output", "partial_input_enabled", default=False)
    )
    partial_input_enabled = partial_input_mode == "true"
    partial_input_force_legacy = partial_input_mode == "force"
    # partial 模式下使用更短分段，保证“按住说话过程中”可持续回传中间结果
    partial_input_seg_duration = _cfg_int(
        "CAPSWRITER_PARTIAL_INPUT_SEG_DURATION",
        "output", "partial_input_seg_duration",
        default=6
    )
    partial_input_seg_overlap = _cfg_int(
        "CAPSWRITER_PARTIAL_INPUT_SEG_OVERLAP",
        "output", "partial_input_seg_overlap",
        default=1
    )

    # 输入设备热插拔自动刷新
    audio_device_auto_refresh = _cfg_bool(
        "CAPSWRITER_AUDIO_DEVICE_AUTO_REFRESH",
        "audio", "device_auto_refresh",
        default=True
    )
    audio_device_poll_interval_secs = _cfg_float(
        "CAPSWRITER_AUDIO_DEVICE_POLL_INTERVAL_SECS",
        "audio", "device_poll_interval_secs",
        default=1.5
    )

    paste        = not _IS_WINDOWS  # 非 Windows 默认使用粘贴，兼容性更好
    restore_clip = True         # 模拟粘贴后是否恢复剪贴板

    save_audio = True           # 是否保存录音文件
    audio_name_len = 20         # 将录音识别结果的前多少个字存储到录音文件名中，建议不要超过200
    
    context = ''                # 提示词上下文，用于辅助 Fun-ASR-Nano 模型识别（例如输入人名、地名、专业术语等）

    trash_punc = '，。,.'       # 识别结果要消除的末尾标点

    traditional_convert = False     # 是否将识别结果转换为繁体中文
    traditional_locale = 'zh-hant'  # 繁体地区：'zh-hant'（标准繁体）, 'zh-tw'（台湾繁体）, 'zh-hk'（香港繁体）

    hot = True                 # 是否启用热词替换（统一 RAG 匹配）
    hot_thresh = 0.85           # RAG 替换热词阈值（高阈值，用于实际替换）
    hot_similar = 0.6           # RAG 相似热词阈值（低阈值，用于 LLM 上下文）
    hot_rectify = 0.6           # 纠错历史 RAG 匹配阈值（低阈值，用于 LLM 上下文）
    hot_rule = True             # 是否启用自定义规则替换（基于正则表达式）

    llm_enabled = True          # 是否启用 LLM 润色功能，需要配置 LLM/ 目录下的角色文件
    llm_stop_key = 'esc'        # 中断 LLM 输出的快捷键

    enable_tray = _IS_WINDOWS   # 非 Windows 阶段先关闭托盘

    # 日志配置
    log_level = 'INFO'          # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'

    mic_seg_duration = 60       # 麦克风听写时分段长度：60秒
    mic_seg_overlap = 4         # 麦克风听写时分段重叠：4秒

    file_seg_duration = 60      # 转录文件时分段长度
    file_seg_overlap = 4        # 转录文件时分段重叠

    file_save_srt = True        # 转录文件时是否保存 srt 字幕
    file_save_txt = True        # 转录文件时是否保存 txt 文本（按标点切分后的）
    file_save_json = True       # 转录文件时是否保存 json 结果（含原始时间戳）
    file_save_merge = False      # 转录文件时是否保存 merge.txt（未切分的段落长文本）

    udp_broadcast = True                # 是否启用 UDP 广播输出结果
    udp_broadcast_targets = [           # UDP 广播目标地址列表，格式: (地址, 端口)
        ('127.255.255.255', 6017),      # 本地回环广播
        # ('192.168.1.255', 6017),      # 局域网广播（示例，按需启用）
    ]

    udp_control = False             # 是否启用 UDP 控制录音（外部程序发送 START/STOP 命令）
    udp_control_addr = '127.0.0.1'  # UDP 控制监听地址（'0.0.0.0' 允许外部访问）
    udp_control_port = 6018         # UDP 控制监听端口


# 快捷键配置说明
r"""
快捷键配置字段说明：
  key        - 按键名称（见下方可用按键列表）
  type       - 输入类型：'keyboard'（键盘）或 'mouse'（鼠标）
  suppress   - 是否阻塞按键（True=阻塞，False=不阻塞）
  hold_mode  - 长按模式（True=按下录音松开停止，False=单击开始再次单击停止）
  enabled    - 是否启用此快捷键

阻塞模式说明：
  - 阻塞模式  ：长按录音识别，短按（<0.3秒）则自动补发按键，不影响单击功能
  - 非阻塞模式：对于 CapsLock/NumLock/ScrollLock 这类切换键，松开时会自动补发，以恢复按键状态

可用按键名称：

  字母数字：a - z, 0 - 9（大键盘）, numpad0 - numpad9（小键盘）

  符号键：, . / \ ` ' - = [ ] ; '

  小键盘：
      decimal(小数点), numpad_add(+), numpad_subtract(-),
      numpad_multiply(*), numpad_divide(/), numpad_enter

  功能键：f1 - f24

  控制键:
      ctrl,   ctrl_r,
      shift,  shift_r,
      alt,    alt_r,
      cmd,    cmd_r,
      fn（部分 macOS 设备可能无法被全局监听）

  组合键：
      ctrl+cmd（示例，支持 `ctrl + command` 这种写法）

  特殊键：
      space, enter, tab, backspace, delete, insert, home, end
      page_up, page_down, esc, caps_lock, num_lock, scroll_lock
      print_screen, pause, menu

  方向键：up, down, left, right

  鼠标键：x1, x2

示例配置：
  {'key': 'caps_lock', 'type': 'keyboard', 'suppress': False, 'hold_mode': True, 'enabled': True}, 
  {'key': 'f12', 'type': 'keyboard', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
  {'key': 'x2', 'type': 'mouse', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
"""
