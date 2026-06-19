"""
MIA 通信渠道模块 — 多入口/多出口通信支持

MIA 核心通过 MessageBus 与外部通信。本模块提供不同的通信渠道，
将外部消息源（微信、Telegram、HTTP 等）桥接到内部消息总线。

当前支持的渠道:
  - wechat: 微信个人号 iLink Bot API（长轮询 + QR 码登录）
"""

