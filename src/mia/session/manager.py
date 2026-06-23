"""
SessionManager — 会话持久化与状态管理

核心职责:
  1. 管理会话元数据（SessionInfo 索引）— 始终加载在内存中
  2. 持久化/恢复会话运行时状态（SessionState）— 按需加载/保存
  3. 自动创建默认会话（首次启动）
  4. 追踪当前活跃会话

设计参考:
  - memory/store.py: 两级文件架构（index + 分片文件）
  - memory/store.py: 原子写入（tmp + rename）
  - memory/store.py: 北京时间（UTC+8）

会话 ID 约定:
  - CLI: "cli_<8位hex>" — 不含冒号，Scheduler 路由到终端
  - WeChat: "wechat:<user_id>" — 含冒号前缀，Scheduler 路由到微信
  - API: "api_<8位hex>" — 预留
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 北京时间时区
_BEIJING_TZ = timezone(timedelta(hours=8))


def _now_beijing() -> str:
    """返回北京时间 ISO 格式时间戳（用于 JSON 序列化）"""
    return datetime.now(_BEIJING_TZ).isoformat()


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════


@dataclass
class SessionInfo:
    """会话元数据 — 存储在 index.json 中

    每个会话一条记录，包含基本标识和统计信息。
    不包含运行时状态（对话历史等），那部分在 SessionState 中。
    """
    session_id: str = ""
    name: str = ""                      # 显示名（用户可重命名）
    source: str = "cli"                # "cli" | "wechat" | "api"
    created_at: str = ""                # 北京时间 ISO
    updated_at: str = ""                # 最后一次活动的北京时间 ISO
    turn_count: int = 0                 # 累计对话轮数
    is_active: bool = False             # 当前活跃会话标记

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": self.turn_count,
            "is_active": self.is_active,
        }

    @classmethod
    def from_dict(cls, session_id: str, data: dict) -> SessionInfo:
        return cls(
            session_id=session_id,
            name=data.get("name", ""),
            source=data.get("source", "cli"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            turn_count=data.get("turn_count", 0),
            is_active=data.get("is_active", False),
        )


@dataclass
class SessionState:
    """会话运行时状态 — 存储在 states/<session_id>.json 中

    对应 MemoryAgent 的三个会话域内存字段:
      - conversation_history: 最近 N 轮对话原文（注入 Scheduler 上下文）
      - working_memory: L1 临时记忆条目（尚未合并到 L2）
      - daily_buffer: 原始对话缓冲（供 L2 合并时使用）
    """
    session_id: str = ""
    updated: str = ""
    conversation_history: list[dict] = field(default_factory=list)
    working_memory: list[dict] = field(default_factory=list)
    daily_buffer: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "updated": self.updated or _now_beijing(),
            "conversation_history": self.conversation_history,
            "working_memory": self.working_memory,
            "daily_buffer": self.daily_buffer,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionState:
        return cls(
            session_id=data.get("session_id", ""),
            updated=data.get("updated", ""),
            conversation_history=data.get("conversation_history", []),
            working_memory=data.get("working_memory", []),
            daily_buffer=data.get("daily_buffer", []),
        )


# ═══════════════════════════════════════════════════════════════
# SessionManager
# ═══════════════════════════════════════════════════════════════


class SessionManager:
    """会话管理器 — 单例模式，全局共享

    两级文件架构:
      index.json                → 会话索引（SessionInfo），始终加载
      states/<session_id>.json  → 会话状态（SessionState），按需加载/保存

    用法:
      sm = SessionManager()
      sm.load_index()
      session = sm.get_or_create_default()

      # 保存状态
      state = SessionState(session_id=session.session_id, ...)
      sm.save_state(session.session_id, state)

      # 加载状态
      state = sm.load_state(session.session_id)
    """

    # 索引文件版本号
    INDEX_VERSION = 1

    def __init__(self, data_dir: Optional[Path] = None):
        """初始化会话管理器

        Args:
            data_dir: 数据目录路径，默认 {项目根}/data/sessions/
        """
        if data_dir is None:
            # 项目根目录: src/mia/session/manager.py → .../../../../data/sessions
            _project_root = Path(__file__).parent.parent.parent.parent
            data_dir = _project_root / "data" / "sessions"

        self._data_dir = Path(data_dir)
        self._states_dir = self._data_dir / "states"
        self._index_path = self._data_dir / "index.json"

        # 内存索引
        self._sessions: dict[str, SessionInfo] = {}
        self._current_session_id: Optional[str] = None
        self._loaded: bool = False

    # ─── 生命周期 ──────────────────────────────────────

    def load_index(self) -> None:
        """从磁盘加载会话索引。如果目录/文件不存在则自动创建默认会话。

        这是 SessionManager 的初始化入口，必须在其他操作之前调用。
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._states_dir.mkdir(parents=True, exist_ok=True)

        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                sessions_raw = data.get("sessions", {})
                self._sessions = {
                    sid: SessionInfo.from_dict(sid, sdata)
                    for sid, sdata in sessions_raw.items()
                }
                self._current_session_id = data.get("last_active", "")
                self._loaded = True
                logger.info(
                    "[SessionManager] 已加载 %d 个会话 (last_active=%s)",
                    len(self._sessions),
                    self._current_session_id or "(无)",
                )
                return
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(
                    "[SessionManager] 索引文件损坏，将创建新索引: %s", e
                )

        # 首次启动 — 创建默认会话
        self._sessions = {}
        self._loaded = True
        self.get_or_create_default()
        logger.info("[SessionManager] 已创建默认会话索引")

    def save_index(self) -> None:
        """原子写入索引文件（tmp + rename）"""
        if not self._loaded:
            return

        # 清理 is_active 标记（只有一个会话可以是活跃的）
        for sid, info in self._sessions.items():
            info.is_active = (sid == self._current_session_id)

        data = {
            "version": self.INDEX_VERSION,
            "updated": _now_beijing(),
            "last_active": self._current_session_id,
            "sessions": {
                sid: info.to_dict() for sid, info in self._sessions.items()
            },
        }

        tmp_path = self._index_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._index_path)
        except OSError as e:
            logger.error("[SessionManager] 保存索引失败: %s", e)
            # 清理临时文件
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    # ─── CRUD ───────────────────────────────────────────

    def list_sessions(self) -> list[SessionInfo]:
        """列出所有会话，按最后活跃时间降序排列

        Returns:
            会话信息列表（最新的在前）
        """
        sessions = list(self._sessions.values())
        sessions.sort(key=lambda s: s.updated_at or "", reverse=True)
        return sessions

    def get_session(self, session_id: str) -> Optional[SessionInfo]:
        """根据 ID 获取会话元数据

        Args:
            session_id: 会话 ID

        Returns:
            SessionInfo 或 None（如果不存在）
        """
        return self._sessions.get(session_id)

    def create_session(
        self,
        name: str,
        source: str = "cli",
        session_id: str = "",
    ) -> SessionInfo:
        """创建新会话

        Args:
            name: 会话显示名称
            source: 来源类型 ("cli" / "wechat" / "api")
            session_id: 显式指定 ID（WeChat 用），留空则自动生成 "cli_<8hex>"

        Returns:
            新创建的 SessionInfo

        Raises:
            ValueError: 会话名包含冒号（会干扰渠道路由）
        """
        # 校验名称
        if ":" in name:
            raise ValueError("会话名不能包含冒号")

        # 生成 ID
        if not session_id:
            session_id = f"cli_{uuid.uuid4().hex[:8]}"

        now = _now_beijing()
        info = SessionInfo(
            session_id=session_id,
            name=name.strip()[:50],
            source=source,
            created_at=now,
            updated_at=now,
            turn_count=0,
            is_active=False,
        )

        self._sessions[session_id] = info
        self.save_index()
        logger.info(
            "[SessionManager] 创建会话: %s (%s) [%s]",
            session_id, name, source,
        )
        return info

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其状态文件

        限制:
          - 不能删除最后一个会话（至少保留一个）
          - 如果删除的是当前活跃会话，需要在外部提前切换

        Args:
            session_id: 要删除的会话 ID

        Returns:
            True 如果删除成功，False 如果会话不存在或是最后一个
        """
        if session_id not in self._sessions:
            logger.warning("[SessionManager] 删除失败: 会话不存在 %s", session_id)
            return False

        if len(self._sessions) <= 1:
            logger.warning("[SessionManager] 删除失败: 不能删除最后一个会话")
            return False

        # 删除状态文件
        self._delete_state_file(session_id)

        # 从索引移除
        del self._sessions[session_id]

        # 如果删除的是当前活跃会话，清除指针
        if self._current_session_id == session_id:
            self._current_session_id = None

        self.save_index()
        logger.info("[SessionManager] 已删除会话: %s", session_id)
        return True

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """重命名会话

        Args:
            session_id: 会话 ID
            new_name: 新显示名称

        Returns:
            True 如果重命名成功
        """
        if session_id not in self._sessions:
            return False

        if ":" in new_name:
            logger.warning("[SessionManager] 重命名失败: 名称包含冒号")
            return False

        clean_name = new_name.strip()[:50]
        if not clean_name:
            return False

        info = self._sessions[session_id]
        info.name = clean_name
        info.updated_at = _now_beijing()
        self.save_index()
        logger.info(
            "[SessionManager] 重命名会话: %s → %s", session_id, clean_name,
        )
        return True

    # ─── 自动创建 ───────────────────────────────────────

    def get_or_create_default(self) -> SessionInfo:
        """获取或创建默认 CLI 会话

        策略:
          1. 如果已有 CLI 会话，返回最近的
          2. 否则创建一个名为"默认"的新会话
        自动设置为当前活跃会话（如果还没有活跃会话）。
        """
        # 查找已有的 CLI 会话
        cli_sessions = [
            s for s in self._sessions.values() if s.source == "cli"
        ]
        if cli_sessions:
            cli_sessions.sort(key=lambda s: s.updated_at or "", reverse=True)
            session = cli_sessions[0]
        else:
            # 创建默认会话
            session = self.create_session("新对话", source="cli")

        # 自动设置为当前活跃会话
        if not self._current_session_id:
            self._current_session_id = session.session_id
            self.save_index()  # 更新 is_active 标记

        return session

    def get_or_create_for_id(
        self,
        session_id: str,
        source: str = "wechat",
    ) -> SessionInfo:
        """为给定的 session_id 获取或创建会话注册（WeChat 自动注册用）

        如果 session_id 已存在，更新其活跃时间。
        如果不存在，自动创建新记录。

        Args:
            session_id: 会话 ID（WeChat: "wechat:<user_id>"）
            source: 来源类型

        Returns:
            SessionInfo
        """
        existing = self._sessions.get(session_id)
        if existing:
            existing.updated_at = _now_beijing()
            self.save_index()
            return existing

        # 自动生成名称
        if ":" in session_id:
            user_part = session_id.split(":", 1)[1] if ":" in session_id else session_id
            if source == "wechat":
                name = f"微信 {user_part[:12]}"
            elif source == "telegram":
                name = f"纸飞机 {user_part[:12]}"
            else:
                name = f"{source} {user_part[:12]}"
        else:
            name = f"{source}_{session_id[:8]}"

        return self.create_session(
            name=name,
            source=source,
            session_id=session_id,
        )

    # ─── 活跃会话追踪 ───────────────────────────────────

    def set_current(self, session_id: str) -> None:
        """设置当前活跃会话

        Args:
            session_id: 要激活的会话 ID
        """
        if session_id and session_id not in self._sessions:
            logger.warning(
                "[SessionManager] set_current: 会话不存在 %s", session_id,
            )
            return

        old_id = self._current_session_id
        self._current_session_id = session_id

        # 更新 is_active 标记
        if old_id and old_id in self._sessions:
            self._sessions[old_id].is_active = False
        if session_id and session_id in self._sessions:
            info = self._sessions[session_id]
            info.is_active = True
            info.updated_at = _now_beijing()

        self.save_index()
        logger.debug(
            "[SessionManager] 切换活跃会话: %s → %s", old_id, session_id,
        )

    def get_current(self) -> Optional[SessionInfo]:
        """获取当前活跃会话的元数据"""
        if not self._current_session_id:
            return None
        return self._sessions.get(self._current_session_id)

    def get_current_session_id(self) -> Optional[str]:
        """获取当前活跃会话的 ID（便捷方法）"""
        return self._current_session_id

    def get_last_active(self) -> Optional[str]:
        """从索引读取上次活跃会话 ID（用于启动恢复）"""
        return self._current_session_id or (
            self._sessions.get("last_active", None)  # unused, we track in memory
        )

    # ─── 状态持久化 ────────────────────────────────────

    def _state_path(self, session_id: str) -> Path:
        """获取会话状态文件路径

        Note: 文件名包含冒号时（WeChat session_id），Windows 不支持，
        因此将冒号替换为下划线。
        """
        safe_id = session_id.replace(":", "_")
        return self._states_dir / f"{safe_id}.json"

    def save_state(self, session_id: str, state: SessionState) -> None:
        """原子写入会话状态文件

        同时更新 SessionInfo 的 updated_at 和 turn_count。

        Args:
            session_id: 会话 ID
            state: 会话运行时状态
        """
        if not self._loaded:
            return

        state_path = self._state_path(session_id)
        data = state.to_dict()
        data["updated"] = _now_beijing()

        # 更新索引中的统计信息
        info = self._sessions.get(session_id)
        if info:
            info.updated_at = data["updated"]
            if state.conversation_history:
                # turn_count = conversation_history 中的轮数
                info.turn_count = len(state.conversation_history)

        # 原子写入
        tmp_path = state_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(state_path)
        except OSError as e:
            logger.error(
                "[SessionManager] 保存状态失败 (%s): %s", session_id, e,
            )
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        # 保存索引（更新 updated_at）
        self.save_index()

    def load_state(self, session_id: str) -> Optional[SessionState]:
        """从磁盘加载会话状态

        Args:
            session_id: 会话 ID

        Returns:
            SessionState 或 None（文件不存在/损坏）
        """
        state_path = self._state_path(session_id)

        if not state_path.exists():
            logger.debug(
                "[SessionManager] 状态文件不存在: %s", state_path.name,
            )
            return None

        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            state = SessionState.from_dict(data)
            logger.debug(
                "[SessionManager] 已加载状态: %s "
                "(hist=%d, working=%d, buffer=%d)",
                session_id,
                len(state.conversation_history),
                len(state.working_memory),
                len(state.daily_buffer),
            )
            return state
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                "[SessionManager] 状态文件损坏 (%s): %s", session_id, e,
            )
            return None

    def _delete_state_file(self, session_id: str) -> None:
        """删除会话状态文件（不抛异常）"""
        state_path = self._state_path(session_id)
        if state_path.exists():
            try:
                state_path.unlink()
            except OSError as e:
                logger.warning(
                    "[SessionManager] 删除状态文件失败 (%s): %s",
                    session_id, e,
                )

    def increment_turn(self, session_id: str) -> None:
        """增加会话的对话轮数计数（便捷方法）"""
        info = self._sessions.get(session_id)
        if info:
            info.turn_count += 1
            info.updated_at = _now_beijing()
