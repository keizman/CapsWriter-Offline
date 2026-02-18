# coding: utf-8
"""
HTTP 转录接口模块。

提供 POST /api/transcript：
- 接收上传的音频文件
- 统一转为 float32/16k/mono
- 复用现有识别队列返回最终文本
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Optional

from aiohttp import web
import numpy as np

from config_server import ServerConfig as Config
from util.constants import AudioFormat
from util.server.queue_guard import queue_guard
from util.server.server_classes import Result, Task
from util.server.server_cosmic import Cosmic
from . import logger


def _authorize_http_request(request: web.Request) -> bool:
    """校验 HTTP 请求 secret（与 WS 保持一致）。"""
    expected_secret = str(getattr(Config, "secret", "")).strip()
    if not expected_secret:
        return True

    provided_secret = str(request.headers.get("X-CapsWriter-Secret", "")).strip()
    return provided_secret == expected_secret


def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件。"""
    local_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local_path = Path.cwd() / local_name
    if local_path.exists():
        return local_path.as_posix()

    found = shutil.which("ffmpeg")
    return found or ""


def _parse_float(raw: Optional[str], default: float, min_value: float) -> float:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return max(min_value, value)


def _parse_int(raw: Optional[str], default: int, min_value: int) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(min_value, value)


async def _save_upload_to_temp(field) -> tuple[Path, str]:
    """保存 multipart 上传文件到临时文件。"""
    raw_name = field.filename or "audio.bin"
    suffix = Path(raw_name).suffix[:16] or ".bin"
    fd, temp_path = tempfile.mkstemp(prefix="capswriter_http_", suffix=suffix)
    os.close(fd)

    with open(temp_path, "wb") as f:
        while True:
            chunk = await field.read_chunk()
            if not chunk:
                break
            f.write(chunk)

    return Path(temp_path), raw_name


async def _enqueue_with_backpressure(task: Task, deadline: float) -> None:
    """受队列限流约束地提交任务，必要时等待。"""
    while True:
        if queue_guard.try_enqueue(task):
            Cosmic.queue_in.put(task)
            return

        if time.time() >= deadline:
            raise TimeoutError("queue backpressure timeout")

        await asyncio.sleep(0.05)


def _result_to_response(result: Result, task_id: str, filename: str) -> dict:
    return {
        "ok": True,
        "task_id": task_id,
        "filename": filename,
        "duration": result.duration,
        "time_start": result.time_start,
        "time_submit": result.time_submit,
        "time_complete": result.time_complete,
        "text": result.text,
        "text_accu": result.text_accu,
        "tokens": result.tokens,
        "timestamps": result.timestamps,
        "is_final": result.is_final,
    }


def _decode_wav_to_float32_bytes(file_path: Path) -> bytes:
    """
    在无 ffmpeg 时，回退解析 WAV（PCM）并转为 float32/16k/mono。
    """
    with wave.open(file_path.as_posix(), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        raw_data = wav.readframes(frame_count)

    if sample_width == 1:
        samples = np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32)
        samples = (samples - 128.0) / 128.0
    elif sample_width == 2:
        samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(raw_data, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise RuntimeError(f"unsupported WAV sample width: {sample_width}")

    if channels > 1:
        usable = (len(samples) // channels) * channels
        if usable == 0:
            return b""
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)

    if sample_rate != AudioFormat.SAMPLE_RATE and len(samples) > 1:
        duration = len(samples) / float(sample_rate)
        target_len = max(1, int(duration * AudioFormat.SAMPLE_RATE))
        x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.float32)

    samples = np.clip(samples, -1.0, 1.0).astype(np.float32, copy=False)
    return samples.tobytes()


async def _transcribe_file_via_queue(
    file_path: Path,
    filename: str,
    context: str,
    seg_duration: float,
    seg_overlap: float,
    timeout_secs: int,
) -> dict:
    """将文件转码并按既有切片策略送入识别队列，等待最终结果。"""
    ffmpeg = _find_ffmpeg()

    task_id = str(uuid.uuid1())
    socket_id = f"http-{uuid.uuid4()}"
    time_start = time.time()
    deadline = time_start + float(timeout_secs)

    loop = asyncio.get_running_loop()
    final_future: asyncio.Future[Result] = loop.create_future()
    Cosmic.http_waiters[task_id] = final_future

    if Cosmic.sockets_id is None:
        raise RuntimeError("recognizer is not ready")

    Cosmic.sockets_id.append(socket_id)

    seg_threshold = seg_duration + seg_overlap * 2
    threshold_bytes = AudioFormat.seconds_to_bytes(seg_threshold)
    segment_bytes = AudioFormat.seconds_to_bytes(seg_duration + seg_overlap)
    stride_bytes = AudioFormat.seconds_to_bytes(seg_duration)
    read_size = AudioFormat.seconds_to_bytes(8)

    process = None
    cache = b""
    offset = 0.0

    try:
        if ffmpeg:
            process = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(file_path),
                "-f",
                "f32le",
                "-ac",
                "1",
                "-ar",
                str(AudioFormat.SAMPLE_RATE),
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            while True:
                if process.stdout is None:
                    raise RuntimeError("ffmpeg stdout unavailable")

                chunk = await process.stdout.read(read_size)
                if not chunk:
                    break

                cache += chunk

                while len(cache) >= threshold_bytes:
                    segment_data = cache[:segment_bytes]
                    cache = cache[stride_bytes:]

                    task = Task(
                        source="file",
                        data=segment_data,
                        offset=offset,
                        task_id=task_id,
                        socket_id=socket_id,
                        overlap=seg_overlap,
                        is_final=False,
                        time_start=time_start,
                        time_submit=time.time(),
                        context=context,
                    )
                    await _enqueue_with_backpressure(task, deadline)
                    offset += seg_duration

            stderr_data = b""
            if process.stderr is not None:
                stderr_data = await process.stderr.read()

            return_code = await process.wait()
            if return_code != 0:
                err = stderr_data.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"ffmpeg failed: {err or f'exit={return_code}'}")
        else:
            if file_path.suffix.lower() != ".wav":
                raise RuntimeError("ffmpeg not found, only WAV upload is supported in fallback mode")

            cache += _decode_wav_to_float32_bytes(file_path)
            while len(cache) >= threshold_bytes:
                segment_data = cache[:segment_bytes]
                cache = cache[stride_bytes:]

                task = Task(
                    source="file",
                    data=segment_data,
                    offset=offset,
                    task_id=task_id,
                    socket_id=socket_id,
                    overlap=seg_overlap,
                    is_final=False,
                    time_start=time_start,
                    time_submit=time.time(),
                    context=context,
                )
                await _enqueue_with_backpressure(task, deadline)
                offset += seg_duration

        final_task = Task(
            source="file",
            data=cache,
            offset=offset,
            task_id=task_id,
            socket_id=socket_id,
            overlap=seg_overlap,
            is_final=True,
            time_start=time_start,
            time_submit=time.time(),
            context=context,
        )
        await _enqueue_with_backpressure(final_task, deadline)

        remaining = max(1.0, deadline - time.time())
        result = await asyncio.wait_for(final_future, timeout=remaining)
        return _result_to_response(result, task_id=task_id, filename=filename)

    finally:
        Cosmic.http_waiters.pop(task_id, None)

        if process and process.returncode is None:
            process.terminate()
            await process.wait()

        if Cosmic.sockets_id is not None and socket_id in Cosmic.sockets_id:
            Cosmic.sockets_id.remove(socket_id)

        queue_guard.on_socket_closed(socket_id)


async def transcript_handler(request: web.Request) -> web.Response:
    """HTTP 转录接口。"""
    if not _authorize_http_request(request):
        return web.json_response(
            {"ok": False, "error": "forbidden: invalid secret"},
            status=403,
        )

    temp_file: Optional[Path] = None

    try:
        if not request.content_type.startswith("multipart/"):
            return web.json_response(
                {"ok": False, "error": "content-type must be multipart/form-data"},
                status=400,
            )

        reader = await request.multipart()
        form_data: dict[str, str] = {}
        filename = "audio.bin"

        while True:
            part = await reader.next()
            if part is None:
                break

            if part.name == "file":
                temp_file, filename = await _save_upload_to_temp(part)
            else:
                form_data[part.name] = await part.text()

        if temp_file is None or not temp_file.exists() or temp_file.stat().st_size == 0:
            return web.json_response(
                {"ok": False, "error": "missing file field or empty file"},
                status=400,
            )

        seg_duration = _parse_float(
            form_data.get("seg_duration"),
            default=float(Config.http_seg_duration),
            min_value=1.0,
        )
        seg_overlap = _parse_float(
            form_data.get("seg_overlap"),
            default=float(Config.http_seg_overlap),
            min_value=0.0,
        )
        seg_overlap = min(seg_overlap, max(0.0, seg_duration - 0.1))

        timeout_secs = _parse_int(
            form_data.get("timeout_secs"),
            default=int(Config.http_timeout_secs),
            min_value=5,
        )
        context = str(form_data.get("context", ""))

        logger.info(
            "HTTP 转录请求: file=%s seg=%.2fs overlap=%.2fs timeout=%ss",
            filename,
            seg_duration,
            seg_overlap,
            timeout_secs,
        )

        response_payload = await _transcribe_file_via_queue(
            file_path=temp_file,
            filename=filename,
            context=context,
            seg_duration=seg_duration,
            seg_overlap=seg_overlap,
            timeout_secs=timeout_secs,
        )
        return web.json_response(response_payload)

    except asyncio.TimeoutError:
        return web.json_response(
            {"ok": False, "error": "transcription timeout"},
            status=504,
        )
    except TimeoutError as e:
        return web.json_response(
            {"ok": False, "error": str(e)},
            status=504,
        )
    except RuntimeError as e:
        return web.json_response(
            {"ok": False, "error": str(e)},
            status=500,
        )
    except Exception as e:
        logger.error(f"HTTP 转录异常: {e}", exc_info=True)
        return web.json_response(
            {"ok": False, "error": "internal server error"},
            status=500,
        )
    finally:
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "status": "running"})


async def start_http_server() -> Optional[web.AppRunner]:
    """启动 HTTP 服务（与 WS 并行）。"""
    if not bool(Config.http_enable):
        logger.info("HTTP Transcript API 已禁用")
        return None

    app = web.Application(client_max_size=int(Config.http_max_upload_mb) * 1024 * 1024)
    app.add_routes(
        [
            web.get("/api/healthz", health_handler),
            web.post("/api/transcript", transcript_handler),
        ]
    )

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, Config.http_addr, int(Config.http_port))
    await site.start()

    logger.info(
        "HTTP Transcript API 正在启动，监听地址: %s:%s",
        Config.http_addr,
        Config.http_port,
    )
    return runner


async def stop_http_server(runner: Optional[web.AppRunner]) -> None:
    if runner is None:
        return
    await runner.cleanup()
