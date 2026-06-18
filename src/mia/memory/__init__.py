"""
MIA Memory Package — 记忆管理模块

基于 ReMe (agentscope-ai/ReMe) 的设计理念：
  - MemoryNode → MemoryEntry (when_to_use → summary)
  - ReMeLight 的 file-based 存储哲学
  - Summarizer + Retriever 分离模式

分级存储架构 (v2):
  - store.py: MemoryEntry + DaySummary + MemoryStore (index + daily shards)
  - retriever.py: MemoryRetriever (两阶段: 扫索引 → 深搜)
"""

from mia.memory.store import MemoryEntry, MemoryStore, DaySummary
from mia.memory.retriever import MemoryRetriever

__all__ = ["MemoryEntry", "MemoryStore", "MemoryRetriever", "DaySummary"]
