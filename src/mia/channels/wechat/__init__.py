"""
微信个人号渠道 — iLink Bot API 集成

基于腾讯 iLink Bot API (https://ilinkai.weixin.qq.com)，
使用长轮询接收消息、HTTP 发送消息、QR 码扫码登录。

组件:
  - client.py: ILinkClient — iLink HTTP API 异步客户端
  - utils.py: AES-128-ECB 加解密 + 请求头生成
  - agent.py: WeChatAgent — MIA 消息总线和微信之间的桥接 Agent
"""

from mia.channels.wechat.client import ILinkClient
from mia.channels.wechat.agent import WeChatAgent

__all__ = ["ILinkClient", "WeChatAgent"]
