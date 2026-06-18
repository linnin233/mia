"""
MemoryStore — 分级记忆存储 (Index + Daily Shards)

两级存储架构:
  Level 1 — index.json: 始终加载，记录每天的摘要，用于快速扫描定位
  Level 2 — daily/YYYY-MM-DD.json: 按需懒加载，存储当日详细 MemoryEntry

设计参考:
  - ReMeLight 的文件分级思想 (MEMORY.md + memory/*.md)
  - 日志轮转模式 (按日分片，自动压缩旧日)

优势:
  - index.json 始终很小 (~1-2 KB, <= 90 条记录)
  - 检索时只加载相关日期的 daily 文件
  - 换日自动生成日摘要，旧日自动压缩
  - 向后兼容旧单文件格式 (自动迁移)
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
    # 取前 10 个字符 (YYYY-MM-DD)
    match = re.match(r'(\d{4}-\d{2}-\d{2})', ts)
    if match:
        return match.group(1)
    return _today_str()


# ─── MemoryEntry (保持不变) ──────────────────────

@dataclass
class MemoryEntry:
    """单条记忆 — 8 个字段保持不变

    设计参考 ReMe MemoryNode 的 when_to_use/content 分离模式:
      - summary → 用于检索匹配 (类似 when_to_use)
      - content → 原始对话内容
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = ""            # "user" | "assistant" | "system"
    content: str = ""         # 原始对话内容
    summary: str = ""         # 一句话摘要 (用于检索匹配)
    keywords: list[str] = field(default_factory=list)
    importance: float = 0.5
    timestamp: str = ""       # ISO 格式，空则自动填充当前北京时间
    session_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = _now_beijing()

    def to_dict(self) -> dict:
        """序列化为纯 dict"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        """从 dict 反序列化 — 过滤多余字段保证兼容性"""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


# ─── DaySummary (新增 — 索引条目) ──────────────

@dataclass
class DaySummary:
    """索引中每日记忆的摘要记录

    始终在内存中 (~100 bytes/天)，用于快速扫描定位。
    """

    date: str = ""                   # "2026-06-19"
    file: str = ""                   # "daily/2026-06-19.json"
    entry_count: int = 0             # 当日记忆条目数
    daily_summary: str = ""          # 当日对话的一句话总结 (LLM 生成)
    keywords: list[str] = field(default_factory=list)   # 当日关键词聚合
    importance: float = 0.5          # 当日最高重要性

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "entry_count": self.entry_count,
            "daily_summary": self.daily_summary,
            "keywords": self.keywords,
            "importance": self.importance,
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
        )

    @property
    def has_summary(self) -> bool:
        """是否已生成日摘要 (未生成则需要 LLM 补充)"""
        return bool(self.daily_summary)


# ─── MemoryStore — 分级存储 ────────────────────

class MemoryStore:
    """分级记忆存储 — Index + Daily Shards

    两级架构:
      - index.json: 始终加载，每天一条摘要记录
      - daily/YYYY-MM-DD.json: 按需懒加载，存储当日详细条目

    使用方式:
      store = MemoryStore()
      store.load()                         # 只加载 index.json
      store.add(entry)                     # 路由到今日 daily 文件
      dates = store.scan_index(keywords)   # 扫索引定位相关日期
      entries = store.load_day(dates[0])   # 按需加载

    向后兼容: 自动检测旧 memory.json 并迁移
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
            # 从 store.py 位置推导项目根目录:
            # mia/src/mia/memory/store.py → 上 4 级 → mia/
            _project_root = Path(__file__).parent.parent.parent.parent
            data_dir = _project_root / "data" / "memory"

        self._data_dir = Path(data_dir)
        self._daily_dir = self._data_dir / "daily"
        self._index_path = self._data_dir / "index.json"
        self._max_daily_files = max_daily_files

        # ─── 运行时状态 ──────────────────────────

        # 始终在内存: 索引 (date → DaySummary)
        self._index: dict[str, DaySummary] = {}

        # 按需缓存: 日文件条目 (date → [MemoryEntry])
        self._cache: dict[str, list[MemoryEntry]] = {}

        # 当日文件是否已修改 (用于延迟写入)
        self._dirty_days: set[str] = set()

        # 旧格式迁移路径
        self._legacy_path = self._data_dir / "memory.json"

        logger.info(
            "[MemoryStore] 初始化, data_dir={}, max_daily_files={}",
            self._data_dir, self._max_daily_files,
        )

    # ─── 属性 ─────────────────────────────────────

    @property
    def count(self) -> int:
        """总记忆条目数 (从 index 汇总)"""
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

        首次调用会自动检测并迁移旧格式。
        """
        # 确保目录存在
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 检查是否需要迁移
        if self._maybe_migrate():
            logger.info("[MemoryStore] 旧格式迁移完成")
            return  # 迁移后 index 已在内存中

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
                "[MemoryStore] 已加载索引: {} 天, {} 条记忆",
                len(self._index),
                self.count,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("[MemoryStore] index.json 损坏: {}, 重置为空", e)
            self._index = {}

    # ═══════════════════════════════════════════════════════
    # 公开 API — 写入
    # ═══════════════════════════════════════════════════════

    def add(self, entry: MemoryEntry) -> None:
        """添加一条记忆 — 自动路由到对应日期的 daily 文件

        流程:
          1. 确定 entry 所属日期 (从 timestamp 提取)
          2. 加载/创建该日文件
          3. 追加 entry
          4. 更新 index 中该日的计数和关键词
          5. 保存日文件 + 索引

        Args:
            entry: 要添加的记忆条目
        """
        date = _date_from_timestamp(entry.timestamp)

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
        else:
            self._index[date] = DaySummary(
                date=date,
                file=f"daily/{date}.json",
                entry_count=len(day_entries),
                daily_summary="",
                keywords=list(entry.keywords),
                importance=entry.importance,
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
            "[MemoryStore] 已添加: date={}, role={}, total={}",
            date, entry.role, self.count,
        )

    # ═══════════════════════════════════════════════════════
    # 公开 API — 读取
    # ═══════════════════════════════════════════════════════

    def get_all(self) -> list[MemoryEntry]:
        """获取所有记忆条目 — 遍历加载所有 daily 文件

        注意: 此操作可能较慢，仅用于 compact 等管理操作。
        日常检索请用 scan_index() + load_day() 两阶段检索。
        """
        all_entries = []
        for date in sorted(self._index.keys()):
            day_entries = self._load_day_entries(date)
            all_entries.extend(day_entries)
        return all_entries

    def get_by_keywords(self, keywords: list[str]) -> list[MemoryEntry]:
        """关键词检索 — 先扫索引再加载相关日文件

        两阶段检索:
          1. scan_index → 定位相关日期
          2. 加载相关日文件 → 关键词匹配

        Args:
            keywords: 关键词列表

        Returns:
            匹配的记忆条目 (按相关性排序)
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
                entry.summary + " " +
                entry.content
            ).lower()
            overlap = sum(1 for kw in keywords if kw.lower() in searchable)
            if overlap > 0:
                score = overlap * 2.0 + entry.importance
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored]

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """获取最近 N 条记忆 — 从最近几天加载

        Args:
            n: 返回条数

        Returns:
            最近的记忆条目列表 (按时间降序)
        """
        if not self._index:
            return []

        recent_dates = self.get_recent_dates(5)
        all_recent = []
        for date in recent_dates:
            all_recent.extend(self._load_day_entries(date))

        # 按 timestamp 降序排列
        all_recent.sort(key=lambda e: e.timestamp, reverse=True)
        return all_recent[:n]

    # ═══════════════════════════════════════════════════════
    # 公开 API — 删除与清空
    # ═══════════════════════════════════════════════════════

    def delete(self, entry_id: str) -> bool:
        """删除一条记忆 — 遍历所有日文件查找并删除

        Args:
            entry_id: 要删除的记忆 ID

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
        """清空所有记忆 — 删除 index + 所有 daily 文件"""
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

        logger.info("[MemoryStore] 记忆已清空")

    def compact(self, summary_text: str, source_session_ids: Optional[list[str]] = None) -> None:
        """压缩对话历史 — 用摘要替换指定会话的记忆

        如果指定 session_ids，删除那些会话的条目，替换为一条 system 摘要。
        否则压缩所有记忆。

        Args:
            summary_text: 压缩后的摘要文本
            source_session_ids: 要压缩的会话 ID 列表 (None = 全部)
        """
        if source_session_ids:
            # 只压缩指定会话
            target_ids = set(source_session_ids)
            for date in list(self._index.keys()):
                entries = self._load_day_entries(date)
                remaining = [e for e in entries if e.session_id not in target_ids]
                if len(remaining) != len(entries):
                    self._cache[date] = remaining
                    self._dirty_days.add(date)
                    self._index[date].entry_count = len(remaining)
                    self._save_daily(date)
        else:
            # 全量压缩: 清空所有日文件
            self.clear()

        # 创建压缩摘要条目，存入今天
        compact_entry = MemoryEntry(
            role="system",
            content=summary_text,
            summary="对话历史压缩摘要",
            keywords=["摘要"],
            importance=1.0,
            session_id="__compact__",
        )
        self.add(compact_entry)

        logger.info(
            "[MemoryStore] 压缩完成: → 1 条摘要, total={}",
            self.count,
        )

    # ═══════════════════════════════════════════════════════
    # 公开 API — 新增 (两阶段检索)
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
                # 评分: 关键词重叠 + 重要性 + 条目数因子
                score = overlap * 2.0 + ds.importance + min(ds.entry_count / 10, 0.5)
                scored.append((score, date))

        scored.sort(key=lambda x: x[0], reverse=True)
        result = [date for _, date in scored[:limit]]

        logger.debug(
            "[MemoryStore] scan_index: keywords={} → {} 个相关日期",
            keywords, len(result),
        )
        return result

    def load_day(self, date: str) -> list[MemoryEntry]:
        """加载指定日期的所有记忆条目

        优先从缓存读取，缓存未命中则从磁盘加载。

        Args:
            date: 日期字符串 "YYYY-MM-DD"

        Returns:
            该日的 MemoryEntry 列表
        """
        return self._load_day_entries(date)

    def get_recent_dates(self, n: int = 3) -> list[str]:
        """获取最近 N 个有记忆的日期

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
        """总记忆条目数"""
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
            # 合并关键词
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

    def _load_day_entries(self, date: str) -> list[MemoryEntry]:
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
            entries = [MemoryEntry.from_dict(e) for e in entries_data]

            # 放入缓存
            self._cache[date] = list(entries)
            # 缓存淘汰
            if len(self._cache) > self.MAX_CACHE_DAYS:
                self._evict_cache()

            return entries

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("[MemoryStore] 日文件损坏 date={}: {}", date, e)
            return []

    def _save_daily(self, date: str) -> None:
        """保存指定日期的 daily 文件 — 原子写入 (tmp + rename)"""
        if date not in self._cache:
            return  # 没有脏数据

        self._daily_dir.mkdir(parents=True, exist_ok=True)
        daily_path = self._daily_path(date)
        tmp_path = daily_path.with_suffix(".json.tmp")

        entries = self._cache[date]
        if not entries:
            # 空条目 → 删除文件
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
            "version": 2,
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
            # 不踢出今天
            if date == _today_str():
                continue
            self._cache.pop(date, None)
            logger.debug("[MemoryStore] 缓存淘汰: date={}", date)

    # ═══════════════════════════════════════════════════════
    # 内部方法 — 迁移
    # ═══════════════════════════════════════════════════════

    def _maybe_migrate(self) -> bool:
        """检测旧 memory.json 并迁移到新格式

        Returns:
            True 如果执行了迁移
        """
        if not self._legacy_path.exists():
            return False

        if self._index_path.exists():
            # 新旧文件共存 → 以新为准，旧文件改名备份
            backup = self._legacy_path.with_suffix(".json.bak")
            try:
                self._legacy_path.rename(backup)
                logger.info("[MemoryStore] 新旧格式共存，旧文件已备份: {}", backup)
            except Exception as e:
                logger.warning("[MemoryStore] 备份旧文件失败: {}", e)
            return False

        logger.info("[MemoryStore] 检测到旧格式 memory.json，开始迁移...")

        try:
            legacy_data = json.loads(self._legacy_path.read_text(encoding="utf-8"))
            entries_data = legacy_data.get("entries", [])
            entries = [MemoryEntry.from_dict(e) for e in entries_data]

            if not entries:
                logger.info("[MemoryStore] 旧文件为空，跳过迁移")
                self._legacy_path.rename(self._legacy_path.with_suffix(".json.bak"))
                return False

            # 按日期分组
            by_date: dict[str, list[MemoryEntry]] = defaultdict(list)
            for entry in entries:
                date = _date_from_timestamp(entry.timestamp)
                by_date[date].append(entry)

            # 创建 daily 文件 + 构建 index
            self._daily_dir.mkdir(parents=True, exist_ok=True)
            for date, day_entries in by_date.items():
                # 写日文件
                self._cache[date] = day_entries
                self._save_daily(date)

                # 构建 index 条目
                all_kw = list(set(
                    kw for e in day_entries for kw in e.keywords
                ))[:20]
                self._index[date] = DaySummary(
                    date=date,
                    file=f"daily/{date}.json",
                    entry_count=len(day_entries),
                    daily_summary="",  # 迁移时暂不生成摘要
                    keywords=all_kw,
                    importance=max((e.importance for e in day_entries), default=0.5),
                )

            self._save_index()

            # 旧文件改名备份
            backup_path = self._legacy_path.with_suffix(".json.bak")
            self._legacy_path.rename(backup_path)

            logger.info(
                "[MemoryStore] 迁移完成: {} 条 → {} 天, 旧文件已备份: {}",
                len(entries), len(by_date), backup_path,
            )
            return True

        except Exception as e:
            logger.error("[MemoryStore] 迁移失败: {}", e)
            return False

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
                # 清除缓存中的详细条目
                self._cache.pop(date, None)
                ds.entry_count = max(ds.entry_count, 1)
                compacted += 1
            else:
                # 无摘要 → 标记，跳过
                logger.debug("[MemoryStore] auto_compact: date={} 无摘要，跳过", date)

        if compacted > 0:
            self._save_index()
            logger.info("[MemoryStore] auto_compact: {} 个旧日文件已清理", compacted)
