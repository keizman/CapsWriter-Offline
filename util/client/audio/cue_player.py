# coding: utf-8
"""
录音提示音播放模块

在开始/结束录音时播放提示音，帮助用户确认状态切换。
"""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

from . import logger

_SOUND_REL_DIR = Path("resources/assets/sounds")
_START_SOUND = "dictation-start.wav"
_STOP_SOUND = "dictation-stop.wav"


def _candidate_roots() -> list[Path]:
    roots: list[Path] = [Path.cwd()]

    try:
        exe_parent = Path(sys.executable).resolve().parent
        roots.append(exe_parent)
        # macOS .app 下 PyInstaller 数据通常位于 Contents/Resources
        roots.append(exe_parent.parent / "Resources")
        # 若存在 .app 包，加入其同级目录（支持双击 .app 时从外部资源目录读取）
        for parent in exe_parent.parents:
            if parent.suffix == ".app":
                roots.append(parent.parent)
                break
    except Exception:
        pass

    # 源码运行兜底：项目根目录
    roots.append(Path(__file__).resolve().parents[3])

    seen = set()
    unique_roots: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique_roots.append(root)
    return unique_roots


def _resolve_sound(sound_name: str) -> Path | None:
    for root in _candidate_roots():
        path = root / _SOUND_REL_DIR / sound_name
        if path.exists():
            return path
    return None


def _play_by_platform(sound_path: Path) -> None:
    system = platform.system()

    if system == "Windows":
        try:
            import winsound

            winsound.PlaySound(str(sound_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception as exc:
            logger.debug(f"播放提示音失败(winsound): {exc}")
        return

    if system == "Darwin":
        try:
            subprocess.Popen(
                ["afplay", str(sound_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            logger.debug(f"播放提示音失败(afplay): {exc}")
        return

    # Linux/其他平台兜底
    for cmd in (["paplay", str(sound_path)], ["aplay", str(sound_path)]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue


def play_dictation_start() -> None:
    """播放开始录音提示音。"""
    path = _resolve_sound(_START_SOUND)
    if not path:
        logger.debug(f"未找到开始提示音: {_SOUND_REL_DIR / _START_SOUND}")
        return
    _play_by_platform(path)


def play_dictation_stop() -> None:
    """播放停止录音提示音。"""
    path = _resolve_sound(_STOP_SOUND)
    if not path:
        logger.debug(f"未找到停止提示音: {_SOUND_REL_DIR / _STOP_SOUND}")
        return
    _play_by_platform(path)
