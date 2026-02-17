# -*- mode: python ; coding: utf-8 -*-
"""
Windows 专用客户端打包配置（Onefile）
"""

from PyInstaller.utils.hooks import collect_all, collect_data_files
from os.path import basename

# ==================== 打包配置选项 ====================

# 是否收集 CUDA provider（客户端通常不需要）
# - True: 包含 onnxruntime_providers_cuda.dll，支持 GPU 加速（需要在用户机器安装 CUDA 和 CUDNN）
# - False: 不包含 CUDA provider，只使用 CPU 模式（打包体积更小，兼容性更好）
INCLUDE_CUDA_PROVIDER = False

# ====================================================


# 初始化空列表
binaries = []
hiddenimports = []
datas = []

# 收集 sherpa_onnx 相关文件（客户端不需要，但保持一致性）
try:
    sherpa_datas = collect_data_files('sherpa_onnx', include_py_files=False)

    # 根据 INCLUDE_CUDA_PROVIDER 决定是否收集 CUDA provider
    if not INCLUDE_CUDA_PROVIDER:
        filtered_datas = []
        for src, dest in sherpa_datas:
            if 'providers_cuda' not in basename(src).lower():
                filtered_datas.append((src, dest))
            else:
                print(f"[INFO] 排除 CUDA provider: {basename(src)}")
        sherpa_datas = filtered_datas

    datas += sherpa_datas
except Exception:
    pass

# 收集 Pillow 相关文件（用于托盘图标）
try:
    pillow_datas = collect_data_files('PIL', include_py_files=False)
    datas += pillow_datas
    pillow_binaries = collect_all('PIL')
    binaries += pillow_binaries[1]
except Exception:
    pass

# 运行时需要的默认资源（onefile 内置 + 启动时按需释放）
datas += [
    ('assets', 'assets'),
    ('LLM', 'LLM'),
    ('resources/assets/sounds', 'resources/assets/sounds'),
    ('hot.txt', '.'),
    ('hot-server.txt', '.'),
    ('hot-rule.txt', '.'),
    ('hot-rectify.txt', '.'),
    ('readme.md', '.'),
]

# 隐藏导入 - 确保所有需要的模块都被包含
hiddenimports += [
    'websockets',
    'websockets.client',
    'websockets.server',
    'rich',
    'rich.console',
    'rich.markdown',
    'keyboard',
    'pyclip',
    'numpy',
    'sounddevice',
    'pypinyin',
    'watchdog',
    'typer',
    'srt',
    'PIL',
    'PIL.Image',
    'pystray',
]

a_2 = Analysis(
    ['start_client.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['build_hook_win.py'],
    excludes=[
        # GUI / scientific stack not used by client runtime
        'IPython', 'PySide6', 'PySide2', 'PyQt5', 'matplotlib', 'wx',
        'pytest', 'jupyter', 'notebook',
        # Heavy ML stacks that slow packaging dramatically
        'torch', 'torchvision', 'torchaudio',
        'tensorflow', 'tensorflow_intel',
        'pandas', 'scipy', 'sklearn',
        'numba', 'llvmlite',
        'faiss', 'faiss_cpu',
    ],
    noarchive=False,
)

# 过滤从系统 CUDA 目录收集的 DLL
filtered_binaries = []
for name, src, type in a_2.binaries:
    src_lower = src.lower() if isinstance(src, str) else ''
    is_system_cuda_dll = (
        '\\nvidia gpu computing toolkit\\cuda\\' in src_lower or
        '\\nvidia\\cudnn\\' in src_lower or
        ('\\cuda\\v' in src_lower and '\\bin\\' in src_lower)
    )
    is_unwanted_onnx_dll = (
        'onnxruntime_providers_cuda.dll' in name.lower() or
        'directml.dll' in name.lower()
    )

    if not is_system_cuda_dll and not is_unwanted_onnx_dll:
        filtered_binaries.append((name, src, type))
    else:
        reason = "环境 CUDA DLL" if is_system_cuda_dll else "冗余 ONNX DLL"
        print(f"[INFO] 排除 {reason}: {name} (从 {src} 收集)")
a_2.binaries = filtered_binaries


pyz_2 = PYZ(a_2.pure)


exe_2 = EXE(
    pyz_2,
    a_2.scripts,
    a_2.binaries,
    a_2.datas,
    [],
    name='start_client',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\\\icon.ico'],
)
