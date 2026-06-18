"""
MemoryStore — 分级知识存储 (Index + Daily Shards)

两级存储架构:
  Level 1 — index.json: 始终加载，记录每天的摘要，用于快速扫描定位
  Level 2 — daily/YYYY-MM-DD.json: 按需懒加载，存储当日 KnowledgeEntry

设计参考:
  - linninpaw 的分层记忆 (原始日志 → 精选记忆 → 梦境优化)
  - ReMeLight 的文件分级思想

KnowledgeEntry vs 旧 MemoryEntry:
  - 旧: 存储原始消息 (user/assistant role + content)
  - 新: 存储提炼后的知识 (category + content, 无 role)
  - 知识不区分"谁说"，只记录"知道什么"

优势:
  - index.json 始终很小 (~1-2 KB, <= 90 条记录)
  - 检索时只加载相关日期的 daily 文件
  - 换日自动生成日摘要，旧日自动压缩
"""

import json
import uuid
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger


# ─── 北京时间时区 ──────────────────────────────────

TZ_BEIJING = timezone(timedelta(hours=8))


def _now_beijing() -> str:
    """返回当前北京时间 ISO 格式字符串"""
    return datetime.now(TZ_BEIJING).isoformat()


def _today_str() -> str:
    """返回北京时间今天的日期字符串 'YYYY-MM-DD'"""
    return datetime.now(TZ_BEIJING).strftime("%Y-%m-%d")


def _date_from_timestamp(ts: str) -> str:
    """从 ISO timestamp 提取日期部分 'YYYY-MM-DD'

    支持格式:
      - '2026-06-18T14:30:00+08:00'
      - '2026-06-18T14:30:00'
      - '2026-06-18'
    """
    if not ts:
        return _today_str()
    match = re.match(r'(\d{4}-\d{2}-\d{2})', ts)
    if match:
        return match.group(1)
    return _today_str()


# ─── 知识类别常量 ──────────────────────────────────

CATEGORY_FACT = "fact"             # 客观事实
CATEGORY_PREFERENCE = "preference"  # 用户偏好
CATEGORY_DECISION = "decision"      # 已做决策
CATEGORY_TASK = "task"             # 待办/任务
CATEGORY_INSIGHT = "insight"        # 洞察/发现

VALID_CATEGORIES = {
    CATEGORY_FACT,
    CATEGORY_PREFERENCE,
    CATEGORY_DECISION,
    CATEGORY_TASK,
    CATEGORY_INSIGHT,
}

# 类别中文标签 (用于展示)
CATEGORY_LABELS = {
    CATEGORY_FACT: "[事实]",
    CATEGORY_PREFERENCE: "[偏好]",
    CATEGORY_DECISION: "[决策]",
    CATEGORY_TASK: "[任务]",
    CATEGORY_INSIGHT: "[洞察]",
}


# ─── KnowledgeEntry — 知识条目 ────────────────────

@dataclass
class KnowledgeEntry:
    """一条从对话中提炼的原子知识

    与旧 MemoryEntry 的核心区别:
      - 不区分 speaker (知识不记录"谁说"，只记录"知道什么")
      - content 是提炼后的知识陈述，不是原始消息
      - category 标记知识类型 (fact/preference/decision/task/insight)
      - confidence 表达确定性，随验证次数提升
      - source_sessions 保留溯源能力

    旧 MemoryEntry 字段对照:
      - role → 移除 (知识没有发言人)
      - summary → 移除 (content 本身就是总结)
      - session_id → 改为 source_sessions (支持跨会话合并)
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    content: str = ""             # 提炼后的知识陈述
    category: str = CATEGORY_FACT  # 知识类别
    confidence: float = 0.5       # 置信度 0.0-1.0
    keywords: list[str] = field(default_factory=list)
    importance: float = 0.5       # 重要度 0.0-1.0
    source_sessions: list[str] = field(default_factory=list)  # 来源会话 ID
    created_at: str = ""          # 首次创建时间 ISO
    updated_at: str = ""          # 最后更新时间 ISO

    def __post_init__(self):
        now = _now_beijing()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        # 标准化 category
        if self.category not in VALID_CATEGORIES:
            logger.debug(
                "[KnowledgeEntry] 未知 category '{}', 降级为 '{}'",
                self.category, CATEGORY_FACT,
            )
            self.category = CATEGORY_FACT

    def to_dict(self) -> dict:
        """序列化为纯 dict"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeEntry":
        """从 dict 反序列化 — 过滤多余字段保证兼容性"""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    @classmethod
    def is_legacy_format(cls, data: dict) -> bool:
        """检测是否为旧 MemoryEntry 格式 (有 'role' 字段)"""
        return "role" in data

    @property
    def category_label(self) -> str:
        """类别中文标签"""
        return CATEGORY_LABELS.get(self.category, f"[{self.category}]")

    @property
    def date(self) -> str:
        """从 created_at 提取日期"""
        return _date_from_timestamp(self.created_at)


# ─── DaySummary — 日索引条目 ──────────────────────

@dataclass
class DaySummary:
    """索引中每日知识的摘要记录

    始终在内存中 (~150 bytes/天)，用于快速扫描定位。
    """

    date: str = ""                   # "2026-06-19"
    file: str = ""                   # "daily/2026-06-19.json"
    entry_count: int = 0             # 当日知识条目数
    daily_summary: str = ""          # 当日知识的一句话总结 (LLM 生成)
    keywords: list[str] = field(default_factory=list)   # 当日关键词聚合
    importance: float = 0.5          # 当日最高重要性
    # 类别分布 (用于快速了解当天知识构成)
    category_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "entry_count": self.entry_count,
            "daily_summary": self.daily_summary,
            "keywords": self.keywords,
            "importance": self.importance,
            "category_distribution": self.category_distribution,
        }

    @classmethod
    def from_dict(cls, date: str, data: dict) -> "DaySummary":
        return cls(
            date=date,
            file=data.get("file", f"daily/{date}.json"),
            entry_count=data.get("entry_count", 0),
            daily_summary=data.get("daily_summary", ""),
            keywords=data.get("keywords", []),
            importance=data.get("importance", 0.5),
            category_distribution=data.get("category_distribution", {}),
        )

    @property
    def has_summary(self) -> bool:
        """是否已生成日摘要"""
        return bool(self.daily_summary)


# ─── MemoryStore — 分级知识存储 ──────────────────

class MemoryStore:
    """分级知识存储 — Index + Daily Shards

    两级架构:
      - index.json: 始终加载，每天一条 DaySummary
      - daily/YYYY-MM-DD.json: 按需懒加载，存储当日 KnowledgeEntry

    使用方式:
      store = MemoryStore()
      store.load()                         # 只加载 index.json
      store.add(entry)                     # 路由到今日 daily 文件
      dates = store.scan_index(keywords)   # 扫索引定位相关日期
      entries = store.load_day(dates[0])   # 按需加载
    """

    # ─── 常量 ─────────────────────────────────────

    MAX_DAILY_FILES = 90       # 超过此数自动压缩最旧的日
    MAX_CACHE_DAYS = 5         # 最多缓存几个日文件在内存

    # ─── 构造 ─────────────────────────────────────

    def __init__(self, data_dir: Optional[Path] = None, max_daily_files: int = MAX_DAILY_FILES):
        """
        Args:
            data_dir: 数据目录路径 (None 则用默认 {project}/data/memory/)
            max_daily_files: 最多保留的日文件数
        """
        if data_dir is None:
            _project_root = Path(__file__).parent.parent.parent.parent
            data_dir = _project_root / "data" / "memory"

        self._data_dir = Path(data_dir)
        self._daily_dir = self._data_dir / "daily"
        self._index_path = self._data_dir / "index.json"
        self._max_daily_files = max_daily_files

        # ─── 运行时状态 ──────────────────────────

        self._index: dict[str, DaySummary] = {}
        self._cache: dict[str, list[KnowledgeEntry]] = {}
        self._dirty_days: set[str] = set()

        # 旧格式路径
        self._legacy_path = self._data_dir / "memory.json"

        logger.info(
            "[MemoryStore] 初始化, data_dir={}, max_daily_files={}",
            self._data_dir, self._max_daily_files,
        )

    # ─── 属性 ─────────────────────────────────────

    @property
    def count(self) -> int:
        """总知识条目数 (从 index 汇总)"""
        return sum(ds.entry_count for ds in self._index.values())

    @property
    def day_count(self) -> int:
        """索引中的天数"""
        return len(self._index)

    @property
    def file_path(self) -> Path:
        """数据目录路径 (兼容旧 API)"""
        return self._data_dir

    # ═══════════════════════════════════════════════════════
    # 公开 API — 生命周期
    # ═══════════════════════════════════════════════════════

    def load(self) -> None:
        """加载记忆系统 — 只加载 index.json (极快)

        首次调用会自动检测旧格式并清理。
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 检查并清理旧 MemoryEntry 格式
        if self._maybe_clean_legacy():
            logger.info("[MemoryStore] 旧格式数据已清理")
            self._index = {}
            return

        # 加载 index.json
        if not self._index_path.exists():
            logger.info("[MemoryStore] index.json 不存在，从空开始")
            self._index = {}
            return

        try:
            raw = json.loads(self._index_path.read_text(encoding="utf-8"))
            days_data = raw.get("days", {})
            self._index = {}
            for date_str, day_data in days_data.items():
                self._index[date_str] = DaySummary.from_dict(date_str, day_data)

            logger.info(
                "[MemoryStore] 已加载索引: {} 天, {} 条知识",
                len(self._index),
                self.count,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("[MemoryStore] index.json 损坏: {}, 重置为空", e)
            self._index = {}

    # ═══════════════════════════════════════════════════════
    # 公开 API — 写入
    # ═══════════════════════════════════════════════════════

    def add(self, entry: KnowledgeEntry) -> None:
        """添加一条知识 — 自动路由到对应日期的 daily 文件

        流程:
          1. 确定 entry 所属日期 (从 created_at 提取)
          2. 加载/创建该日文件
          3. 追加 entry
          4. 更新 index 中该日的计数、关键词、类别分布
          5. 保存日文件 + 索引

        Args:
            entry: 要添加的知识条目
        """
        date = entry.date

        # 加载该日文件 (从缓存或磁盘)
        day_entries = self._load_day_entries(date)

        # 追加条目
        day_entries.append(entry)

        # 更新缓存
        self._cache[date] = day_entries
        self._dirty_days.add(date)

        # 更新或创建 index 条目
        if date in self._index:
            ds = self._index[date]
            ds.entry_count = len(day_entries)
            # 聚合关键词 (去重，最多保留 20 个)
            all_kw = list(ds.keywords)
            for kw in entry.keywords:
                if kw not in all_kw:
                    all_kw.append(kw)
            ds.keywords = all_kw[:20]
            ds.importance = max(ds.importance, entry.importance)
            # 更新类别分布
            cat = entry.category
            ds.category_distribution[cat] = ds.category_distribution.get(cat, 0) + 1
        else:
            self._index[date] = DaySummary(
                date=date,
                file=f"daily/{date}.json",
                entry_count=len(day_entries),
                daily_summary="",
                keywords=list(entry.keywords),
                importance=entry.importance,
                category_distribution={entry.category: 1},
            )

        # 持久化
        self._save_daily(date)
        self._save_index()

        # 缓存淘汰
        self._evict_cache()

        # 自动压缩检查
        if len(self._index) > self._max_daily_files:
            logger.info(
                "[MemoryStore] daily 文件数 {} > {}, 触发自动压缩",
                len(self._index), self._max_daily_files,
            )
            self._auto_compact()

        logger.debug(
            "[MemoryStore] 已添加: date={}, category={}, total={}",
            date, entry.category, self.count,
        )

    # ═══════════════════════════════════════════════════════
    # 公开 API — 读取
    # ═══════════════════════════════════════════════════════

    def get_all(self) -> list[KnowledgeEntry]:
        """获取所有知识条目 — 遍历加载所有 daily 文件

        注意: 此操作可能较慢，仅用于 compact 等管理操作。
        日常检索请用 scan_index() + load_day() 两阶段检索。
        """
        all_entries = []
        for date in sorted(self._index.keys()):
            day_entries = self._load_day_entries(date)
            all_entries.extend(day_entries)
        return all_entries

    def get_by_keywords(self, keywords: list[str]) -> list[KnowledgeEntry]:
        """关键词检索 — 先扫索引再加载相关日文件

        两阶段检索:
          1. scan_index → 定位相关日期
          2. 加载相关日文件 → 关键词匹配

        Args:
            keywords: 关键词列表

        Returns:
            匹配的知识条目 (按相关性排序)
        """
        if not keywords or not self._index:
            return []

        # Phase 1: 扫索引定位
        relevant_dates = self.scan_index(keywords, limit=7)

        # Phase 2: 加载 + 匹配
        candidates = []
        for date in relevant_dates:
            candidates.extend(self._load_day_entries(date))

        if not candidates:
            # 降级: 最近 3 天
            for date in self.get_recent_dates(3):
                candidates.extend(self._load_day_entries(date))

        # 关键词匹配评分
        scored = []
        for entry in candidates:
            searchable = (
                " ".join(entry.keywords) + " " +
                entry.content
            ).lower()
            overlap = sum(1 for kw in keywords if kw.lower() in searchable)
            if overlap > 0:
                score = overlap * 2.0 + entry.importance
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored]

    def get_recent(self, n: int = 10) -> list[KnowledgeEntry]:
        """获取最近 N 条知识 — 从最近几天加载

        Args:
            n: 返回条数

        Returns:
            最近的知识条目列表 (按创建时间降序)
        """
        if not self._index:
            return []

        recent_dates = self.get_recent_dates(5)
        all_recent = []
        for date in recent_dates:
            all_recent.extend(self._load_day_entries(date))

        # 按 created_at 降序排列
        all_recent.sort(key=lambda e: e.created_at, reverse=True)
        return all_recent[:n]

    # ═══════════════════════════════════════════════════════
    # 公开 API — 删除与清空
    # ═══════════════════════════════════════════════════════

    def delete(self, entry_id: str) -> bool:
        """删除一条知识 — 遍历所有日文件查找并删除

        Args:
            entry_id: 要删除的知识 ID

        Returns:
            True 如果成功删除
        """
        for date in list(self._index.keys()):
            entries = self._load_day_entries(date)
            for i, entry in enumerate(entries):
                if entry.id == entry_id:
                    entries.pop(i)
                    self._cache[date] = entries
                    self._dirty_days.add(date)

                    # 更新 index
                    self._index[date].entry_count = len(entries)
                    if len(entries) == 0:
                        # 删除空日文件
                        daily_path = self._daily_dir / f"{date}.json"
                        if daily_path.exists():
                            daily_path.unlink()
                        del self._index[date]
                        self._cache.pop(date, None)

                    self._save_daily(date)
                    self._save_index()
                    logger.info("[MemoryStore] 已删除: id={}, date={}", entry_id, date)
                    return True

        logger.warning("[MemoryStore] 未找到要删除的条目: id={}", entry_id)
        return False

    def clear(self) -> None:
        """清空所有知识 — 删除 index + 所有 daily 文件"""
        # 删除所有 daily 文件
        if self._daily_dir.exists():
            for f in self._daily_dir.iterdir():
                if f.suffix == ".json":
                    f.unlink()
            logger.info("[MemoryStore] 已删除所有 daily 文件")

        # 删除 index
        if self._index_path.exists():
            self._index_path.unlink()

        # 清空内存
        self._index.clear()
        self._cache.clear()
        self._dirty_days.clear()

        logger.info("[MemoryStore] 知识已清空")

    def compact(self, summary_text: str, source_session_ids: Optional[list[str]] = None) -> None:
        """压缩知识 — 用摘要替换指定会话的知识条目

        如果指定 session_ids，删除那些会话的条目，替换为一条 system 摘要。
        否则压缩所有知识。

        Args:
            summary_text: 压缩后的摘要文本
            source_session_ids: 要压缩的会话 ID 列表 (None = 全部)
        """
        if source_session_ids:
            target_ids = set(source_session_ids)
            for date in list(self._index.keys()):
                entries = self._load_day_entries(date)
                remaining = [
                    e for e in entries
                    if not set(e.source_sessions) & target_ids
                ]
                if len(remaining) != len(entries):
                    self._cache[date] = remaining
                    self._dirty_days.add(date)
                    self._index[date].entry_count = len(remaining)
                    self._save_daily(date)
        else:
            self.clear()

        # 创建压缩摘要条目，存入今天
        compact_entry = KnowledgeEntry(
            content=summary_text,
            category=CATEGORY_INSIGHT,
            keywords=["摘要"],
            importance=1.0,
            source_sessions=source_session_ids or [],
        )
        self.add(compact_entry)

        logger.info(
            "[MemoryStore] 压缩完成: → 1 条摘要, total={}",
            self.count,
        )

    # ═══════════════════════════════════════════════════════
    # 公开 API — 两阶段检索
    # ═══════════════════════════════════════════════════════

    def scan_index(self, keywords: list[str], limit: int = 7) -> list[str]:
        """扫索引定位相关日期 — 两阶段检索的第一阶段

        遍历 index 中的日摘要，按关键词重叠匹配。
        O(天数) ≈ O(90)，极快。

        Args:
            keywords: 关键词列表
            limit: 最多返回几个日期

        Returns:
            相关日期列表 (按相关性排序)
        """
        if not keywords or not self._index:
            return []

        scored = []
        for date, ds in self._index.items():
            searchable = f"{ds.daily_summary} {' '.join(ds.keywords)}".lower()
            overlap = sum(1 for kw in keywords if kw.lower() in searchable)
            if overlap > 0:
                score = overlap * 2.0 + ds.importance + min(ds.entry_count / 10, 0.5)
                scored.append((score, date))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = [date for _, date in scored[:limit]]

        logger.debug(
            "[MemoryStore] scan_index: keywords={} → {} 个相关日期",
            keywords, len(result),
        )
        return result

    def load_day(self, date: str) -> list[KnowledgeEntry]:
        """加载指定日期的所有知识条目

        优先从缓存读取，缓存未命中则从磁盘加载。

        Args:
            date: 日期字符串 "YYYY-MM-DD"

        Returns:
            该日的 KnowledgeEntry 列表
        """
        return self._load_day_entries(date)

    def get_recent_dates(self, n: int = 3) -> list[str]:
        """获取最近 N 个有知识的日期

        Args:
            n: 返回日期数

        Returns:
            日期字符串列表 (按日期降序)
        """
        if not self._index:
            return []
        sorted_dates = sorted(self._index.keys(), reverse=True)
        return sorted_dates[:n]

    def get_index_summaries(self) -> dict[str, DaySummary]:
        """获取完整索引 — 供 /memory 命令展示

        Returns:
            date → DaySummary 的字典副本 (按日期降序)
        """
        return dict(sorted(self._index.items(), reverse=True))

    def get_total_count(self) -> int:
        """总知识条目数"""
        return self.count

    # ═══════════════════════════════════════════════════════
    # 公开 API — 日摘要生成
    # ═══════════════════════════════════════════════════════

    def update_day_summary(
        self,
        date: str,
        summary: str,
        keywords: Optional[list[str]] = None,
    ) -> None:
        """更新某日的摘要 — 由 MemoryAgent 调用 LLM 生成后写入索引

        Args:
            date: 日期字符串
            summary: 日摘要文本
            keywords: 聚合后的关键词 (可选)
        """
        if date not in self._index:
            logger.warning("[MemoryStore] 日期不在索引中: {}", date)
            return

        ds = self._index[date]
        ds.daily_summary = summary
        if keywords:
            all_kw = list(ds.keywords)
            for kw in keywords:
                if kw not in all_kw:
                    all_kw.append(kw)
            ds.keywords = all_kw[:20]

        self._save_index()
        logger.info("[MemoryStore] 日摘要已更新: date={}, summary={}", date, summary[:60])

    def get_days_without_summary(self) -> list[str]:
        """获取未生成摘要的日期列表 (供 MemoryAgent 批量补充)"""
        return [
            date for date, ds in self._index.items()
            if not ds.has_summary and ds.entry_count > 0
        ]

    # ═══════════════════════════════════════════════════════
    # 内部方法 — 日文件读写
    # ═══════════════════════════════════════════════════════

    def _daily_path(self, date: str) -> Path:
        """获取指定日期的 daily 文件路径"""
        return self._daily_dir / f"{date}.json"

    def _load_day_entries(self, date: str) -> list[KnowledgeEntry]:
        """加载指定日期的条目 — 缓存优先"""
        # 命中缓存
        if date in self._cache:
            return list(self._cache[date])

        # 从磁盘加载
        daily_path = self._daily_path(date)
        if not daily_path.exists():
            return []

        try:
            raw = json.loads(daily_path.read_text(encoding="utf-8"))
            entries_data = raw.get("entries", [])
            entries = [KnowledgeEntry.from_dict(e) for e in entries_data]

            # 放入缓存
            self._cache[date] = list(entries)
            if len(self._cache) > self.MAX_CACHE_DAYS:
                self._evict_cache()

            return entries

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("[MemoryStore] 日文件损坏 date={}: {}", date, e)
            return []

    def _save_daily(self, date: str) -> None:
        """保存指定日期的 daily 文件 — 原子写入 (tmp + rename)"""
        if date not in self._cache:
            return

        self._daily_dir.mkdir(parents=True, exist_ok=True)
        daily_path = self._daily_path(date)
        tmp_path = daily_path.with_suffix(".json.tmp")

        entries = self._cache[date]
        if not entries:
            if daily_path.exists():
                daily_path.unlink()
            if tmp_path.exists():
                tmp_path.unlink()
            return

        data = {
            "date": date,
            "updated": _now_beijing(),
            "entries": [e.to_dict() for e in entries],
        }

        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(daily_path)
            self._dirty_days.discard(date)
            logger.debug("[MemoryStore] 日文件已保存: date={}, entries={}", date, len(entries))

        except Exception as e:
            logger.error("[MemoryStore] 日文件保存失败 date={}: {}", date, e)
            if tmp_path.exists():
                tmp_path.unlink()

    def _save_index(self) -> None:
        """保存索引文件 — 原子写入 (tmp + rename)"""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._index_path.with_suffix(".json.tmp")

        days_data = {}
        for date in sorted(self._index.keys()):
            days_data[date] = self._index[date].to_dict()

        data = {
            "version": 3,  # v3: KnowledgeEntry 格式
            "updated": _now_beijing(),
            "days": days_data,
        }

        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._index_path)
            logger.debug("[MemoryStore] 索引已保存: {} 天", len(days_data))

        except Exception as e:
            logger.error("[MemoryStore] 索引保存失败: {}", e)
            if tmp_path.exists():
                tmp_path.unlink()

    # ═══════════════════════════════════════════════════════
    # 内部方法 — 缓存管理
    # ═══════════════════════════════════════════════════════

    def _evict_cache(self) -> None:
        """缓存淘汰 — 踢出最旧的缓存条目，保持 MAX_CACHE_DAYS 限制"""
        if len(self._cache) <= self.MAX_CACHE_DAYS:
            return

        # 先保存脏数据
        for date in list(self._dirty_days):
            if date in self._cache:
                self._save_daily(date)

        # 按日期排序，踢出最旧的
        sorted_dates = sorted(self._cache.keys())
        to_evict = sorted_dates[:len(self._cache) - self.MAX_CACHE_DAYS]

        for date in to_evict:
            if date == _today_str():
                continue
            self._cache.pop(date, None)
            logger.debug("[MemoryStore] 缓存淘汰: date={}", date)

    # ═══════════════════════════════════════════════════════
    # 内部方法 — 旧格式清理
    # ═══════════════════════════════════════════════════════

    def _maybe_clean_legacy(self) -> bool:
        """检测并清理旧 MemoryEntry 格式的数据

        检测策略:
          1. 检查 index.json 版本号 (v2 = 旧 MemoryEntry 格式)
          2. 检查 daily 文件中是否有 'role' 字段
          3. 检查旧 memory.json 文件

        Returns:
            True 如果执行了清理
        """
        cleaned = False

        # 检查旧 memory.json
        if self._legacy_path.exists():
            logger.info("[MemoryStore] 检测到旧 memory.json，清理中...")
            backup = self._legacy_path.with_suffix(".json.bak.v2")
            try:
                self._legacy_path.rename(backup)
                logger.info("[MemoryStore] 旧文件已备份: {}", backup)
            except Exception as e:
                logger.warning("[MemoryStore] 备份旧文件失败: {}", e)
            cleaned = True

        # 检查 index.json 版本
        if self._index_path.exists():
            try:
                raw = json.loads(self._index_path.read_text(encoding="utf-8"))
                version = raw.get("version", 1)
                if version < 3:
                    logger.info(
                        "[MemoryStore] 检测到旧版本 index.json (v{})，清理中...",
                        version,
                    )
                    backup = self._index_path.with_suffix(".json.bak.v2")
                    self._index_path.rename(backup)
                    logger.info("[MemoryStore] 旧索引已备份: {}", backup)
                    cleaned = True
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("[MemoryStore] index.json 读取失败: {}", e)

        # 检查 daily 文件是否包含旧 MemoryEntry 格式 (有 'role' 字段)
        if self._daily_dir.exists() and not cleaned:
            for f in list(self._daily_dir.iterdir())[:3]:  # 抽样检查前 3 个
                if f.suffix == ".json" and not f.name.endswith(".tmp"):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        entries = data.get("entries", [])
                        if entries and KnowledgeEntry.is_legacy_format(entries[0]):
                            logger.info(
                                "[MemoryStore] 检测到旧 MemoryEntry 格式 (有 role 字段)，清理中..."
                            )
                            cleaned = True
                            break
                    except Exception:
                        continue

        if cleaned:
            # 删除所有旧 daily 文件
            if self._daily_dir.exists():
                for f in self._daily_dir.iterdir():
                    if f.suffix == ".json" and not f.name.endswith(".bak.v2"):
                        try:
                            f.unlink()
                        except Exception as e:
                            logger.warning("[MemoryStore] 删除旧文件失败: {} - {}", f, e)
                logger.info("[MemoryStore] 旧 daily 文件已删除")

            # 如果旧 index 还没备份，也备份一下
            if self._index_path.exists():
                backup = self._index_path.with_suffix(".json.bak.v2")
                try:
                    self._index_path.rename(backup)
                except Exception:
                    pass

        return cleaned

    # ═══════════════════════════════════════════════════════
    # 内部方法 — 自动压缩
    # ═══════════════════════════════════════════════════════

    def _auto_compact(self) -> None:
        """自动压缩最旧的日文件

        当 daily 文件数超过 MAX_DAILY_FILES 时触发。
        策略: 取最旧的日期，如果日摘要已存在则直接删文件，
        否则保留 (等待 LLM 生成摘要后再处理)。
        """
        if len(self._index) <= self._max_daily_files:
            return

        sorted_dates = sorted(self._index.keys())  # 升序，最旧的在前
        excess = len(self._index) - self._max_daily_files

        compacted = 0
        for date in sorted_dates[:excess]:
            ds = self._index[date]

            if ds.has_summary:
                # 有摘要 → 可以安全删除日文件 (索引中保留摘要)
                daily_path = self._daily_path(date)
                if daily_path.exists():
                    daily_path.unlink()
                    logger.info("[MemoryStore] auto_compact: 删除旧日文件 {}", date)
                self._cache.pop(date, None)
                ds.entry_count = max(ds.entry_count, 1)
                compacted += 1
            else:
                logger.debug("[MemoryStore] auto_compact: date={} 无摘要，跳过", date)

        if compacted > 0:
            self._save_index()
            logger.info("[MemoryStore] auto_compact: {} 个旧日文件已清理", compacted)
