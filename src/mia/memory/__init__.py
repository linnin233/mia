"""
MIA Memory Package — 记忆管理模块

基于 ReMe (agentscope-ai/ReMe) 的设计理念：
  - MemoryNode → MemoryEntry (when_to_use → summary)
  - ReMeLight 的 file-based 存储哲学
  - Summarizer + Retriever 分离模式

模块结构:
  - store.py: MemoryEntry dataclass + MemoryStore (JSON 持久化)
  - retriever.py: MemoryRetriever (关键词 + LLM 相关性检索)
"""

from mia.memory.store import MemoryEntry, MemoryStore
from mia.memory.retriever import MemoryRetriever

__all__ = ["MemoryEntry", "MemoryStore", "MemoryRetriever"]
