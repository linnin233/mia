"""
MemoryAgent — 记忆管理 Agent

基于 ReMe (agentscope-ai/ReMe) 的设计模式:

参考的 ReMe 源码文件:
  - reme/reme.py: ReMe 主 API (add_memory/retrieve_memory/summarize_memory)
  - reme/memory/vector_based/base_memory_agent.py: BaseMemoryAgent 模式
  - reme/memory/vector_based/personal/personal_retriever.py: PersonalRetriever
  - reme/memory/vector_based/personal/personal_summarizer.py: PersonalSummarizer
  - reme/reme_light.py: ReMeLight.pre_reasoning_hook (注入记忆到推理前)

职责:
  1. 拦截 USER_INTENT → 检索相关记忆 → 注入 memory_context → 转发给 Scheduler
  2. 监听 CONVERSATION_DONE → 存储本轮 Q&A → 持久化到 JSON
  3. 提供 compact() — LLM 压缩对话历史

消息流:
  ReceiverAgent → USER_INTENT (target="memory_agent") → MemoryAgent
      → 检索 + 注入 → USER_INTENT (target="scheduler") → SchedulerAgent
"""

import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from loguru import logger

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.memory.store import MemoryEntry, MemoryStore
from mia.memory.retriever import MemoryRetriever
from mia.providers.base import BaseProvider


# ─── 摘要生成 prompt (用于存储记忆时自动生成 summary + keywords) ───

SUMMARY_EXTRACTION_PROMPT = """为以下对话生成一句话摘要 (30字以内) 和 3-5 个关键词。

只返回 JSON:
{{"summary": "用户查询嘉兴天气，得到晴天预报", "keywords": ["嘉兴", "天气", "晴天"]}}

对话内容: {content}"""


# ─── /compact 压缩 prompt ───────────────────────────

COMPACT_PROMPT = """将以下多轮对话历史压缩为一段简短摘要（200字以内），
保留关键信息：人名、地名、数字、重要结论、用户偏好、未完成任务等。
丢弃不必要的闲聊细节。

{memory_text}

请直接输出摘要文本:"""


class MemoryAgent(BaseAgent):
    """记忆管理 Agent — 拦截 USER_INTENT 注入记忆上下文

    参考 ReMe 的设计:
      - ReMe.summarize_memory() → MemoryAgent.compact()
      - ReMe.retrieve_memory() → MemoryAgent._on_user_intent()
      - ReMe.add_memory() → MemoryAgent._store_conversation()
      - ReMeLight.pre_reasoning_hook → _on_user_intent() 的注入逻辑
    """

    MAX_RETRIEVED = 5  # 最多注入 5 条相关记忆

    def __init__(
        self,
        bus: MessageBus,
        provider: BaseProvider,
        store: Optional[MemoryStore] = None,
        model: Optional[str] = None,
        fallback_provider: Optional[BaseProvider] = None,
        fallback_model: Optional[str] = None,
        enable_auto_store: bool = True,
    ):
        """
        Args:
            bus: 消息总线
            provider: LLM Provider (用于摘要生成和相关性判断)
            store: 记忆存储 (不传则使用默认路径)
            model: 模型名
            fallback_provider: 备选 Provider
            fallback_model: 备选模型名
            enable_auto_store: 是否自动存储每轮对话 (默认 True)
        """
        super().__init__(name="memory_agent", bus=bus)
        self.provider = provider
        self.model = model
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model
        self.enable_auto_store = enable_auto_store

        # 初始化存储和检索器
        self.store = store or MemoryStore()

        self.retriever = MemoryRetriever(
            provider=provider,
            fallback_provider=fallback_provider,
            enable_llm_rerank=True,
        )

        # 暂存当前轮的用户意图 (用于 CONVERSATION_DONE 时配对存储)
        self._pending_intent: Optional[str] = None
        self._pending_session_id: Optional[str] = None
        self._pending_original: Optional[str] = None

    # ─── 生命周期 ────────────────────────────────────

    async def on_start(self) -> None:
        """启动时加载持久化记忆"""
        self.store.load()
        logger.info(
            "[MemoryAgent] 已就绪, 记忆: {} 条, provider={}",
            self.store.count,
            self.provider.__class__.__name__,
        )

    async def handle(self, msg: Message) -> None:
        """消息分发 — 处理 USER_INTENT 和 CONVERSATION_DONE"""
        if msg.msg_type == MessageType.USER_INTENT:
            await self._on_user_intent(msg)
        elif msg.msg_type == MessageType.CONVERSATION_DONE:
            await self._on_conversation_done(msg)
        else:
            logger.debug("[MemoryAgent] 忽略消息类型: {}", msg.msg_type)

    # ─── USER_INTENT 处理 ────────────────────────────

    async def _on_user_intent(self, msg: Message) -> None:
        """处理用户意图 — 检索记忆 → 注入上下文 → 转发给 Scheduler

        参考 ReMeLight.pre_reasoning_hook 的注入模式:
          每次推理前检查并注入压缩后的记忆摘要

        Args:
            msg: USER_INTENT 消息
        """
        intent = msg.payload.get("intent", "")
        original = msg.payload.get("original", "")
        session_id = msg.session_id

        # 暂存当前意图 (用于后续存储)
        self._pending_intent = intent
        self._pending_session_id = session_id
        self._pending_original = original

        # ─── 检索相关记忆 ──────────────────────────
        memory_context = ""
        if self.store.count > 0:
            try:
                retrieved = await self.retriever.retrieve(
                    intent=intent,
                    store=self.store,
                    top_k=self.MAX_RETRIEVED,
                )
                if retrieved:
                    memory_context = await self.retriever.summarize_for_context(
                        intent=intent,
                        retrieved=retrieved,
                    )
            except Exception as e:
                logger.warning("[MemoryAgent] 记忆检索失败: {}", e)
                # 降级: 用最近 3 条
                recent = self.store.get_recent(3)
                if recent:
                    memory_context = self.retriever._simple_summary(recent)

        # ─── 结构化展示 ────────────────────────────
        print(f"\033[34m[MemoryAgent]\033[0m 检索记忆")
        print(f"   \033[90m├─\033[0m 意图: {intent[:80]}")
        print(f"   \033[90m├─\033[0m 记忆库: {self.store.count} 条")
        if memory_context:
            print(f"   \033[90m└─\033[0m 注入上下文: {memory_context[:100]}...")
        else:
            print(f"   \033[90m└─\033[0m 无相关记忆")
        print()

        # ─── 构造转发消息 ─────────────────────────
        payload = dict(msg.payload)  # 复制原始 payload
        payload["memory_context"] = memory_context  # 注入记忆上下文

        forward_msg = Message(
            msg_type=MessageType.USER_INTENT,
            source=self.name,
            target="scheduler",
            payload=payload,
            session_id=session_id,
        )

        await self.send(forward_msg)
        logger.info(
            "[MemoryAgent] USER_INTENT 已转发 to scheduler, memory_context_len={}",
            len(memory_context),
        )

    # ─── CONVERSATION_DONE 处理 ──────────────────────

    async def _on_conversation_done(self, msg: Message) -> None:
        """对话完成 — 存储本轮 Q&A 到记忆库

        参考 ReMe.add_memory() 的存储模式:
          为每轮对话创建 user + assistant 两条 MemoryEntry

        Args:
            msg: CONVERSATION_DONE 消息
        """
        if not self.enable_auto_store:
            return

        reply = msg.payload.get("message", "")
        if not reply or not self._pending_intent:
            return

        # ─── 生成摘要和关键词 (带超时保护) ────────
        # MiMo 有时很慢 (25s+)，用 asyncio.wait_for 保护
        summary = ""
        keywords: list[str] = []

        try:
            summary, keywords = await asyncio.wait_for(
                self._generate_summary(
                    user_content=self._pending_intent,
                    assistant_content=reply,
                ),
                timeout=10.0,  # 10 秒超时，超时则降级
            )
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
            # 注意: CancelledError 是 BaseException 不是 Exception，必须显式捕获
            if isinstance(e, asyncio.TimeoutError):
                logger.warning("[MemoryAgent] 摘要生成超时 (10s)，使用降级")
            elif isinstance(e, asyncio.CancelledError):
                logger.warning("[MemoryAgent] 摘要被取消 (系统关闭)，使用降级")
            else:
                logger.warning("[MemoryAgent] 摘要生成失败: {}", e)
            # 降级: 用简单截断
            summary = self._pending_intent[:50]
            keywords = []

        # ─── 存储 User 条目 ─────────────────────────
        user_entry = MemoryEntry(
            role="user",
            content=self._pending_original or self._pending_intent,
            summary=f"用户: {summary}" if summary else self._pending_intent[:60],
            keywords=keywords,
            importance=0.6,
            session_id=self._pending_session_id or "",
        )
        self.store.add(user_entry)

        # ─── 存储 Assistant 条目 ────────────────────
        assistant_entry = MemoryEntry(
            role="assistant",
            content=reply,
            summary=f"助手: {summary}" if summary else reply[:60],
            keywords=keywords,
            importance=0.6,
            session_id=self._pending_session_id or "",
        )
        self.store.add(assistant_entry)

        logger.info(
            "[MemoryAgent] 记忆已存储: user+assistant, total={}",
            self.store.count,
        )

        # 清理暂存
        self._pending_intent = None
        self._pending_session_id = None
        self._pending_original = None

    # ─── /compact 压缩 ─────────────────────────────

    async def compact(self) -> str:
        """压缩对话历史 — 调用 LLM 将多轮对话总结为摘要

        参考 ReMe ReMeLight.compact_memory() 的压缩模式:
          将多条记忆条目合并为一条 system 摘要

        Returns:
            压缩后的摘要文本

        Raises:
            RuntimeError: LLM 调用失败时抛出
        """
        entries = self.store.get_all()
        if not entries:
            return "对话历史为空，无需压缩。"

        # 构建记忆文本
        parts = []
        for entry in entries:
            role_label = {
                "user": "用户",
                "assistant": "助手",
                "system": "📋",
            }.get(entry.role, entry.role)
            parts.append(f"[{role_label}] {entry.content}")
        memory_text = "\n".join(parts)

        prompt = COMPACT_PROMPT.format(memory_text=memory_text)
        messages = [{"role": "user", "content": prompt}]

        # 调用 LLM 压缩
        summary = await self._call_llm_with_fallback(messages, max_tokens=512, temperature=0.3)

        if not summary:
            raise RuntimeError("压缩对话历史失败: 主备 Provider 均不可用")

        original_count = self.store.count

        # 用压缩摘要替换所有记忆
        self.store.compact(summary.strip(), source_session_ids=None)

        logger.info(
            "[MemoryAgent] 对话历史已压缩: {} 条 → 1 条摘要",
            original_count,
        )
        return summary.strip()

    # ─── 辅助方法 ──────────────────────────────────

    async def _generate_summary(
        self,
        user_content: str,
        assistant_content: str,
    ) -> tuple[str, list[str]]:
        """调用 LLM 生成对话摘要和关键词

        Returns:
            (summary, keywords) 元组
        """
        content = f"用户: {user_content}\n助手: {assistant_content}"
        prompt = SUMMARY_EXTRACTION_PROMPT.format(content=content)
        messages = [{"role": "user", "content": prompt}]

        response = await self._call_llm_with_fallback(messages, max_tokens=128, temperature=0.3)
        if not response:
            # LLM 失败时直接降级，不抛异常 (避免触发上层重试)
            logger.debug("[MemoryAgent] 摘要 LLM 不可用，使用简单截断")
            return (user_content[:50], [])

        # 解析 JSON
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return (
                data.get("summary", user_content[:50]),
                data.get("keywords", []),
            )

        # 降级
        return (user_content[:50], [])

    async def _call_llm_with_fallback(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """调用 LLM (主 Provider + 备选 fallback)

        参考 SchedulerAgent 和 TaskAgent 的 fallback 模式

        Returns:
            LLM 响应文本，失败返回 None
        """
        # 尝试主 Provider
        try:
            return await self.provider.chat_sync(
                messages=messages,
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:
            logger.warning("[MemoryAgent] 主 Provider 失败: {}. 尝试备选...", e)

        # 尝试备选 Provider
        if self.fallback_provider:
            try:
                return await self.fallback_provider.chat_sync(
                    messages=messages,
                    model=self.fallback_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as e:
                logger.error("[MemoryAgent] 备选 Provider 也失败: {}", e)

        return None
