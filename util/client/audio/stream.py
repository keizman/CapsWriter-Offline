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
        return f"{index}|{hostapi_name}|{name}"

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
            for dev in inputs:
                if dev["index"] == default_index:
                    return dev

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
        preferred_signature = self._preferred_device_signature
        available = {dev["signature"] for dev in inputs}

        if self.state.stream is None and inputs:
            logger.info("检测到可用输入设备，正在自动恢复音频流")
            self.reopen(reason="自动刷新：检测到可用设备，恢复音频流")
            return

        if active_signature and active_signature not in available:
            logger.warning("当前输入设备已断开，正在自动切换到可用设备")
            self.reopen(reason="自动刷新：当前设备断开，切换到可用设备")
            return

        if (
            preferred_signature
            and preferred_signature in available
            and active_signature != preferred_signature
        ):
            logger.info("检测到优先麦克风已恢复，正在自动切回")
            self.reopen(reason="自动刷新：优先设备已恢复，切回优先设备")

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
        device_name = selected.get("name", "未知设备")
        hostapi_name = selected.get("hostapi_name", "")
        if hostapi_name:
            device_display = f"{device_name} ({hostapi_name})"
        else:
            device_display = str(device_name)

        console.print(
            f'使用音频设备：[italic]{device_display}，声道数：{self._channels}',
            end='\n\n'
        )
        logger.info(
            f"使用音频设备: {device_display}, index={selected['index']}, "
            f"声道数={self._channels}"
        )

        # 如果尚未确定优先设备，则以首次成功设备作为优先设备
        selected_signature = selected["signature"]
        if not self._preferred_device_signature:
            self._preferred_device_signature = selected_signature
            logger.info(f"优先输入设备已设置为: {device_display}")

        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=selected["index"],
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()

            self.state.stream = stream
            self._running = True
            self._active_device_signature = selected_signature
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
            self._running = True
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
