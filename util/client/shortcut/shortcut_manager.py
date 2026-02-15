# coding: utf-8
"""
快捷键管理器

统一管理多个快捷键，支持：
1. Windows: win32_event_filter（可阻塞）
2. macOS/Linux: on_press/on_release（不阻塞）
"""

from __future__ import annotations

import platform
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from pynput import keyboard, mouse

from . import logger
from util.client.shortcut.key_mapper import (
    KeyMapper,
    KEYBOARD_MESSAGES,
    KEY_DOWN_MESSAGES,
    KEY_UP_MESSAGES,
    MOUSE_MESSAGES,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
    XBUTTON1,
)
from util.client.shortcut.emulator import ShortcutEmulator
from util.client.shortcut.event_handler import ShortcutEventHandler
from util.client.shortcut.task import ShortcutTask

if TYPE_CHECKING:
    from util.client.shortcut.shortcut_config import Shortcut
    from util.client.state import ClientState


class ShortcutManager:
    """快捷键管理器"""

    def __init__(self, state: 'ClientState', shortcuts: List['Shortcut']):
        self.state = state
        self.shortcuts = shortcuts
        self._is_windows = platform.system() == 'Windows'

        # 监听器
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None

        # 快捷键任务映射（key -> ShortcutTask）
        self.tasks: Dict[str, ShortcutTask] = {}
        self.single_tasks: Dict[str, ShortcutTask] = {}
        self.combo_tasks: Dict[Tuple[str, ...], ShortcutTask] = {}

        # 线程池
        self._pool = ThreadPoolExecutor(max_workers=4)

        # 按键模拟器
        self._emulator = ShortcutEmulator()

        # 按键恢复状态追踪
        self._restoring_keys = set()
        self._pressed_keys: Set[str] = set()

        # 事件处理器
        self._event_handler = ShortcutEventHandler(self.tasks, self._pool, self._emulator)

        self._init_tasks()

    def _init_tasks(self) -> None:
        """初始化所有快捷键任务"""
        from config_client import ClientConfig as Config

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue

            task = ShortcutTask(shortcut, self.state)
            task._manager_ref = lambda: self
            task.pool = self._pool
            task.threshold = shortcut.get_threshold(Config.threshold)
            self.tasks[shortcut.key] = task

            if shortcut.type == 'keyboard' and '+' in shortcut.key:
                combo = self._split_combo_key(shortcut.key)
                if len(combo) >= 2:
                    self.combo_tasks[combo] = task
                    continue
            self.single_tasks[shortcut.key] = task

    @staticmethod
    def _split_combo_key(key: str) -> Tuple[str, ...]:
        """将组合键字符串拆分为按键元组，如 'ctrl+cmd' -> ('ctrl','cmd')。"""
        return tuple(part.strip() for part in key.split('+') if part.strip())

    @staticmethod
    def _is_key_matched(combo_key: str, event_key: str) -> bool:
        """判断事件键是否匹配组合键中的某个键位（兼容左右修饰键）。"""
        if combo_key == event_key:
            return True
        alias_map = {
            'ctrl': {'ctrl', 'ctrl_r'},
            'cmd': {'cmd', 'cmd_r'},
            'alt': {'alt', 'alt_r'},
            'shift': {'shift', 'shift_r'},
        }
        return event_key in alias_map.get(combo_key, {combo_key})

    def _is_combo_pressed(self, combo_keys: Tuple[str, ...]) -> bool:
        """判断组合键是否全部按下（兼容左右修饰键）。"""
        for combo_key in combo_keys:
            if not any(self._is_key_matched(combo_key, k) for k in self._pressed_keys):
                return False
        return True

    # ========== 平台无关事件处理 ==========

    def _should_ignore_key(self, key_name: str, is_release: bool, is_mouse: bool = False) -> bool:
        """防自捕获检查：模拟按键和恢复按键。"""
        if self._emulator.is_emulating(key_name):
            if is_release:
                self._emulator.clear_emulating_flag(key_name)
            return True

        if not is_mouse and self.is_restoring(key_name):
            if is_release:
                self.clear_restoring_flag(key_name)
            return True

        return False

    def _handle_keyboard_press(self, key_name: str) -> None:
        if not key_name:
            return
        if self._should_ignore_key(key_name, is_release=False):
            return

        self._pressed_keys.add(key_name)

        # 单键快捷键
        single_task = self.single_tasks.get(key_name)
        if single_task:
            self._event_handler.handle_keydown(key_name, single_task)

        # 组合键快捷键：所有键都按下时触发
        for combo_keys, combo_task in self.combo_tasks.items():
            if not any(self._is_key_matched(combo_key, key_name) for combo_key in combo_keys):
                continue
            if self._is_combo_pressed(combo_keys) and not combo_task.is_recording:
                combo_name = '+'.join(combo_keys)
                self._event_handler.handle_keydown(combo_name, combo_task)

    def _handle_keyboard_release(self, key_name: str) -> None:
        if not key_name:
            return
        if self._should_ignore_key(key_name, is_release=True):
            self._pressed_keys.discard(key_name)
            return

        # 组合键：松开任一成员键即结束
        for combo_keys, combo_task in self.combo_tasks.items():
            in_combo = any(self._is_key_matched(combo_key, key_name) for combo_key in combo_keys)
            if in_combo and combo_task.is_recording:
                combo_name = '+'.join(combo_keys)
                self._event_handler.handle_keyup(combo_name, combo_task)

        # 单键快捷键
        single_task = self.single_tasks.get(key_name)
        if single_task:
            self._event_handler.handle_keyup(key_name, single_task)

        self._pressed_keys.discard(key_name)

    def _handle_mouse_press(self, button_name: str) -> None:
        if not button_name or button_name not in self.tasks:
            return
        if self._should_ignore_key(button_name, is_release=False, is_mouse=True):
            return
        self._event_handler.handle_keydown(button_name, self.tasks[button_name])

    def _handle_mouse_release(self, button_name: str) -> None:
        if not button_name:
            return
        if self._should_ignore_key(button_name, is_release=True, is_mouse=True):
            return
        task = self.tasks.get(button_name)
        if not task:
            return
        self._handle_mouse_keyup(button_name, task)

    # ========== Windows: win32_event_filter ==========

    def create_keyboard_filter(self):
        """创建 Windows 键盘事件过滤器。"""

        def win32_event_filter(msg, data):
            if msg not in KEYBOARD_MESSAGES:
                return True

            key_name = KeyMapper.vk_to_name(data.vkCode)
            task = self.single_tasks.get(key_name)

            if msg in KEY_DOWN_MESSAGES:
                self._handle_keyboard_press(key_name)
            elif msg in KEY_UP_MESSAGES:
                self._handle_keyboard_release(key_name)

            # 组合键的 suppress 暂按“成员键包含即阻塞”处理
            combo_suppress = any(
                any(self._is_key_matched(combo_key, key_name) for combo_key in combo_keys)
                and combo_task.shortcut.suppress
                for combo_keys, combo_task in self.combo_tasks.items()
            )
            if self.keyboard_listener and ((task and task.shortcut.suppress) or combo_suppress):
                self.keyboard_listener.suppress_event()

            return True

        return win32_event_filter

    def create_mouse_filter(self):
        """创建 Windows 鼠标事件过滤器。"""

        def win32_event_filter(msg, data):
            if msg not in MOUSE_MESSAGES:
                return True

            xbutton = (data.mouseData >> 16) & 0xFFFF
            button_name = 'x1' if xbutton == XBUTTON1 else 'x2'
            task = self.tasks.get(button_name)

            if msg == WM_XBUTTONDOWN:
                self._handle_mouse_press(button_name)
            elif msg == WM_XBUTTONUP:
                self._handle_mouse_release(button_name)

            if task and task.shortcut.suppress and self.mouse_listener:
                self.mouse_listener.suppress_event()

            return True

        return win32_event_filter

    # ========== macOS/Linux: pynput 回调 ==========

    def _on_keyboard_press(self, key) -> None:
        key_name = KeyMapper.key_to_name(key)
        self._handle_keyboard_press(key_name)

    def _on_keyboard_release(self, key) -> None:
        key_name = KeyMapper.key_to_name(key)
        self._handle_keyboard_release(key_name)

    def _on_mouse_click(self, _x, _y, button, pressed) -> None:
        x1_button = getattr(mouse.Button, 'x1', None)
        x2_button = getattr(mouse.Button, 'x2', None)

        if x1_button is not None and button == x1_button:
            button_name = 'x1'
        elif x2_button is not None and button == x2_button:
            button_name = 'x2'
        else:
            return

        if pressed:
            self._handle_mouse_press(button_name)
        else:
            self._handle_mouse_release(button_name)

    # ========== 鼠标释放处理 ==========

    def _handle_mouse_keyup(self, button_name: str, task) -> None:
        """处理鼠标按键释放事件"""
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{button_name}] 松开按键，持续时间: {duration:.3f}s")

        if duration < task.threshold:
            task.cancel()
            if task.shortcut.suppress:
                logger.debug(f"[{button_name}] 安排异步补发鼠标按键")
                self._pool.submit(self._emulator.emulate_mouse_click, button_name)
        else:
            task.finish()

    # ========== 按键恢复管理 ==========

    def schedule_restore(self, key: str) -> None:
        """
        安排按键恢复（延迟执行，避免在事件处理中阻塞）
        """
        self._restoring_keys.add(key)

        def do_restore():
            time.sleep(0.05)
            if key == 'caps_lock':
                controller = keyboard.Controller()
                controller.press(keyboard.Key.caps_lock)
                controller.release(keyboard.Key.caps_lock)

        self._pool.submit(do_restore)

    def is_restoring(self, key: str) -> bool:
        return key in self._restoring_keys

    def clear_restoring_flag(self, key: str) -> None:
        self._restoring_keys.discard(key)

    # ========== 公共接口 ==========

    def start(self) -> None:
        """启动所有监听器"""
        has_keyboard = any(s.type == 'keyboard' for s in self.shortcuts if s.enabled)
        has_mouse = any(s.type == 'mouse' for s in self.shortcuts if s.enabled)

        if not self._is_windows:
            suppressed = [s.key for s in self.shortcuts if s.enabled and s.suppress]
            if suppressed:
                logger.warning(
                    f"当前平台不支持稳定阻塞模式，以下快捷键将按非阻塞处理: {suppressed}"
                )

        if has_keyboard:
            if self._is_windows:
                self.keyboard_listener = keyboard.Listener(
                    win32_event_filter=self.create_keyboard_filter()
                )
            else:
                self.keyboard_listener = keyboard.Listener(
                    on_press=self._on_keyboard_press,
                    on_release=self._on_keyboard_release,
                )
            self.keyboard_listener.start()
            logger.info("键盘监听器已启动")

        if has_mouse:
            if self._is_windows:
                self.mouse_listener = mouse.Listener(
                    win32_event_filter=self.create_mouse_filter()
                )
            else:
                self.mouse_listener = mouse.Listener(
                    on_click=self._on_mouse_click,
                )
            self.mouse_listener.start()
            logger.info("鼠标监听器已启动")

        for shortcut in self.shortcuts:
            if shortcut.enabled:
                mode = "长按" if shortcut.hold_mode else "单击"
                toggle = "可恢复" if shortcut.is_toggle_key() else "普通键"
                logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}, {toggle}")

    def stop(self) -> None:
        """停止所有监听器和清理资源"""
        if self.keyboard_listener:
            self.keyboard_listener.stop()
            logger.debug("键盘监听器已停止")

        if self.mouse_listener:
            self.mouse_listener.stop()
            logger.debug("鼠标监听器已停止")

        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()

        self._pool.shutdown(wait=False)
        logger.debug("快捷键管理器线程池已关闭")
