"""
Telegram Bot 渠道 — 收发分离

- TelegramClient: Bot API HTTP 客户端 (getUpdates / sendMessage / sendVoice)
- TelegramReceiverAgent: 入站长轮询 → 解析消息 → 发布 RAW_INPUT
- TelegramSenderAgent: 出站 → 发送文本/语音到 Telegram

session_id 格式: telegram:<chat_id>
"""

from mia.channels.telegram.client import TelegramClient
from mia.channels.telegram.receiver import TelegramReceiverAgent
from mia.channels.telegram.sender import TelegramSenderAgent

__all__ = [
    "TelegramClient",
    "TelegramReceiverAgent",
    "TelegramSenderAgent",
]
