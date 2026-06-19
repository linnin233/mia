# -*- coding: utf-8 -*-
"""微信 iLink 渠道工具函数 — AES-128-ECB 加解密 + HTTP 请求头生成

iLink Bot API 的媒体文件（图片/文件/视频）存储在微信 CDN 上，
使用 AES-128-ECB + PKCS7 加密。本模块提供加解密和请求头构建。

密钥格式说明（iLink 协议有三种 AES key 编码方式）:
  Format A: base64(16 字节原始 key) — 用于图片
  Format B: base64(32 字符 hex 字符串) — 用于文件/语音/视频
  Format C: 32 字符 hex 字符串（如图片的 aeskey 字段）

依赖: pycryptodome（可选 — 仅媒体加解密时需要）
"""

from __future__ import annotations

import base64
import secrets
from typing import Dict


def make_headers(bot_token: str = "") -> Dict[str, str]:
    """构建 iLink API HTTP 请求头

    每个请求包含:
      - Content-Type: application/json
      - AuthorizationType: ilink_bot_token（固定值）
      - X-WECHAT-UIN: base64(random_uint32) — 反重放随机值
      - Authorization: Bearer <bot_token>（仅当 token 可用时）

    Args:
        bot_token: QR 码登录后获取的 bearer token

    Returns:
        HTTP 请求头字典
    """
    # 生成随机 UIN 作为反重放措施（与官方 SDK 行为一致）
    uin_val = secrets.randbelow(0xFFFFFFFF)
    uin_b64 = base64.b64encode(str(uin_val).encode()).decode()

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": uin_b64,
    }
    if bot_token:
        headers["Authorization"] = f"Bearer {bot_token}"
    return headers


def aes_ecb_decrypt(data: bytes, key_b64: str) -> bytes:
    """AES-128-ECB 解密微信 CDN 媒体文件

    自动检测三种密钥格式（与官方 TypeScript SDK 的 parseAesKey 逻辑一致）:
      1. 32/48/64 字符 hex 字符串 → bytes.fromhex()
      2. Base64 编码（16 字节原始 key 或 32 字符 hex）
      3. 其他格式 → 尝试直接使用

    Args:
        data: 加密的字节数据（从 CDN 下载）
        key_b64: AES 密钥字符串（三种格式之一）

    Returns:
        解密后的字节数据（已去除 PKCS7 padding）

    Raises:
        ImportError: 如果 pycryptodome 未安装
        ValueError: 如果密钥长度无效
    """
    try:
        from Crypto.Cipher import AES  # pycryptodome
    except ImportError as exc:
        raise ImportError(
            "pycryptodome is required for WeChat media decryption. "
            "Install with: pip install pycryptodome",
        ) from exc

    # ─── 自动检测密钥格式 ──────────────────────────────
    key: bytes
    raw = key_b64.strip()

    if len(raw) in (32, 48, 64) and all(
        c in "0123456789abcdefABCDEF" for c in raw
    ):
        # 格式: 纯 hex 字符串（如 image_item.aeskey — 32 hex chars = 16 bytes）
        key = bytes.fromhex(raw)
    else:
        # 格式: Base64 编码 — base64(16 raw bytes) 或 base64(32-char hex)
        try:
            decoded = base64.b64decode(raw + "==")
        except Exception:
            decoded = raw.encode()

        if len(decoded) == 16:
            # Format A: base64(raw 16 bytes) — 图片使用此格式
            key = decoded
        elif len(decoded) == 32 and all(
            c in b"0123456789abcdefABCDEF" for c in decoded
        ):
            # Format B: base64(hex string) — 文件/语音/视频使用此格式
            key = bytes.fromhex(decoded.decode("ascii"))
        else:
            key = decoded

    if len(key) not in (16, 24, 32):
        raise ValueError(
            f"Invalid AES key length: {len(key)} (from key_b64={raw[:20]!r})",
        )

    # ─── AES-128-ECB 解密 ──────────────────────────────
    cipher = AES.new(key, AES.MODE_ECB)
    decrypted = cipher.decrypt(data)

    # 去除 PKCS7 padding
    from Crypto.Util.Padding import unpad
    return unpad(decrypted, AES.block_size)


def aes_ecb_encrypt(data: bytes, key_b64: str) -> bytes:
    """AES-128-ECB 加密 + PKCS7 padding — 用于上传媒体到微信 CDN

    Args:
        data: 原始文件字节
        key_b64: Base64 编码的 16 字节 AES 密钥

    Returns:
        加密后的字节数据
    """
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
    except ImportError as exc:
        raise ImportError(
            "pycryptodome is required for WeChat media encryption. "
            "Install with: pip install pycryptodome",
        ) from exc

    key = base64.b64decode(key_b64)
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(pad(data, AES.block_size))


def generate_aes_key_b64() -> str:
    """生成加密安全的 16 字节随机 AES 密钥

    Returns:
        Base64 编码的 16 字节 AES 密钥
    """
    key = secrets.token_bytes(16)
    return base64.b64encode(key).decode()
