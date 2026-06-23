"""
TelegramReceiverAgent — Telegram 入站消息接收 Agent（纯接收）

职责（只负责入站）:
  1. 后台线程长轮询 Telegram getUpdates API 获取新消息
  2. 消息去重（基于 update_id）
  3. 解析文本/语音/图片消息
  4. 发布 RAW_INPUT 到 MessageBus，payload 中携带 chat_id

不处理任何输出/发送 — 发送由 TelegramSenderAgent 负责。

session_id 格式: telegram:<chat_id>
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────

# 默认 token 文件路径
_DEFAULT_TOKEN_FILE = Path.home() / ".mia" / "telegram_bot_token"


class TelegramReceiverAgent(BaseAgent):
    """Telegram 入站消息接收 Agent — 纯入站，不处理输出

    通过后台线程长轮询 Telegram Bot API getUpdates，
    接收消息并发布 RAW_INPUT。只负责接收，不负责发送回复。

    Args:
        bus: MIA 消息总线
        bot_token: Telegram Bot token
        bot_token_file: Token 持久化文件路径
        enabled: 是否启用此渠道
    """

    def __init__(
        self,
        bus: MessageBus,
        bot_token: str = "",
        bot_token_file: str = "",
        enabled: bool = True,
    ):
        super().__init__(name="telegram_receiver", bus=bus)
        self.enabled = enabled
        self.bot_token = bot_token

        # Token 文件
        self._bot_token_file = (
            Path(bot_token_file).expanduser()
            if bot_token_file
            else _DEFAULT_TOKEN_FILE
        )

        # TelegramClient 实例（延迟创建）
        self._client = None  # type: Optional[TelegramClient]

        # ─── 长轮询状态 ──────────────────────────────
        self._poll_loop: Optional[asyncio.AbstractEventLoop] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop_accepting = threading.Event()

        # update_id 追踪（单调递增，用于去重 + offset）
        self._last_update_id: int = 0

        # ─── 消息去重 ────────────────────────────────
        self._processed_update_ids: set[int] = set()
        self._max_processed_ids = 2000

    # ─── 生命周期 ──────────────────────────────────────

    async def on_start(self) -> None:
        """Agent 启动 — 加载 token，验证连接，启动后台轮询"""
        if not self.enabled:
            logger.info("[TelegramReceiver] 渠道已禁用，跳过初始化")
            return

        # 1. 尝试从文件加载 token
        if not self.bot_token:
            self.bot_token = self._load_token_from_file()

        if not self.bot_token:
            logger.error(
                "[TelegramReceiver] 无 bot_token，"
                "请设置 MIA_TELEGRAM_BOT_TOKEN 环境变量或 "
                "通过 /interface 命令配置"
            )
            self.enabled = False
            return

        # 2. 创建 TelegramClient
        from mia.channels.telegram.client import TelegramClient

        self._client = TelegramClient(bot_token=self.bot_token)
        await self._client.start()

        # 3. 验证 token
        try:
            me = await self._client.get_me()
            if me.get("ok"):
                bot_info = me.get("result", {})
                logger.info(
                    "[TelegramReceiver] 已连接: @%s (id=%s)",
                    bot_info.get("username", "?"),
                    bot_info.get("id", "?"),
                )
                print(
                    f"\033[32m[Telegram]\033[0m "
                    f"已连接 @{bot_info.get('username', '?')}"
                )
        except Exception as e:
            logger.error("[TelegramReceiver] Token 验证失败: %s", e)
            self.enabled = False
            await self._client.stop()
            self._client = None
            return

        # 4. 启动后台长轮询线程
        self._start_poll_thread()
        logger.info("[TelegramReceiver] Telegram 接收渠道已就绪 ✓")

    async def on_stop(self) -> None:
        """Agent 停止 — 关闭轮询线程和 HTTP 客户端"""
        self._stop_event.set()
        self._loop_accepting.clear()

        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5.0)

        if self._client:
            await self._client.stop()
            self._client = None

        logger.info("[TelegramReceiver] Telegram 接收渠道已停止")

    async def handle(self, msg: Message) -> None:
        """处理消息总线消息 — Receiver 不处理输出"""
        if not self.enabled:
            return
        logger.debug(
            "[TelegramReceiver] 忽略消息: msg_type=%s", msg.msg_type.name,
        )

    # ─── 长轮询（后台线程，复用 WeChatReceiver 模型）────

    def _start_poll_thread(self) -> None:
        """启动后台长轮询线程"""
        if self._poll_thread and self._poll_thread.is_alive():
            return

        self._stop_event.clear()
        self._loop_accepting.set()

        self._poll_thread = threading.Thread(
            target=self._run_poll_forever,
            name="tg-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("[TelegramReceiver] 长轮询线程已启动")

    def _run_poll_forever(self) -> None:
        """后台线程入口：在专用 event loop 中运行长轮询"""
        if sys.platform == "darwin":
            poll_loop = asyncio.SelectorEventLoop()
        else:
            poll_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(poll_loop)
        self._poll_loop = poll_loop

        try:
            self._poll_task = poll_loop.create_task(self._poll_loop_async())
            poll_loop.run_until_complete(self._poll_task)
        except asyncio.CancelledError:
            logger.info("[TelegramReceiver] 轮询任务已取消")
        except Exception:
            logger.exception("[TelegramReceiver] 轮询线程异常")
        finally:
            self._poll_task = None
            try:
                pending = asyncio.all_tasks(poll_loop)
                for task in pending:
                    task.cancel()
                if pending:
                    poll_loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True),
                    )
                poll_loop.run_until_complete(poll_loop.shutdown_asyncgens())
                poll_loop.close()
            except Exception:
                pass
            self._poll_loop = None

    async def _poll_loop_async(self) -> None:
        """异步长轮询循环 — 持续调用 getUpdates 获取新消息"""
        from mia.channels.telegram.client import TelegramClient

        # 为此线程创建独立的 HTTP 客户端
        client = TelegramClient(bot_token=self.bot_token)
        await client.start()

        # 断路器
        consecutive_failures = 0
        max_backoff_seconds = 60

        try:
            while not self._stop_event.is_set():
                try:
                    offset = self._last_update_id + 1 if self._last_update_id > 0 else 0
                    data = await client.get_updates(offset=offset, timeout=30)

                    if data.get("ok"):
                        updates: List[Dict[str, Any]] = data.get("result", [])
                        for update in updates:
                            await self._on_update(update, client)

                        # 成功 → 重置断路器
                        consecutive_failures = 0
                    else:
                        logger.warning(
                            "[TelegramReceiver] getUpdates 失败: %s",
                            data.get("description", ""),
                        )
                        consecutive_failures += 1

                except asyncio.CancelledError:
                    break
                except Exception:
                    consecutive_failures += 1
                    backoff = min(
                        2 ** consecutive_failures,
                        max_backoff_seconds,
                    )
                    logger.exception(
                        "[TelegramReceiver] poll error (%d consecutive), "
                        "retry in %ds",
                        consecutive_failures,
                        backoff,
                    )
                    if not self._stop_event.is_set():
                        await asyncio.sleep(backoff)
        finally:
            await client.stop()

    # ─── 入站消息处理 ──────────────────────────────────

    async def _on_update(
        self,
        update: Dict[str, Any],
        client,  # TelegramClient
    ) -> None:
        """解析一条 Telegram Update 并转发到 MIA 消息总线

        Args:
            update: Telegram Update 对象
            client: 当前轮询线程的 TelegramClient 实例
        """
        try:
            update_id = update.get("update_id", 0)
            if not update_id:
                return

            # 更新 offset（确保消息确认）
            if update_id > self._last_update_id:
                self._last_update_id = update_id

            # 去重
            if update_id in self._processed_update_ids:
                return
            self._processed_update_ids.add(update_id)
            # 限制去重集合大小
            if len(self._processed_update_ids) > self._max_processed_ids:
                self._processed_update_ids.clear()

            # 提取 Message 对象
            message = update.get("message") or update.get("edited_message")
            if not message:
                return

            chat = message.get("chat", {})
            chat_id = chat.get("id", 0)
            if not chat_id:
                return

            from_user = message.get("from", {})
            message_id = message.get("message_id", 0)

            # ─── 解析消息内容 ──────────────────────────
            text_parts: List[str] = []
            voice_path: Optional[str] = None
            image_paths: List[str] = []

            # 文本消息
            text = (message.get("text") or "").strip()
            if text:
                # 跳过 Telegram 命令
                if not text.startswith("/"):
                    text_parts.append(text)
                else:
                    # Bot 命令，简单响应
                    cmd = text.split()[0].lower()
                    if cmd == "/start":
                        text_parts.append("你好，我是 MIA，有什么可以帮你？")
                    elif cmd == "/help":
                        text_parts.append("直接输入问题开始对话。支持文字和语音。")
                    else:
                        # 非标准命令，当作普通文本
                        text_parts.append(text)

            # 语音消息
            voice = message.get("voice")
            if voice:
                file_id = voice.get("file_id", "")
                if file_id:
                    try:
                        # 下载语音文件（Telegram 语音是 OGG/OPUS 格式）
                        file_info = await client.get_file(file_id)
                        file_path = file_info.get("result", {}).get("file_path", "")
                        if file_path:
                            audio_data = await client.download_file(file_path)
                            # 保存到临时目录
                            import tempfile
                            tmp_dir = Path(tempfile.gettempdir()) / "mia_telegram"
                            tmp_dir.mkdir(parents=True, exist_ok=True)
                            saved_path = tmp_dir / f"tg_voice_{file_id}.ogg"
                            saved_path.write_bytes(audio_data)
                            voice_path = str(saved_path)
                            logger.info(
                                "[TelegramReceiver] 语音已下载: %s (%d bytes)",
                                saved_path.name, len(audio_data),
                            )
                    except Exception as e:
                        logger.warning(
                            "[TelegramReceiver] 语音下载失败: %s", e,
                        )
                # 如果没有文字，给一个默认提示
                if not text_parts:
                    text_parts.append("请理解这段语音")

            # 图片消息（暂不做 VL，只提示）
            photo = message.get("photo")
            if photo and not text_parts:
                text_parts.append("[收到一张图片]")

            # 贴纸
            sticker = message.get("sticker")
            if sticker:
                emoji = sticker.get("emoji", "")
                text_parts.append(f"[贴纸 {emoji}]" if emoji else "[贴纸]")

            # 文件/文档
            document = message.get("document")
            if document:
                file_name = document.get("file_name", "文件")
                text_parts.append(f"[收到文件: {file_name}]")

            # ─── 构建用户输入 ──────────────────────────
            user_input = "\n".join(text_parts).strip()
            if not user_input and not voice_path and not image_paths:
                return

            # 生成 session_id
            session_id = f"telegram:{chat_id}"

            # 用户名信息
            sender_name = (
                from_user.get("first_name", "")
                or from_user.get("username", "")
                or str(chat_id)
            )

            logger.info(
                "[TelegramReceiver] recv: chat=%s from=%s text_len=%s voice=%s",
                chat_id,
                sender_name[:20],
                len(user_input),
                "yes" if voice_path else "no",
            )

            # ─── 发布 RAW_INPUT 到消息总线 ─────────────
            self._dispatch_to_main_loop(
                self._publish_raw_input(
                    user_input=user_input,
                    voice_path=voice_path,
                    image_paths=image_paths,
                    session_id=session_id,
                    chat_id=chat_id,
                    message_id=message_id,
                ),
                description=f"publish RAW_INPUT for {session_id}",
            )

        except Exception:
            logger.exception("[TelegramReceiver] _on_update 失败")

    async def _publish_raw_input(
        self,
        user_input: str,
        voice_path: Optional[str],
        image_paths: List[str],
        session_id: str,
        chat_id: int,
        message_id: int,
    ) -> None:
        """在主事件循环中发布 RAW_INPUT 消息到总线

        payload 中携带 chat_id，供 TelegramSenderAgent 回复路由使用。
        """
        payload: Dict[str, Any] = {
            "text": user_input,
            "source": "telegram",
            "chat_id": chat_id,
            "message_id": message_id,
        }

        if image_paths:
            payload["image"] = image_paths[0]

        if voice_path:
            payload["voice"] = voice_path

        raw_msg = Message(
            msg_type=MessageType.RAW_INPUT,
            source="telegram",
            target="receiver",
            payload=payload,
            session_id=session_id,
        )
        await self.bus.publish(raw_msg)

        voice_hint = " + 语音" if voice_path else ""
        print(
            f"\033[34m[Telegram]\033[0m 收到消息 "
            f"\033[90m(chat={chat_id}){voice_hint}: {user_input[:80]}\033[0m"
        )

    # ─── Token 持久化 ──────────────────────────────────

    def _load_token_from_file(self) -> str:
        """从文件加载持久化的 bot_token"""
        try:
            if self._bot_token_file.exists():
                token = self._bot_token_file.read_text(encoding="utf-8").strip()
                if token:
                    logger.info(
                        "[TelegramReceiver] 已从 %s 加载 token",
                        self._bot_token_file,
                    )
                    return token
        except Exception:
            logger.debug("[TelegramReceiver] 读取 token 文件失败", exc_info=True)
        return ""

    # ─── 跨线程调度 ────────────────────────────────────

    def _dispatch_to_main_loop(
        self,
        coro,
        *,
        description: str = "",
    ) -> bool:
        """将协程安全地调度到主事件循环（从轮询线程调用）"""
        if not self._loop_accepting.is_set():
            logger.debug(
                "[TelegramReceiver] skipping dispatch (loop not accepting): %s",
                description,
            )
            coro.close()
            return False

        loop = asyncio.get_event_loop()
        if loop.is_closed():
            coro.close()
            return False

        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
            return True
        except RuntimeError:
            logger.debug(
                "[TelegramReceiver] dispatch failed (loop stopped): %s",
                description,
            )
            coro.close()
            return False
