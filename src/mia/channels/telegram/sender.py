"""
TelegramSenderAgent — MIA 到 Telegram 的消息发送 Agent

职责:
  1. 监听 SEND_TEXT / STREAM_* / SEND_VOICE 消息
  2. 通过 Telegram Bot API 将文本或语音发送给用户
  3. 发送完成后发布 CONVERSATION_DONE

架构位置:
  MIA Scheduler → MessageBus → TelegramSenderAgent → (Bot API) → Telegram 用户

相比 WeChatSenderAgent 的简化:
  - 无 AES 加密（Telegram 标准 HTTPS）
  - 无 CDN 上传（Telegram 自带文件存储）
  - 无 context_token（不需要，chat_id 即可路由）
  - 语音发送更简单（multipart upload via sendAudio）

session_id 格式: telegram:<chat_id>
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────

_DEFAULT_TOKEN_FILE = Path.home() / ".mia" / "telegram_bot_token"


class TelegramSenderAgent(BaseAgent):
    """Telegram 消息发送 Agent — 将 MIA 的回复发送到 Telegram 用户

    接收 SEND_TEXT / STREAM_* / SEND_VOICE 消息，
    通过 Telegram Bot API 发送给指定 chat_id 的用户。

    Args:
        bus: MIA 消息总线
        bot_token: Telegram Bot token
        bot_token_file: Token 持久化文件路径
        enabled: 是否启用此渠道
        mimo: MiMoProvider 实例（可选，用于 TTS 语音合成）
        workspace_dir: TTS 语音文件输出目录
    """

    def __init__(
        self,
        bus: MessageBus,
        bot_token: str = "",
        bot_token_file: str = "",
        enabled: bool = True,
        mimo=None,  # Optional MiMoProvider for TTS
        workspace_dir: str = "",
    ):
        super().__init__(name="telegram_sender", bus=bus)
        self.enabled = enabled
        self.bot_token = bot_token
        self._mimo = mimo

        # Token 文件
        self._bot_token_file = (
            Path(bot_token_file).expanduser()
            if bot_token_file
            else _DEFAULT_TOKEN_FILE
        )

        # 工作目录（TTS 输出）
        self._workspace_dir = (
            Path(workspace_dir) if workspace_dir
            else Path(__file__).parent.parent.parent.parent.parent / "workspace"
        )

        # TelegramClient（延迟创建）
        self._client = None  # type: Optional[TelegramClient]

        # 流式输出缓冲
        self._stream_buffer: str = ""
        self._stream_chat_id: Optional[int] = None
        self._stream_msg_id: str = ""

    # ─── 生命周期 ──────────────────────────────────────

    async def on_start(self) -> None:
        """Agent 启动 — 加载 token，创建客户端"""
        if not self.enabled:
            logger.info("[TelegramSender] 渠道已禁用，跳过初始化")
            return

        if not self.bot_token:
            self.bot_token = self._load_token_from_file()

        if not self.bot_token:
            logger.warning("[TelegramSender] 无 bot_token，TTS 不可用")
            # Sender 仍可正常工作（仅文本模式）
            self.enabled = False
            return

        from mia.channels.telegram.client import TelegramClient

        self._client = TelegramClient(bot_token=self.bot_token)
        await self._client.start()

        logger.info("[TelegramSender] Telegram 发送渠道已就绪 ✓")

    async def on_stop(self) -> None:
        """Agent 停止"""
        if self._client:
            await self._client.stop()
            self._client = None
        logger.info("[TelegramSender] Telegram 发送渠道已停止")

    # ─── 消息处理 ──────────────────────────────────────

    async def handle(self, msg: Message) -> None:
        """消息分拣 — 文本/流式/语音"""
        if not self.enabled:
            logger.debug("[TelegramSender] 渠道已禁用，忽略消息")
            return

        if msg.msg_type == MessageType.SEND_TEXT:
            await self._handle_send_text(msg)
        elif msg.msg_type == MessageType.STREAM_START:
            self._handle_stream_start(msg)
        elif msg.msg_type == MessageType.STREAM_CHUNK:
            self._handle_stream_chunk(msg)
        elif msg.msg_type == MessageType.STREAM_END:
            await self._handle_stream_end(msg)
        elif msg.msg_type == MessageType.SEND_VOICE:
            await self._handle_send_voice(msg)
        else:
            logger.debug(
                "[TelegramSender] 忽略消息类型: %s", msg.msg_type.name,
            )

    # ─── 文本回复 ──────────────────────────────────────

    async def _handle_send_text(self, msg: Message) -> None:
        """发送纯文本消息到 Telegram"""
        chat_id = self._get_chat_id(msg)
        text = msg.payload.get("message", "") or msg.payload.get("text", "")
        print(f"\033[34m[TelegramSender]\033[0m 收到 SEND_TEXT: chat={chat_id} text_len={len(text)}")
        if not chat_id:
            print(f"\033[33m[TelegramSender]\033[0m ⚠ 缺少 chat_id，无法发送")
            return
        if not text:
            return

        max_len = 4000
        if len(text) <= max_len:
            await self._send_text(chat_id, text)
        else:
            parts = [text[i:i + max_len] for i in range(0, len(text), max_len)]
            for i, part in enumerate(parts):
                prefix = f"({i + 1}/{len(parts)})\n" if len(parts) > 1 else ""
                await self._send_text(chat_id, prefix + part)

        await self._publish_done(msg, text)

    async def _send_text(self, chat_id: int, text: str) -> None:
        """发送单条文本"""
        if not self._client:
            print(f"\033[31m[TelegramSender]\033[0m ✗ 客户端未初始化，无法发送")
            return
        try:
            result = await self._client.send_message(chat_id, text)
            ok = result.get("ok", False)
            if ok:
                print(f"\033[32m[TelegramSender]\033[0m ✓ 已发送: chat={chat_id} len={len(text)}")
                logger.info("[TelegramSender] 已发送文字回复: chat=%s len=%d", chat_id, len(text))
            else:
                print(f"\033[31m[TelegramSender]\033[0m ✗ API 返回失败: {result.get('description', 'unknown')}")
                logger.error("[TelegramSender] API 返回失败: %s", result)
        except Exception as e:
            print(f"\033[31m[TelegramSender]\033[0m ✗ 发送异常: {e}")
            logger.error("[TelegramSender] 发送文字失败: %s", e)

    # ─── 流式回复 ──────────────────────────────────────

    def _handle_stream_start(self, msg: Message) -> None:
        """流式开始 — 初始化缓冲"""
        self._stream_buffer = ""
        self._stream_chat_id = self._get_chat_id(msg)
        self._stream_msg_id = msg.msg_id

    def _handle_stream_chunk(self, msg: Message) -> None:
        """流式块 — 追加到缓冲（不立即发送，等 END 时一次性发送）"""
        chunk = msg.payload.get("text", "")
        self._stream_buffer += chunk

    async def _handle_stream_end(self, msg: Message) -> None:
        """流式结束 — 发送完整文本到 Telegram"""
        chat_id = self._stream_chat_id or self._get_chat_id(msg)
        full_text = self._stream_buffer or msg.payload.get("text", "")
        print(f"\033[34m[TelegramSender]\033[0m STREAM_END: chat={chat_id} text_len={len(full_text)} client={'OK' if self._client else 'NONE'}")

        if chat_id and full_text:
            await self._send_text(chat_id, full_text)
        else:
            print(f"\033[33m[TelegramSender]\033[0m ⚠ STREAM_END 无法发送: chat={chat_id} has_text={bool(full_text)}")

        await self._publish_done(msg, full_text)
        self._stream_buffer = ""
        self._stream_chat_id = None

    # ─── 语音回复 ──────────────────────────────────────

    async def _handle_send_voice(self, msg: Message) -> None:
        """发送语音回复 — TTS 合成 → Telegram sendAudio"""
        chat_id = self._get_chat_id(msg)
        if not chat_id:
            logger.warning("[TelegramSender] SEND_VOICE 缺少 chat_id")
            return

        text = msg.payload.get("message", "") or msg.payload.get("text", "")
        if not text:
            return

        # 尝试 TTS 合成（如果 MiMo 可用）
        voice_path = None
        if self._mimo:
            try:
                voice_data = await self._mimo.synthesize(text, voice="冰糖", format="wav")
                voice_path = str(
                    self._workspace_dir / f"reply_{msg.msg_id}.wav"
                )
                Path(voice_path).write_bytes(voice_data)
                logger.info(
                    "[TelegramSender] TTS 合成完成: %s", voice_path,
                )
            except Exception as e:
                logger.warning("[TelegramSender] TTS 合成失败: %s", e)

        # 发送语音或降级为文字
        if voice_path and self._client:
            try:
                await self._client.send_audio(chat_id, voice_path, caption=f"🎤 {text[:50]}")
                logger.info(
                    "[TelegramSender] 已发送语音: chat=%s", chat_id,
                )
            except Exception as e:
                logger.error("[TelegramSender] 发送语音失败: %s", e)
                # 降级为文字
                await self._send_text(chat_id, f"🎤 {text}")
        else:
            # 无 TTS → 只发文字
            await self._send_text(chat_id, f"🎤 {text}")

        # 清理临时文件
        if voice_path:
            try:
                Path(voice_path).unlink(missing_ok=True)
            except Exception:
                pass

        await self._publish_done(msg, text)

    # ─── 辅助方法 ──────────────────────────────────────

    def _get_chat_id(self, msg: Message) -> Optional[int]:
        """从消息 payload 提取 chat_id

        Receiver 在 payload 中放置了 chat_id，
        Scheduler 通过消息工厂透传。
        """
        chat_id = msg.payload.get("chat_id")
        if chat_id is not None:
            try:
                return int(chat_id)
            except (ValueError, TypeError):
                pass
        return None

    async def _publish_done(self, msg: Message, text: str) -> None:
        """发布 CONVERSATION_DONE 到 main 和 memory_agent（双发）"""
        done_main = Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="main",
            payload={"message": text},
            session_id=msg.session_id,
            parent_id=msg.msg_id,
        )
        await self.bus.publish(done_main)

        done_mem = Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source=self.name,
            target="memory_agent",
            payload={"message": text},
            session_id=msg.session_id,
            parent_id=msg.msg_id,
        )
        await self.bus.publish(done_mem)

    def _load_token_from_file(self) -> str:
        """从文件加载持久化的 bot_token"""
        try:
            if self._bot_token_file.exists():
                token = self._bot_token_file.read_text(encoding="utf-8").strip()
                if token:
                    return token
        except Exception:
            pass
        return ""
