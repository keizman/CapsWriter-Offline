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
import subprocess
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

_FLOW_BAR_BOTTOM_PADDING = 24
_FLOW_BAR_EDGE_PADDING = 8

_BASE_SCREEN_WIDTH = 1920.0
_BASE_SCREEN_HEIGHT = 1080.0
_MIN_UI_SCALE = 1.0
_MAX_UI_SCALE = 2.2

_DEFAULT_BG_COLOR = "#101214"
_WINDOWS_TRANSPARENT_KEY = "#00ff00"
_MACOS_TRANSPARENT_BG = "systemTransparent"

_MACOS_DOCK_MIN_INSET = 44
_MACOS_DOCK_MAX_INSET = 220
_MACOS_DOCK_AUTOHIDE_INSET = 10

_AUDIO_NOISE_FLOOR = 0.04
_AUDIO_VISUAL_SMOOTH = 0.24

_BAR_COUNT = 10
_BAR_ENVELOPE_SILENT = [0.08, 0.11, 0.14, 0.18, 0.23, 0.23, 0.18, 0.14, 0.11, 0.08]


class _FlowBarIndicator:
    def __init__(self) -> None:
        self._commands: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._state = _STATE_RESTING
        self._audio_level = 0.0
        self._audio_visual_level = 0.0
        self._phase = 0.0

        style = _STYLES[_STATE_RESTING]
        self._current_width = style.width
        self._current_height = style.height
        self._target_width = style.width
        self._target_height = style.height
        self._current_alpha = _STATE_ALPHAS[_STATE_RESTING]
        self._target_alpha = _STATE_ALPHAS[_STATE_RESTING]
        self._ui_scale = 1.0
        self._window_bg_color = _DEFAULT_BG_COLOR
        self._macos_transparent_bg_enabled = False
        self._frame_count = 0
        self._macos_dock_bottom_inset = 0

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
            try:
                root.attributes("-alpha", self._current_alpha)
            except Exception:
                pass

            self._window_bg_color = _WINDOWS_TRANSPARENT_KEY if platform.system() == "Windows" else _DEFAULT_BG_COLOR

            if platform.system() == "Darwin":
                # macOS: 强制无标题栏样式，隐藏红黄绿按钮
                try:
                    root.tk.call("::tk::unsupported::MacWindowStyle", "style", root._w, "floating", "none")
                except Exception:
                    pass
                try:
                    root.tk.call("::tk::unsupported::MacWindowStyle", "style", root._w, "plain", "none")
                except Exception:
                    pass

            if platform.system() == "Darwin":
                bg = _MACOS_TRANSPARENT_BG
            else:
                bg = self._window_bg_color
            root.configure(bg=bg)
            canvas = tk.Canvas(root, highlightthickness=0, bd=0, bg=bg)
            canvas.pack(fill=tk.BOTH, expand=True)
            if platform.system() == "Darwin":
                try:
                    root.wm_attributes("-transparent", True)
                    self._macos_transparent_bg_enabled = True
                except Exception:
                    self._macos_transparent_bg_enabled = False
                    root.configure(bg=_DEFAULT_BG_COLOR)
                    canvas.configure(bg=_DEFAULT_BG_COLOR)
            if platform.system() == "Windows":
                try:
                    # 仅显示胶囊形主体，隐藏矩形窗口底板
                    root.wm_attributes("-transparentcolor", bg)
                except Exception:
                    self._window_bg_color = _DEFAULT_BG_COLOR
                    root.configure(bg=self._window_bg_color)
                    canvas.configure(bg=self._window_bg_color)

            self._refresh_ui_scale(reset_current=True)
            if platform.system() == "Darwin":
                self._macos_dock_bottom_inset = self._detect_macos_dock_bottom_inset()
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
                self._audio_visual_level += (self._audio_level - self._audio_visual_level) * _AUDIO_VISUAL_SMOOTH
                self._phase += 0.34
                self._frame_count += 1
                if self._frame_count % 60 == 0:
                    self._refresh_ui_scale(reset_current=False)
                if platform.system() == "Darwin" and self._frame_count % 180 == 0:
                    self._macos_dock_bottom_inset = self._detect_macos_dock_bottom_inset()

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
                style = self._style_for_state(self._state)
                self._target_width = style.width
                self._target_height = style.height
                self._target_alpha = _STATE_ALPHAS.get(self._state, _STATE_ALPHAS[_STATE_RESTING])
                if self._state == _STATE_RESTING:
                    self._audio_level = 0.0
                    self._audio_visual_level = 0.0
            elif cmd == "audio":
                self._audio_level = float(payload)

        return False

    def _style_for_state(self, state: str) -> _FlowStyle:
        base = _STYLES.get(state, _STYLES[_STATE_RESTING])
        scale = max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, float(self._ui_scale)))
        return _FlowStyle(
            width=base.width * scale,
            height=base.height * scale,
        )

    def _refresh_ui_scale(self, reset_current: bool = False) -> None:
        if not self._root:
            return
        self._ui_scale = self._detect_ui_scale()
        style = self._style_for_state(self._state)
        self._target_width = style.width
        self._target_height = style.height
        if reset_current:
            self._current_width = style.width
            self._current_height = style.height

    def _detect_ui_scale(self) -> float:
        if not self._root:
            return 1.0

        try:
            screen_w = float(self._root.winfo_screenwidth())
            screen_h = float(self._root.winfo_screenheight())
        except Exception:
            screen_w = _BASE_SCREEN_WIDTH
            screen_h = _BASE_SCREEN_HEIGHT

        resolution_scale = min(screen_w / _BASE_SCREEN_WIDTH, screen_h / _BASE_SCREEN_HEIGHT)
        resolution_scale = max(1.0, resolution_scale)

        dpi_scale = 1.0
        try:
            pixels_per_inch = float(self._root.winfo_fpixels("1i"))
            if pixels_per_inch > 0:
                dpi_scale = pixels_per_inch / 96.0
        except Exception:
            pass

        final_scale = max(resolution_scale, dpi_scale, 1.0)
        return max(_MIN_UI_SCALE, min(_MAX_UI_SCALE, final_scale))

    def _apply_geometry(self) -> None:
        if not self._root:
            return
        width = max(16, int(self._current_width))
        height = max(8, int(self._current_height))
        left, top, right, bottom = self._get_usable_screen_rect()
        usable_w = max(1, right - left)
        x = int(left + (usable_w - width) / 2)
        bottom_padding = int(_FLOW_BAR_BOTTOM_PADDING * self._ui_scale)
        edge_padding = int(_FLOW_BAR_EDGE_PADDING * self._ui_scale)
        platform_bottom_inset = self._get_platform_bottom_inset(bottom)
        y = int(bottom - bottom_padding - height - platform_bottom_inset)
        if y < top + edge_padding:
            y = top + edge_padding
        self._root.geometry(f"{width}x{height}+{x}+{y}")

    def _get_platform_bottom_inset(self, usable_bottom: int) -> int:
        if platform.system() != "Darwin" or not self._root:
            return 0

        # 若工作区已排除 Dock（usable_bottom < screen_h），不再重复抬高。
        try:
            screen_h = int(self._root.winfo_screenheight())
        except Exception:
            screen_h = usable_bottom
        if usable_bottom < screen_h - 2:
            return 0
        return max(0, int(self._macos_dock_bottom_inset))

    def _read_macos_dock_pref(self, key: str) -> str:
        try:
            result = subprocess.run(
                ["defaults", "read", "com.apple.dock", key],
                capture_output=True,
                text=True,
                timeout=0.3,
                check=False,
            )
            if result.returncode != 0:
                return ""
            return result.stdout.strip()
        except Exception:
            return ""

    def _detect_macos_dock_bottom_inset(self) -> int:
        if platform.system() != "Darwin":
            return 0

        orientation = self._read_macos_dock_pref("orientation").lower()
        if orientation and orientation != "bottom":
            return 0

        autohide_raw = self._read_macos_dock_pref("autohide").lower()
        autohide = autohide_raw in {"1", "true", "yes"}
        if autohide:
            return int(max(_MACOS_DOCK_AUTOHIDE_INSET, _MACOS_DOCK_AUTOHIDE_INSET * self._ui_scale))

        magnification_raw = self._read_macos_dock_pref("magnification").lower()
        magnification = magnification_raw in {"1", "true", "yes"}

        def _to_int(text: str, fallback: int) -> int:
            try:
                return int(float(text))
            except Exception:
                return fallback

        tile_size = _to_int(self._read_macos_dock_pref("tilesize"), 48)
        large_size = _to_int(self._read_macos_dock_pref("largesize"), tile_size)
        dock_size = large_size if magnification and large_size > 0 else tile_size
        estimated = dock_size + 24

        # 下限与上限保护，避免配置异常导致偏移过大/过小。
        min_inset = int(_MACOS_DOCK_MIN_INSET * self._ui_scale)
        max_inset = int(_MACOS_DOCK_MAX_INSET * self._ui_scale)
        return max(min_inset, min(estimated, max_inset))

    def _get_usable_screen_rect(self) -> tuple[int, int, int, int]:
        if not self._root:
            return (0, 0, 1920, 1080)

        # 默认使用整屏尺寸
        left = 0
        top = 0
        right = int(self._root.winfo_screenwidth())
        bottom = int(self._root.winfo_screenheight())

        # 优先尝试 Tk 的虚拟根工作区（部分平台会排除任务栏/Dock）
        try:
            v_left = int(self._root.winfo_vrootx())
            v_top = int(self._root.winfo_vrooty())
            v_width = int(self._root.winfo_vrootwidth())
            v_height = int(self._root.winfo_vrootheight())
            if v_width > 0 and v_height > 0:
                left = v_left
                top = v_top
                right = v_left + v_width
                bottom = v_top + v_height
        except Exception:
            pass

        # Windows 使用系统工作区，精确避开任务栏（含置顶/高任务栏）
        if platform.system() == "Windows":
            try:
                import ctypes

                class _Rect(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]

                rect = _Rect()
                SPI_GETWORKAREA = 0x0030
                ok = ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
                )
                if ok:
                    left = int(rect.left)
                    top = int(rect.top)
                    right = int(rect.right)
                    bottom = int(rect.bottom)
            except Exception:
                pass

        return left, top, right, bottom

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
                self._root.tk.call("::tk::unsupported::MacWindowStyle", "style", self._root._w, "floating", "none")
            except Exception:
                pass
            try:
                self._root.tk.call("::tk::unsupported::MacWindowStyle", "style", self._root._w, "plain", "none")
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
        r = h / 2.0

        fill = "#15181b"
        if self._state == _STATE_RESTING:
            fill = "#0c0f12"
        elif self._state == _STATE_PROCESSING:
            fill = "#1a1f25"

        # 用圆头线段绘制胶囊背景，边缘更圆滑。
        cy = h / 2.0
        x1 = max(r, 0.0)
        x2 = max(x1, w - r)
        self._canvas.create_line(x1, cy, x2, cy, fill=fill, width=h, capstyle="round")

        if self._state == _STATE_RESTING:
            return

        bars = _BAR_COUNT
        base_bar_w = max(2.0, (w * 0.42) / bars)
        bar_w = max(1.2, base_bar_w * 0.8)
        gap = base_bar_w * 0.55
        total = bars * bar_w + (bars - 1) * gap
        start_x = (w - total) / 2.0
        cy = h / 2.0

        audio_activity_raw = max(
            0.0,
            min(1.0, (self._audio_visual_level - _AUDIO_NOISE_FLOOR) / (1.0 - _AUDIO_NOISE_FLOOR))
        )
        # 提升低电平区段灵敏度，让轻声输入更容易进入“有声”波动
        audio_activity = audio_activity_raw ** 0.62

        for i in range(bars):
            px = self._phase + i * 0.62
            wave = abs(math.sin(px))
            if self._state == _STATE_ACTIVE_PTT:
                # 无声录音态：中心更高，仅轻微律动。
                silent_wave = 0.02 * abs(math.sin(self._phase * 0.68 + i * 0.50))
                silent_scale = _BAR_ENVELOPE_SILENT[i] + silent_wave

                # 有声录音态：保持原有显著波幅，并用音量放大。
                audio_amp = 0.36 + 0.82 * self._audio_visual_level
                audio_scale = 0.28 + audio_amp * wave

                # 根据音频活动度在 silent/audio 两种样式间平滑过渡。
                scale = silent_scale * (1.0 - audio_activity) + audio_scale * audio_activity
            else:
                scale = 0.35 + 0.55 * wave

            scale = max(0.0, min(1.0, scale))
            if self._state == _STATE_ACTIVE_PTT:
                # 无音时接近“....”小点；检测到语音后逐步恢复到长柱波动。
                silent_min_bar_h = max(1.0, h * 0.05)
                silent_max_bar_h = max(2.0, h * 0.24)
                audio_min_bar_h = max(2.0, h * 0.16)
                audio_max_bar_h = h * 0.62
                min_bar_h = silent_min_bar_h * (1.0 - audio_activity) + audio_min_bar_h * audio_activity
                max_bar_h = silent_max_bar_h * (1.0 - audio_activity) + audio_max_bar_h * audio_activity
            else:
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
