"""
EDID Editor Tool V2 - 入口文件

直接运行: python main.py
或:        python -m edid_gui
"""

import sys
import os

# 确保可以导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from edid_gui import main

if __name__ == '__main__':
    main()
