# coding: utf-8
"""
服务端实时队列限流器（主进程内存态）。

职责：
1. 入队前检查全局/单客户端待处理上限
2. 维护待处理计数（配合 ws_send 的完成回执）
3. 客户端断开时清理计数，避免积压幽灵占位
"""

from __future__ import annotations

from collections import defaultdict

from config_server import ServerConfig as Config
from util.server.server_classes import Task
from . import logger


class QueueGuard:
    def __init__(self) -> None:
        self.pending_total: int = 0
        self.pending_by_socket = defaultdict(int)

    def try_enqueue(self, task: Task) -> bool:
        """
        入队前限流。

        策略：
        - final 片段优先放行（避免会话尾包丢失）
        - 非 final 片段应用全局和单客户端上限
        """
        socket_id = task.socket_id
        per_client = int(self.pending_by_socket.get(socket_id, 0))

        if not task.is_final:
            if per_client >= int(Config.queue_max_per_client):
                logger.warning(
                    "丢弃音频片段：超过单客户端排队上限 "
                    "(socket=%s, pending=%s, limit=%s)",
                    socket_id,
                    per_client,
                    Config.queue_max_per_client,
                )
                return False

            if self.pending_total >= int(Config.queue_max_total):
                logger.warning(
                    "丢弃音频片段：超过全局排队上限 "
                    "(pending=%s, limit=%s)",
                    self.pending_total,
                    Config.queue_max_total,
                )
                return False

        self.pending_total += 1
        self.pending_by_socket[socket_id] = per_client + 1
        return True

    def on_task_done(self, socket_id: str) -> None:
        """任务完成/丢弃回执后，减少计数。"""
        current = int(self.pending_by_socket.get(socket_id, 0))
        if current > 0:
            self.pending_by_socket[socket_id] = current - 1
        else:
            self.pending_by_socket[socket_id] = 0

        if self.pending_by_socket[socket_id] <= 0:
            self.pending_by_socket.pop(socket_id, None)

        if self.pending_total > 0:
            self.pending_total -= 1
        else:
            self.pending_total = 0

    def on_socket_closed(self, socket_id: str) -> None:
        """客户端断开时，直接回收该客户端的排队占位。"""
        removed = int(self.pending_by_socket.pop(socket_id, 0))
        if removed <= 0:
            return
        self.pending_total = max(0, self.pending_total - removed)
        logger.info(
            "连接断开，回收排队占位 (socket=%s, removed=%s, pending_total=%s)",
            socket_id,
            removed,
            self.pending_total,
        )


queue_guard = QueueGuard()
