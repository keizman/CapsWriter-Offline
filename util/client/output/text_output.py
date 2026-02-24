# coding: utf-8
"""
文本输出模块

提供 TextOutput 类用于将识别结果输出到当前窗口。
"""

from __future__ import annotations

import asyncio
import platform
from typing import Optional
import re

from pynput import keyboard as pynput_keyboard

from config_client import ClientConfig as Config
from util.client.clipboard import paste_text
from . import logger

if platform.system() == 'Windows':
    import keyboard as keyboard_lib
else:
    keyboard_lib = None



class TextOutput:
    """
    文本输出器
    
    提供文本输出功能，支持模拟打字和粘贴两种方式。
    """
    
    @staticmethod
    def strip_punc(text: str) -> str:
        """
        消除末尾最后一个标点
        
        Args:
            text: 原始文本
            
        Returns:
            去除末尾标点后的文本
        """
        if not text or not Config.trash_punc:
            return text
        clean_text = re.sub(f"(?<=.)[{Config.trash_punc}]$", "", text)
        return clean_text
    
    async def output(
        self,
        text: str,
        paste: Optional[bool] = None,
        paste_profile: str = "default",
    ) -> None:
        """
        输出识别结果
        
        根据配置选择使用模拟打字或粘贴方式输出文本。
        
        Args:
            text: 要输出的文本
            paste: 是否使用粘贴方式（None 表示使用配置值）
        """
        if not text:
            return
        
        # 确定输出方式
        if paste is None:
            paste = Config.paste
        
        if paste:
            await self._paste_text(text, paste_profile=paste_profile)
        else:
            self._type_text(text)

    async def output_streaming(
        self,
        text: str,
        paste: Optional[bool] = None,
        char_interval_ms: int = 0,
        paste_profile: str = "default",
    ) -> None:
        """
        逐字流式输出文本（仅在 partial 输入模式下使用）。

        Args:
            text: 要输出的增量文本
            paste: 是否使用粘贴模式；partial 模式下通常为 False
            char_interval_ms: 每个字符之间的间隔（毫秒）
        """
        if not text:
            return

        if paste is None:
            paste = Config.paste

        if paste:
            # 粘贴模式无法提供逐字感知，回退为一次性输出。
            await self._paste_text(text, paste_profile=paste_profile)
            return

        delay = max(0.0, float(char_interval_ms) / 1000.0)
        for ch in text:
            self._type_text(ch)
            if delay > 0:
                await asyncio.sleep(delay)
    
    @staticmethod
    def _paste_timing(paste_profile: str) -> tuple[int, int]:
        profile = (paste_profile or "default").strip().lower()
        if profile == "remote":
            return (
                int(Config.paste_remote_pre_delay_ms),
                int(Config.paste_remote_restore_delay_ms),
            )
        return (
            int(Config.paste_pre_delay_ms),
            int(Config.paste_restore_delay_ms),
        )

    @staticmethod
    def _paste_profile_params(paste_profile: str) -> dict:
        profile = (paste_profile or "default").strip().lower()
        if profile == "remote":
            return {
                "copy_pulse_count": 3,
                "copy_pulse_interval_ms": 120,
                "restore_guard_window_ms": 2800,
                "restore_guard_interval_ms": 250,
                "restore_baseline_window_ms": 18000,
            }
        return {
            "copy_pulse_count": 1,
            "copy_pulse_interval_ms": 120,
            "restore_guard_window_ms": 0,
            "restore_guard_interval_ms": 250,
            "restore_baseline_window_ms": 12000,
        }

    async def _paste_text(self, text: str, paste_profile: str = "default") -> None:
        """
        通过粘贴方式输出文本
        
        Args:
            text: 要粘贴的文本
        """
        pre_delay_ms, restore_delay_ms = self._paste_timing(paste_profile)
        profile_params = self._paste_profile_params(paste_profile)
        logger.debug(
            "使用粘贴方式输出文本，长度=%s, profile=%s",
            len(text),
            paste_profile,
        )
        await paste_text(
            text,
            restore_clipboard=Config.restore_clip,
            pre_delay_ms=pre_delay_ms,
            restore_delay_ms=restore_delay_ms,
            safe_restore_only_if_unchanged=bool(Config.restore_clip_safeguard),
            restore_retry_count=3,
            restore_retry_interval_ms=80,
            copy_pulse_count=profile_params["copy_pulse_count"],
            copy_pulse_interval_ms=profile_params["copy_pulse_interval_ms"],
            restore_baseline_window_ms=profile_params["restore_baseline_window_ms"],
            restore_guard_window_ms=profile_params["restore_guard_window_ms"],
            restore_guard_interval_ms=profile_params["restore_guard_interval_ms"],
        )
    
    def _type_text(self, text: str) -> None:
        """
        通过模拟打字方式输出文本

        使用 keyboard.write 替代 pynput.keyboard.Controller.type()，
        避免与中文输入法冲突。

        Args:
            text: 要输出的文本
        """
        logger.debug(f"使用打字方式输出文本，长度: {len(text)}")
        if keyboard_lib is not None:
            keyboard_lib.write(text)
            return

        # 非 Windows 场景回退到 pynput
        controller = pynput_keyboard.Controller()
        controller.type(text)
