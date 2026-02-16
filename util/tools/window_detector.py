"""
前台窗口检测器

检测当前前台活动的应用程序信息，用于兼容性配置
"""
import platform
import ctypes
from ctypes import wintypes


def get_active_window_info() -> dict:
    """
    获取当前前台窗口信息

    Returns:
        包含窗口信息的字典:
        - title: 窗口标题
        - class_name: 窗口类名
        - process_name: 进程名
        - app_name: 应用名称（推测）
    """
    system = platform.system()

    if system == 'Windows':
        return _get_windows_window_info()
    elif system == 'Darwin':  # macOS
        return _get_macos_window_info()
    elif system == 'Linux':
        return _get_linux_window_info()
    else:
        return {}


def _get_windows_window_info() -> dict:
    """Windows 平台窗口检测"""
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()

        # 获取窗口标题
        title = win32gui.GetWindowText(hwnd)

        # 获取窗口类名
        class_name = win32gui.GetClassName(hwnd)

        # 获取进程 ID 和进程名
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            import psutil
            process = psutil.Process(pid)
            process_name = process.name()
        except:
            process_name = ""

        # 推测应用名称
        app_name = _guess_app_name(title, class_name, process_name)

        return {
            'title': title,
            'class_name': class_name,
            'process_name': process_name,
            'app_name': app_name
        }
    except ImportError:
        # 缺少 pywin32 时，回退到 ctypes 原生 WinAPI
        return _get_windows_window_info_ctypes()
    except Exception:
        # pywin32 路径失败时，同样回退到 ctypes，提升鲁棒性
        return _get_windows_window_info_ctypes()


def _get_windows_window_info_ctypes() -> dict:
    """Windows: 不依赖 pywin32 的前台窗口检测兜底实现。"""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}

        # 窗口标题
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value

        # 窗口类名
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        class_name = class_buf.value

        # 进程名
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = ""

        if pid.value:
            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            access = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
            process_handle = kernel32.OpenProcess(access, False, pid.value)
            if process_handle:
                try:
                    exe_buf = ctypes.create_unicode_buffer(260)
                    if psapi.GetModuleBaseNameW(process_handle, None, exe_buf, 260):
                        process_name = exe_buf.value
                finally:
                    kernel32.CloseHandle(process_handle)

        app_name = _guess_app_name(title, class_name, process_name)

        return {
            'title': title,
            'class_name': class_name,
            'process_name': process_name,
            'app_name': app_name
        }
    except Exception:
        return {}


def _get_macos_window_info() -> dict:
    """macOS 平台窗口检测"""
    try:
        import subprocess
        from plistlib import loads

        # 使用 AppleScript 获取前台窗口信息
        script = '''
        tell application "System Events"
            set frontApp to name of first application process whose frontmost is true
            if frontApp contains "Safari" then
                tell application frontApp
                    if (count of windows) > 0 then
                        set windowTitle to name of front window
                    else
                        set windowTitle to ""
                    end if
                end tell
            else if frontApp contains "Terminal" then
                tell application frontApp
                    if (count of windows) > 0 then
                        set windowTitle to name of front window
                    else
                        set windowTitle to ""
                    end if
                end tell
            else
                set windowTitle to ""
            end if
        end tell
        return frontApp & "||" & windowTitle
        '''

        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            parts = result.stdout.strip().split('||')
            app_name = parts[0] if len(parts) > 0 else ""
            title = parts[1] if len(parts) > 1 else ""

            return {
                'title': title,
                'class_name': '',
                'process_name': app_name,
                'app_name': app_name
            }
    except Exception:
        pass

    return {}


def _get_linux_window_info() -> dict:
    """Linux 平台窗口检测"""
    try:
        import subprocess

        # 使用 wmctrl 获取活动窗口
        result = subprocess.run(
            ['wmctrl', '-G', '-a', ':ACTIVE:'],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            parts = result.stdout.strip().split()
            if len(parts) >= 5:
                title = ' '.join(parts[5:])
                return {
                    'title': title,
                    'class_name': '',
                    'process_name': '',
                    'app_name': title.split()[0] if title else ''
                }
    except Exception:
        pass

    return {}


def _guess_app_name(title: str, class_name: str, process_name: str) -> str:
    """
    根据窗口信息推测应用名称

    Args:
        title: 窗口标题
        class_name: 窗口类名
        process_name: 进程名

    Returns:
        推测的应用名称
    """
    # 优先使用进程名
    if process_name:
        # 去除 .exe 后缀
        name = process_name.replace('.exe', '').lower()
        return name

    # 其次使用类名
    if class_name:
        # Windows 常见类名映射
        class_mappings = {
            'chrome': 'Chrome',
            'msedge': 'Edge',
            'firefox': 'Firefox',
            'notepad': 'Notepad',
            'notepad++': 'Notepad++',
            'vscode': 'VSCode',
            'winword': 'Word',
            'xlmain': 'Excel',
            'pptmain': 'PowerPoint',
            'wndclass_desktop_glass': 'Desktop',
        }

        class_lower = class_name.lower()
        for key, value in class_mappings.items():
            if key in class_lower:
                return value

    # 最后使用标题的第一个词
    if title:
        first_word = title.split()[0]
        return first_word

    return ''


def is_likely_editor(window_info: dict) -> bool:
    """
    判断当前窗口是否可能是编辑器

    Args:
        window_info: 窗口信息字典

    Returns:
        True 表示可能是编辑器
    """
    if not window_info:
        return True  # 默认安全起见

    title = window_info.get('title', '').lower()
    class_name = window_info.get('class_name', '').lower()
    process_name = window_info.get('process_name', '').lower()

    # 编辑器关键词
    editor_keywords = [
        'visual studio', 'vscode', 'vim', 'nano', 'emacs',
        'notepad', 'sublime', 'atom', 'intellij', 'pycharm',
        'webstorm', 'idea', 'editor'
    ]

    # 检查标题
    for keyword in editor_keywords:
        if keyword in title or keyword in class_name or keyword in process_name:
            return True

    return False


def is_likely_browser(window_info: dict) -> bool:
    """
    判断当前窗口是否可能是浏览器

    Args:
        window_info: 窗口信息字典

    Returns:
        True 表示可能是浏览器
    """
    if not window_info:
        return False

    class_name = window_info.get('class_name', '').lower()
    process_name = window_info.get('process_name', '').lower()

    browser_keywords = ['chrome', 'firefox', 'edge', 'safari', 'opera', 'brave']

    for keyword in browser_keywords:
        if keyword in class_name or keyword in process_name:
            return True

    return False
