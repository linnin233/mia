"""
SenderAgent — 消息发送 Agent

职责:
  1. 接收 Scheduler 的 SEND_TEXT / SEND_VOICE 指令
  2. 生成最终回复文本 (SEND_TEXT)
  3. 可选调用 MiMo TTS 生成语音 (SEND_VOICE)
  4. 输出到 CLI 终端
"""

from pathlib import Path
from typing import Optional

from loguru import logger

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.providers.mimo import MiMoProvider


class SenderAgent(BaseAgent):
    """消息发送 Agent — 生成最终回复并输出到用户界面"""

    def __init__(
        self,
        bus: MessageBus,
        mimo: Optional[MiMoProvider] = None,
        output_dir: str = "workspace",
    ):
        """
        Args:
            bus: 消息总线
            mimo: MiMo Provider (用于 TTS 语音合成, 可选)
            output_dir: 语音文件输出目录
        """
        super().__init__(name="sender", bus=bus)
        self.mimo = mimo
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def handle(self, msg: Message) -> None:
        """处理 Scheduler 的发送指令"""
        if msg.msg_type == MessageType.SEND_TEXT:
            await self._handle_send_text(msg)
        elif msg.msg_type == MessageType.SEND_VOICE:
            await self._handle_send_voice(msg)
        elif msg.msg_type == MessageType.STREAM_START:
            await self._handle_stream_start(msg)
        elif msg.msg_type == MessageType.STREAM_CHUNK:
            await self._handle_stream_chunk(msg)
        elif msg.msg_type == MessageType.STREAM_END:
            await self._handle_stream_end(msg)
        else:
            logger.debug("[Sender] 忽略消息类型: {}", msg.msg_type)

    async def _handle_send_text(self, msg: Message) -> None:
        """处理文本发送指令"""
        message = msg.payload.get("message", "")

        # 结构化展示
        from mia.config import get_config
        verbose = get_config().agent.verbose
        if verbose:
            print()
            print(f"\033[32m[Sender]\033[0m 输出回复:")
            print(f"   \033[90m└─\033[0m {message}")
            print()
            print(f"\033[1m{'-'*50}\033[0m")

        logger.info("[Sender] 文本回复已输出, len={}", len(message))

        # 通知 main 对话已完成
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="main",
            payload={"message": message},
            session_id=msg.session_id,
        ))

        # 同时通知 MemoryAgent 存储本轮对话
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="memory_agent",
            payload={"message": message},
            session_id=msg.session_id,
        ))

    # ─── 流式输出处理 ──────────────────────────────────

    async def _handle_stream_start(self, msg: Message) -> None:
        """流式输出开始 — 打印 header，准备接收增量文本"""
        from mia.config import get_config
        verbose = get_config().agent.verbose
        if verbose:
            print()
            print(f"\033[32m[Sender]\033[0m 输出回复:")
            print(f"   \033[90m└─\033[0m ", end="", flush=True)

    async def _handle_stream_chunk(self, msg: Message) -> None:
        """流式输出文本块 — 立即打印增量文本，不换行"""
        delta = msg.payload.get("delta", "")
        if delta:
            print(delta, end="", flush=True)

    async def _handle_stream_end(self, msg: Message) -> None:
        """流式输出结束 — 打印 footer，发送 CONVERSATION_DONE"""
        message = msg.payload.get("message", "")
        print()  # 流式文本结束，换行
        from mia.config import get_config
        if get_config().agent.verbose:
            print()
            print(f"\033[1m{'-'*50}\033[0m")

        logger.info("[Sender] 流式回复完成, len={}", len(message))

        # 通知 main 对话已完成
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="main",
            payload={"message": message},
            session_id=msg.session_id,
        ))

        # 同时通知 MemoryAgent 存储本轮对话
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="memory_agent",
            payload={"message": message},
            session_id=msg.session_id,
        ))

    async def _handle_send_voice(self, msg: Message) -> None:
        """处理语音发送指令"""
        message = msg.payload.get("message", "")
        voice = msg.payload.get("voice", "冰糖")
        audio_format = msg.payload.get("format", "wav")

        if not self.mimo:
            logger.warning("[Sender] MiMo Provider 未配置，降级为文本输出")
            await self._handle_send_text(msg)
            return

        # 结构化展示
        print()
        print(f"\033[32m[Sender]\033[0m 输出语音回复 (音色: {voice}):")
        print(f"   \033[90m├─\033[0m 文本: {message}")

        try:
            audio_bytes = await self.mimo.synthesize(
                text=message,
                voice=voice,
                audio_format=audio_format,
            )

            # 保存语音文件
            filename = f"reply_{msg.msg_id}.{audio_format}"
            filepath = self.output_dir / filename
            filepath.write_bytes(audio_bytes)

            print(f"   \033[90m└─\033[0m 语音文件: {filepath}")

            # 自动播放语音 (后台线程，不阻塞)
            import asyncio as _asyncio
            _asyncio.get_event_loop().run_in_executor(
                None, _play_audio_file, str(filepath),
            )
            print()
            print(f"\033[1m{'-'*50}\033[0m")

            logger.info("[Sender] 语音回复已保存: {}", filepath)

        except Exception as e:
            logger.error("[Sender] TTS 合成失败: {}", e)
            print(f"   \033[90m└─\033[0m \033[31m语音合成失败: {e}\033[0m")
            print(f"   \033[90m└─\033[0m 降级为文本: {message}")
            print()
            print(f"\033[1m{'-'*50}\033[0m")

        # 通知 main 对话已完成
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="main",
            payload={"message": message},
            session_id=msg.session_id,
        ))

        # 同时通知 MemoryAgent 存储本轮对话
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="memory_agent",
            payload={"message": message},
            session_id=msg.session_id,
        ))


# ─── 播放辅助函数 (模块级，供 executor 线程调用) ───

def _play_audio_file(filepath: str) -> None:
    """在后台线程中播放音频文件 — 静默失败不影响主流程"""
    try:
        from mia.audio.playback import play_audio
        play_audio(filepath, blocking=False)
    except Exception:
        pass  # 播放失败不打扰用户

