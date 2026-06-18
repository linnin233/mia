"""
MemoryStore — 文件持久化的记忆存储

参考 ReMeLight (reme/reme_light.py) 的 file-based 存储设计：
  - 人类可读的 JSON 格式
  - 单文件存储 (data/memory/memory.json)
  - 支持 load/save/add/delete/clear/compact
  - 类似 ReMeInMemoryMemory (reme/memory/file_based/reme_in_memory_memory.py)
    的 get_memory() 模式
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger


# ─── MemoryEntry — 简化版 ReMe MemoryNode ─────────────────

# 参考: reme/core/schema/memory_node.py MemoryNode
# MIA 适配: when_to_use → summary, 去掉 vector/embedding 依赖

@dataclass
class MemoryEntry:
    """单条记忆条目 — 简化版 ReMe MemoryNode

    参考 ReMe 的 MemoryNode 字段映射:
      - memory_id → id
      - when_to_use → summary (描述"何时使用这条记忆")
      - memory_type → 简化: 用 role 区分 (user/assistant/system)
      - message_time → timestamp
      - ref_memory_id → session_id (关联同一轮对话)

    Attributes:
        id: 唯一标识符 (UUID)
        role: 角色 — "user" | "assistant" | "system"
        content: 原始对话内容
        summary: 一句话摘要 (类似 ReMe 的 when_to_use，用于检索匹配)
        keywords: 关键词列表 (用于快速关键词匹配)
        importance: 重要性 0.0-1.0
        timestamp: ISO 格式时间戳 (北京时间)
        session_id: 所属会话 ID
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = ""            # "user" | "assistant" | "system"
    content: str = ""         # 原始内容
    summary: str = ""         # 一句话摘要 (≈ ReMe when_to_use)
    keywords: list[str] = field(default_factory=list)  # 关键词
    importance: float = 0.5   # 重要性 0.0-1.0
    timestamp: str = ""       # ISO 时间戳
    session_id: str = ""      # 关联会话

    def to_dict(self) -> dict:
        """序列化为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        """从字典反序列化"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ─── MemoryStore — JSON 文件持久化 ──────────────────────

# 参考: ReMeLight 的 file-based 存储模式
#   - working_dir/.reme/memory/*.md (ReMe)
#   - data/memory/memory.json (MIA)

class MemoryStore:
    """文件持久化的记忆存储 (JSON)

    参考 ReMeLight 的设计哲学:
      - 人类可读、可编辑、可迁移
      - 启动时从文件加载，写操作后自动保存

    JSON 文件格式:
    {
      "version": 1,
      "created": "2026-06-18T12:00:00",
      "updated": "2026-06-18T15:00:00",
      "entries": [...]
    }
    """

    MAX_ENTRIES = 500  # 默认最大条数

    def __init__(self, file_path: Optional[Path] = None, max_entries: int = MAX_ENTRIES):
        """
        Args:
            file_path: JSON 文件路径 (默认: data/memory/memory.json)
            max_entries: 最大记忆条数 (超出时自动裁剪最旧的)
        """
        if file_path is None:
            # 默认路径: mia/data/memory/memory.json
            _project_root = Path(__file__).parent.parent.parent.parent
            file_path = _project_root / "data" / "memory" / "memory.json"

        self.file_path = Path(file_path)
        self.max_entries = max_entries
        self._entries: list[MemoryEntry] = []
        self._dirty = False  # 是否有未保存的更改

    # ─── 属性 ─────────────────────────────────────────

    @property
    def entries(self) -> list[MemoryEntry]:
        """记忆条目列表 (只读)"""
        return list(self._entries)

    @property
    def count(self) -> int:
        """当前记忆条数"""
        return len(self._entries)

    # ─── 持久化操作 ──────────────────────────────────

    def load(self) -> list[MemoryEntry]:
        """从 JSON 文件加载记忆

        如果文件不存在，返回空列表 (不报错)。

        Returns:
            加载的记忆条目列表
        """
        if not self.file_path.exists():
            logger.info("[MemoryStore] 记忆文件不存在，从空开始: {}", self.file_path)
            self._entries = []
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            entries_data = data.get("entries", [])
            self._entries = [
                MemoryEntry.from_dict(e) for e in entries_data
            ]
            self._dirty = False

            logger.info(
                "[MemoryStore] 已加载 {} 条记忆 from {}",
                len(self._entries),
                self.file_path,
            )
            return list(self._entries)

        except (json.JSONDecodeError, KeyError) as e:
            logger.error("[MemoryStore] 记忆文件损坏: {} — {}", self.file_path, e)
            self._entries = []
            return []

    def save(self) -> bool:
        """保存记忆到 JSON 文件

        Returns:
            True 如果保存成功
        """
        # 确保目录存在
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone(timedelta(hours=8))).isoformat()

        data = {
            "version": 1,
            "created": now,
            "updated": now,
            "entries": [e.to_dict() for e in self._entries],
        }

        # 原子写入: 先写临时文件，再重命名
        tmp_path = self.file_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            tmp_path.replace(self.file_path)
            self._dirty = False

            logger.debug("[MemoryStore] 已保存 {} 条记忆 to {}", len(self._entries), self.file_path)
            return True

        except Exception as e:
            logger.error("[MemoryStore] 保存失败: {}", e)
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            return False

    # ─── CRUD 操作 ───────────────────────────────────

    def add(self, entry: MemoryEntry) -> None:
        """添加一条记忆

        自动设置时间戳 (如果未设置) 和裁剪超量条目。

        Args:
            entry: 要添加的记忆条目
        """
        # 自动设置时间戳
        if not entry.timestamp:
            entry.timestamp = datetime.now(
                timezone(timedelta(hours=8))
            ).isoformat()

        self._entries.append(entry)

        # 裁剪超量 (保留最新)
        if len(self._entries) > self.max_entries:
            overflow = len(self._entries) - self.max_entries
            self._entries = self._entries[overflow:]
            logger.info("[MemoryStore] 裁剪了 {} 条旧记忆", overflow)

        self._dirty = True
        self.save()

        logger.debug(
            "[MemoryStore] 已添加记忆: role={}, summary={}, total={}",
            entry.role,
            entry.summary[:50] if entry.summary else "(无摘要)",
            len(self._entries),
        )

    def get_all(self) -> list[MemoryEntry]:
        """获取所有记忆 (按时间排序)"""
        return list(self._entries)

    def get_by_keywords(self, keywords: list[str]) -> list[MemoryEntry]:
        """关键词匹配检索 — 快速初筛

        匹配策略: 记忆的 keywords 或 summary 中是否包含任意检索词

        Args:
            keywords: 检索关键词列表

        Returns:
            匹配的记忆列表 (按重要性+时间排序)
        """
        if not keywords:
            return []

        results = []
        for entry in self._entries:
            # 在 keywords 和 summary 中匹配
            searchable = " ".join(entry.keywords) + " " + entry.summary
            searchable_lower = searchable.lower()
            matched = any(
                kw.lower() in searchable_lower
                for kw in keywords
            )
            if matched:
                results.append(entry)

        # 排序: 重要性高的在前，同样重要性按时间倒序
        results.sort(
            key=lambda e: (e.importance, e.timestamp),
            reverse=True,
        )
        return results

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """获取最近 N 条记忆

        Args:
            n: 返回条数

        Returns:
            最近 N 条记忆 (按时间倒序)
        """
        recent = self._entries[-n:]
        return list(reversed(recent))

    def delete(self, entry_id: str) -> bool:
        """删除一条记忆

        Args:
            entry_id: 记忆 ID

        Returns:
            True 如果找到并删除
        """
        for i, entry in enumerate(self._entries):
            if entry.id == entry_id:
                self._entries.pop(i)
                self._dirty = True
                self.save()
                logger.debug("[MemoryStore] 已删除记忆: {}", entry_id)
                return True

        logger.warning("[MemoryStore] 未找到记忆: {}", entry_id)
        return False

    def clear(self) -> None:
        """清空所有记忆"""
        count = len(self._entries)
        self._entries.clear()
        self._dirty = True
        self.save()
        logger.info("[MemoryStore] 已清空 {} 条记忆", count)

    def compact(self, summary_text: str, source_session_ids: Optional[list[str]] = None) -> None:
        """压缩记忆 — 用摘要替换多条记忆

        参考 ReMe ReMeLight.compact_memory() 模式:
          将多条记忆压缩为一条 system 摘要

        Args:
            summary_text: 压缩后的摘要文本
            source_session_ids: 被压缩的会话 ID 列表 (可选)
        """
        if source_session_ids:
            # 只删除指定会话的记忆
            self._entries = [
                e for e in self._entries
                if e.session_id not in source_session_ids
            ]

        # 添加压缩摘要
        now = datetime.now(timezone(timedelta(hours=8))).isoformat()
        summary_entry = MemoryEntry(
            role="system",
            content=summary_text,
            summary="对话历史压缩摘要",
            keywords=["摘要"],
            importance=1.0,
            timestamp=now,
            session_id="__compact__",
        )
        self._entries.insert(0, summary_entry)

        self._dirty = True
        self.save()
        logger.info("[MemoryStore] 记忆已压缩 → 1 条摘要 (总 {} 条)", len(self._entries))
