"""
BaseProvider 抽象类 — 所有 LLM Provider 的统一接口

所有 Provider 都走 OpenAI 兼容协议，使用 AsyncOpenAI 客户端。
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class BaseProvider(ABC):
    """LLM Provider 抽象基类

    子类需要实现:
      - chat(): 流式对话
      - chat_sync(): 非流式对话 (返回完整文本)
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        stream: bool = True,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ):
        """
        发起对话请求 (OpenAI 兼容 streaming)

        Args:
            messages: OpenAI 格式消息列表
            model: 模型名 (None 则用默认)
            stream: 是否流式输出
            tools: function calling 工具定义
            max_tokens: 最大输出 token 数
            temperature: 温度参数 (0-2)

        Returns:
            stream=True  → AsyncIterator[ChatCompletionChunk]
            stream=False → ChatCompletion
        """
        ...

    @abstractmethod
    async def chat_sync(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """
        非流式对话 — 返回完整文本内容

        用于 Scheduler 决策等需要完整 JSON 解析的场景。
        """
        ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """
        流式对话 — 返回文本 token 的异步迭代器

        用于用户可见的回复生成，实现逐字输出的流式效果。
        每个 yield 返回一个文本增量 (delta)，调用方负责拼接和展示。

        Args:
            messages: OpenAI 格式消息列表
            model: 模型名 (None 则用默认)
            max_tokens: 最大输出 token 数
            temperature: 温度参数 (0-2)

        Yields:
            文本增量字符串 (可能包含多个 token)
        """
        ...
