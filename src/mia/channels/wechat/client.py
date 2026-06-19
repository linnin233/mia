# -*- coding: utf-8 -*-
"""iLink Bot HTTP 客户端 — 微信个人号 Bot API 异步封装

所有 iLink API 端点都在 https://ilinkai.weixin.qq.com 下。
协议: HTTP/JSON，无需第三方 SDK。

认证流程:
  1. GET /ilink/bot/get_bot_qrcode?bot_type=3  → 获取二维码
  2. 轮询 GET /ilink/bot/get_qrcode_status?qrcode=<qrcode> 直到确认
  3. 保存 bot_token，后续所有请求用 Bearer token 认证

消息收发:
  - 收: POST /ilink/bot/getupdates (长轮询，最多 hold 35 秒)
  - 发: POST /ilink/bot/sendmessage (支持文本/图片/文件/视频)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import uuid
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import httpx

from .utils import (
    aes_ecb_decrypt,
    aes_ecb_encrypt,
    make_headers,
)

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────

_DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
_CHANNEL_VERSION = "2.0.1"
# 长轮询 hold 时间最长为 35 秒（服务端控制）
_GETUPDATES_TIMEOUT = 45.0
_DEFAULT_TIMEOUT = 15.0
# iLink 二维码状态轮询 hold 约 30s，使用更长的超时
_QRCODE_STATUS_TIMEOUT = 60.0


class ILinkClient:
    """微信 iLink Bot API 异步 HTTP 客户端

    封装所有 iLink API 调用，包括认证、消息收发、媒体上传下载。

    Args:
        bot_token: QR 码登录后获取的 Bearer token
        base_url: iLink API 基础 URL（默认 ilinkai.weixin.qq.com）
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
            timeout=httpx.Timeout(_GETUPDATES_TIMEOUT),
        )

    async def stop(self) -> None:
        """关闭底层 httpx 客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ─── 内部辅助方法 ──────────────────────────────────

    def _url(self, path: str) -> str:
        """构建完整 API URL"""
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    async def _get(
        self,
        path: str,
        params: Dict[str, Any] = None,
        *,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        """发送 GET 请求（自动附加 iLink 请求头）"""
        if self._client is None:
            raise RuntimeError("ILinkClient not started — call start() first")
        headers = make_headers(self.bot_token)
        resp = await self._client.get(
            self._url(path),
            params=params or {},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    async def _post(
        self,
        path: str,
        body: Dict[str, Any],
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> Any:
        """发送 POST 请求（自动附加 iLink 请求头）"""
        if self._client is None:
            raise RuntimeError("ILinkClient not started — call start() first")
        headers = make_headers(self.bot_token)
        resp = await self._client.post(
            self._url(path),
            json=body,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ─── 认证 API ──────────────────────────────────────

    async def get_bot_qrcode(self) -> Dict[str, Any]:
        """获取登录二维码

        Returns:
            dict with keys:
                qrcode (str): 二维码字符串，用于轮询状态
                qrcode_img_content (str): Base64 编码的 PNG 二维码图片
        """
        return await self._get("ilink/bot/get_bot_qrcode", {"bot_type": 3})

    async def get_qrcode_status(self, qrcode: str) -> Dict[str, Any]:
        """轮询二维码扫码状态

        Args:
            qrcode: 从 get_bot_qrcode() 获取的二维码字符串

        Returns:
            dict with keys:
                status (str): "waiting" | "scanned" | "confirmed" | "expired"
                bot_token (str): Bearer token（仅 status=="confirmed" 时）
                baseurl (str): API 基础 URL（仅 status=="confirmed" 时）
        """
        return await self._get(
            "ilink/bot/get_qrcode_status",
            {"qrcode": qrcode},
            timeout=_QRCODE_STATUS_TIMEOUT,
        )

    async def wait_for_login(
        self,
        qrcode: str,
        poll_interval: float = 1.5,
        max_wait: float = 300.0,
    ) -> Tuple[str, str]:
        """阻塞等待二维码被扫码确认（最长 300 秒）

        Args:
            qrcode: 二维码字符串
            poll_interval: 轮询间隔秒数
            max_wait: 最大等待秒数

        Returns:
            (bot_token, base_url) 元组

        Raises:
            TimeoutError: 超时未被扫码
            RuntimeError: 二维码过期
        """
        elapsed = 0.0
        while elapsed < max_wait:
            try:
                data = await self.get_qrcode_status(qrcode)
            except httpx.ReadTimeout:
                logger.warning(
                    "wechat: QR status poll timed out, retrying…"
                )
                elapsed += poll_interval
                continue

            status = data.get("status", "")
            if status == "confirmed":
                token = data.get("bot_token", "")
                base_url = data.get("baseurl", self.base_url)
                return token, base_url
            if status == "expired":
                raise RuntimeError(
                    "WeChat QR code expired, please retry login"
                )

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(f"WeChat QR code not scanned within {max_wait}s")

    # ─── 消息收发 API ──────────────────────────────────

    async def getupdates(self, cursor: str = "") -> Dict[str, Any]:
        """长轮询获取新消息（hold 最多 35 秒）

        Args:
            cursor: 上一轮返回的 get_updates_buf，首次传空字符串

        Returns:
            dict with keys:
                ret (int): 0=成功, -1=超时无新消息
                msgs (list): WeChatMessage 字典列表（可能不存在）
                get_updates_buf (str): 下一轮轮询的 cursor
                longpolling_timeout_ms (int): 服务端 hold 时间
        """
        body: Dict[str, Any] = {
            "get_updates_buf": cursor,
            "base_info": {"channel_version": _CHANNEL_VERSION},
        }
        return await self._post(
            "ilink/bot/getupdates",
            body,
            timeout=_GETUPDATES_TIMEOUT,
        )

    async def sendmessage(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """发送消息到微信用户

        Args:
            msg: 消息字典，必需字段:
                to_user_id (str): 接收者用户 ID (xxx@im.wechat)
                message_type (int): 2 = BOT
                message_state (int): 2 = FINISH
                context_token (str): 入站消息中的 context_token（必需！）
                item_list (list): 内容项列表

        Returns:
            API 响应字典
        """
        return await self._post(
            "ilink/bot/sendmessage",
            {"msg": msg, "base_info": {"channel_version": _CHANNEL_VERSION}},
        )

    async def send_text(
        self,
        to_user_id: str,
        text: str,
        context_token: str,
    ) -> Dict[str, Any]:
        """便捷方法：发送纯文本消息

        Args:
            to_user_id: 接收者用户 ID
            text: 消息文本
            context_token: 入站消息中的 context_token（必须回传！）

        Returns:
            API 响应字典
        """
        return await self.sendmessage(
            {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,   # BOT
                "message_state": 2,  # FINISH
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
        )

    # ─── 媒体辅助方法 ──────────────────────────────────

    async def download_media(
        self,
        url: str,
        aes_key_b64: str = "",
        encrypt_query_param: str = "",
    ) -> bytes:
        """下载 CDN 媒体文件并可选解密

        iLink 媒体文件存储在 https://novac2c.cdn.weixin.qq.com/c2c。
        图片/文件消息中的 'url' 字段是 hex media-ID（非 HTTP URL），
        实际下载 URL 由 CDN base + encrypt_query_param 拼接。

        Args:
            url: CDN HTTP URL，或 hex media-ID（若有 encrypt_query_param 则忽略）
            aes_key_b64: Base64 编码的 AES-128 密钥；空字符串表示不解密
            encrypt_query_param: 媒体项的 encrypt_query_param 字段

        Returns:
            解密后（或原始）的文件字节
        """
        if self._client is None:
            raise RuntimeError("ILinkClient not started — call start() first")

        if encrypt_query_param:
            cdn_base = "https://novac2c.cdn.weixin.qq.com/c2c"
            enc = quote(encrypt_query_param, safe="")
            download_url = f"{cdn_base}/download?encrypted_query_param={enc}"
        elif url.startswith("http"):
            download_url = url
        else:
            raise ValueError(
                f"Cannot download media: no valid HTTP URL. "
                f"url={url[:40]!r}, encrypt_query_param empty."
            )

        resp = await self._client.get(download_url, timeout=60.0)
        resp.raise_for_status()
        data = resp.content
        if aes_key_b64:
            data = aes_ecb_decrypt(data, aes_key_b64)
        return data

    async def getuploadurl(
        self,
        filekey: str,
        media_type: int,
        to_user_id: str,
        rawsize: int,
        rawfilemd5: str,
        filesize: int,
        aeskey: str,
        no_need_thumb: bool = True,
    ) -> Dict[str, Any]:
        """获取媒体文件上传 URL 和参数

        Args:
            filekey: 16 字节随机 hex 字符串（唯一文件标识）
            media_type: 1=图片, 2=视频, 3=文件, 4=语音
            to_user_id: 接收者用户 ID
            rawsize: 原始文件大小（字节）
            rawfilemd5: 原始文件 MD5（32 hex chars）
            filesize: 加密后文件大小（AES-ECB PKCS7 padding 后）
            aeskey: 32 字符 hex 字符串（16 字节 AES 密钥）
            no_need_thumb: 是否跳过缩略图生成

        Returns:
            dict with keys:
                upload_param (str): 加密的上传参数
                upload_full_url (str): 完整上传 URL
        """
        body: Dict[str, Any] = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "aeskey": aeskey,
            "no_need_thumb": no_need_thumb,
            "base_info": {"channel_version": _CHANNEL_VERSION},
        }
        return await self._post("ilink/bot/getuploadurl", body)

    async def upload_media(
        self,
        file_path: str,
        media_type: int,
        to_user_id: str,
    ) -> Dict[str, Any]:
        """上传并加密媒体文件到微信 CDN

        便捷方法，执行完整上传流程:
          1. 生成 AES 密钥和 filekey
          2. 计算 MD5 和文件大小
          3. AES-128-ECB 加密文件
          4. 获取上传 URL
          5. 上传加密文件到 CDN
          6. 返回 sendmessage 需要的下载参数

        Args:
            file_path: 本地文件路径
            media_type: 1=图片, 2=视频, 3=文件, 4=语音
            to_user_id: 接收者用户 ID

        Returns:
            dict with keys:
                encrypt_query_param (str): 用于 media.encrypt_query_param
                aes_key_b64 (str): Base64 编码 AES 密钥，用于 media.aes_key
                filesize (int): 加密后的文件大小
        """
        if self._client is None:
            raise RuntimeError("ILinkClient not started — call start() first")

        # 读取原始文件
        with open(file_path, "rb") as f:
            raw_data = f.read()

        rawsize = len(raw_data)
        rawfilemd5 = hashlib.md5(raw_data).hexdigest()

        # 生成 AES 密钥和 filekey
        aes_key_raw_bytes = secrets.token_bytes(16)
        aes_key_hex = aes_key_raw_bytes.hex()           # 32 hex chars，用于 API 调用
        aes_key_for_msg = base64.b64encode(              # base64(hex_string)，用于消息
            aes_key_hex.encode()
        ).decode()
        aes_key_b64_for_encrypt = base64.b64encode(      # base64(raw bytes)，用于加密
            aes_key_raw_bytes
        ).decode()
        filekey = secrets.token_hex(16)                  # 16 bytes random hex

        # AES-128-ECB 加密 + PKCS7 padding
        encrypted_data = aes_ecb_encrypt(raw_data, aes_key_b64_for_encrypt)
        filesize = len(encrypted_data)

        # 获取上传 URL
        upload_resp = await self.getuploadurl(
            filekey=filekey,
            media_type=media_type,
            to_user_id=to_user_id,
            rawsize=rawsize,
            rawfilemd5=rawfilemd5,
            filesize=filesize,
            aeskey=aes_key_hex,
        )

        logger.debug(f"getuploadurl response: {upload_resp}")
        upload_url = upload_resp.get("upload_full_url", "")
        if not upload_url:
            # API 可能返回 upload_param 而不是 upload_full_url，需要手动构造
            upload_param = upload_resp.get("upload_param", "")
            if upload_param:
                cdn_base = "https://novac2c.cdn.weixin.qq.com/c2c"
                enc_param = quote(upload_param, safe="")
                upload_url = (
                    f"{cdn_base}/upload?encrypted_query_param={enc_param}"
                    f"&filekey={filekey}"
                )
                logger.debug(
                    "Constructed upload URL from upload_param "
                    f"with filekey={filekey}"
                )
            else:
                raise ValueError(
                    "No upload_full_url or upload_param in "
                    f"getuploadurl response: {upload_resp}"
                )

        # 上传加密文件到 CDN（使用 upload_param 时不带 auth header）
        headers = {"Content-Type": "application/octet-stream"}

        logger.debug(f"Uploading to URL: {upload_url[:100]}...")
        resp = await self._client.post(
            upload_url,
            content=encrypted_data,
            headers=headers,
            timeout=120.0,
        )
        logger.debug(
            f"Upload response status: {resp.status_code}, "
            f"headers: {dict(resp.headers)}"
        )
        resp.raise_for_status()

        # 从响应头获取下载参数
        encrypt_query_param = resp.headers.get(
            "x-encrypted-param", ""
        ) or resp.headers.get("X-Encrypted-Param", "")

        if not encrypt_query_param:
            logger.error(
                "upload_media: encrypt_query_param is empty! "
                "Sent files will appear blank on receiver side. "
                f"Response headers: {dict(resp.headers)}"
            )
            raise ValueError(
                "upload_media failed: CDN did not return "
                "encrypt_query_param in response headers."
            )

        return {
            "encrypt_query_param": encrypt_query_param,
            "aes_key_b64": aes_key_for_msg,
            "rawsize": rawsize,    # 明文大小（file_item.len 用）
            "filesize": filesize,  # 密文大小（image_item.mid_size 用）
        }

    async def send_image(
        self,
        to_user_id: str,
        image_path: str,
        context_token: str,
    ) -> Dict[str, Any]:
        """发送图片消息

        Args:
            to_user_id: 接收者用户 ID
            image_path: 本地图片路径
            context_token: 入站消息的 context_token

        Returns:
            API 响应字典
        """
        upload_result = await self.upload_media(image_path, 1, to_user_id)

        encrypt_preview = (
            upload_result["encrypt_query_param"][:50]
            if upload_result["encrypt_query_param"]
            else "EMPTY"
        )
        logger.info(
            f"Image media params: encrypt_query_param={encrypt_preview}..., "
            f"aes_key={upload_result['aes_key_b64'][:20]}..., "
            f"filesize={upload_result['filesize']}"
        )

        return await self.sendmessage(
            {
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": 2,
                        "image_item": {
                            "media": {
                                "encrypt_query_param": upload_result[
                                    "encrypt_query_param"
                                ],
                                "aes_key": upload_result["aes_key_b64"],
                                "encrypt_type": 1,
                            },
                            "mid_size": upload_result["filesize"],
                        },
                    },
                ],
            },
        )

    async def send_file(
        self,
        to_user_id: str,
        file_path: str,
        filename: str,
        context_token: str,
    ) -> Dict[str, Any]:
        """发送文件消息

        Args:
            to_user_id: 接收者用户 ID
            file_path: 本地文件路径
            filename: 显示文件名
            context_token: 入站消息的 context_token

        Returns:
            API 响应字典
        """
        upload_result = await self.upload_media(file_path, 3, to_user_id)

        return await self.sendmessage(
            {
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": 4,
                        "file_item": {
                            "media": {
                                "encrypt_query_param": upload_result[
                                    "encrypt_query_param"
                                ],
                                "aes_key": upload_result["aes_key_b64"],
                                "encrypt_type": 1,
                            },
                            "file_name": filename,
                            "len": str(upload_result["rawsize"]),  # 明文大小
                        },
                    },
                ],
            },
        )
