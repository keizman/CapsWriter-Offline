# coding: utf-8
"""
按键映射相关

处理按键名称和虚拟键码之间的转换，以及相关常量定义。
"""

from __future__ import annotations

import platform

from pynput import keyboard

from . import logger


IS_WINDOWS = platform.system() == 'Windows'

# 仅 Windows 需要 KeyTranslator；其他平台不导入 win32 私有模块
if IS_WINDOWS:
    try:
        from pynput._util.win32 import KeyTranslator
        _key_translator = KeyTranslator()
    except Exception as e:
        logger.warning(f"KeyTranslator 初始化失败: {e}")
        _key_translator = None
else:
    _key_translator = None


def _normalize_key_name(name: str) -> str:
    """将 pynput 的按键名规范化为项目配置用名称。"""
    alias = {
        'ctrl_l': 'ctrl',
        'ctrl_r': 'ctrl_r',
        'shift_l': 'shift',
        'shift_r': 'shift_r',
        'alt_l': 'alt',
        'alt_r': 'alt_r',
        'cmd_l': 'cmd',
        'cmd_r': 'cmd_r',
    }
    return alias.get(name, name)


def _build_special_vk_map() -> dict[int, keyboard.Key]:
    """构建 VK -> Key 映射（仅对存在 vk 的按键生效）。"""
    mapping: dict[int, keyboard.Key] = {}
    for key in keyboard.Key:
        value = getattr(key, 'value', None)
        vk = getattr(value, 'vk', None)
        if vk is not None:
            mapping[vk] = key
    return mapping


# 特殊键 VK 映射（Windows 下生效，其他平台通常为空）
_SPECIAL_KEYS = _build_special_vk_map()

# 小键盘按键映射（VK -> 名称）
NUMPAD_KEYS = {
    0x60: 'numpad0',  0x61: 'numpad1',  0x62: 'numpad2',  0x63: 'numpad3',
    0x64: 'numpad4',  0x65: 'numpad5',  0x66: 'numpad6',  0x67: 'numpad7',
    0x68: 'numpad8',  0x69: 'numpad9',
    0x6A: 'numpad_multiply',
    0x6B: 'numpad_add',
    0x6C: 'numpad_separator',
    0x6D: 'numpad_subtract',
    0x6E: 'numpad_decimal',
    0x6F: 'numpad_divide',
}

# Windows 键盘消息常量
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# Windows 鼠标消息常量
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

# 按键消息集合
KEYBOARD_MESSAGES = (WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP)
KEY_UP_MESSAGES = (WM_KEYUP, WM_SYSKEYUP)
KEY_DOWN_MESSAGES = (WM_KEYDOWN, WM_SYSKEYDOWN)
MOUSE_MESSAGES = (WM_XBUTTONDOWN, WM_XBUTTONUP)

# 可恢复的切换键（需要录音完成后恢复状态的锁键）
RESTORABLE_KEYS = {
    'caps_lock',
    'num_lock',
    'scroll_lock',
}


class KeyMapper:
    """按键映射器"""

    _SPECIAL_KEY_OBJECTS = None

    @classmethod
    def _get_special_key_objects(cls):
        """获取按键名 -> pynput Key 对象映射（延迟初始化）。"""
        if cls._SPECIAL_KEY_OBJECTS is None:
            cls._SPECIAL_KEY_OBJECTS = {
                'caps_lock': keyboard.Key.caps_lock,
                'space': keyboard.Key.space,
                'tab': keyboard.Key.tab,
                'enter': keyboard.Key.enter,
                'esc': keyboard.Key.esc,
                'delete': keyboard.Key.delete,
                'backspace': keyboard.Key.backspace,
                'shift': keyboard.Key.shift,
                'shift_r': getattr(keyboard.Key, 'shift_r', keyboard.Key.shift),
                'ctrl': keyboard.Key.ctrl,
                'ctrl_r': getattr(keyboard.Key, 'ctrl_r', keyboard.Key.ctrl),
                'alt': keyboard.Key.alt,
                'alt_r': getattr(keyboard.Key, 'alt_r', keyboard.Key.alt),
                'cmd': keyboard.Key.cmd,
                'cmd_r': getattr(keyboard.Key, 'cmd_r', keyboard.Key.cmd),
                'f1': keyboard.Key.f1, 'f2': keyboard.Key.f2, 'f3': keyboard.Key.f3, 'f4': keyboard.Key.f4,
                'f5': keyboard.Key.f5, 'f6': keyboard.Key.f6, 'f7': keyboard.Key.f7, 'f8': keyboard.Key.f8,
                'f9': keyboard.Key.f9, 'f10': keyboard.Key.f10, 'f11': keyboard.Key.f11, 'f12': keyboard.Key.f12,
            }
        return cls._SPECIAL_KEY_OBJECTS

    @staticmethod
    def key_to_name(key_event) -> str:
        """将 pynput 键盘事件对象转换为配置键名。"""
        if isinstance(key_event, keyboard.KeyCode):
            if key_event.char:
                return key_event.char.lower()
            vk = getattr(key_event, 'vk', None)
            if vk is not None:
                return KeyMapper.vk_to_name(vk)
            return ''

        name = getattr(key_event, 'name', '') or ''
        if not name:
            return ''
        return _normalize_key_name(name)

    @staticmethod
    def vk_to_name(vk: int) -> str:
        """
        将虚拟键码转换为按键名称（主要用于 Windows 低层钩子）。
        """
        if vk in _SPECIAL_KEYS:
            key_name = _SPECIAL_KEYS[vk].name or ''
            return _normalize_key_name(key_name)

        if vk in NUMPAD_KEYS:
            return NUMPAD_KEYS[vk]

        if _key_translator is not None:
            try:
                params = _key_translator(vk, is_press=True)
                if 'char' in params and params['char'] is not None:
                    return params['char'].lower()
            except Exception:
                pass

        return f'vk_{vk}'

    @staticmethod
    def name_to_key(key_name: str):
        """
        将按键名称转换为 pynput 按键对象。
        """
        special_keys = KeyMapper._get_special_key_objects()
        if key_name in special_keys:
            return special_keys[key_name]

        if len(key_name) == 1:
            return keyboard.KeyCode.from_char(key_name)

        logger.warning(f"未知按键名称: {key_name}")
        return None
