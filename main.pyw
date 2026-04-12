# -*- coding: utf-8 -*-
"""
應用入口：可直接 python main.py 執行。
"""
import os
import sys

# 確保可以以模組方式匯入同層檔案
CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if CUR_DIR not in sys.path:
    sys.path.insert(0, CUR_DIR)

from lib.gui_main import LoginApp  # type: ignore
from lib.log import log_msg


def main():
    """啟動主程式。"""
    log_msg("App starting")
    LoginApp().run()
    log_msg("App finished")


if __name__ == "__main__":
    main()
