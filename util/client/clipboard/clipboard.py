# coding: utf-8
"""
剪贴板工具模块

提供统一的剪贴板操作接口，包括：
1. 安全读取剪贴板（支持多种编码）
2. 安全写入剪贴板
3. 剪贴板保存/恢复上下文管理器
4. 粘贴文本（模拟 Ctrl+V）
"""
import asyncio
import hashlib
import platform
import time
from contextlib import contextmanager
import pyclip
from pynput import keyboard
from . import logger


# 支持的编码列表
CLIPBOARD_ENCODINGS = ['utf-8', 'gbk', 'utf-16', 'latin1']
_PASTE_LOCK = asyncio.Lock()


def safe_paste() -> str:
    """
    安全地从剪贴板读取并解码文本

    尝试多种编码方式，确保能够正确读取

    Returns:
        解码后的文本字符串，失败返回空字符串
    """
    try:
        clipboard_data = pyclip.paste()

        if clipboard_data is None:
            return ""

        if isinstance(clipboard_data, str):
            return clipboard_data

        # 尝试多种编码方式
        for encoding in CLIPBOARD_ENCODINGS:
            try:
                return clipboard_data.decode(encoding)
            except (UnicodeDecodeError, AttributeError):
                continue

        # 如果所有编码都失败，返回空字符串
        logger.debug(f"剪贴板解码失败，尝试了编码: {CLIPBOARD_ENCODINGS}")
        return ""

    except Exception as e:
        logger.warning(f"剪贴板读取失败: {e}")
        return ""


def safe_copy(content: str) -> bool:
    """
    安全地复制内容到剪贴板

    Args:
        content: 要复制的内容

    Returns:
        是否成功
    """
    if not content:
        return False

    try:
        pyclip.copy(content)
        logger.debug(f"剪贴板写入成功，长度: {len(content)}")
        return True
    except Exception as e:
        logger.warning(f"剪贴板写入失败: {e}")
        return False


def copy_to_clipboard(content: str):
    """
    复制内容到剪贴板（兼容旧 API）

    Args:
        content: 要复制的内容
    """
    safe_copy(content)


@contextmanager
def save_and_restore_clipboard():
    """
    剪贴板保存/恢复上下文管理器

    用法:
        with save_and_restore_clipboard():
            # 在这里操作剪贴板
            pyclip.copy("临时内容")
        # 退出后剪贴板恢复原内容
    """
    original = ""
    has_original = False
    try:
        original = safe_paste()
        has_original = True
    except Exception:
        pass
    try:
        yield
    finally:
        if has_original:
            pyclip.copy(original)
            logger.debug("剪贴板已恢复")


def _text_fingerprint(text: str) -> str:
    value = text or ""
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


async def paste_text(
    text: str,
    restore_clipboard: bool = True,
    *,
    pre_delay_ms: int = 0,
    restore_delay_ms: int = 100,
    safe_restore_only_if_unchanged: bool = False,
    restore_retry_count: int = 2,
    restore_retry_interval_ms: int = 80,
):
    """
    通过模拟 Ctrl+V 粘贴文本

    Args:
        text: 要粘贴的文本
        restore_clipboard: 粘贴后是否恢复原剪贴板内容
        pre_delay_ms: 复制到剪贴板后，发送 Ctrl/Cmd+V 前的等待时间
        restore_delay_ms: 发送粘贴后，恢复剪贴板前的等待时间
        safe_restore_only_if_unchanged: 仅当剪贴板仍是本次注入文本时才恢复
        restore_retry_count: 恢复失败时的补偿重试次数
        restore_retry_interval_ms: 恢复重试间隔
    """
    if not text:
        return

    injected_text = str(text)
    op_id = f"paste-{int(time.time() * 1000)}"
    fp = _text_fingerprint(injected_text)

    async with _PASTE_LOCK:
        # 保存原剪贴板
        original = ""
        has_original = False
        if restore_clipboard:
            try:
                original = safe_paste()
                has_original = True
            except Exception:
                has_original = False

        # 复制要粘贴的文本
        pyclip.copy(injected_text)
        logger.debug(
            "paste[%s] copied, len=%s, fp=%s, pre_delay_ms=%s restore_delay_ms=%s",
            op_id,
            len(injected_text),
            fp,
            pre_delay_ms,
            restore_delay_ms,
        )

        if pre_delay_ms > 0:
            await asyncio.sleep(max(0.0, pre_delay_ms / 1000.0))

        # 粘贴结果（使用 pynput 模拟 Ctrl+V）
        controller = keyboard.Controller()
        if platform.system() == 'Darwin':
            # macOS: Command+V
            with controller.pressed(keyboard.Key.cmd):
                controller.tap('v')
        else:
            # Windows/Linux: Ctrl+V
            with controller.pressed(keyboard.Key.ctrl):
                controller.tap('v')

        logger.debug("paste[%s] sent hotkey", op_id)

        # 还原剪贴板
        if restore_clipboard and has_original:
            if restore_delay_ms > 0:
                await asyncio.sleep(max(0.0, restore_delay_ms / 1000.0))

            if safe_restore_only_if_unchanged:
                current = safe_paste()
                if current != injected_text:
                    logger.debug("paste[%s] skip restore: clipboard changed externally", op_id)
                    return

            def _restore_once() -> bool:
                try:
                    pyclip.copy(original)
                    return safe_paste() == original
                except Exception:
                    return False

            restored = _restore_once()
            if not restored:
                retries = max(0, int(restore_retry_count))
                interval_sec = max(0.0, float(restore_retry_interval_ms) / 1000.0)
                for _ in range(retries):
                    if interval_sec > 0:
                        await asyncio.sleep(interval_sec)
                    if _restore_once():
                        restored = True
                        break

            if restored:
                logger.debug("paste[%s] restored clipboard", op_id)
            else:
                logger.warning("paste[%s] restore clipboard failed after retries", op_id)
