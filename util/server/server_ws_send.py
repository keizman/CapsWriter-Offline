import json
import base64
import asyncio
from multiprocessing import Queue

from util.server.server_cosmic import console, Cosmic
from util.server.server_classes import Result, QueueAck
from util.server.queue_guard import queue_guard
from util.tools.asyncio_to_thread import to_thread
from . import logger
from rich import inspect



async def ws_send():

    queue_out = Cosmic.queue_out
    sockets = Cosmic.sockets
    http_waiters = Cosmic.http_waiters

    logger.info("WebSocket 发送任务已启动")

    while True:
        try:
            # 获取识别结果（从多进程队列）
            result: Result = await to_thread(queue_out.get)

            # 得到退出的通知
            if result is None:
                logger.info("收到退出通知，停止发送任务")
                return

            # 队列回执（如：识别进程丢弃过期片段）
            if isinstance(result, QueueAck):
                queue_guard.on_task_done(result.socket_id)
                if result.dropped:
                    logger.info(
                        "片段已丢弃: task=%s socket=%s reason=%s",
                        result.task_id,
                        result.socket_id,
                        result.reason,
                    )
                continue

            # HTTP 请求等待通道：只消费最终结果，不经过 WS 下发
            http_waiter = http_waiters.get(result.task_id)
            if http_waiter is not None:
                queue_guard.on_task_done(result.socket_id)
                if result.is_final and not http_waiter.done():
                    http_waiters.pop(result.task_id, None)
                    http_waiter.set_result(result)
                continue

            # 构建消息
            message = {
                'task_id': result.task_id,
                'duration': result.duration,
                'time_start': result.time_start,
                'time_submit': result.time_submit,
                'time_complete': result.time_complete,
                'text': result.text,               # 主要输出（简单拼接）
                'text_accu': result.text_accu,     # 精确输出（时间戳拼接）
                'tokens': result.tokens,
                'timestamps': result.timestamps,
                'is_final': result.is_final,
            }

            # 获得 socket
            websocket = next(
                (ws for ws in sockets.values() if str(ws.id) == result.socket_id),
                None,
            )

            if not websocket:
                queue_guard.on_task_done(result.socket_id)
                logger.warning(f"客户端 {result.socket_id} 不存在，跳过发送结果，任务ID: {result.task_id}")
                continue

            # 发送消息
            await websocket.send(json.dumps(message))
            queue_guard.on_task_done(result.socket_id)
            logger.debug(f"发送识别结果，任务ID: {result.task_id}, 文本长度: {len(result.text)}")

            if result.source == 'mic':
                console.print(f'识别结果：\n    [green]{result.text}')
                logger.info(f"麦克风识别结果: {result.text}")
            elif result.source == 'file':
                console.print(f'    转录进度：{result.duration:.2f}s', end='\r')
                logger.debug(f"文件转录进度: {result.duration:.2f}s")
                if result.is_final:
                    console.print('\n    [green]转录完成')
                    logger.info(f"文件转录完成，任务ID: {result.task_id}, 总时长: {result.duration:.2f}s")

        except Exception as e:
            logger.error(f"发送结果时发生错误: {e}", exc_info=True)
            print(e)
