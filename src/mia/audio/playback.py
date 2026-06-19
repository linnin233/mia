"""
Audio Playback — 音频播放工具

使用 sounddevice + soundfile 进行跨平台音频播放。
"""

import sys
from typing import Optional


def play_audio(filepath: str, blocking: bool = False) -> bool:
    """
    播放音频文件 (WAV/FLAC/OGG 等 soundfile 支持的格式)

    Args:
        filepath: 音频文件路径
        blocking: True = 阻塞等待播放完成, False = 后台播放

    Returns:
        True 如果开始播放，False 如果失败
    """
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        print(
            "  \033[33m[警告]\033[0m 缺少音频播放依赖 (sounddevice/soundfile)，"
            "请运行: pip install sounddevice soundfile",
            file=sys.stderr,
        )
        return False

    try:
        data, samplerate = sf.read(filepath)
        sd.play(data, samplerate)

        if blocking:
            sd.wait()

        return True
    except Exception as e:
        print(f"  \033[33m[警告]\033[0m 音频播放失败: {e}", file=sys.stderr)
        return False
