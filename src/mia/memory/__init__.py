"""
MIA Memory Package — 知识记忆管理模块

基于 linninpaw 的分层记忆设计：
  - 原始日志 → 精选记忆 → 梦境优化
  - KnowledgeEntry: 提炼后的原子知识（非原始消息）

两级记忆梯度 (v3):
  - Level 1 (Working Memory): 每轮对话实时提取，存内存，立即可检索
  - Level 2 (Persistent Knowledge): 换日/compact 合并去重，持久化到磁盘
  - store.py: KnowledgeEntry + DaySummary + MemoryStore (index + daily shards)
  - retriever.py: MemoryRetriever (两阶段: 扫索引 → 深搜 + working memory 合并)
  - browser.py: MemoryBrowser (交互式 TUI: 日期→条目→详情 3级钻取)
"""

from mia.memory.store import (
    KnowledgeEntry,
    MemoryStore,
    DaySummary,
    CATEGORY_FACT,
    CATEGORY_PREFERENCE,
    CATEGORY_DECISION,
    CATEGORY_TASK,
    CATEGORY_INSIGHT,
    CATEGORY_LABELS,
    VALID_CATEGORIES,
)
from mia.memory.retriever import MemoryRetriever
from mia.memory.browser import MemoryBrowser

__all__ = [
    "KnowledgeEntry",
    "MemoryStore",
    "MemoryRetriever",
    "DaySummary",
    "MemoryBrowser",
    "CATEGORY_FACT",
    "CATEGORY_PREFERENCE",
    "CATEGORY_DECISION",
    "CATEGORY_TASK",
    "CATEGORY_INSIGHT",
    "CATEGORY_LABELS",
    "VALID_CATEGORIES",
]
