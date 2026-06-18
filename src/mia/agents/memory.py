"""
MemoryAgent — 知识记忆管理 Agent

基于 linninpaw 的分层记忆设计:
  - Level 1 (Working Memory): 每轮对话后实时提取原子知识，存内存
  - Level 2 (Persistent Knowledge): 换日或 /compact 时 LLM 合并去重 → 持久化

设计参考:
  - linninpaw file_memory_manager.py: 原始日志 + MEMORY.md 分层
  - linninpaw base_memory_manager.py: 异步总结队列 + dream 优化
  - linninpaw prompts.py: DREAM_OPTIMIZATION 提示词 (精简/去重/合并/废弃)

职责:
  1. 拦截 USER_INTENT → 检索相关记忆 → 注入 memory_context → 转发给 Scheduler
  2. 监听 CONVERSATION_DONE → 缓冲对话 + 实时提取临时知识
  3. 换日检测 → LLM 合并去重 → 持久化到 MemoryStore
  4. 提供 compact() — 手动触发知识合并持久化

消息流:
  ReceiverAgent → USER_INTENT (target="memory_agent") → MemoryAgent
      → 检索 (working + persistent) → 注入 → USER_INTENT (target="scheduler")
"""

import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from loguru import logger

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.memory.store import (
    KnowledgeEntry,
    MemoryStore,
    _today_str,
    _now_beijing,
    CATEGORY_FACT,
    CATEGORY_PREFERENCE,
    CATEGORY_DECISION,
    CATEGORY_TASK,
    CATEGORY_INSIGHT,
)
from mia.memory.retriever import MemoryRetriever
from mia.providers.base import BaseProvider


# ─── Level 1: 临时知识提取 prompt (轻量级, 从单轮对话提取原子知识) ───

WORKING_KNOWLEDGE_PROMPT = """从以下单轮对话中提取 1-3 条原子知识。每条知识应该是一个独立的事实、偏好、决策、任务或洞察。

规则:
- 不要复述对话内容，要提炼出可复用的知识
- 每条知识是自包含的，不依赖上下文就能理解
- category 从以下选择: fact(事实), preference(偏好), decision(决策), task(任务), insight(洞察)
- importance 0.0-1.0: 临时闲聊 0.2, 用户偏好 0.7, 重要决策 0.9
- confidence 固定 0.5 (临时知识需要后续验证)

只返回 JSON 数组:
[{{"content": "用户偏好使用中文进行技术交流", "category": "preference", "keywords": ["中文", "偏好"], "importance": 0.7}}]

用户消息: {user_msg}

助手回复: {assistant_reply}"""


# ─── Level 2: 知识合并去重 prompt (重量级, 换日/compact 时使用) ───

CONSOLIDATION_PROMPT = """你是一个知识管理助手。将以下临时记忆和原始对话合并提炼为持久知识条目。

合并规则:
1. 合并: 相同主题的多条临时知识合并为一条，保留最完整的信息
2. 去重: 重复的事实/偏好只保留最新的版本
3. 更新: 如果新信息与旧知识冲突，用新信息覆盖 (用户偏好可能改变)
4. 废弃: 过时、不再相关、或价值低的信息丢弃 (如问候、道别)
5. 置信度: 多次出现的知识置信度更高 (临时默认 0.5, 合并后至少 0.7)
6. 精简: 删除流水账，仅保留核心决策、确认的用户偏好、高价值可复用经验

输入:
- 临时记忆 (已从每轮对话中初步提取):
{temporary_knowledge}

- 原始对话 (保留上下文用于判断):
{raw_conversations}

只返回 JSON 数组 (5-15 条):
[{{"content": "...", "category": "fact|preference|decision|task|insight", "confidence": 0.0-1.0, "keywords": [...], "importance": 0.0-1.0, "source_sessions": ["session_id1"]}}]"""


# ─── /compact 压缩 prompt ───────────────────────────

COMPACT_PROMPT = """将以下知识条目压缩为一段简短摘要（200字以内），
保留关键信息：用户偏好、重要决策、未完成任务、核心事实等。
丢弃不重要的细节。

{knowledge_text}

请直接输出摘要文本:"""


class MemoryAgent(BaseAgent):
    """知识记忆管理 Agent — 两级记忆梯度

    Level 1 - 临时记忆 (Working Memory):
      - 每轮对话后实时提取 1-3 条原子知识
      - 存内存，立即可被检索
      - confidence 默认 0.5 (临时)

    Level 2 - 持久知识 (Persistent Knowledge):
      - 换日或 /compact 时触发
      - LLM 合并去重 Level 1 的临时知识
      - 持久化到 MemoryStore 磁盘
      - confidence >= 0.7 (持久)
    """

    MAX_RETRIEVED = 5           # 最多注入 5 条相关记忆
    MAX_WORKING_ENTRIES = 30    # 临时记忆上限 (触发强制合并)
    EXTRACTION_TIMEOUT = 8.0    # 临时提取超时秒数
    CONSOLIDATION_TIMEOUT = 30.0  # 合并去重超时秒数

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
            provider: LLM Provider (用于知识提取和合并)
            store: 记忆存储 (不传则使用默认路径)
            model: 模型名
            fallback_provider: 备选 Provider
            fallback_model: 备选模型名
            enable_auto_store: 是否启用自动知识提取 (默认 True)
        """
        super().__init__(name="memory_agent", bus=bus)
        self.provider = provider
        self.model = model
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model
        self.enable_auto_store = enable_auto_store

        # 持久存储 (Level 2)
        self.store = store or MemoryStore()

        # 检索器
        self.retriever = MemoryRetriever(
            provider=provider,
            fallback_provider=fallback_provider,
            enable_llm_rerank=True,
        )

        # ─── Level 1: 临时记忆 (内存) ──────────────
        self._working_memory: list[KnowledgeEntry] = []

        # ─── 原始对话缓冲 (用于合并时 LLM 有完整上下文) ──
        self._daily_buffer: list[dict] = []

        # ─── 当前日期 (用于换日检测) ──────────────
        self._current_date: str = _today_str()

        # ─── 当前轮用户意图暂存 (用于 CONVERSATION_DONE 时配对) ──
        self._pending_intent: Optional[str] = None
        self._pending_session_id: Optional[str] = None
        self._pending_original: Optional[str] = None

    # ─── 生命周期 ────────────────────────────────────

    async def on_start(self) -> None:
        """启动时加载持久化知识"""
        self.store.load()
        self._current_date = _today_str()
        logger.info(
            "[MemoryAgent] 已就绪, 持久知识: {} 条, 临时记忆: {} 条, provider={}",
            self.store.count,
            len(self._working_memory),
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
        """处理用户意图 — 检索记忆 (working + persistent) → 注入上下文 → 转发

        Args:
            msg: USER_INTENT 消息
        """
        intent = msg.payload.get("intent", "")
        original = msg.payload.get("original", "")
        session_id = msg.session_id

        # 暂存当前意图 (用于后续知识提取)
        self._pending_intent = intent
        self._pending_session_id = session_id
        self._pending_original = original

        # ─── 检测换日 ──────────────────────────────
        today = _today_str()
        if today != self._current_date:
            logger.info(
                "[MemoryAgent] 检测到换日: {} → {}，触发合并",
                self._current_date, today,
            )
            await self._consolidate_daily()
            self._current_date = today

        # ─── 检索相关记忆 (working + persistent 合并) ──
        memory_context = ""
        total_available = self.store.count + len(self._working_memory)

        if total_available > 0:
            try:
                retrieved = await self._retrieve_merged(
                    intent=intent,
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
                recent = self._get_recent_merged(3)
                if recent:
                    memory_context = self.retriever._simple_summary(recent)

        # ─── 结构化展示 ────────────────────────────
        print(f"\033[34m[MemoryAgent]\033[0m 检索记忆")
        print(f"   \033[90m├─\033[0m 意图: {intent[:80]}")
        print(f"   \033[90m├─\033[0m 持久知识: {self.store.count} 条")
        print(f"   \033[90m├─\033[0m 临时记忆: {len(self._working_memory)} 条")
        if memory_context:
            print(f"   \033[90m└─\033[0m 注入上下文: {memory_context[:100]}...")
        else:
            print(f"   \033[90m└─\033[0m 无相关记忆")
        print()

        # ─── 构造转发消息 ─────────────────────────
        payload = dict(msg.payload)
        payload["memory_context"] = memory_context

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
        """对话完成 — 缓冲对话 + 实时提取临时知识 (Level 1)

        Args:
            msg: CONVERSATION_DONE 消息
        """
        if not self.enable_auto_store:
            return

        reply = msg.payload.get("message", "")
        if not reply or not self._pending_intent:
            return

        session_id = self._pending_session_id

        # ─── 1. 追加原始对话到缓冲 (用于合并时的完整上下文) ──
        self._daily_buffer.append({
            "user": self._pending_original or self._pending_intent,
            "assistant": reply,
            "session_id": session_id,
            "timestamp": _now_beijing(),
        })

        # ─── 2. 实时提取临时知识 (Level 1) ─────────
        try:
            extracted = await asyncio.wait_for(
                self._extract_working_knowledge(
                    user_msg=self._pending_intent,
                    assistant_reply=reply,
                    session_id=session_id,
                ),
                timeout=self.EXTRACTION_TIMEOUT,
            )
            if extracted:
                self._working_memory.extend(extracted)
                logger.info(
                    "[MemoryAgent] Level 1 临时知识已提取: {} 条, working_total={}",
                    len(extracted), len(self._working_memory),
                )
        except asyncio.TimeoutError:
            logger.warning("[MemoryAgent] 临时知识提取超时 ({:.0f}s)，跳过本轮",
                           self.EXTRACTION_TIMEOUT)
        except asyncio.CancelledError:
            logger.warning("[MemoryAgent] 临时知识提取被取消")
        except Exception as e:
            logger.warning("[MemoryAgent] 临时知识提取失败: {}", e)

        # ─── 3. 检查是否需要强制合并 ──────────────
        if len(self._working_memory) >= self.MAX_WORKING_ENTRIES:
            logger.info(
                "[MemoryAgent] 临时记忆达到上限 ({} >= {})，触发强制合并",
                len(self._working_memory), self.MAX_WORKING_ENTRIES,
            )
            await self._consolidate_daily()

        # ─── 4. 清理暂存 ───────────────────────────
        self._pending_intent = None
        self._pending_session_id = None
        self._pending_original = None

    # ═══════════════════════════════════════════════════════
    # Level 1: 临时知识提取
    # ═══════════════════════════════════════════════════════

    async def _extract_working_knowledge(
        self,
        user_msg: str,
        assistant_reply: str,
        session_id: str,
    ) -> list[KnowledgeEntry]:
        """从单轮对话中提取临时知识 (Level 1)

        调用 LLM 提取 1-3 条原子知识，confidence 默认 0.5。

        Args:
            user_msg: 用户消息
            assistant_reply: 助手回复
            session_id: 会话 ID

        Returns:
            提取的知识条目列表
        """
        prompt = WORKING_KNOWLEDGE_PROMPT.format(
            user_msg=user_msg[:500],
            assistant_reply=assistant_reply[:500],
        )
        messages = [{"role": "user", "content": prompt}]

        response = await self._call_llm_with_fallback(
            messages, max_tokens=384, temperature=0.3,
        )
        if not response:
            logger.debug("[MemoryAgent] Level 1 LLM 不可用，跳过临时提取")
            return []

        # 解析 JSON 数组
        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if not json_match:
            logger.debug("[MemoryAgent] Level 1 响应无 JSON 数组: {}", response[:100])
            return []

        try:
            data = json.loads(json_match.group(0))
            if not isinstance(data, list):
                return []
        except json.JSONDecodeError as e:
            logger.debug("[MemoryAgent] Level 1 JSON 解析失败: {}", e)
            return []

        # 构造 KnowledgeEntry
        entries = []
        for item in data[:5]:  # 最多 5 条 (prompt 说 1-3, 防御)
            if not isinstance(item, dict):
                continue
            content = item.get("content", "").strip()
            if not content or len(content) < 3:
                continue

            category = item.get("category", CATEGORY_FACT)
            entries.append(KnowledgeEntry(
                content=content,
                category=category,
                confidence=0.5,  # 临时知识默认置信度
                keywords=item.get("keywords", []),
                importance=float(item.get("importance", 0.5)),
                source_sessions=[session_id],
            ))

        return entries

    # ═══════════════════════════════════════════════════════
    # Level 2: 知识合并去重 (持久化)
    # ═══════════════════════════════════════════════════════

    async def _consolidate_daily(self) -> None:
        """合并提炼临时记忆 → 持久知识 (Level 2)

        将 _working_memory + _daily_buffer 提交给 LLM，
        合并去重后持久化到 MemoryStore。
        """
        if not self._working_memory and not self._daily_buffer:
            logger.debug("[MemoryAgent] 无需合并: working 和 buffer 均为空")
            return

        logger.info(
            "[MemoryAgent] 开始 Level 2 合并: working={}, buffer={} 轮对话",
            len(self._working_memory), len(self._daily_buffer),
        )

        # ─── 构建临时知识文本 ──────────────────────
        if self._working_memory:
            wk_parts = []
            for i, entry in enumerate(self._working_memory):
                wk_parts.append(
                    f"{i+1}. [{entry.category_label}] {entry.content} "
                    f"(importance={entry.importance:.1f})"
                )
            temp_knowledge_text = "\n".join(wk_parts)
        else:
            temp_knowledge_text = "(无临时记忆)"

        # ─── 构建原始对话文本 ──────────────────────
        if self._daily_buffer:
            conv_parts = []
            for i, turn in enumerate(self._daily_buffer):
                conv_parts.append(
                    f"--- 对话 {i+1} (session: {turn['session_id']}) ---\n"
                    f"用户: {turn['user'][:300]}\n"
                    f"助手: {turn['assistant'][:300]}"
                )
            raw_text = "\n\n".join(conv_parts)
        else:
            raw_text = "(无原始对话)"

        # ─── 调用 LLM 合并去重 ─────────────────────
        prompt = CONSOLIDATION_PROMPT.format(
            temporary_knowledge=temp_knowledge_text,
            raw_conversations=raw_text,
        )
        messages = [{"role": "user", "content": prompt}]

        try:
            response = await asyncio.wait_for(
                self._call_llm_with_fallback(
                    messages, max_tokens=2048, temperature=0.3,
                ),
                timeout=self.CONSOLIDATION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("[MemoryAgent] Level 2 合并超时 ({:.0f}s)，降级为直接存储",
                           self.CONSOLIDATION_TIMEOUT)
            # 降级: 直接将临时记忆持久化 (不合并)
            await self._fallback_persist()
            return
        except Exception as e:
            logger.warning("[MemoryAgent] Level 2 合并失败: {}，降级为直接存储", e)
            await self._fallback_persist()
            return

        if not response:
            logger.warning("[MemoryAgent] Level 2 LLM 不可用，降级为直接存储")
            await self._fallback_persist()
            return

        # ─── 解析合并后的知识 ───────────────────────
        import re
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if not json_match:
            logger.warning("[MemoryAgent] Level 2 响应无 JSON 数组，降级为直接存储")
            await self._fallback_persist()
            return

        try:
            data = json.loads(json_match.group(0))
            if not isinstance(data, list):
                raise ValueError("不是数组")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("[MemoryAgent] Level 2 JSON 解析失败: {}，降级为直接存储", e)
            await self._fallback_persist()
            return

        # ─── 持久化合并后的知识 ────────────────────
        consolidated_count = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            content = item.get("content", "").strip()
            if not content or len(content) < 3:
                continue

            entry = KnowledgeEntry(
                content=content,
                category=item.get("category", CATEGORY_FACT),
                confidence=max(0.7, float(item.get("confidence", 0.7))),
                keywords=item.get("keywords", []),
                importance=float(item.get("importance", 0.6)),
                source_sessions=item.get("source_sessions", []),
            )
            self.store.add(entry)
            consolidated_count += 1

        logger.info(
            "[MemoryAgent] Level 2 合并完成: {} 条临时记忆 + {} 轮对话 → {} 条持久知识",
            len(self._working_memory), len(self._daily_buffer), consolidated_count,
        )

        # ─── 清空临时记忆和缓冲 ────────────────────
        self._working_memory.clear()
        self._daily_buffer.clear()

    async def _fallback_persist(self) -> None:
        """降级方案: 直接将临时记忆持久化到 store (不合并)"""
        count = 0
        for entry in self._working_memory:
            # 持久化时提升置信度
            entry.confidence = max(0.6, entry.confidence)
            self.store.add(entry)
            count += 1

        logger.info(
            "[MemoryAgent] 降级持久化: {} 条临时记忆 → store, total={}",
            count, self.store.count,
        )

        self._working_memory.clear()
        self._daily_buffer.clear()

    # ═══════════════════════════════════════════════════════
    # 合并检索 (working + persistent)
    # ═══════════════════════════════════════════════════════

    async def _retrieve_merged(
        self,
        intent: str,
        top_k: int = 5,
    ) -> list[KnowledgeEntry]:
        """合并检索 — working memory (热) + persistent store (冷)

        working memory 时效性高但置信度低，
        persistent store 置信度高但可能不够新鲜。
        合并后按综合分数排序。

        Args:
            intent: 用户意图
            top_k: 返回条数

        Returns:
            合并后的知识条目列表
        """
        results = []

        # ─── 搜索持久知识 (Level 2) ──────────────────
        if self.store.count > 0:
            try:
                persistent_results = await self.retriever.retrieve(
                    intent=intent,
                    store=self.store,
                    top_k=top_k,
                )
                results.extend(persistent_results)
            except Exception as e:
                logger.warning("[MemoryAgent] 持久知识检索失败: {}", e)

        # ─── 搜索临时记忆 (Level 1) ──────────────────
        if self._working_memory:
            try:
                working_results = self.retriever._keyword_match(
                    keywords=await self.retriever._extract_keywords(intent),
                    entries=self._working_memory,
                )
                # 临时记忆加分 (时效性)
                for entry in working_results:
                    entry.importance = min(1.0, entry.importance + 0.1)
                results.extend(working_results[:top_k])
            except Exception as e:
                logger.warning("[MemoryAgent] 临时记忆检索失败: {}", e)

        # ─── 去重 + 排序 ────────────────────────────
        seen_ids = set()
        unique_results = []
        for entry in results:
            if entry.id not in seen_ids:
                seen_ids.add(entry.id)
                unique_results.append(entry)

        # 按 (importance, confidence) 综合排序
        unique_results.sort(
            key=lambda e: (e.importance * 0.6 + e.confidence * 0.4),
            reverse=True,
        )

        return unique_results[:top_k]

    def _get_recent_merged(self, n: int) -> list[KnowledgeEntry]:
        """获取最近 N 条记忆 (working + persistent 合并)"""
        results = list(self._working_memory[-n:])
        if self.store.count > 0:
            results.extend(self.store.get_recent(n))
        # 去重 + 按时间排序
        seen_ids = set()
        unique_results = []
        for entry in results:
            if entry.id not in seen_ids:
                seen_ids.add(entry.id)
                unique_results.append(entry)
        unique_results.sort(key=lambda e: e.created_at, reverse=True)
        return unique_results[:n]

    # ═══════════════════════════════════════════════════════
    # /compact 压缩
    # ═══════════════════════════════════════════════════════

    async def compact(self) -> str:
        """压缩持久知识 — 先合并临时记忆，再压缩所有知识为摘要

        Returns:
            压缩后的摘要文本

        Raises:
            RuntimeError: LLM 调用失败时抛出
        """
        # 先合并临时记忆
        if self._working_memory or self._daily_buffer:
            await self._consolidate_daily()

        entries = self.store.get_all()
        if not entries:
            return "知识库为空，无需压缩。"

        # 构建知识文本
        parts = []
        for entry in entries:
            parts.append(
                f"[{entry.category_label}] {entry.content} "
                f"(confidence={entry.confidence:.1f}, importance={entry.importance:.1f})"
            )
        knowledge_text = "\n".join(parts)

        prompt = COMPACT_PROMPT.format(knowledge_text=knowledge_text)
        messages = [{"role": "user", "content": prompt}]

        summary = await self._call_llm_with_fallback(
            messages, max_tokens=512, temperature=0.3,
        )

        if not summary:
            raise RuntimeError("压缩知识失败: 主备 Provider 均不可用")

        original_count = self.store.count
        self.store.compact(summary.strip(), source_session_ids=None)

        logger.info(
            "[MemoryAgent] 知识已压缩: {} 条 → 1 条摘要",
            original_count,
        )
        return summary.strip()

    # ═══════════════════════════════════════════════════════
    # LLM 调用辅助
    # ═══════════════════════════════════════════════════════

    async def _call_llm_with_fallback(
        self,
        messages: list[dict],
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> Optional[str]:
        """调用 LLM (主 Provider + 备选 fallback)

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
