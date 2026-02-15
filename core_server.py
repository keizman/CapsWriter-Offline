import os
import sys
from pathlib import Path
from platform import system
import asyncio
import websockets
from config_server import ServerConfig as Config, __version__
from util.server.server_ws_recv import ws_recv
from util.server.server_ws_send import ws_send
from util.tools.empty_working_set import empty_current_working_set
from util.logger import setup_logger
from util.common.lifecycle import lifecycle
from util.server.cleanup import setup_tray, print_banner, cleanup_server_resources, console
from util.server.service import start_recognizer_process
import logging


def _debug_base_resolution(message: str) -> None:
    if os.getenv("CAPSWRITER_DEBUG_BASE_DIR", "0") == "1":
        print(f"[base_dir][server] {message}", flush=True)


def _resolve_base_dir() -> str:
    """解析运行基目录，兼容 macOS .app 与源码运行。"""
    # 普通源码运行
    if not getattr(sys, "frozen", False):
        _debug_base_resolution(f"non-frozen base={os.path.dirname(__file__)}")
        return os.path.dirname(__file__)

    # PyInstaller 可执行
    raw_exe_path = Path(sys.executable)
    exe_path = raw_exe_path.resolve()
    _debug_base_resolution(f"sys.executable={raw_exe_path.as_posix()} resolved={exe_path.as_posix()}")
    # macOS .app：从任意 .app 内部路径反推到 .app 外层目录
    # 兼容 sys.executable 位于 Contents/MacOS 或 Contents/Frameworks 的情况
    for parent in [raw_exe_path, *raw_exe_path.parents, exe_path, *exe_path.parents]:
        if parent.suffix == ".app":
            app_parent = parent.parent
            _debug_base_resolution(
                f"found_bundle={parent.as_posix()} app_parent={app_parent.as_posix()} "
                f"models_exists={(app_parent / 'models').exists()}"
            )
            if (app_parent / "models").exists():
                return app_parent.as_posix()
            # 如果找到了 .app 但同级没有 models，继续用其他候选路径尝试

    cwd = Path.cwd()
    if (cwd / "models").exists():
        _debug_base_resolution(f"use_cwd={cwd.as_posix()}")
        return cwd.as_posix()

    # 兜底：可执行文件所在目录
    _debug_base_resolution(f"fallback={exe_path.parent.as_posix()}")
    return exe_path.parent.as_posix()


BASE_DIR = _resolve_base_dir()
os.chdir(BASE_DIR)

# 初始化日志系统
logger = setup_logger('server', level=Config.log_level)

# 手动接管 websockets 日志
ws_logger = logging.getLogger('websockets')
ws_logger.setLevel(logging.WARNING) # 仅记录 WARNING 及以上
ws_logger.propagate = False
for handler in logger.handlers:
    ws_logger.addHandler(handler)




async def run_websocket_server():
    """运行 WebSocket 服务器"""
    loop = asyncio.get_running_loop()
    
    # 1. 更新生命周期管理器的事件循环
    lifecycle._loop = loop
    # 如果在启动前就已请求退出（例如启动时按了 Ctrl+C），则不再启动服务
    if lifecycle.is_shutting_down:
        logger.info("检测到退出标记，停止启动")
        return

    from util.concurrency.daemon_executor import SimpleDaemonExecutor
    loop.set_default_executor(SimpleDaemonExecutor())

    # 清空物理内存工作集
    if system() == 'Windows':
        empty_current_working_set()

    # 2. 启动服务器
    logger.info(f"WebSocket 服务器正在启动，监听地址: {Config.addr}:{Config.port}")
    async with websockets.serve(ws_recv,
                                Config.addr,
                                Config.port,
                                subprotocols=["binary"],
                                max_size=None):
        
        send_task = asyncio.create_task(ws_send())
        
        # 3. 等待退出信号
        # 如果已经处于 shutting down 状态，ensure event is set
        if lifecycle.is_shutting_down:
            lifecycle._shutdown_event.set()

        wait_shutdown_task = asyncio.create_task(lifecycle.wait_for_shutdown())

        done, pending = await asyncio.wait(
            [send_task, wait_shutdown_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        if wait_shutdown_task in done:
            logger.info("收到退出信号，正在关闭服务...")
        
        # 4. 取消所有相关任务
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        if send_task in done and not send_task.cancelled():
            try:
                await send_task
            except Exception as e:
                logger.error(f"发送任务异常退出: {e}")


def init():
    """初始化并启动服务"""
    # 尽早初始化生命周期以激活信号处理（防手抖）
    lifecycle.initialize(logger=logger, exit_on_signal=False)
    lifecycle.register_on_shutdown(cleanup_server_resources)

    logger.info("=" * 50)
    logger.info("CapsWriter Offline Server 正在启动")
    logger.info(f"版本: {__version__}")
    logger.info(f"日志级别: {Config.log_level}")

    setup_tray()
    print_banner()

    try:
        start_recognizer_process()
        asyncio.run(run_websocket_server())
        # 正常退出后的显式清理
        lifecycle.cleanup()

    except KeyboardInterrupt:
        logger.warning("收到停止信号，正在停止服务...")
        console.print('\n[yellow]正在停止服务...')
        lifecycle.cleanup()
    except OSError as e:
        logger.error(f"OSError 错误: {e}")
        console.print(f'出错了：{e}', style='bright_red'); console.input('...')
        lifecycle.cleanup()
    except Exception as e:
        logger.error(f"未处理的异常: {e}", exc_info=True)
        print(e)
        lifecycle.cleanup()
        raise
     
        
if __name__ == "__main__":
    init()
