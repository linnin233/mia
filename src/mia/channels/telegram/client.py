"""
Telegram Bot API HTTP 客户端

比 WeChat iLink 简单: 无加密、无 SILK、无 CDN、标准 REST JSON API。
只需要 bot_token 即可认证，不需要 QR 码登录。

API 参考: https://core.telegram.org/bots/api
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Telegram Bot API 基础 URL（官方，可能需要代理）
_DEFAULT_BASE_URL = "https://api.telegram.org"

# 长轮询超时（Telegram 服务端 hold 时间）
_LONG_POLL_TIMEOUT = 35.0
_DEFAULT_TIMEOUT = 15.0


class TelegramClient:
    """Telegram Bot API 异步 HTTP 客户端

    封装 getUpdates（长轮询）、sendMessage、sendVoice、getFile 等核心 API。

    Args:
        bot_token: Bot Token（从 @BotFather 获取）
        base_url: API 基础 URL（默认 https://api.telegram.org）
    """

    def __init__(
        self,
        bot_token: str = "",
        base_url: str = _DEFAULT_BASE_URL,
    ) -> None:
        self.bot_token = bot_token
        self.base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    # ─── 生命周期 ──────────────────────────────────────

    async def start(self) -> None:
        """创建底层 httpx 客户端"""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_LONG_POLL_TIMEOUT),
        )

    async def stop(self) -> None:
        """关闭底层 httpx 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── 内部辅助 ──────────────────────────────────────

    def _bot_url(self, method: str) -> str:
        """构建 Bot API URL: https://api.telegram.org/bot<token>/<method>"""
        return f"{self.base_url}/bot{self.bot_token}/{method}"

    async def _post(
        self,
        method: str,
        json_data: Dict[str, Any] = None,
        files: Dict[str, Any] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """发送 POST 请求到 Bot API"""
        if self._client is None:
            raise RuntimeError("TelegramClient not started — call start() first")

        if files:
            # multipart 上传（语音文件等）
            resp = await self._client.post(
                self._bot_url(method),
                data=json_data or {},
                files=files,
                timeout=timeout,
            )
        else:
            resp = await self._client.post(
                self._bot_url(method),
                json=json_data or {},
                timeout=timeout,
            )
        resp.raise_for_status()
        result = resp.json()
        if not result.get("ok", False):
            logger.warning(
                "Telegram API error: method=%s error=%s",
                method,
                result.get("description", "unknown"),
            )
        return result

    async def _get(
        self,
        method: str,
        params: Dict[str, Any] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Dict[str, Any]:
        """发送 GET 请求到 Bot API"""
        if self._client is None:
            raise RuntimeError("TelegramClient not started — call start() first")

        resp = await self._client.get(
            self._bot_url(method),
            params=params or {},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ─── 认证 ──────────────────────────────────────────

    async def get_me(self) -> Dict[str, Any]:
        """验证 Bot Token，返回 Bot 信息

        Returns:
            {"ok": true, "result": {"id": ..., "username": "@zhuangy_bot", ...}}
        """
        return await self._get("getMe")

    # ─── 消息接收（长轮询） ─────────────────────────────

    async def get_updates(
        self,
        offset: int = 0,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """长轮询获取新消息

        Telegram 服务端会 hold 最多 timeout 秒，
        有新消息时立即返回，无消息则超时返回空结果。

        Args:
            offset: 上次处理的最大 update_id + 1（确认已处理的消息）
            timeout: 长轮询超时秒数（1-100，默认 30）

        Returns:
            {"ok": true, "result": [Update, ...]}
        """
        params: Dict[str, Any] = {
            "timeout": timeout,
        }
        if offset > 0:
            params["offset"] = offset

        # 使用 GET（非 POST），Telegram getUpdates 是 GET
        return await self._get("getUpdates", params=params, timeout=timeout + 10.0)

    # ─── 消息发送 ──────────────────────────────────────

    async def send_message(
        self,
        chat_id: int,
        text: str,
        parse_mode: str = "",
    ) -> Dict[str, Any]:
        """发送文本消息

        Args:
            chat_id: 目标聊天 ID
            text: 消息文本（最大 4096 字符）
            parse_mode: "MarkdownV2" / "HTML" / ""（纯文本）

        Returns:
            API 响应
        """
        body: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
        }
        if parse_mode:
            body["parse_mode"] = parse_mode

        return await self._post("sendMessage", json_data=body)

    async def send_voice(
        self,
        chat_id: int,
        voice_path: str,
        caption: str = "",
    ) -> Dict[str, Any]:
        """发送语音消息（OGG 格式，OPUS 编码）

        Telegram 支持 OGG/OPUS 格式的语音消息。
        MiMo TTS 输出 WAV，需要先转换为 OGG 或直接作为 audio 发送。

        Args:
            chat_id: 目标聊天 ID
            voice_path: 本地语音文件路径（OGG 格式）
            caption: 可选的语音说明文字

        Returns:
            API 响应
        """
        voice_file = Path(voice_path)
        if not voice_file.exists():
            raise FileNotFoundError(f"Voice file not found: {voice_path}")

        data: Dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption

        # Telegram sendVoice 需要 multipart/form-data
        files = {
            "voice": (
                voice_file.name,
                voice_file.read_bytes(),
                "audio/ogg",
            )
        }

        return await self._post(
            "sendVoice",
            json_data=data,
            files=files,
            timeout=60.0,
        )

    async def send_audio(
        self,
        chat_id: int,
        audio_path: str,
        caption: str = "",
    ) -> Dict[str, Any]:
        """发送音频文件（通用格式，包括 WAV/MP3）

        用于 MiMo TTS 直接输出（不必转码为 OGG）。

        Args:
            chat_id: 目标聊天 ID
            audio_path: 本地音频文件路径
            caption: 可选的说明文字

        Returns:
            API 响应
        """
        audio_file = Path(audio_path)
        if not audio_file.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # 推断 MIME 类型
        suffix = audio_file.suffix.lower()
        mime_map = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".opus": "audio/ogg",
        }
        mime_type = mime_map.get(suffix, "audio/wav")

        data: Dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption

        files = {
            "audio": (
                audio_file.name,
                audio_file.read_bytes(),
                mime_type,
            )
        }

        return await self._post(
            "sendAudio",
            json_data=data,
            files=files,
            timeout=60.0,
        )

    # ─── 文件下载 ──────────────────────────────────────

    async def get_file(self, file_id: str) -> Dict[str, Any]:
        """获取文件信息（下载前先调用此方法获取 file_path）

        Args:
            file_id: 消息中的 file_id

        Returns:
            {"ok": true, "result": {"file_path": "..."}}
        """
        return await self._get("getFile", params={"file_id": file_id})

    async def download_file(self, file_path: str) -> bytes:
        """下载文件内容

        Args:
            file_path: 从 getFile 返回的 file_path

        Returns:
            文件字节内容
        """
        url = f"{self.base_url}/file/bot{self.bot_token}/{file_path}"
        if self._client is None:
            raise RuntimeError("TelegramClient not started — call start() first")

        resp = await self._client.get(url, timeout=60.0)
        resp.raise_for_status()
        return resp.content
