# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

import asyncio
import time
from threading import Event
from typing import TYPE_CHECKING, Optional

from config_client import ClientConfig as Config
from . import logger
from util.client.audio.cue_player import play_dictation_start, play_dictation_stop
from util.client.ui import (
    set_flow_state_active_ptt,
    set_flow_state_processing,
    set_flow_state_resting,
)
from util.tools.my_status import Status

if TYPE_CHECKING:
    from util.client.shortcut.shortcut_config import Shortcut
    from util.client.state import ClientState
    from util.client.audio.recorder import AudioRecorder



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, shortcut: 'Shortcut', state: 'ClientState', recorder_class=None):
        """
        初始化快捷键任务

        Args:
            shortcut: 快捷键配置
            state: 客户端状态实例
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.shortcut = shortcut
        self.state = state
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self._finish_pending: bool = False

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    def _get_recorder(self) -> 'AudioRecorder':
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from util.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.state)

    def launch(self) -> None:
        """启动录音任务"""
        # 如果存在延迟结束中的旧会话，先取消其尾留音收尾任务
        self._finish_pending = False
        logger.info(f"[{self.shortcut.key}] 触发：开始录音")
        play_dictation_start()
        set_flow_state_active_ptt()

        # 记录开始时间
        self.recording_start_time = time.time()
        self.is_recording = True

        # 将开始标志放入队列
        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({'type': 'begin', 'time': self.recording_start_time, 'data': None}),
            self.state.loop
        )

        # 更新录音状态
        self.state.start_recording(self.recording_start_time)

        # 打印动画：正在录音
        self._status.start()

        # 启动识别任务
        recorder = self._get_recorder()
        self.task = asyncio.run_coroutine_threadsafe(
            recorder.record_and_send(),
            self.state.loop,
        )

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）")
        self._finish_pending = False
        play_dictation_stop()
        set_flow_state_resting()

        self.is_recording = False
        self.state.stop_recording()
        self._status.stop()

        self.task.cancel()
        self.task = None

    async def _finish_with_release_tail(self) -> None:
        """
        松键后尾留音，避免尾字丢失。

        规则：
        - 至少等待 release_tail_ms
        - 若仍有语音活动，则最多延长到 release_tail_max_ms
        - 检测到连续静音 release_tail_silence_ms 后结束
        """
        release_time = time.time()
        min_wait = max(0.0, float(Config.release_tail_ms) / 1000.0)
        max_wait = max(min_wait, float(Config.release_tail_max_ms) / 1000.0)
        silence_wait = max(0.0, float(Config.release_tail_silence_ms) / 1000.0)
        adaptive = bool(Config.release_tail_adaptive)

        while self._finish_pending and self.is_recording:
            now = time.time()
            elapsed = now - release_time

            if elapsed >= max_wait:
                break

            if elapsed < min_wait:
                await asyncio.sleep(min(0.02, min_wait - elapsed))
                continue

            if not adaptive:
                break

            last_voice = max(
                float(getattr(self.state, "last_voice_activity_time", 0.0)),
                self.recording_start_time,
            )
            silence_elapsed = now - last_voice
            if silence_elapsed >= silence_wait:
                break

            await asyncio.sleep(0.02)

        if not self._finish_pending or not self.is_recording:
            return
        self._finalize_finish()

    def _finalize_finish(self) -> None:
        """真正结束录音并发送最终片段标志。"""
        if not self.is_recording:
            self._finish_pending = False
            return

        self._finish_pending = False
        logger.info(f"[{self.shortcut.key}] 释放：完成录音")
        play_dictation_stop()
        set_flow_state_processing()

        self.is_recording = False
        self.state.stop_recording()
        self._status.stop()

        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'finish',
                'time': time.time(),
                'data': None
            }),
            self.state.loop
        )

        # 执行 restore（可恢复按键 + 非阻塞模式）
        # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

    def finish(self) -> None:
        """完成录音任务"""
        if not self.is_recording:
            return
        if self._finish_pending:
            return

        tail_enabled = bool(Config.release_tail_enabled)
        tail_ms = max(0, int(Config.release_tail_ms))

        if not tail_enabled or tail_ms <= 0:
            self._finalize_finish()
            return

        self._finish_pending = True
        logger.debug(
            f"[{self.shortcut.key}] 松键尾留音: min={tail_ms}ms, "
            f"max={int(Config.release_tail_max_ms)}ms, adaptive={bool(Config.release_tail_adaptive)}"
        )
        asyncio.run_coroutine_threadsafe(
            self._finish_with_release_tail(),
            self.state.loop
        )

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
