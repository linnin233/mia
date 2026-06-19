"""
消息类型定义 — MessageBus 上流转的所有消息结构

定义了系统中所有 Agent 之间通信的消息格式。
"""

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


class MessageType(enum.Enum):
    """消息类型枚举 — 定义所有可能的 Agent 间通信类型"""

    # ─── Receiver → Scheduler ───────────────────────────
    USER_INTENT = "user_intent"
    """用户意图消息: Receiver 理解用户输入后发送给 Scheduler"""

    # ─── Scheduler → Sender ─────────────────────────────
    SEND_TEXT = "send_text"
    """文本回复指令: Scheduler 要求 Sender 发送文本消息"""

    SEND_VOICE = "send_voice"
    """语音回复指令: Scheduler 要求 Sender 发送语音消息"""

    # ─── Scheduler → Sender (流式) ──────────────────────
    STREAM_START = "stream_start"
    """流式回复开始: Scheduler 通知 Sender 准备接收流式文本"""

    STREAM_CHUNK = "stream_chunk"
    """流式文本块: Scheduler 推送一个文本增量 (delta) 给 Sender"""

    STREAM_END = "stream_end"
    """流式回复结束: Scheduler 通知 Sender 流式文本已完成"""

    # ─── TUI 界面消息 (Scheduler/Task/Memory → TUI App) ──
    TUI_THOUGHT = "tui_thought"
    """思考过程: Agent 向 TUI 发送思考/推理过程 (可折叠展示)"""

    TUI_TOOL = "tui_tool"
    """工具调用: TaskAgent 向 TUI 发送工具调用和结果"""

    TUI_TOAST = "tui_toast"
    """通知提示: 向 TUI 发送 toast 通知 (info/success/warning/error)"""

    TUI_STATUS = "tui_status"
    """状态栏更新: 向 TUI 发送状态栏键值对更新"""

    # ─── Scheduler ⇄ TaskAgent ─────────────────────────
    EXECUTE_TASK = "execute_task"
    """任务执行指令: Scheduler 要求 TaskAgent 执行任务"""

    TASK_RESULT = "task_result"
    """任务执行结果: TaskAgent 返回执行结果给 Scheduler"""

    TASK_ERROR = "task_error"
    """任务执行错误: TaskAgent 返回错误信息给 Scheduler"""

    # ─── 系统消息 ───────────────────────────────────────
    SYSTEM_READY = "system_ready"
    """Agent 启动完成通知"""

    SYSTEM_SHUTDOWN = "system_shutdown"
    """Agent 停止通知"""

    CONVERSATION_DONE = "conversation_done"
    """对话完成通知 (Sender → Main)"""

    # ─── 用户输入 (内部) ───────────────────────────────
    RAW_INPUT = "raw_input"
    """原始用户输入: CLI/API 层发给 Receiver 的原始消息"""


@dataclass
class Message:
    """消息 — MessageBus 上流转的标准数据单元

    所有 Agent 之间通过 Message 通信，不直接调用对方的方法。
    每条消息有唯一 ID、明确的来源/目标，以及结构化的 payload。
    """

    msg_type: MessageType
    """消息类型"""

    source: str
    """发送方名称 (如 'receiver', 'scheduler', 'task_agent')"""

    target: str
    """目标名称 (如 'scheduler', 'sender') 或 'broadcast' (广播)"""

    payload: dict = field(default_factory=dict)
    """消息负载 — 具体数据，格式因 msg_type 而异"""

    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    """消息唯一 ID (12 位 hex)"""

    timestamp: float = field(default_factory=time.time)
    """消息创建时间 (Unix timestamp)"""

    # ─── 关联字段 (可选) ────────────────────────────────

    parent_id: Optional[str] = None
    """父消息 ID — 用于追踪任务链 (EXECUTE_TASK → TASK_RESULT)"""

    session_id: Optional[str] = None
    """会话 ID — 用于多轮对话关联"""

    def __repr__(self) -> str:
        return (
            f"Message({self.msg_type.value}, "
            f"{self.source} → {self.target}, "
            f"id={self.msg_id})"
        )


# ─── Payload 工厂函数 ───────────────────────────────────
# 提供类型安全的 payload 构建方式


def make_user_intent(
    original: str,
    intent: str,
    media_refs: Optional[list[str]] = None,
    session_id: Optional[str] = None,
) -> Message:
    """构建 USER_INTENT 消息

    Args:
        original: 用户原始输入文本
        intent: Receiver 理解后的意图描述
        media_refs: 关联的媒体文件路径列表 (图片/音频)
        session_id: 会话 ID
    """
    # 注意: target 设为 "memory_agent"，而非直接 "scheduler"
    # MemoryAgent 会拦截 USER_INTENT，检索记忆上下文，
    # 然后将 enriched 消息转发给 Scheduler
    return Message(
        msg_type=MessageType.USER_INTENT,
        source="receiver",
        target="memory_agent",
        payload={
            "original": original,
            "intent": intent,
            "media_refs": media_refs or [],
        },
        session_id=session_id,
    )


def make_send_text(
    message: str,
    session_id: Optional[str] = None,
) -> Message:
    """构建 SEND_TEXT 消息

    Args:
        message: 要发送给用户的文本内容
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.SEND_TEXT,
        source="scheduler",
        target="sender",
        payload={"message": message},
        session_id=session_id,
    )


def make_send_voice(
    message: str,
    voice: str = "冰糖",
    audio_format: str = "wav",
    session_id: Optional[str] = None,
) -> Message:
    """构建 SEND_VOICE 消息

    Args:
        message: 要合成语音的文本
        voice: 音色 ID
        audio_format: 输出格式 (wav/pcm16)
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.SEND_VOICE,
        source="scheduler",
        target="sender",
        payload={
            "message": message,
            "voice": voice,
            "format": audio_format,
        },
        session_id=session_id,
    )


def make_execute_task(
    task: str,
    tools_hint: Optional[list[str]] = None,
    parent_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Message:
    """构建 EXECUTE_TASK 消息

    Args:
        task: 任务描述 (给 TaskAgent 看)
        tools_hint: 建议使用的工具列表
        parent_id: 父消息 ID (关联的 USER_INTENT 或上一个 TASK_RESULT)
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.EXECUTE_TASK,
        source="scheduler",
        target="task_agent",
        payload={
            "task": task,
            "tools_hint": tools_hint or [],
        },
        parent_id=parent_id,
        session_id=session_id,
    )


def make_task_result(
    task_id: str,
    result: str,
    tool_calls: Optional[list[dict]] = None,
    session_id: Optional[str] = None,
) -> Message:
    """构建 TASK_RESULT 消息

    Args:
        task_id: 原 EXECUTE_TASK 的 msg_id
        result: 任务执行结果文本
        tool_calls: 工具调用记录列表
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.TASK_RESULT,
        source="task_agent",
        target="scheduler",
        payload={
            "task_id": task_id,
            "result": result,
            "tool_calls": tool_calls or [],
        },
        parent_id=task_id,
        session_id=session_id,
    )


def make_task_error(
    task_id: str,
    error: str,
    session_id: Optional[str] = None,
) -> Message:
    """构建 TASK_ERROR 消息

    Args:
        task_id: 原 EXECUTE_TASK 的 msg_id
        error: 错误描述
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.TASK_ERROR,
        source="task_agent",
        target="scheduler",
        payload={
            "task_id": task_id,
            "error": error,
        },
        parent_id=task_id,
        session_id=session_id,
    )


# ─── 流式消息工厂函数 ─────────────────────────────────


def make_stream_start(session_id: Optional[str] = None) -> Message:
    """构建 STREAM_START 消息 — 通知 Sender 准备接收流式文本

    Args:
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.STREAM_START,
        source="scheduler",
        target="sender",
        payload={},
        session_id=session_id,
    )


def make_stream_chunk(delta: str, session_id: Optional[str] = None) -> Message:
    """构建 STREAM_CHUNK 消息 — 推送一个文本增量

    Args:
        delta: 增量文本 (LLM 流式输出的一个 chunk)
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.STREAM_CHUNK,
        source="scheduler",
        target="sender",
        payload={"delta": delta},
        session_id=session_id,
    )


def make_stream_end(
    full_message: str,
    session_id: Optional[str] = None,
) -> Message:
    """构建 STREAM_END 消息 — 通知 Sender 流式文本已完成

    Args:
        full_message: 完整的回复文本 (供 MemoryAgent 存储)
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.STREAM_END,
        source="scheduler",
        target="sender",
        payload={"message": full_message},
        session_id=session_id,
    )


# ─── TUI 消息工厂函数 ─────────────────────────────────


def make_tui_thought(
    agent: str,
    title: str,
    detail: str = "",
    session_id: Optional[str] = None,
) -> Message:
    """构建 TUI_THOUGHT 消息 — Agent 思考过程

    Args:
        agent: Agent 名称 (scheduler/task/memory/receiver)
        title: 思考标题 (如 "分析用户意图")
        detail: 思考详情 (如 reasoning 文本)
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.TUI_THOUGHT,
        source=agent,
        target="tui",
        payload={
            "agent": agent,
            "title": title,
            "detail": detail,
        },
        session_id=session_id,
    )


def make_tui_tool(
    tool_name: str,
    tool_args: str = "",
    result: str = "",
    status: str = "running",
    session_id: Optional[str] = None,
) -> Message:
    """构建 TUI_TOOL 消息 — 工具调用和结果

    Args:
        tool_name: 工具名称 (web_search/weather/shell/file)
        tool_args: 工具调用参数
        result: 工具执行结果文本
        status: 状态 (running/success/error)
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.TUI_TOOL,
        source="task_agent",
        target="tui",
        payload={
            "tool_name": tool_name,
            "tool_args": tool_args,
            "result": result,
            "status": status,
        },
        session_id=session_id,
    )


def make_tui_toast(
    level: str,
    message: str,
    session_id: Optional[str] = None,
) -> Message:
    """构建 TUI_TOAST 消息 — 通知提示

    Args:
        level: 通知级别 (info/success/warning/error)
        message: 通知文本
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.TUI_TOAST,
        source="system",
        target="tui",
        payload={
            "level": level,
            "message": message,
        },
        session_id=session_id,
    )


def make_tui_status(
    key: str,
    value: str,
    session_id: Optional[str] = None,
) -> Message:
    """构建 TUI_STATUS 消息 — 状态栏更新

    Args:
        key: 状态键 (memory/model/session)
        value: 状态值
        session_id: 会话 ID
    """
    return Message(
        msg_type=MessageType.TUI_STATUS,
        source="system",
        target="tui",
        payload={
            "key": key,
            "value": value,
        },
        session_id=session_id,
    )
