# coding: utf-8


'''
这个文件仅仅是为了 PyInstaller 打包用
'''

from multiprocessing import freeze_support
import sys


if __name__ == '__main__':
    # 在 frozen 环境中必须先执行 freeze_support，再导入业务模块，
    # 否则 multiprocessing 子进程可能重复执行主入口。
    freeze_support()
    import core_server
    core_server.init()
    sys.exit(0)
