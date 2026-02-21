# coding: utf-8
"""
客户端 UI 门面模块

该模块作为客户端访问 UI 功能的统一入口。
通过延迟导入避免在无 Tk 环境下导入阶段失败。
"""

from __future__ import annotations

from .. import logger
import util.ui

# 注入 Client Logger 到通用 UI 模块
util.ui.set_ui_logger(logger)

from util.client.ui.tips import TipsDisplay


def toast(*args, **kwargs):
    # 直接从子模块导入函数，避免被 util.ui.toast 同名模块对象覆盖。
    from util.ui.toast import toast as _toast
    return _toast(*args, **kwargs)


def toast_stream(*args, **kwargs):
    # 直接从子模块导入函数，避免被 util.ui.toast 同名模块对象覆盖。
    from util.ui.toast import toast_stream as _toast_stream
    return _toast_stream(*args, **kwargs)


def enable_min_to_tray(*args, **kwargs):
    from util.ui import enable_min_to_tray as _enable_min_to_tray
    return _enable_min_to_tray(*args, **kwargs)


def stop_tray(*args, **kwargs):
    from util.ui import stop_tray as _stop_tray
    return _stop_tray(*args, **kwargs)


def on_add_rectify_record(*args, **kwargs):
    from util.ui.rectify_menu_handler import on_add_rectify_record as _on_add_rectify_record
    return _on_add_rectify_record(*args, **kwargs)


def on_add_hotword(*args, **kwargs):
    from util.ui.hotword_menu_handler import on_add_hotword as _on_add_hotword
    return _on_add_hotword(*args, **kwargs)


def on_edit_context(*args, **kwargs):
    from util.ui.context_menu_handler import on_edit_context as _on_edit_context
    return _on_edit_context(*args, **kwargs)


def start_flow_bar(*args, **kwargs):
    from util.client.ui.flow_bar import start_flow_bar as _start_flow_bar
    return _start_flow_bar(*args, **kwargs)


def stop_flow_bar(*args, **kwargs):
    from util.client.ui.flow_bar import stop_flow_bar as _stop_flow_bar
    return _stop_flow_bar(*args, **kwargs)


def set_flow_state_resting(*args, **kwargs):
    from util.client.ui.flow_bar import set_flow_state_resting as _set_flow_state_resting
    return _set_flow_state_resting(*args, **kwargs)


def set_flow_state_active_ptt(*args, **kwargs):
    from util.client.ui.flow_bar import set_flow_state_active_ptt as _set_flow_state_active_ptt
    return _set_flow_state_active_ptt(*args, **kwargs)


def set_flow_state_processing(*args, **kwargs):
    from util.client.ui.flow_bar import set_flow_state_processing as _set_flow_state_processing
    return _set_flow_state_processing(*args, **kwargs)


def set_flow_audio_level(*args, **kwargs):
    from util.client.ui.flow_bar import set_flow_audio_level as _set_flow_audio_level
    return _set_flow_audio_level(*args, **kwargs)


def __getattr__(name: str):
    if name in ("ToastMessage", "ToastMessageManager"):
        from util.ui import ToastMessage, ToastMessageManager
        mapping = {
            "ToastMessage": ToastMessage,
            "ToastMessageManager": ToastMessageManager,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    'logger',
    'TipsDisplay',
    'toast',
    'toast_stream',
    'ToastMessage',
    'ToastMessageManager',
    'enable_min_to_tray',
    'stop_tray',
    'on_add_rectify_record',
    'on_add_hotword',
    'on_edit_context',
    'start_flow_bar',
    'stop_flow_bar',
    'set_flow_state_resting',
    'set_flow_state_active_ptt',
    'set_flow_state_processing',
    'set_flow_audio_level',
]
