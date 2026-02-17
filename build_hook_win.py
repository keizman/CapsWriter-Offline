import os
import shutil
import sys
from os.path import dirname, exists, join
from pathlib import Path


def _copy_missing_tree(src: Path, dst: Path) -> None:
    """只复制缺失文件，避免覆盖用户自定义内容。"""
    for root, _, files in os.walk(src):
        root_path = Path(root)
        rel = root_path.relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            src_file = root_path / name
            dst_file = target_dir / name
            if not dst_file.exists():
                shutil.copy2(src_file, dst_file)


def _seed_onefile_runtime_assets() -> None:
    """
    Windows onefile 模式下，将打包内置资源按需释放到 exe 同目录。
    """
    if not getattr(sys, "frozen", False):
        return

    bundle_dir = Path(getattr(sys, "_MEIPASS", ""))
    if not bundle_dir.exists():
        return

    exe_dir = Path(sys.executable).resolve().parent

    dir_mappings = [
        ("assets", "assets"),
        ("LLM", "LLM"),
        ("resources/assets/sounds", "resources/assets/sounds"),
    ]
    file_mappings = [
        ("hot.txt", "hot.txt"),
        ("hot-server.txt", "hot-server.txt"),
        ("hot-rule.txt", "hot-rule.txt"),
        ("hot-rectify.txt", "hot-rectify.txt"),
        ("readme.md", "readme.md"),
    ]

    for src_rel, dst_rel in dir_mappings:
        src = bundle_dir / src_rel
        dst = exe_dir / dst_rel
        try:
            if src.exists():
                _copy_missing_tree(src, dst)
        except Exception:
            pass

    for src_rel, dst_rel in file_mappings:
        src = bundle_dir / src_rel
        dst = exe_dir / dst_rel
        try:
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except Exception:
            pass


_seed_onefile_runtime_assets()

# 将「执行文件所在目录」添加到「模块查找路径」
executable_dir = dirname(sys.executable)
sys.path.insert(0, executable_dir)

# one-folder 打包时第三方依赖在 internal/；onefile 下无此目录，不存在时自动跳过
internal_dir = join(executable_dir, "internal")
if exists(internal_dir):
    sys.path.insert(0, internal_dir)
