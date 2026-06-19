"""
Audio Recorder — 麦克风录音工具

使用 sounddevice 进行跨平台音频采集，soundfile 保存为 WAV。
提供简洁的 keyboard-driven 录音接口，适配 CLI 交互场景。

用法:
    from mia.audio.recorder import record_until_keypress

    # 阻塞式录音: 按 Enter 开始 → 按 Enter 结束
    wav_path = record_until_keypress()
    if wav_path:
        print(f"录音已保存: {wav_path}")
"""

import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional


def record_until_keypress(
    samplerate: int = 16000,
    channels: int = 1,
    device: Optional[int] = None,
) -> Optional[str]:
    """
    键盘控制的麦克风录音 — 阻塞式

    流程:
      1. 调用后立即开始录音
      2. 等待用户按 Enter 停止
      3. 保存为临时 WAV 文件并返回路径

    此函数设计为通过 loop.run_in_executor() 在后台线程中调用，
    避免阻塞 asyncio 事件循环。input() 在 executor 线程中是安全的。

    Args:
        samplerate: 采样率 (Hz)，默认 16000 (语音识别推荐)
        channels: 声道数，默认 1 (单声道)
        device: 音频输入设备 ID (None = 系统默认)

    Returns:
        临时 WAV 文件路径，录音为空或失败返回 None

    Raises:
        ImportError: sounddevice 或 soundfile 未安装
    """
    try:
        import sounddevice as sd
        import soundfile as sf
        import numpy as np
    except ImportError as e:
        raise ImportError(
            f"录音功能需要 sounddevice 和 soundfile。请运行: pip install sounddevice soundfile\n"
            f"原始错误: {e}"
        ) from e

    # 检查可用输入设备
    try:
        devices = sd.query_devices()
        input_devices = [d for d in devices if d.get("max_input_channels", 0) > 0]
        if not input_devices:
            print("  \033[33m[警告]\033[0m 未检测到音频输入设备，录音可能失败", file=sys.stderr)
    except Exception:
        pass  # 设备查询失败不阻止尝试录音

    # ─── 录音缓冲区 ────────────────────────────────
    audio_chunks: list = []
    stop_event = threading.Event()

    def audio_callback(indata, frames, time_info, status):
        """sounddevice 音频回调 — 将音频块追加到缓冲区"""
        if status:
            print(f"  \033[33m[警告]\033[0m 录音状态: {status}", file=sys.stderr)
        audio_chunks.append(indata.copy())

    # ─── 启动录音流 ────────────────────────────────
    try:
        stream = sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
            callback=audio_callback,
            device=device,
        )
        stream.start()
    except sd.PortAudioError as e:
        print(f"  \033[31m[错误]\033[0m 无法打开音频输入设备: {e}", file=sys.stderr)
        print(f"  \033[90m提示: 检查麦克风是否已连接且在系统设置中已启用\033[0m", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  \033[31m[错误]\033[0m 音频设备初始化失败: {e}", file=sys.stderr)
        return None

    # ─── 等待用户停止 ──────────────────────────────
    # input() 在此线程中阻塞，不影响 asyncio 事件循环
    try:
        input("")
    except (EOFError, OSError):
        # 非交互环境 (如 pytest)，立即停止
        pass

    # ─── 停止录音 ──────────────────────────────────
    stream.stop()
    stream.close()

    # ─── 保存为 WAV ────────────────────────────────
    if not audio_chunks:
        print("  \033[33m[警告]\033[0m 未录制到任何音频数据", file=sys.stderr)
        return None

    audio_data = np.concatenate(audio_chunks, axis=0)

    # 检查是否有有效音频 (非纯静音)
    peak = np.max(np.abs(audio_data)) if len(audio_data) > 0 else 0.0
    if peak < 0.001:
        print("  \033[33m[警告]\033[0m 录制的音频为静音 (peak={:.4f})".format(peak), file=sys.stderr)
        # 即使是静音也保存，让 ASR 去判断

    duration = len(audio_data) / samplerate
    print(f"  \033[90m录音时长: {duration:.1f}s, 峰值: {peak:.3f}\033[0m")

    # 写入临时文件
    tmpdir = Path(tempfile.gettempdir()) / "mia_recordings"
    tmpdir.mkdir(parents=True, exist_ok=True)
    tmpfile = tempfile.mktemp(suffix=".wav", prefix="mia_rec_", dir=str(tmpdir))

    try:
        sf.write(tmpfile, audio_data, samplerate)
    except Exception as e:
        print(f"  \033[31m[错误]\033[0m 保存录音文件失败: {e}", file=sys.stderr)
        return None

    return tmpfile
