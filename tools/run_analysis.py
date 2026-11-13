#!/usr/bin/env python3
"""隔离模式运行器：显式登记包路径后再导入项目代码。

数据文件是不可信输入，所有读取数据的 Python 进程都以 -I 启动，防止数据
目录中的同名模块（如 json.py）被导入。本运行器位于可信代码区，负责把
仓库根目录显式加入 sys.path，再转交 CLI。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from coverage_data.cli import main  # noqa: E402

if __name__ == '__main__':
    main()
