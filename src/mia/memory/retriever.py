"""
MemoryRetriever — 记忆检索器

参考 ReMe 的检索设计：
  - ReMe: 向量 + BM25 混合检索 (vector_weight=0.7, bm25=0.3)
  - MIA: 两阶段检索 — 索引扫描 + 按需加载 (无向量数据库依赖)

检索流程 (两阶段):
  Phase 1: scan_index() → 扫 index.json 的日摘要定位相关日期
  Phase 2: load_day() → 只加载相关日期的 daily 文件 → 关键词匹配 + LLM 重排序
  Phase 3: summarize_for_context() → 生成精炼上下文摘要注入 Scheduler

降级策略:
  - 索引无匹配 → 加载最近 3 天
  - LLM 关键词提取失败 → 简单分词
  - LLM 相关性评分失败 → 仅用关键词排序
  - LLM 摘要生成失败 → 简单拼接

同时参考了:
  - reme/memory/file_based/tools/memory_search.py (MemorySearch tool)
  - reme/extension/procedural_memory/retrieve/memory_retrieval.py
"""

import asyncio
from typing import Optional

from loguru import logger

from mia.memory.store import MemoryEntry, MemoryStore
from mia.providers.base import BaseProvider


# ─── 关键词提取 prompt ──────────────────────────────

KEYWORD_EXTRACTION_PROMPT = """从以下用户问题中提取 3-5 个关键词，用于检索相关的历史对话记忆。
关键词应该是名词、动词或短语，覆盖主题、实体、动作等。
只返回 JSON: {"keywords": ["kw1", "kw2", "kw3"]}

用户问题: {intent}"""


# ─── 相关性判断 prompt ─────────────────────────────

RELEVANCE_PROMPT = """判断以下历史记忆是否与用户当前问题相关。
返回 0.0 到 1.0 之间的相关性分数 (浮点数)。

当前问题: {intent}

历史记忆:
- 角色: {role}
- 摘要: {summary}
- 内容: {content}

只返回数字 (如 0.85):"""


# ─── 上下文摘要生成 prompt ──────────────────────────

CONTEXT_SUMMARY_PROMPT = """你是一个记忆摘要生成器。根据用户当前问题和检索到的相关历史记忆，
生成一段简洁的上下文摘要 (100字以内)，帮助 AI 理解对话背景。

当前问题: {intent}

相关历史记忆:
{memories_text}

请生成上下文摘要，直接输出文本，不要加前缀:"""


class MemoryRetriever:
    """记忆检索器 — 关键词 + LLM 混合检索

    MVP 策略 (无向量数据库):
      1. 关键词重叠匹配 (快速初筛)
      2. LLM 相关性评分 (精确过滤，可选)
      3. 按重要性+时间 综合排序
      4. 返回 top_k 条

    未来可扩展: embedding + 向量检索 (参考 ReMe 的 VectorStore 抽象层)
    """

    # 快速检索的最大候选数
    MAX_CANDIDATES = 30

    def __init__(
        self,
        provider: Optional[BaseProvider] = None,
        fallback_provider: Optional[BaseProvider] = None,
        enable_llm_rerank: bool = True,
    ):
        """
        Args:
            provider: LLM Provider (用于关键词提取和相关性评分)
            fallback_provider: 备选 Provider
            enable_llm_rerank: 是否启用 LLM 相关性评分 (关闭则只用关键词匹配)
        """
        self.provider = provider
        self.fallback_provider = fallback_provider
        self.enable_llm_rerank = enable_llm_rerank

    # ─── 公开 API ───────────────────────────────────

    async def retrieve(
        self,
        intent: str,
        store: MemoryStore,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """检索与用户意图最相关的历史记忆 — 两阶段检索

        Phase 1: 扫索引 (scan_index) → 定位相关日期 (O(天数), ~90 条)
        Phase 2: 按需加载 (load_day) → 只加载相关日文件 → 关键词匹配 + LLM 重排序

        降级: 索引无匹配 → 加载最近 3 天

        Args:
            intent: 用户意图描述
            store: 记忆存储 (分级存储)
            top_k: 返回条数

        Returns:
            相关记忆列表 (按相关性排序)
        """
        if store.count == 0:
            return []

        # 阶段 1: 关键词提取 (调用 LLM)
        keywords = await self._extract_keywords(intent)
        if not keywords:
            # LLM 失败，降级: 从 intent 中简单分词
            keywords = self._simple_tokenize(intent)

        logger.debug("[MemoryRetriever] 关键词: {}", keywords)

        # 阶段 2: 扫索引 → 定位相关日期 (两阶段检索核心)
        relevant_dates = store.scan_index(keywords, limit=7)

        # 阶段 3: 按需加载相关日文件 → 收集候选条目
        candidates = []
        for date in relevant_dates:
            candidates.extend(store.load_day(date))

        if not candidates:
            # 索引无匹配，降级: 加载最近 3 天
            for date in store.get_recent_dates(3):
                candidates.extend(store.load_day(date))
            logger.debug(
                "[MemoryRetriever] 索引无匹配，降级到最近 {} 天, {} 条",
                len(store.get_recent_dates(3)), len(candidates),
            )

        # 阶段 4: 关键词重叠匹配 → 快速初筛
        candidates = self._keyword_match(keywords, candidates)

        if not candidates:
            # 仍未匹配，回退到最近 N 条
            candidates = store.get_recent(top_k * 2)
            logger.debug("[MemoryRetriever] 关键词无匹配，回退到最近 {} 条", len(candidates))

        # 阶段 5: LLM 相关性评分 (可选)
        if self.enable_llm_rerank and len(candidates) > top_k and self.provider:
            try:
                candidates = await self._llm_rerank(intent, candidates, top_k)
            except Exception as e:
                logger.warning("[MemoryRetriever] LLM 相关性评分失败: {}, 使用关键词排序", e)

        # 阶段 6: Top-K
        results = candidates[:top_k]
        logger.info(
            "[MemoryRetriever] 检索完成: {} 条候选 → {} 条结果 (扫了 {} 天索引)",
            len(candidates),
            len(results),
            len(relevant_dates),
        )
        return results

    async def summarize_for_context(
        self,
        intent: str,
        retrieved: list[MemoryEntry],
    ) -> str:
        """将检索到的记忆生成精炼的上下文摘要

        参考 ReMe ReMeLight.pre_reasoning_hook 的注入模式:
          将记忆总结为一段文本，注入到 LLM 上下文的最前面

        Args:
            intent: 用户当前意图
            retrieved: 检索到的记忆列表

        Returns:
            上下文摘要文本 (可直接注入 Scheduler LLM context)
        """
        if not retrieved:
            return ""

        # 简单情况: 只有 1-2 条记忆，直接用模板拼接
        if len(retrieved) <= 2:
            parts = ["## 相关历史记忆"]
            for entry in retrieved:
                role_label = {
                    "user": "用户",
                    "assistant": "助手",
                    "system": "📋",
                }.get(entry.role, entry.role)
                parts.append(f"- [{role_label}] {entry.content[:200]}")
            return "\n".join(parts)

        # 多条记忆: 调用 LLM 生成精炼摘要
        if self.provider:
            try:
                return await self._llm_summarize(intent, retrieved)
            except Exception as e:
                logger.warning("[MemoryRetriever] LLM 摘要生成失败: {}, 降级为简单拼接", e)

        # 降级: 简单拼接
        return self._simple_summary(retrieved)

    # ─── 私有方法 ────────────────────────────────────

    async def _extract_keywords(self, intent: str) -> list[str]:
        """调用 LLM 提取关键词 (用于检索)

        失败时降级为简单分词
        """
        if not self.provider:
            return self._simple_tokenize(intent)

        prompt = KEYWORD_EXTRACTION_PROMPT.format(intent=intent)

        try:
            response = await self.provider.chat_sync(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=128,
                temperature=0.1,
            )
            # 解析 JSON (兼容 LLM 可能返回的多种格式)
            import json
            import re

            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    logger.debug("[MemoryRetriever] JSON 解析失败: {}", json_match.group(0)[:100])
                    raise
                # 兼容 "keyword" (单数) 和 "keywords" (复数) 两种 key
                keywords_list = data.get("keywords") or data.get("keyword") or []
                if isinstance(keywords_list, list):
                    return keywords_list
                logger.debug("[MemoryRetriever] keywords 不是列表: {}", type(keywords_list))

        except Exception as e:
            logger.warning("[MemoryRetriever] 关键词提取失败: {}", e)

        return self._simple_tokenize(intent)

    @staticmethod
    def _simple_tokenize(text: str) -> list[str]:
        """简单中文分词 — 降级方案

        按常见分隔符切分，取长度 >= 2 的词
        """
        import re
        # 按空格、标点、数字切分
        tokens = re.findall(r'[一-鿿\w]{2,}', text)
        # 过滤停用词
        stopwords = {"用户问", "用户说", "请问", "帮我", "我想", "可以", "什么", "怎么", "如何", "这是", "那个", "这个"}
        return [t for t in tokens if t not in stopwords][:5]

    def _keyword_match(
        self,
        keywords: list[str],
        entries: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """关键词重叠匹配 — 快速初筛

        参考 ReMe 的 BM25 关键词匹配部分 (权重 0.3)
        """
        if not keywords:
            return list(reversed(entries[-self.MAX_CANDIDATES:]))

        scored = []
        for entry in entries:
            # 在 keywords、summary、content 中匹配
            searchable = (
                " ".join(entry.keywords) + " " +
                entry.summary + " " +
                entry.content
            ).lower()

            overlap = sum(
                1 for kw in keywords
                if kw.lower() in searchable
            )
            if overlap > 0:
                # 评分: 关键词重叠数 + 重要性 + 时间衰减
                score = overlap * 2.0 + entry.importance
                scored.append((score, entry))

        # 按分数降序排序
        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:self.MAX_CANDIDATES]]

    async def _llm_rerank(
        self,
        intent: str,
        candidates: list[MemoryEntry],
        top_k: int,
    ) -> list[MemoryEntry]:
        """LLM 相关性评分 — 精确过滤

        参考 ReMe 的 rerank_memory 模式:
          reme/extension/procedural_memory/retrieve/rerank_memory.py
        """
        # 对每个候选调用 LLM 评分 (最多 10 条)
        max_to_judge = min(len(candidates), 10)

        async def judge_one(entry: MemoryEntry) -> tuple[float, MemoryEntry]:
            prompt = RELEVANCE_PROMPT.format(
                intent=intent,
                role=entry.role,
                summary=entry.summary,
                content=entry.content[:300],
            )
            try:
                response = await self.provider.chat_sync(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=16,
                    temperature=0.1,
                )
                score = float(response.strip())
                return (score, entry)
            except Exception:
                return (0.0, entry)

        # 并发评分
        tasks = [judge_one(entry) for entry in candidates[:max_to_judge]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored = []
        for result in results:
            if isinstance(result, tuple):
                scored.append(result)
            else:
                logger.debug("[MemoryRetriever] 评分失败: {}", result)

        # 综合排序: LLM 相关性 + 重要性 + 时间
        scored.sort(
            key=lambda x: (x[0], x[1].importance),
            reverse=True,
        )
        return [entry for _, entry in scored[:top_k]]

    async def _llm_summarize(
        self,
        intent: str,
        retrieved: list[MemoryEntry],
    ) -> str:
        """调用 LLM 生成精炼的上下文摘要"""
        # 构建记忆文本
        memory_text_parts = []
        for i, entry in enumerate(retrieved):
            role_label = {
                "user": "用户",
                "assistant": "助手",
                "system": "📋",
            }.get(entry.role, entry.role)
            memory_text_parts.append(
                f"{i+1}. [{role_label}] {entry.content[:300]}"
            )
        memories_text = "\n".join(memory_text_parts)

        prompt = CONTEXT_SUMMARY_PROMPT.format(
            intent=intent,
            memories_text=memories_text,
        )

        try:
            summary = await self.provider.chat_sync(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256,
                temperature=0.3,
            )
            return f"## 相关历史记忆\n{summary.strip()}"

        except Exception as e:
            logger.warning("[MemoryRetriever] LLM 摘要失败: {}", e)
            raise

    @staticmethod
    def _simple_summary(retrieved: list[MemoryEntry]) -> str:
        """简单拼接摘要 — 降级方案"""
        parts = ["## 相关历史记忆"]
        for entry in retrieved:
            role_label = {
                "user": "用户",
                "assistant": "助手",
                "system": "📋",
            }.get(entry.role, entry.role)
            parts.append(f"- [{role_label}] {entry.summary or entry.content[:150]}")
        return "\n".join(parts)
