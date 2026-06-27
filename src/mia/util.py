"""
MIA 共享工具函数
"""
from datetime import datetime


def ts() -> str:
    """返回当前时间戳字符串 HH:MM:SS.mmm"""
    return datetime.now().strftime("%H:%M:%S.") + f"{datetime.now().microsecond // 1000:03d}"
