# -*- coding: utf-8 -*-
"""WeChatAgent — 微信通信渠道桥接 Agent

WeChatAgent 是 MIA 消息总线和微信 iLink Bot API 之间的桥梁。

职责:
  入站（微信 → MIA）:
    1. 后台线程长轮询 iLink API 获取新消息
    2. 将微信消息转为 RAW_INPUT 发布到 MessageBus
    3. 支持文本和图片消息（后续可扩展语音/视频）

  出站（MIA → 微信）:
    4. 监听总线上的 SEND_TEXT / STREAM_* 输出消息
    5. 将回复文本发送给微信用户

认证:
  - 优先使用配置的 bot_token
  - 无 token 时自动启动 QR 码扫码登录
  - Token 持久化到文件，下次启动自动加载

架构位置:
  微信用户 ←→ (iLink API) ←→ WeChatAgent ←→ MessageBus ←→ MIA Agent 链路
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────

# 去重集合上限
_MAX_PROCESSED_IDS = 2000

# 内容去重时间窗口（秒）
_TEXT_DEDUP_TTL = 30.0

# 默认 token 文件路径
_DEFAULT_TOKEN_FILE = Path.home() / ".mia" / "wechat_bot_token"
_DEFAULT_CONTEXT_TOKENS_FILE = Path.home() / ".mia" / "wechat_context_tokens.json"


class WeChatAgent(BaseAgent):
    """微信渠道 Agent — 桥接 iLink Bot API 和 MIA MessageBus

    作为 MIA 的输入/输出渠道之一，与 ReceiverAgent + SenderAgent 并行工作。

    Args:
        bus: MIA 消息总线
        bot_token: iLink Bot token（空字符串表示需要 QR 码登录）
        bot_token_file: Token 持久化文件路径
        base_url: iLink API 基础 URL
        enabled: 是否启用此渠道
        media_dir: 媒体文件下载目录
    """

    def __init__(
        self,
        bus: MessageBus,
        bot_token: str = "",
        bot_token_file: str = "",
        base_url: str = "",
        enabled: bool = True,
        media_dir: str = "",
    ):
        super().__init__(name="wechat", bus=bus)
        self.enabled = enabled
        self.bot_token = bot_token
        self._base_url = base_url or "https://ilinkai.weixin.qq.com"

        # Token 文件和媒体目录
        self._bot_token_file = (
            Path(bot_token_file).expanduser()
            if bot_token_file
            else _DEFAULT_TOKEN_FILE
        )
        self._context_tokens_file = (
            self._bot_token_file.parent / "wechat_context_tokens.json"
        )
        self._media_dir = (
            Path(media_dir).expanduser()
            if media_dir
            else Path.home() / ".mia" / "media"
        )

        # ILinkClient 实例（延迟创建，在 start() 中初始化）
        self._client = None  # type: Optional[ILinkClient]

        # ─── 长轮询状态 ──────────────────────────────
        self._poll_loop: Optional[asyncio.AbstractEventLoop] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._loop_accepting = threading.Event()

        # 长轮询 cursor（get_updates_buf）
        self._cursor: str = ""

        # ─── 消息去重 ────────────────────────────────
        self._processed_ids: OrderedDict[str, None] = OrderedDict()
        self._processed_ids_lock = threading.Lock()

        # 内容级去重
        self._text_dedup: OrderedDict[str, float] = OrderedDict()

        # ─── 用户状态缓存 ────────────────────────────
        # 缓存每个用户最近一次的 context_token（用于主动发送）
        self._user_context_tokens: Dict[str, str] = {}

        # 当前活跃对话的元数据（用于路由回复）
        # session_id → {to_user_id, context_token, ...}
        self._active_sessions: Dict[str, Dict[str, Any]] = {}

        # 流式回复缓冲（session_id → 累积文本）
        self._stream_buffers: Dict[str, str] = {}

    # ─── 生命周期 ──────────────────────────────────────

    async def on_start(self) -> None:
        """Agent 启动 — 尝试加载 token，初始化客户端，启动后台轮询"""
        if not self.enabled:
            logger.info("[WeChatAgent] 渠道已禁用，跳过初始化")
            return

        # 1. 尝试从文件加载 token
        if not self.bot_token:
            self.bot_token = self._load_token_from_file()

        # 2. 创建 ILinkClient（延迟导入避免循环依赖）
        from mia.channels.wechat.client import ILinkClient

        self._client = ILinkClient(
            bot_token=self.bot_token,
            base_url=self._base_url,
        )
        await self._client.start()

        # 3. 加载持久化的 context_tokens
        self._load_context_tokens()

        # 4. 如果没有 token，执行 QR 码登录
        if not self.bot_token:
            logger.info("[WeChatAgent] 无 bot_token，启动 QR 码登录...")
            success = await self._do_qrcode_login()
            if not success:
                logger.error("[WeChatAgent] QR 码登录失败，微信渠道不可用")
                self.enabled = False
                return

        # 5. 启动后台长轮询线程
        self._start_poll_thread()
        logger.info("[WeChatAgent] 微信渠道已就绪 ✓")

    async def on_stop(self) -> None:
        """Agent 停止 — 关闭轮询线程和 HTTP 客户端"""
        # 通知轮询线程停止
        self._stop_event.set()
        self._loop_accepting.clear()

        # 等待轮询线程结束
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5.0)

        # 关闭 ILinkClient
        if self._client:
            await self._client.stop()
            self._client = None

        logger.info("[WeChatAgent] 微信渠道已停止")

    async def handle(self, msg: Message) -> None:
        """处理来自 MessageBus 的输出消息

        监听 Scheduler 发来的输出消息，转发给微信用户:
          - SEND_TEXT: 纯文本回复
          - STREAM_START/CHUNK/END: 流式回复
          - SEND_VOICE: 语音回复（微信暂不支持语音发送，降级为文本）
        """
        if not self.enabled:
            return

        if msg.msg_type == MessageType.SEND_TEXT:
            await self._handle_output_text(msg)
        elif msg.msg_type == MessageType.STREAM_START:
            await self._handle_stream_start(msg)
        elif msg.msg_type == MessageType.STREAM_CHUNK:
            await self._handle_stream_chunk(msg)
        elif msg.msg_type == MessageType.STREAM_END:
            await self._handle_stream_end(msg)
        elif msg.msg_type == MessageType.SEND_VOICE:
            # 微信 iLink Bot 暂不支持直接发送语音，
            # 降级为发送文本消息
            message = msg.payload.get("message", "")
            await self._send_to_user(msg.session_id, message)

    # ─── 输出处理 ──────────────────────────────────────

    async def _handle_output_text(self, msg: Message) -> None:
        """处理文本输出 — 直接发送到微信用户"""
        message = msg.payload.get("message", "")
        if message:
            await self._send_to_user(msg.session_id, message)

    async def _handle_stream_start(self, msg: Message) -> None:
        """流式开始 — 初始化缓冲"""
        sid = msg.session_id or ""
        self._stream_buffers[sid] = ""

    async def _handle_stream_chunk(self, msg: Message) -> None:
        """流式增量 — 累积文本"""
        sid = msg.session_id or ""
        delta = msg.payload.get("delta", "")
        if sid in self._stream_buffers:
            self._stream_buffers[sid] += delta

    async def _handle_stream_end(self, msg: Message) -> None:
        """流式结束 — 发送完整文本到微信"""
        sid = msg.session_id or ""
        full_text = msg.payload.get("message", "")

        # 从缓冲获取完整文本（优先使用 payload 中的完整文本）
        if not full_text and sid in self._stream_buffers:
            full_text = self._stream_buffers.pop(sid, "")
        elif sid in self._stream_buffers:
            del self._stream_buffers[sid]

        if full_text:
            await self._send_to_user(sid, full_text)

    async def _send_to_user(self, session_id: Optional[str], text: str) -> None:
        """将文本发送到对应的微信用户

        根据 session_id 查找对应的微信用户和 context_token，
        通过 iLink API 发送文本消息。

        Args:
            session_id: MIA 会话 ID（格式: wechat:<user_id>）
            text: 回复文本
        """
        if not self._client or not text:
            return

        # 解析 session_id 获取微信用户 ID
        to_user_id = ""
        context_token = ""

        if session_id and session_id in self._active_sessions:
            meta = self._active_sessions[session_id]
            to_user_id = meta.get("to_user_id", "")
            context_token = meta.get("context_token", "")

        # 如果 session 元数据中没有 context_token，尝试从缓存获取
        if not context_token and to_user_id:
            context_token = self._user_context_tokens.get(to_user_id, "")

        if not to_user_id:
            logger.warning(
                "[WeChatAgent] 无法确定微信接收者: session_id=%s",
                session_id,
            )
            return

        if not context_token:
            logger.warning(
                "[WeChatAgent] 无 context_token for user=%s，"
                "消息可能发送失败",
                to_user_id[:20],
            )

        try:
            resp = await self._client.send_text(
                to_user_id, text, context_token,
            )
            if isinstance(resp, dict):
                ret = resp.get("ret", 0)
                if ret != 0:
                    logger.warning(
                        "[WeChatAgent] send_text 被拒绝: "
                        "ret=%s errcode=%s",
                        ret,
                        resp.get("errcode", ""),
                    )
            logger.info(
                "[WeChatAgent] 已发送回复 to %s, len=%d",
                to_user_id[:20],
                len(text),
            )
        except Exception:
            logger.exception(
                "[WeChatAgent] 发送回复失败 to=%s",
                to_user_id[:20],
            )

    # ─── 长轮询（后台线程） ────────────────────────────

    def _start_poll_thread(self) -> None:
        """启动后台长轮询线程"""
        if self._poll_thread and self._poll_thread.is_alive():
            return

        self._stop_event.clear()
        self._loop_accepting.set()

        self._poll_thread = threading.Thread(
            target=self._run_poll_forever,
            name="wechat-poll",
            daemon=True,
        )
        self._poll_thread.start()
        logger.info("[WeChatAgent] 长轮询线程已启动")

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
            logger.info("[WeChatAgent] 轮询任务已取消")
        except Exception:
            logger.exception("[WeChatAgent] 轮询线程异常")
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
        """异步长轮询循环 — 持续调用 getupdates 获取新消息"""
        from mia.channels.wechat.client import ILinkClient

        # 为此线程创建独立的 HTTP 客户端
        client = ILinkClient(
            bot_token=self.bot_token,
            base_url=self._base_url,
        )
        await client.start()
        cursor = self._cursor

        # 断路器：连续失败指数退避
        consecutive_failures = 0
        max_backoff_seconds = 120

        try:
            while not self._stop_event.is_set():
                try:
                    data = await client.getupdates(cursor)
                    ret = data.get("ret", -1)
                    new_cursor = data.get("get_updates_buf")
                    if new_cursor is not None:
                        cursor = new_cursor
                        self._cursor = cursor

                    msgs: List[Dict[str, Any]] = data.get("msgs") or []
                    for msg in msgs:
                        await self._on_message(msg, client)

                    # 成功后重置断路器
                    consecutive_failures = 0

                    if ret != 0 and not msgs:
                        if ret == -1:
                            logger.debug(
                                "wechat getupdates timeout (ret=-1), "
                                "continue polling"
                            )
                        else:
                            logger.warning(
                                "wechat getupdates non-zero ret=%s, "
                                "retry in 3s",
                                ret,
                            )
                            await asyncio.sleep(3)

                except asyncio.CancelledError:
                    break
                except Exception:
                    consecutive_failures += 1
                    backoff = min(
                        5 * (2 ** (consecutive_failures - 1)),
                        max_backoff_seconds,
                    )
                    logger.exception(
                        "wechat poll error (%d consecutive), "
                        "retry in %ds",
                        consecutive_failures,
                        backoff,
                    )
                    if not self._stop_event.is_set():
                        await asyncio.sleep(backoff)
        finally:
            await client.stop()

    # ─── 入站消息处理 ──────────────────────────────────

    async def _on_message(
        self,
        msg: Dict[str, Any],
        client,  # ILinkClient (from poll thread)
    ) -> None:
        """解析一条微信入站消息并转发到 MIA 消息总线

        Args:
            msg: iLink getupdates 返回的原始消息字典
            client: 当前轮询线程的 ILinkClient 实例
        """
        try:
            from_user_id = msg.get("from_user_id", "")
            to_user_id = msg.get("to_user_id", "")
            context_token = msg.get("context_token", "")
            group_id = msg.get("group_id", "")
            msg_type = msg.get("message_type", 0)

            # 只处理用户→Bot 的消息（message_type == 1）
            if msg_type != 1:
                return

            # ─── 去重 ──────────────────────────────────
            dedup_key = (
                context_token
                or f"{from_user_id}_{msg.get('msg_id', '')}"
            )
            if dedup_key and self._is_duplicate(dedup_key):
                logger.debug(
                    "wechat: duplicate message skipped: %s",
                    dedup_key[:40],
                )
                return

            # 内容级去重
            raw_text = "".join(
                (item.get("text_item") or {}).get("text", "")
                for item in (msg.get("item_list") or [])
                if item.get("type", 0) == 1
            ).strip()
            if raw_text and self._is_text_duplicate(from_user_id, raw_text):
                logger.debug(
                    "wechat: content-duplicate message skipped: "
                    "user=%s text_len=%d",
                    from_user_id[:12],
                    len(raw_text),
                )
                return

            # ─── 解析消息内容 ──────────────────────────
            text_parts: List[str] = []
            image_paths: List[str] = []

            item_list: List[Dict[str, Any]] = msg.get("item_list") or []
            for item in item_list:
                item_type = item.get("type", 0)

                if item_type == 1:  # 文本
                    text = (
                        (item.get("text_item") or {})
                        .get("text", "")
                        .strip()
                    )
                    # 过滤掉纯文件名（避免文件消息触发误回复）
                    if text and not self._looks_like_filename(text):
                        text_parts.append(text)

                elif item_type == 2:  # 图片
                    img_item = item.get("image_item") or {}
                    media = img_item.get("media") or {}
                    encrypt_query_param = media.get(
                        "encrypt_query_param", ""
                    )
                    aeskey_hex = img_item.get("aeskey", "")
                    if aeskey_hex:
                        import base64 as _b64
                        aes_key = _b64.b64encode(
                            bytes.fromhex(aeskey_hex)
                        ).decode()
                    else:
                        aes_key = media.get("aes_key", "")

                    if encrypt_query_param:
                        path = await self._download_media(
                            client,
                            aes_key,
                            "image.jpg",
                            encrypt_query_param=encrypt_query_param,
                        )
                        if path:
                            image_paths.append(path)
                        else:
                            text_parts.append("[图片下载失败]")
                    else:
                        text_parts.append("[图片: 无下载链接]")

                elif item_type == 3:  # 语音
                    voice_item = item.get("voice_item") or {}
                    asr_text = (
                        voice_item.get("text_item", {}).get("text", "")
                        .strip()
                        if isinstance(
                            voice_item.get("text_item"), dict
                        )
                        else voice_item.get("text", "").strip()
                    )
                    if asr_text:
                        text_parts.append(asr_text)
                    else:
                        text_parts.append("[语音: 无转写]")

                elif item_type == 4:  # 文件
                    file_item = item.get("file_item") or {}
                    filename = (
                        file_item.get("file_name", "file.bin")
                        or "file.bin"
                    )
                    text_parts.append(f"[收到文件: {filename}]")

                elif item_type == 5:  # 视频
                    text_parts.append("[收到视频]")

                # 处理引用消息（回复某条消息）
                ref_msg = item.get("ref_msg")
                if ref_msg:
                    quoted_text = self._extract_quoted_text(ref_msg)
                    if quoted_text:
                        text_parts.insert(
                            0, f"[引用消息: {quoted_text}]"
                        )

            # ─── 构建用户输入 ──────────────────────────
            text = "\n".join(text_parts).strip()
            if not text and not image_paths:
                return

            # 生成 session_id
            is_group = bool(group_id)
            if is_group:
                session_id = f"wechat:group:{group_id}"
            else:
                session_id = f"wechat:{from_user_id}" if from_user_id else ""

            # 保存活跃会话元数据（用于后续回复路由）
            if from_user_id and context_token:
                self._active_sessions[session_id] = {
                    "to_user_id": from_user_id,
                    "context_token": context_token,
                    "is_group": is_group,
                    "group_id": group_id,
                }
                # 同时更新用户级 context_token 缓存
                self._user_context_tokens[from_user_id] = context_token
                self._save_context_tokens()

            # ─── 发布 RAW_INPUT 到消息总线 ─────────────
            user_input = text or "请分析这张图片"

            logger.info(
                "wechat recv: from=%s group=%s text_len=%s",
                (from_user_id or "")[:20],
                (group_id or "")[:20],
                len(text),
            )

            # 将消息转发到主事件循环（跨线程安全）
            self._dispatch_to_main_loop(
                self._publish_raw_input(
                    user_input=user_input,
                    image_paths=image_paths,
                    session_id=session_id,
                ),
                description=f"publish RAW_INPUT for {session_id}",
            )

        except Exception:
            logger.exception("[WeChatAgent] _on_message 失败")

    async def _publish_raw_input(
        self,
        user_input: str,
        image_paths: List[str],
        session_id: str,
    ) -> None:
        """在主事件循环中发布 RAW_INPUT 消息到总线

        这会触发完整的 MIA Agent 链路:
          ReceiverAgent → MemoryAgent → SchedulerAgent → TaskAgent → SenderAgent

        Args:
            user_input: 用户文本输入
            image_paths: 图片文件路径列表
            session_id: 会话 ID
        """
        # 构建 payload
        payload: Dict[str, Any] = {
            "text": user_input,
            "source": "wechat",  # 标记来源为微信
        }

        # 如果有图片，将第一张图片作为主要图片
        if image_paths:
            payload["image"] = image_paths[0]

        raw_msg = Message(
            msg_type=MessageType.RAW_INPUT,
            source="wechat",
            target="receiver",  # 走正常的 ReceiverAgent 流程
            payload=payload,
            session_id=session_id,
        )
        await self.bus.publish(raw_msg)

        print(
            f"\033[32m[WeChat]\033[0m 收到消息 → "
            f"\033[90m{user_input[:80]}\033[0m"
        )

    # ─── QR 码登录 ─────────────────────────────────────

    async def _do_qrcode_login(self) -> bool:
        """执行 QR 码扫码登录流程

        打印二维码 URL 到终端，等待用户扫码确认。
        成功后自动保存 token 到文件。

        Returns:
            True 如果登录成功
        """
        if not self._client:
            return False

        try:
            qr_data = await self._client.get_bot_qrcode()
            qrcode = qr_data.get("qrcode", "")
            qrcode_url = qr_data.get("url") or qr_data.get(
                "qrcode_img_content", ""
            )

            print()
            print(f"\033[1;33m{'='*50}\033[0m")
            print(f"\033[1;33m  MIA 微信登录 — 请扫描下方二维码\033[0m")
            print(f"\033[1;33m{'='*50}\033[0m")
            print()
            print(f"  QR 码 URL: {qrcode_url or '(见 debug 日志)'}")
            print()
            print(f"  \033[90m等待扫码中... (最长 300 秒)\033[0m")

            logger.info(
                "wechat: waiting for QR code scan (up to 300s)…"
            )

            token, base_url = await self._client.wait_for_login(qrcode)
            self.bot_token = token
            self._client.bot_token = token

            if base_url and base_url != self._client.base_url:
                self._client.base_url = base_url.rstrip("/")
                self._base_url = base_url.rstrip("/")

            self._save_token_to_file(token)
            print(f"  \033[32m[OK]\033[0m 微信登录成功！")
            print()

            logger.info("wechat: QR code login succeeded")
            return True

        except Exception:
            logger.exception("wechat: QR code login failed")
            print(f"  \033[31m[FAIL]\033[0m 微信登录失败，请重试")
            print()
            return False

    # ─── Token 持久化 ──────────────────────────────────

    def _load_token_from_file(self) -> str:
        """从文件加载持久化的 bot_token"""
        try:
            if self._bot_token_file.exists():
                token = self._bot_token_file.read_text(
                    encoding="utf-8"
                ).strip()
                if token:
                    logger.info(
                        "wechat: loaded bot_token from %s",
                        self._bot_token_file,
                    )
                    return token
        except Exception:
            logger.debug(
                "wechat: failed to read token file", exc_info=True
            )
        return ""

    def _save_token_to_file(self, token: str) -> None:
        """持久化 bot_token 到文件"""
        try:
            self._bot_token_file.parent.mkdir(parents=True, exist_ok=True)
            self._bot_token_file.write_text(token, encoding="utf-8")
            logger.info(
                "wechat: bot_token saved to %s", self._bot_token_file
            )
        except Exception:
            logger.warning(
                "wechat: failed to save token file", exc_info=True
            )

    def _load_context_tokens(self) -> None:
        """从文件加载持久化的 context_tokens"""
        try:
            if self._context_tokens_file.exists():
                data = json.loads(
                    self._context_tokens_file.read_text(encoding="utf-8")
                )
                if isinstance(data, dict):
                    self._user_context_tokens = {
                        k: v
                        for k, v in data.items()
                        if isinstance(k, str) and isinstance(v, str)
                    }
                    logger.info(
                        "wechat: loaded %d context_tokens from %s",
                        len(self._user_context_tokens),
                        self._context_tokens_file,
                    )
        except Exception:
            logger.debug(
                "wechat: failed to load context_tokens file",
                exc_info=True,
            )

    def _save_context_tokens(self) -> None:
        """持久化当前 context_tokens 到文件"""
        try:
            self._context_tokens_file.parent.mkdir(
                parents=True, exist_ok=True
            )
            self._context_tokens_file.write_text(
                json.dumps(
                    self._user_context_tokens, ensure_ascii=False
                ),
                encoding="utf-8",
            )
        except Exception:
            logger.debug(
                "wechat: failed to save context_tokens file",
                exc_info=True,
            )

    # ─── 消息去重 ──────────────────────────────────────

    def _is_duplicate(self, msg_id: str) -> bool:
        """ID 级去重 — 防止同一消息被重复处理"""
        with self._processed_ids_lock:
            if msg_id in self._processed_ids:
                return True
            self._processed_ids[msg_id] = None
            while len(self._processed_ids) > _MAX_PROCESSED_IDS:
                self._processed_ids.popitem(last=False)
        return False

    def _is_text_duplicate(
        self, from_user_id: str, text: str
    ) -> bool:
        """内容级去重 — 捕捉跨 poll 的重复消息"""
        import hashlib
        import time

        content_hash = hashlib.md5(text.encode()).hexdigest()[:16]
        key = f"{from_user_id}:{content_hash}"
        now = time.time()
        with self._processed_ids_lock:
            prev_time = self._text_dedup.get(key)
            if (
                prev_time is not None
                and now - prev_time < _TEXT_DEDUP_TTL
            ):
                return True
            self._text_dedup[key] = now
            while len(self._text_dedup) > _MAX_PROCESSED_IDS:
                self._text_dedup.popitem(last=False)
        return False

    # ─── 媒体下载 ──────────────────────────────────────

    async def _download_media(
        self,
        client,  # ILinkClient
        aes_key: str = "",
        filename_hint: str = "file.bin",
        encrypt_query_param: str = "",
    ) -> Optional[str]:
        """下载并解密 CDN 媒体文件

        Returns:
            本地文件路径，失败返回 None
        """
        import hashlib

        try:
            data = await client.download_media(
                "", aes_key, encrypt_query_param
            )
            self._media_dir.mkdir(parents=True, exist_ok=True)
            safe_name = (
                "".join(
                    c
                    for c in filename_hint
                    if c.isalnum() or c in "-_."
                )
                or "media"
            )
            url_hash = hashlib.md5(
                encrypt_query_param.encode()
            ).hexdigest()[:8]
            path = self._media_dir / f"wechat_{url_hash}_{safe_name}"
            path.write_bytes(data)
            return str(path)
        except Exception:
            logger.exception(
                "wechat _download_media failed"
            )
            return None

    # ─── 跨线程调度 ────────────────────────────────────

    def _dispatch_to_main_loop(
        self,
        coro,
        *,
        description: str = "",
    ) -> bool:
        """将协程安全地调度到主事件循环（从轮询线程调用）

        Args:
            coro: 要在主循环中执行的协程
            description: 调试描述

        Returns:
            True 如果成功调度
        """
        if not self._loop_accepting.is_set():
            logger.debug(
                "wechat: skipping dispatch (loop not accepting): %s",
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
                "wechat: dispatch failed (loop stopped): %s",
                description,
            )
            coro.close()
            return False

    # ─── 辅助方法 ──────────────────────────────────────

    @staticmethod
    def _looks_like_filename(text: str) -> bool:
        """检查文本是否看起来像纯文件名（用于过滤文件消息的误触发）"""
        common_extensions = (
            ".txt",
            ".doc",
            ".docx",
            ".pdf",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".mp4",
            ".avi",
            ".mov",
            ".mp3",
            ".wav",
            ".zip",
            ".rar",
            ".xlsx",
            ".xls",
            ".ppt",
            ".pptx",
        )
        text_lower = text.lower().strip()
        return any(
            text_lower.endswith(ext) for ext in common_extensions
        )

    @staticmethod
    def _extract_quoted_text(
        ref_msg: Dict[str, Any],
    ) -> str:
        """从引用消息中提取文本内容"""
        quoted_item = ref_msg.get("message_item") or {}
        quoted_type = quoted_item.get("type", 0)

        if quoted_type == 1:  # 文本
            return (
                (quoted_item.get("text_item") or {})
                .get("text", "")
                .strip()
            )
        elif quoted_type == 3:  # 语音（ASR 转写）
            voice_item = quoted_item.get("voice_item") or {}
            return (
                voice_item.get("text_item", {}).get("text", "").strip()
                if isinstance(voice_item.get("text_item"), dict)
                else voice_item.get("text", "").strip()
            )
        elif quoted_type == 4:  # 文件
            file_item = quoted_item.get("file_item") or {}
            filename = file_item.get("file_name", "") or ""
            return f"[文件: {filename}]" if filename else "[文件]"
        elif quoted_type == 2:  # 图片
            return "[图片]"
        elif quoted_type == 5:  # 视频
            return "[视频]"

        return ""
