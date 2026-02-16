# coding: utf-8
"""
录音状态 Flow Bar 悬浮指示器

状态：
- resting: 未触发，小圆柱
- active_ptt: 录音中，放大 + 实时波形
- processing: 录音结束后等待结果，放大 + 处理动画

实现约束：
- macOS AppKit 要求 NSWindow 必须在主线程创建。
- 因此窗口创建与绘制在主线程事件循环内执行；后台线程仅投递命令。
"""

from __future__ import annotations

import asyncio
import math
import platform
import queue
import threading
from dataclasses import dataclass

from util.client import logger


@dataclass(frozen=True)
class _FlowStyle:
    width: float
    height: float


_STATE_RESTING = "resting"
_STATE_ACTIVE_PTT = "active_ptt"
_STATE_PROCESSING = "processing"

_STYLES = {
    _STATE_RESTING: _FlowStyle(width=40.0, height=8.0),
    _STATE_ACTIVE_PTT: _FlowStyle(width=73.0, height=30.0),
    _STATE_PROCESSING: _FlowStyle(width=98.0, height=30.0),
}

_STATE_ALPHAS = {
    _STATE_RESTING: 0.58,
    _STATE_ACTIVE_PTT: 0.92,
    _STATE_PROCESSING: 0.86,
}


class _FlowBarIndicator:
    def __init__(self) -> None:
        self._commands: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._state = _STATE_RESTING
        self._audio_level = 0.0
        self._phase = 0.0

        style = _STYLES[_STATE_RESTING]
        self._current_width = style.width
        self._current_height = style.height
        self._target_width = style.width
        self._target_height = style.height
        self._current_alpha = _STATE_ALPHAS[_STATE_RESTING]
        self._target_alpha = _STATE_ALPHAS[_STATE_RESTING]

        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

        self._tk = None
        self._host = None
        self._root = None
        self._canvas = None

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if self._running:
            return

        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.warning("Flow Bar 启动失败：当前线程没有运行中的事件循环")
                return

        # macOS 必须在主线程创建窗口
        if platform.system() == "Darwin" and threading.current_thread() is not threading.main_thread():
            logger.warning("Flow Bar 启动失败：macOS 需要在主线程创建窗口")
            return

        try:
            import tkinter as tk
        except Exception as exc:
            logger.warning(f"Flow Bar 初始化失败（Tk 不可用）: {exc}")
            return

        try:
            # 使用隐藏 host + 顶层 bar。host 被强制隐藏到屏幕外，避免出现额外标题栏窗口。
            host = tk.Tk()
            try:
                host.withdraw()
            except Exception:
                pass
            try:
                host.overrideredirect(True)
            except Exception:
                pass
            try:
                host.wm_overrideredirect(True)
            except Exception:
                pass
            try:
                host.attributes("-alpha", 0.0)
            except Exception:
                pass
            try:
                host.geometry("1x1-10000-10000")
            except Exception:
                pass

            root = tk.Toplevel(host)
            # 先隐藏，应用样式后再显示，减少 macOS 首帧带标题栏闪现
            try:
                root.withdraw()
            except Exception:
                pass
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            # 再次兜底设置，部分平台需 wm_overrideredirect 才稳定生效
            try:
                root.wm_overrideredirect(True)
            except Exception:
                pass
            self._set_window_alpha(self._current_alpha)

            if platform.system() == "Darwin":
                # macOS: 强制无标题栏样式，隐藏红黄绿按钮
                try:
                    root.tk.call("::tk::unsupported::MacWindowStyle", "style", root._w, "help", "none")
                except Exception:
                    pass
                # 备用样式，部分 Tk 版本对 help 样式不稳定
                try:
                    root.tk.call("::tk::unsupported::MacWindowStyle", "style", root._w, "floating", "none")
                except Exception:
                    pass

            bg = "#101214"
            root.configure(bg=bg)
            canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg=bg)
            canvas.pack(fill=tk.BOTH, expand=True)
            try:
                root.deiconify()
            except Exception:
                pass

            self._tk = tk
            self._host = host
            self._root = root
            self._canvas = canvas
            self._loop = loop
            self._running = True

            self._task = loop.create_task(self._run_loop())
        except Exception as exc:
            logger.warning(f"Flow Bar 初始化失败（窗口创建异常）: {exc}")
            self._running = False
            self._tk = None
            self._host = None
            self._root = None
            self._canvas = None

    def stop(self) -> None:
        if not self._running:
            return
        self._commands.put(("stop", None))
        # 确保尽快唤醒主循环处理 stop
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)

    def set_state(self, state: str) -> None:
        if state not in _STYLES:
            return
        self._commands.put(("state", state))

    def set_audio_level(self, level: float) -> None:
        value = float(level)
        if value < 0:
            value = 0.0
        if value > 1:
            value = 1.0
        self._commands.put(("audio", value))

    async def _run_loop(self) -> None:
        try:
            while self._running and self._root and self._canvas:
                if self._process_commands():
                    break

                self._current_width += (self._target_width - self._current_width) * 0.28
                self._current_height += (self._target_height - self._current_height) * 0.28
                self._current_alpha += (self._target_alpha - self._current_alpha) * 0.28
                self._phase += 0.34

                self._enforce_borderless()
                self._apply_geometry()
                self._set_window_alpha(self._current_alpha)
                self._draw_pill()

                self._root.update_idletasks()
                self._root.update()
                await asyncio.sleep(0.033)
        except Exception as exc:
            logger.warning(f"Flow Bar 运行异常，已自动禁用: {exc}")
        finally:
            self._destroy_window()
            self._running = False
            self._task = None

    def _process_commands(self) -> bool:
        while True:
            try:
                cmd, payload = self._commands.get_nowait()
            except queue.Empty:
                break

            if cmd == "stop":
                return True

            if cmd == "state":
                self._state = str(payload)
                style = _STYLES.get(self._state, _STYLES[_STATE_RESTING])
                self._target_width = style.width
                self._target_height = style.height
                self._target_alpha = _STATE_ALPHAS.get(self._state, _STATE_ALPHAS[_STATE_RESTING])
                if self._state == _STATE_RESTING:
                    self._audio_level = 0.0
            elif cmd == "audio":
                self._audio_level = float(payload)

        return False

    def _apply_geometry(self) -> None:
        if not self._root:
            return
        width = max(16, int(self._current_width))
        height = max(8, int(self._current_height))
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = int((screen_w - width) / 2)
        y = int(screen_h - 120 - height)
        self._root.geometry(f"{width}x{height}+{x}+{y}")

    def _enforce_borderless(self) -> None:
        if not self._root:
            return
        try:
            self._root.overrideredirect(True)
        except Exception:
            pass
        try:
            self._root.wm_overrideredirect(True)
        except Exception:
            pass
        if platform.system() == "Darwin":
            try:
                self._root.tk.call("::tk::unsupported::MacWindowStyle", "style", self._root._w, "help", "none")
            except Exception:
                pass

    def _set_window_alpha(self, value: float) -> None:
        if not self._root:
            return
        clamped = max(0.20, min(1.0, float(value)))
        try:
            self._root.attributes("-alpha", clamped)
        except Exception:
            pass

    def _draw_pill(self) -> None:
        if not self._canvas:
            return
        self._canvas.delete("all")
        w = max(16.0, self._current_width)
        h = max(8.0, self._current_height)
        r = min(h / 2.0, 12.0)

        fill = "#15181b"
        if self._state == _STATE_RESTING:
            fill = "#0c0f12"
        elif self._state == _STATE_PROCESSING:
            fill = "#1a1f25"

        # 无描边胶囊，避免左右出现“透明圆环”视觉。
        x1 = max(0.0, r - 1.0)
        x2 = min(w, max(r, w - r) + 1.0)
        self._canvas.create_rectangle(
            x1,
            0,
            x2,
            h,
            fill=fill,
            outline="",
        )
        self._canvas.create_oval(0, 0, 2 * r, h, fill=fill, outline="")
        self._canvas.create_oval(max(0, w - 2 * r), 0, w, h, fill=fill, outline="")

        if self._state == _STATE_RESTING:
            return

        bars = 10
        bar_w = max(2.0, (w * 0.42) / bars)
        gap = bar_w * 0.55
        total = bars * bar_w + (bars - 1) * gap
        start_x = (w - total) / 2.0
        cy = h / 2.0

        for i in range(bars):
            px = self._phase + i * 0.62
            wave = abs(math.sin(px))
            if self._state == _STATE_ACTIVE_PTT:
                amp = 0.25 + 0.75 * self._audio_level
                scale = 0.35 + amp * wave
            else:
                scale = 0.35 + 0.55 * wave

            max_bar_h = h * 0.62
            min_bar_h = max(2.0, h * 0.16)
            bar_h = min_bar_h + (max_bar_h - min_bar_h) * scale
            x1 = start_x + i * (bar_w + gap)
            x2 = x1 + bar_w
            y1 = cy - bar_h / 2.0
            y2 = cy + bar_h / 2.0
            self._canvas.create_rectangle(x1, y1, x2, y2, fill="#f2f6ff", outline="")

    def _destroy_window(self) -> None:
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass
        if self._host:
            try:
                self._host.destroy()
            except Exception:
                pass
        self._host = None
        self._root = None
        self._canvas = None
        self._tk = None


_FLOW_BAR: _FlowBarIndicator | None = None
_FLOW_LOCK = threading.Lock()


def _manager() -> _FlowBarIndicator:
    global _FLOW_BAR
    with _FLOW_LOCK:
        if _FLOW_BAR is None:
            _FLOW_BAR = _FlowBarIndicator()
        return _FLOW_BAR


def start_flow_bar() -> None:
    """启动 Flow Bar（幂等）。"""
    _manager().start()


def stop_flow_bar() -> None:
    """停止 Flow Bar（幂等）。"""
    global _FLOW_BAR
    with _FLOW_LOCK:
        if _FLOW_BAR is None:
            return
        _FLOW_BAR.stop()
        _FLOW_BAR = None


def set_flow_state_resting() -> None:
    _manager().set_state(_STATE_RESTING)


def set_flow_state_active_ptt() -> None:
    _manager().set_state(_STATE_ACTIVE_PTT)


def set_flow_state_processing() -> None:
    _manager().set_state(_STATE_PROCESSING)


def set_flow_audio_level(level: float) -> None:
    _manager().set_audio_level(level)
