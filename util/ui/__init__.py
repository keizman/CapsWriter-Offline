"""UI 工具模块

提供 Toast 浮动消息通知和系统托盘功能。
该模块设计为 Client 和 Server 共用，日志记录器通过注入方式加载。
"""
import logging
from typing import Any

# ============================================================
# Logger 代理机制
# ============================================================

class _LoggerProxy:
    """
    日志代理类（利用 __getattr__ 动态转发）
    允许先导入 logger 对象，稍后再注入真正的实现。
    """
    def __init__(self):
        self._target = logging.getLogger('util.ui')  # 默认 logger

    def set_target(self, logger):
        """注入真正的 logger 实现"""
        self._target = logger

    def __getattr__(self, name):
        """将所有属性访问转发给真正的 logger"""
        return getattr(self._target, name)

# 1. 创建代理实例
logger = _LoggerProxy()

def set_ui_logger(real_logger):
    """设置 UI 模块使用的日志记录器"""
    logger.set_target(real_logger)

# ============================================================
# 延迟导出（避免 server 场景在导入阶段触发 Tk 依赖）
# ============================================================

def toast(*args: Any, **kwargs: Any):
    """延迟导入 toast，避免无关场景提前加载 Tk。"""
    from .toast import toast as _toast
    return _toast(*args, **kwargs)


def toast_stream(*args: Any, **kwargs: Any):
    """延迟导入 toast_stream，避免无关场景提前加载 Tk。"""
    from .toast import toast_stream as _toast_stream
    return _toast_stream(*args, **kwargs)


def enable_min_to_tray(*args: Any, **kwargs: Any):
    """延迟导入托盘模块。"""
    from .tray import enable_min_to_tray as _enable_min_to_tray
    return _enable_min_to_tray(*args, **kwargs)


def stop_tray(*args: Any, **kwargs: Any):
    """延迟导入托盘模块。"""
    from .tray import stop_tray as _stop_tray
    return _stop_tray(*args, **kwargs)


def __getattr__(name: str):
    """兼容 `from util.ui import ToastMessage` 等懒加载访问。"""
    if name in ("ToastMessage", "ToastMessageManager"):
        from .toast import ToastMessage, ToastMessageManager
        mapping = {
            "ToastMessage": ToastMessage,
            "ToastMessageManager": ToastMessageManager,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'logger',
    'set_ui_logger',
    'toast',
    'toast_stream',
    'ToastMessage',
    'ToastMessageManager',
    'enable_min_to_tray',
    'stop_tray',
]
