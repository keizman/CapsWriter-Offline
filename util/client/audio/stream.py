# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import sys
import time
import threading
from typing import TYPE_CHECKING, Optional, Any

import numpy as np
import sounddevice as sd

from config_client import ClientConfig as Config
from util.client.state import console
from util.client.ui import set_flow_audio_level
from . import logger
from util.common.lifecycle import lifecycle

if TYPE_CHECKING:
    from util.client.state import ClientState



class AudioStreamManager:
    """
    音频流管理器
    
    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭
    
    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """
    
    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms
    VIS_RMS_FLOOR_INIT = 3e-4
    VIS_RMS_PEAK_INIT = 2e-3
    VIS_FLOOR_FOLLOW_DOWN = 0.12
    VIS_FLOOR_FOLLOW_UP = 0.01
    VIS_PEAK_DECAY = 0.012
    VIS_GAMMA = 0.50
    VIS_MIN_ACTIVE_LEVEL = 0.22
    
    def __init__(self, state: 'ClientState'):
        """
        初始化音频流管理器
        
        Args:
            state: 客户端状态实例
        """
        self.state = state
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._visual_level = 0.0
        self._rms_floor = self.VIS_RMS_FLOOR_INIT
        self._rms_peak = self.VIS_RMS_PEAK_INIT
        self._active_device_signature: Optional[str] = None
        self._preferred_device_signature: Optional[str] = None
        self._device_monitor_thread: Optional[threading.Thread] = None
        self._device_monitor_stop = threading.Event()
        self._device_snapshot: tuple[str, ...] = tuple()
        self._stream_lock = threading.RLock()
        self._last_reported_device_signature: Optional[str] = None

    @staticmethod
    def _safe_str(value: Any) -> str:
        try:
            return str(value)
        except Exception:
            return ""

    def _resolve_hostapi_name(self, hostapi_index: Any) -> str:
        try:
            index = int(hostapi_index)
            hostapi = sd.query_hostapis(index)
            return self._safe_str(hostapi.get("name", ""))
        except Exception:
            return ""

    def _device_signature(self, index: int, info: dict) -> str:
        name = self._safe_str(info.get("name", ""))
        hostapi_name = self._resolve_hostapi_name(info.get("hostapi"))
        # 不使用 index，避免 Windows 下设备索引波动导致“同设备被误判为变化”。
        return f"{hostapi_name}|{name}"

    @staticmethod
    def _is_windows() -> bool:
        return sys.platform.startswith("win")

    @staticmethod
    def _is_sound_mapper_name(name: str) -> bool:
        lowered = str(name or "").lower()
        return "sound mapper" in lowered

    def _windows_wasapi_default_index(self) -> Optional[int]:
        """
        获取 Windows WASAPI 的默认输入设备索引（如果存在）。

        目的：避免落到 MME 的 Sound Mapper 抽象设备，优先使用具体物理设备。
        """
        if not self._is_windows():
            return None
        try:
            hostapis = sd.query_hostapis()
        except Exception:
            return None
        for hostapi in hostapis:
            name = self._safe_str(hostapi.get("name", "")).lower()
            if "wasapi" not in name:
                continue
            try:
                idx = int(hostapi.get("default_input_device", -1))
            except Exception:
                idx = -1
            if idx >= 0:
                return idx
        return None

    def _list_input_devices(self) -> list[dict]:
        devices: list[dict] = []
        try:
            raw_devices = sd.query_devices()
        except Exception:
            return devices

        for idx, dev in enumerate(raw_devices):
            try:
                channels = int(dev.get("max_input_channels", 0))
            except Exception:
                channels = 0
            if channels <= 0:
                continue
            signature = self._device_signature(idx, dev)
            devices.append({
                "index": idx,
                "name": self._safe_str(dev.get("name", "未知设备")),
                "hostapi_name": self._resolve_hostapi_name(dev.get("hostapi")),
                "max_input_channels": channels,
                "signature": signature,
            })
        return devices

    def _normalize_input_device_index(self, value: Any) -> Optional[int]:
        """
        从 sounddevice 返回值中提取输入设备索引。
        """
        if isinstance(value, (tuple, list)):
            if not value:
                return None
            value = value[0]
        try:
            idx = int(value)
            return idx if idx >= 0 else None
        except Exception:
            return None

    def _default_input_device_index(self) -> Optional[int]:
        try:
            default_device = sd.default.device
        except Exception:
            return None

        if isinstance(default_device, (tuple, list)) and default_device:
            try:
                index = int(default_device[0])
                return index if index >= 0 else None
            except Exception:
                return None
        return None

    def _pick_best_input_device(self, inputs: list[dict]) -> Optional[dict]:
        if not inputs:
            return None

        if self._preferred_device_signature:
            for dev in inputs:
                if dev["signature"] == self._preferred_device_signature:
                    return dev

        default_index = self._default_input_device_index()
        if default_index is not None:
            selected_default = None
            for dev in inputs:
                if dev["index"] == default_index:
                    selected_default = dev
                    break
            if selected_default is not None:
                # Windows 下默认若落到 Sound Mapper，尽量切到 WASAPI 的具体默认输入设备。
                if self._is_windows() and self._is_sound_mapper_name(selected_default.get("name", "")):
                    wasapi_default_index = self._windows_wasapi_default_index()
                    if wasapi_default_index is not None:
                        for dev in inputs:
                            if dev["index"] == wasapi_default_index:
                                return dev
                return selected_default

        return inputs[0]

    def _capture_device_snapshot(self, inputs: list[dict]) -> tuple[str, ...]:
        signatures = [dev["signature"] for dev in inputs]
        return tuple(sorted(signatures))

    def _start_device_monitor(self) -> None:
        if not bool(getattr(Config, "audio_device_auto_refresh", True)):
            return
        if self._device_monitor_thread and self._device_monitor_thread.is_alive():
            return

        self._device_monitor_stop.clear()
        self._device_monitor_thread = threading.Thread(
            target=self._device_monitor_loop,
            name="capswriter-audio-device-monitor",
            daemon=True,
        )
        self._device_monitor_thread.start()
        logger.debug("音频设备自动刷新线程已启动")

    def _stop_device_monitor(self) -> None:
        self._device_monitor_stop.set()
        thread = self._device_monitor_thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._device_monitor_thread = None
        logger.debug("音频设备自动刷新线程已停止")

    def _device_monitor_loop(self) -> None:
        interval = max(1.0, float(getattr(Config, "audio_device_poll_interval_secs", 1.5)))
        while not self._device_monitor_stop.wait(interval):
            if lifecycle.is_shutting_down:
                break
            if not self._running:
                continue

            try:
                inputs = self._list_input_devices()
                snapshot = self._capture_device_snapshot(inputs)
                if snapshot == self._device_snapshot:
                    continue

                prev = set(self._device_snapshot)
                curr = set(snapshot)
                removed = len(prev - curr)
                added = len(curr - prev)
                self._device_snapshot = snapshot
                logger.info(f"检测到音频设备变化：新增 {added}，移除 {removed}")
                self._handle_device_change(inputs)
            except Exception as e:
                logger.debug(f"音频设备自动刷新检查失败: {e}")

    def _handle_device_change(self, inputs: list[dict]) -> None:
        active_signature = self._active_device_signature
        available = {dev["signature"] for dev in inputs}
        selected = self._pick_best_input_device(inputs)
        selected_signature = selected["signature"] if selected else None

        if self.state.stream is None and selected:
            logger.info("检测到可用输入设备，正在自动恢复音频流")
            self.reopen(reason="自动刷新：检测到可用设备，恢复音频流")
            return

        # 设备集合变化但最终选中设备没变：不重启、不重复选举。
        if self.state.stream is not None and active_signature and selected_signature == active_signature:
            logger.debug("设备变化但选中设备未变化，跳过重启")
            return

        if active_signature and active_signature not in available:
            logger.warning("当前输入设备已断开，正在自动切换到可用设备")
            self.reopen(reason="自动刷新：当前设备断开，切换到可用设备")
            return

        if self.state.stream is not None and selected_signature and active_signature != selected_signature:
            logger.info("检测到输入设备优先级变化，正在切换")
            self.reopen(reason="自动刷新：输入设备优先级变化，切换设备")

    def _create_stream(self, exit_on_missing_device: bool = False) -> Optional[sd.InputStream]:
        # 检测并选择音频设备
        try:
            inputs = self._list_input_devices()
            self._device_snapshot = self._capture_device_snapshot(inputs)
            selected = self._pick_best_input_device(inputs)
            if selected is None:
                raise sd.PortAudioError("no input device")
        except UnicodeDecodeError:
            console.print(
                "由于编码问题，暂时无法获得麦克风设备名字",
                end='\n\n',
                style='bright_red'
            )
            logger.warning("无法获取音频设备名称（编码问题）")
            return None
        except sd.PortAudioError:
            if exit_on_missing_device:
                console.print("没有找到麦克风设备", end='\n\n', style='bright_red')
                logger.error("未找到麦克风设备")
                input('按回车键退出')
                sys.exit(1)
            logger.warning("未找到可用输入设备，等待设备恢复...")
            return None

        self._channels = min(2, int(selected["max_input_channels"]))
        selected_name = selected.get("name", "未知设备")
        selected_hostapi = selected.get("hostapi_name", "")
        selected_display = (
            f"{selected_name} ({selected_hostapi})" if selected_hostapi else str(selected_name)
        )

        # Windows 下如果命中 Sound Mapper，改为 device=None，交给 PortAudio 解析真实默认设备（接近旧行为）。
        chosen_device_index: Optional[int] = int(selected["index"])
        if self._is_windows() and self._is_sound_mapper_name(selected_name):
            logger.debug(
                "检测到 Sound Mapper，回退为系统默认输入设备解析: %s",
                selected_display,
            )
            chosen_device_index = None

        selected_signature = selected["signature"]

        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=chosen_device_index,
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()

            # 以“实际打开后的输入设备”为准更新显示与签名。
            actual_index = self._normalize_input_device_index(getattr(stream, "device", None))
            actual_display = selected_display
            actual_signature = selected_signature
            if actual_index is not None:
                try:
                    actual_raw = sd.query_devices(actual_index)
                    actual_name = self._safe_str(actual_raw.get("name", "未知设备"))
                    actual_hostapi = self._resolve_hostapi_name(actual_raw.get("hostapi"))
                    actual_display = (
                        f"{actual_name} ({actual_hostapi})" if actual_hostapi else actual_name
                    )
                    actual_signature = self._device_signature(actual_index, actual_raw)
                except Exception:
                    pass

            # 如果尚未确定优先设备，则以首次成功“实际设备”作为优先设备
            if not self._preferred_device_signature:
                self._preferred_device_signature = actual_signature
                logger.info(f"优先输入设备已设置为: {actual_display}")

            if self._last_reported_device_signature != actual_signature:
                console.print(
                    f'使用音频设备：[italic]{actual_display}，声道数：{self._channels}',
                    end='\n\n'
                )
                logger.info(
                    f"使用音频设备: {actual_display}, stream_device={actual_index}, "
                    f"声道数={self._channels}"
                )
                self._last_reported_device_signature = actual_signature
            else:
                logger.debug("继续使用相同音频设备: %s", actual_display)

            self.state.stream = stream
            self._running = True
            self._active_device_signature = actual_signature
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )
            return stream

        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None
    
    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数
        
        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # 只在录音状态时处理数据
        if not self.state.recording:
            return

        # 更新 Flow Bar 音量级别（0~1）
        try:
            # 自适应 AGC：动态跟随噪声底与说话峰值，提升中小音量可视化反馈。
            rms = float(np.sqrt(np.mean(np.square(indata))))
            rms = max(0.0, rms)
            self.state.latest_rms = rms
            if rms >= float(Config.release_tail_vad_threshold):
                self.state.last_voice_activity_time = time.time()

            if rms < self._rms_floor:
                self._rms_floor += (rms - self._rms_floor) * self.VIS_FLOOR_FOLLOW_DOWN
            else:
                self._rms_floor += (rms - self._rms_floor) * self.VIS_FLOOR_FOLLOW_UP

            self._rms_floor = max(5e-5, self._rms_floor)

            self._rms_peak = max(rms, self._rms_peak * (1.0 - self.VIS_PEAK_DECAY))
            if self._rms_peak < self._rms_floor * 1.6:
                self._rms_peak = self._rms_floor * 1.6

            dynamic_range = max(2e-4, self._rms_peak - self._rms_floor)
            norm = (rms - self._rms_floor) / dynamic_range
            norm = min(1.0, max(0.0, norm))
            target_level = float(norm ** self.VIS_GAMMA)

            if target_level > 0.0:
                target_level = max(target_level, self.VIS_MIN_ACTIVE_LEVEL)
            else:
                target_level = 0.0

            # 上升快、下降慢，避免跳动且保证有输入时反馈及时
            smooth = 0.58 if target_level > self._visual_level else 0.14
            self._visual_level += (target_level - self._visual_level) * smooth
            set_flow_audio_level(self._visual_level)
        except Exception:
            pass

        import asyncio
        
        # 将数据放入队列
        if self.state.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.state.loop
            )
    
    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        
        # 只有在应该运行且不是手动停止、且系统未处于关闭状态的情况下才重启
        if self._running and not lifecycle.is_shutting_down:
            logger.info("音频流意外结束，正在尝试重启...")
            self.reopen(reason="音频流意外结束，自动重启")
        else:
            logger.debug("音频流已正常结束")
    
    def open(self) -> Optional[sd.InputStream]:
        """
        打开音频流
        
        Returns:
            创建的音频输入流，如果失败返回 None
        """
        with self._stream_lock:
            stream = self._create_stream(exit_on_missing_device=True)
            if stream:
                self._start_device_monitor()
            return stream

    def _close_stream_only(self) -> None:
        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已关闭")
            except Exception as e:
                logger.debug(f"关闭音频流时发生错误: {e}")
            finally:
                self.state.stream = None
        self._active_device_signature = None
    
    def close(self) -> None:
        """关闭音频流"""
        self._running = False  # 标记为停止
        self._device_monitor_stop.set()
        with self._stream_lock:
            self._close_stream_only()
        self._stop_device_monitor()
    
    def reopen(self, reason: str = "正在重启音频流...") -> Optional[sd.InputStream]:
        """
        重新打开音频流
        
        Returns:
            新创建的音频输入流
        """
        with self._stream_lock:
            logger.info(reason)
            # 先标记非运行，避免“主动关流”被 finished_callback 误判为异常而重复重启。
            self._running = False
            self._close_stream_only()

            # 重载 PortAudio，更新设备列表
            try:
                sd._terminate()
                # macOS 下保留完整重载；Windows/Linux 避免 dlclose 带来的不稳定。
                if sys.platform == "darwin":
                    sd._ffi.dlclose(sd._lib)
                    sd._lib = sd._ffi.dlopen(sd._libname)
                sd._initialize()
            except Exception as e:
                logger.warning(f"重载 PortAudio 时发生警告: {e}")

            # 等待设备稳定
            time.sleep(0.1)

            # 打开新流
            stream = self._create_stream(exit_on_missing_device=False)
            if stream:
                self._start_device_monitor()
            return stream
