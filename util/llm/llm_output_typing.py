"""
LLM Typing 输出模式

直接打字输出，根据 paste 参数或 Config.paste 选择：
- paste=True: 等流式输出完成后一次性粘贴
- paste=False: 实时流式 write，每个字都打出来
"""
import asyncio
import platform
from pynput import keyboard as pynput_keyboard

from config_client import ClientConfig as Config
from util.tools.asyncio_to_thread import to_thread
from util.tools.window_detector import get_active_window_info
from util.client.output.text_output import TextOutput
from util.llm.llm_stop_monitor import reset, should_stop
from . import logger

if platform.system() == 'Windows':
    import keyboard as keyboard_lib
else:
    keyboard_lib = None


_PYNPUT_CONTROLLER = pynput_keyboard.Controller()
_TEXT_OUTPUT = TextOutput()


def _is_remote_compat_window() -> bool:
    """
    检测当前窗口是否为远控/兼容场景，需使用 remote 粘贴时序。
    """
    info = get_active_window_info()
    if not info:
        return False

    compatibility_keywords = (
        "weixin",
        "wechat",
        "微信",
        "rustdesk",
        "scrcpy",
        "mstsc",
        "remote desktop",
        "rdp",
        "远程桌面",
    )
    fields = (
        str(info.get("title", "")).lower(),
        str(info.get("class_name", "")).lower(),
        str(info.get("process_name", "")).lower(),
        str(info.get("app_name", "")).lower(),
    )
    for keyword in compatibility_keywords:
        token = keyword.lower()
        if any(token and token in field for field in fields):
            return True
    return False


async def _paste_via_text_output(text: str) -> None:
    profile = "remote" if _is_remote_compat_window() else "default"
    await _TEXT_OUTPUT.output(text, paste=True, paste_profile=profile)


def _write_text(text: str) -> None:
    """跨平台文本输出：Windows 用 keyboard，其他平台用 pynput。"""
    if not text:
        return
    if keyboard_lib is not None:
        keyboard_lib.write(text)
    else:
        _PYNPUT_CONTROLLER.type(text)


async def handle_typing_mode(text: str, paste: bool = None, matched_hotwords=None, role_config=None, content=None) -> tuple:
    """打字输出模式"""
    from util.llm.llm_handler import get_handler
    from util.llm.llm_error_handler import handle_llm_error

    handler = get_handler()
    # 如果没传，则现场检测一次（兼容性）
    if not role_config or content is None:
        role_config, content = handler.detect_role(text)
    
    if not role_config:
        # 不应发生，但作为防守
        result_text = TextOutput.strip_punc(text)
        await output_text(result_text, paste)
        return (result_text, 0, 0.0)

    reset()  # 重置停止标志

    try:
        if paste:
            return await _process_paste(handler, role_config, content, matched_hotwords)
        else:
            return await _process_streaming(handler, role_config, content, matched_hotwords)

    except Exception as e:
        result_text, _ = handle_llm_error(e, content, role_config.name if role_config else "LLM")
        result_text = TextOutput.strip_punc(result_text)
        await output_text(result_text, paste)
        return (result_text, 0, 0.0)


async def _process_paste(handler, role_config, content, matched_hotwords) -> tuple:
    """处理粘贴模式：获取全文后一次性粘贴"""
    polished_text, token_count, gen_time = await to_thread(
        handler.process, role_config, content, matched_hotwords, None, should_stop
    )
    if should_stop():
        return ("", 0, 0.0)

    final_text = TextOutput.strip_punc(polished_text or content)
    await _paste_via_text_output(final_text)
    return (final_text, token_count, gen_time)


async def _process_streaming(handler, role_config, content, matched_hotwords) -> tuple:
    """处理流式打字模式：边生成边模拟按键打字"""
    chunks = []
    pending_buffer = ""

    def stream_write_chunk(chunk: str):
        nonlocal pending_buffer
        if not chunk: return
        chunks.append(chunk)

        full_current = pending_buffer + chunk
        content_to_write = full_current
        trailing = ""
        
        # 从右向左寻找第一个非 trash 字符
        for i in range(len(full_current) - 1, -1, -1):
            char = full_current[i]
            if char == '\n' or char in Config.trash_punc:
                continue
            else:
                content_to_write = full_current[:i+1]
                trailing = full_current[i+1:]
                break
        else:
            content_to_write = ""
            trailing = full_current

        if content_to_write:
            logger.debug(f"output_text: keyboard.write '{content_to_write}'")
            _write_text(content_to_write)
            pending_buffer = trailing
        else:
            pending_buffer = trailing

    # 执行流式处理
    polished_text, token_count, gen_time = await to_thread(
        handler.process, role_config, content, matched_hotwords, stream_write_chunk, should_stop
    )

    # 阻塞，直到正常结束，或用户按下 ESC
    if should_stop():
        final_text = TextOutput.strip_punc(''.join(chunks) or content)
        return (final_text, 0, 0.0)

    # 如果模型没有任何输出，直接打出原文字
    if not chunks:
        final_text = TextOutput.strip_punc(content)
        logger.debug(f"output_text: keyboard.write '{final_text}' (降级)")
        _write_text(final_text)
        return (final_text, 0, 0.0)
    
    # 如果 LLM 只输出标点，会被拦截，就要做补偿输出
    full_output = ''.join(chunks).strip()
    if len(full_output) == 1 and full_output in Config.trash_punc:
        _write_text(full_output)
    
    return (TextOutput.strip_punc(polished_text), token_count, gen_time)


async def output_text(text: str, paste: bool = None):
    """输出文本（根据 paste 或 Config.paste 选择方式）"""
    if paste is None:
        paste = Config.paste

    if paste:
        await _paste_via_text_output(text)
    else:
        logger.debug(f"output_text: keyboard.write '{text}'")
        _write_text(text)
