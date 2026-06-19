"""
MIA TUI Application — 基于 Textual 的交互式终端界面

参照 OpenCode TUI 的设计模式:
  - 顶部状态栏 (session/model/memory)
  - 中间聊天历史面板 (消息气泡 + 可折叠思考/工具调用)
  - 底部输入区域 (文本输入 + 发送按钮)
  - 流式文本实时追加
  - Toast 通知

用法:
    from mia.tui.app import MiaTuiApp
    app = MiaTuiApp()
    await app.run_async()
"""

import asyncio
import sys
import uuid
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, ScrollableContainer
from textual.widgets import Header, Footer, Static

from loguru import logger

from mia.config import get_config
from mia.bus.bus import MessageBus
from mia.bus.message import (
    Message,
    MessageType,
    make_tui_thought,
    make_tui_tool,
    make_tui_toast,
    make_tui_status,
)
from mia.providers.mimo import MiMoProvider
from mia.providers.deepseek import DeepSeekProvider
from mia.agents.receiver import ReceiverAgent
from mia.agents.scheduler import SchedulerAgent
from mia.agents.sender import SenderAgent
from mia.agents.task import TaskAgent
from mia.agents.memory import MemoryAgent

from mia.tui.widgets import (
    StatusBar,
    MessageBubble,
    ThoughtSection,
    ToolCallSection,
    StreamingText,
    InputArea,
)


class MiaTuiApp(App):
    """MIA 主 TUI 应用

    完整的 AI 对话终端界面，集成 MIA 的 MessageBus + Agent 系统。
    """

    CSS_PATH = str(Path(__file__).parent / "theme.css")

    BINDINGS = [
        ("ctrl+c", "quit_app", "退出"),
        ("ctrl+q", "quit_app", "退出"),
        ("escape", "focus_input", "聚焦输入框"),
    ]

    # 最大聊天消息数（防止内存泄漏）
    MAX_CHAT_MESSAGES = 100

    def __init__(self) -> None:
        super().__init__()
        self.config = get_config()

        # Agent 系统组件 (在 on_mount 中初始化)
        self.bus: MessageBus | None = None
        self.mimo: MiMoProvider | None = None
        self.deepseek: DeepSeekProvider | None = None
        self.receiver: ReceiverAgent | None = None
        self.memory_agent: MemoryAgent | None = None
        self.scheduler: SchedulerAgent | None = None
        self.sender: SenderAgent | None = None
        self.task_agent: TaskAgent | None = None
        self._agent_tasks: list[asyncio.Task] = []
        self._running = False

        # 当前流式消息状态
        self._current_stream: StreamingText | None = None
        self._message_count = 0

    # ─── Compose: 布局 ──────────────────────────────────

    def compose(self) -> ComposeResult:
        """构建 TUI 布局"""
        # 使用内置 Header (显示时钟 + 标题)
        yield Header(show_clock=True, name="MIA")

        # 主内容区
        with Vertical():
            # 聊天历史 (可滚动)
            yield ScrollableContainer(id="chat-history")

            # 输入区域
            yield InputArea(id="input-area")

        # 我们使用 Textual 内置 Footer 来显示快捷键
        yield Footer()

    # ─── 生命周期 ──────────────────────────────────────

    async def on_mount(self) -> None:
        """TUI 挂载后启动 Agent 系统"""
        # 设置 Header 标题
        try:
            header = self.query_one(Header)
            header.title = "MIA — Modular Intelligent Agent"
        except Exception:
            pass

        # 更新状态栏
        try:
            status = StatusBar()
            await self.mount(status)
        except Exception:
            pass

        # 启动 Agent 系统
        await self._start_agent_system()

        # 订阅消息总线 (两个主题)
        await self.bus.subscribe("tui")
        await self.bus.subscribe("sender")

        # 启动消息处理 worker
        self._running = True
        self.run_worker(self._process_bus_messages(), exclusive=False)

        # 显示欢迎消息
        self._add_system_message(
            "MIA v0.2.0 已就绪\n"
            f"  模型: {self.config.mimo.chat_model}\n"
            f"  TUI: Textual 模式\n"
            f"  输入 /help 查看命令, /quit 退出"
        )

        # 聚焦输入框
        try:
            input_area = self.query_one("#input-area", InputArea)
            input_area.focus_input()
        except Exception:
            pass

    async def on_unmount(self) -> None:
        """TUI 关闭时清理 Agent 系统"""
        self._running = False
        await self._stop_agent_system()

    # ─── Agent 系统生命周期 ────────────────────────────

    async def _start_agent_system(self) -> None:
        """启动完整的 Agent 系统 (与 main.py 能力一致)"""
        config = self.config

        # 创建 MessageBus
        self.bus = MessageBus(max_queue_size=100)
        await self.bus.start()

        # 创建 Providers
        self.mimo = MiMoProvider(api_key=config.mimo.api_key)
        self.deepseek = DeepSeekProvider(api_key=config.deepseek.api_key)

        # 创建 Agents
        self.receiver = ReceiverAgent(bus=self.bus, mimo=self.mimo)
        self.scheduler = SchedulerAgent(
            bus=self.bus,
            provider=self.mimo,
            model=config.mimo.chat_model,
            fallback_provider=self.deepseek,
            fallback_model=config.deepseek.chat_model,
            enable_streaming=config.agent.enable_streaming,
        )
        self.sender = SenderAgent(
            bus=self.bus,
            mimo=self.mimo,
            output_dir=config.agent.workspace_dir,
        )
        self.task_agent = TaskAgent(
            bus=self.bus,
            provider=self.mimo,
            model=config.mimo.chat_model,
            fallback_provider=self.deepseek,
            fallback_model=config.deepseek.chat_model,
        )
        self.memory_agent = MemoryAgent(
            bus=self.bus,
            provider=self.mimo,
            model=config.mimo.chat_model,
            fallback_provider=self.deepseek,
            fallback_model=config.deepseek.chat_model,
        )

        # 启动所有 Agent
        await self.receiver.start()
        await self.memory_agent.start()
        await self.scheduler.start()
        await self.sender.start()
        await self.task_agent.start()

        # 后台消息处理循环
        for agent in [
            self.receiver,
            self.memory_agent,
            self.scheduler,
            self.sender,
            self.task_agent,
        ]:
            task = asyncio.create_task(agent.run())
            self._agent_tasks.append(task)

        # 等待 Agent 就绪
        await asyncio.sleep(0.3)

        logger.info("[TUI] Agent 系统启动完成")

    async def _stop_agent_system(self) -> None:
        """关闭 Agent 系统"""
        logger.info("[TUI] 正在关闭 Agent 系统...")

        # 停止所有 Agent
        for agent in [
            self.receiver,
            self.memory_agent,
            self.scheduler,
            self.sender,
            self.task_agent,
        ]:
            if agent:
                try:
                    await agent.stop()
                except Exception:
                    pass

        # 取消后台任务
        for task in self._agent_tasks:
            task.cancel()
        if self._agent_tasks:
            await asyncio.gather(*self._agent_tasks, return_exceptions=True)

        # 停止总线
        if self.bus:
            await self.bus.stop()

        logger.info("[TUI] Agent 系统已关闭")

    # ─── 消息处理 Worker ───────────────────────────────

    async def _process_bus_messages(self) -> None:
        """后台 Worker: 从 MessageBus 接收消息并更新 TUI

        订阅 "tui" (TUI 专用消息) 和 "sender" (流式输出消息) 两个主题。
        """
        while self._running:
            try:
                # 检查 "tui" 主题
                msg = await self.bus.receive("tui", timeout=0.05)
                if msg:
                    await self._handle_tui_message(msg)

                # 检查 "sender" 主题 (流式消息和 CONVERSATION_DONE)
                msg = await self.bus.receive("sender", timeout=0.05)
                if msg:
                    await self._handle_sender_message(msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[TUI] 消息处理异常: {}", e)

        logger.debug("[TUI] 消息处理 Worker 退出")

    async def _handle_tui_message(self, msg: Message) -> None:
        """处理 TUI 专用消息"""
        msg_type = msg.msg_type

        if msg_type == MessageType.TUI_THOUGHT:
            self._add_thought(
                agent=msg.payload.get("agent", "?"),
                title=msg.payload.get("title", ""),
                detail=msg.payload.get("detail", ""),
            )

        elif msg_type == MessageType.TUI_TOOL:
            self._add_tool_call(
                tool_name=msg.payload.get("tool_name", "?"),
                tool_args=msg.payload.get("tool_args", ""),
                result=msg.payload.get("result", ""),
                status=msg.payload.get("status", "running"),
            )

        elif msg_type == MessageType.TUI_TOAST:
            level = msg.payload.get("level", "info")
            message = msg.payload.get("message", "")
            self._show_toast(level, message)

        elif msg_type == MessageType.TUI_STATUS:
            key = msg.payload.get("key", "")
            value = msg.payload.get("value", "")
            self._update_status(key, value)

    async def _handle_sender_message(self, msg: Message) -> None:
        """处理来自 Sender 的消息"""
        msg_type = msg.msg_type

        if msg_type == MessageType.STREAM_START:
            # 开始新的流式回复
            self._start_stream()

        elif msg_type == MessageType.STREAM_CHUNK:
            # 追加流式文本增量
            delta = msg.payload.get("delta", "")
            self._append_stream(delta)

        elif msg_type == MessageType.STREAM_END:
            # 流式回复完成 → 转为永久消息气泡
            message = msg.payload.get("message", "")
            self._end_stream(message)

        elif msg_type == MessageType.SEND_TEXT:
            # 非流式文本回复 (spice 模式或 fallback)
            message = msg.payload.get("message", "")
            self._add_assistant_message(message)

        elif msg_type == MessageType.CONVERSATION_DONE:
            # 对话完成 (SenderAgent 已发布给 main 和 memory_agent，
            # 我们这里只做记录，不做额外操作)
            pass

        elif msg_type == MessageType.TASK_ERROR:
            error = msg.payload.get("error", "")
            self._show_toast("error", f"任务错误: {error}")

    # ─── 用户输入处理 ──────────────────────────────────

    def on_input_area_submitted(self, event: InputArea.Submitted) -> None:
        """用户提交了文本消息"""
        text = event.text
        session_id = uuid.uuid4().hex[:12]

        # 添加用户消息气泡到聊天历史
        self._add_user_message(text)

        # 发布到 MessageBus
        if self.bus:
            raw_msg = Message(
                msg_type=MessageType.RAW_INPUT,
                source="tui",
                target="receiver",
                payload={
                    "text": text,
                    "image": None,
                    "voice": None,
                },
                session_id=session_id,
            )
            asyncio.create_task(self.bus.publish(raw_msg))

    def on_input_area_command(self, event: InputArea.Command) -> None:
        """用户输入了斜杠命令"""
        command = event.command.lower()
        self._handle_command(command)

    # ─── 命令处理 ──────────────────────────────────────

    def _handle_command(self, command: str) -> None:
        """处理 / 命令"""
        if command in ("/quit", "/exit", "/q"):
            self._add_system_message("再见~")
            asyncio.create_task(self._delayed_quit())

        elif command in ("/help", "/h"):
            help_text = (
                "**MIA 命令列表**\n\n"
                "  `/quit`, `/exit`, `/q` — 退出\n"
                "  `/help`, `/h` — 显示帮助\n"
                "  `/compact` — 压缩对话历史 (将多轮对话总结为摘要)\n"
                "  `/memory` — 查看记忆状态\n"
                "  `/image <path>` — 发送图片\n"
                "  Ctrl+C — 退出\n"
                "  Esc — 聚焦输入框"
            )
            self._add_system_message(help_text)

        elif command == "/compact":
            self._add_system_message("正在压缩对话历史...")
            if self.memory_agent:
                asyncio.create_task(self._do_compact())

        elif command == "/memory":
            if self.memory_agent:
                w = len(self.memory_agent._working_memory)
                p = self.memory_agent.store.count
                h = len(self.memory_agent._conversation_history)
                # 列出临时记忆
                parts = [f"**记忆状态**: 临时记忆 {w} 条, 持久知识 {p} 条, 对话历史 {h} 轮"]
                for i, entry in enumerate(self.memory_agent._working_memory):
                    parts.append(
                        f"  [{entry.category_label}] {entry.content[:80]} "
                        f"(confidence={entry.confidence:.1f})"
                    )
                self._add_system_message("\n".join(parts))
            else:
                self._add_system_message("记忆系统未就绪")

        elif command.startswith("/image "):
            # TODO: 图片输入
            self._show_toast("warning", "图片输入功能开发中...")

        else:
            # 未知命令，尝试模糊匹配
            known = ["/quit", "/exit", "/q", "/help", "/h", "/compact", "/memory", "/image"]
            suggestions = [c for c in known if c.startswith(command[:3])]
            if suggestions:
                self._add_system_message(
                    f"未知命令 `{command}`，你是想输入 {' / '.join(suggestions[:3])} 吗？"
                )
            else:
                self._add_system_message(f"未知命令 `{command}`，输入 /help 查看可用命令")

    async def _do_compact(self) -> None:
        """执行 /compact 操作"""
        try:
            summary = await self.memory_agent.compact()
            new_count = self.memory_agent.store.count
            self._add_system_message(
                f"对话历史已压缩\n"
                f"  摘要: {summary[:200]}...\n"
                f"  持久知识: {new_count} 条"
            )
        except Exception as e:
            self._show_toast("error", f"压缩失败: {e}")

    async def _delayed_quit(self) -> None:
        """延迟退出 (让用户看到再见消息)"""
        await asyncio.sleep(0.5)
        await self.action_quit_app()

    # ─── UI 更新方法 ───────────────────────────────────

    def _add_user_message(self, text: str) -> None:
        """添加用户消息气泡到聊天面板"""
        bubble = MessageBubble(role="user", content=text, role_label="You")
        self._append_to_chat(bubble)

    def _add_assistant_message(self, text: str) -> None:
        """添加 AI 回复消息气泡到聊天面板"""
        bubble = MessageBubble(role="assistant", content=text, role_label="MIA")
        self._append_to_chat(bubble)

    def _add_system_message(self, text: str) -> None:
        """添加系统消息 (灰色提示) 到聊天面板"""
        msg = Static(
            f"[dim italic]{text}[/dim italic]",
            classes="system-message",
        )
        self._append_to_chat(msg)

    def _add_thought(self, agent: str, title: str, detail: str) -> None:
        """添加可折叠的思考过程区块"""
        section = ThoughtSection(agent=agent, title=title, detail=detail)
        self._append_to_chat(section)

    def _add_tool_call(
        self,
        tool_name: str,
        tool_args: str,
        result: str,
        status: str,
    ) -> None:
        """添加可折叠的工具调用区块"""
        section = ToolCallSection(
            tool_name=tool_name,
            tool_args=tool_args,
            result=result,
            status=status,
        )
        self._append_to_chat(section)

    def _start_stream(self) -> None:
        """开始新的流式回复 — 创建 StreamingText widget"""
        self._current_stream = StreamingText()
        self._append_to_chat(self._current_stream)

    def _append_stream(self, delta: str) -> None:
        """追加流式文本增量"""
        if self._current_stream:
            self._current_stream.append(delta)
            # 自动滚动到底部
            try:
                chat = self.query_one("#chat-history", ScrollableContainer)
                chat.scroll_end(animate=False)
            except Exception:
                pass

    def _end_stream(self, full_text: str) -> None:
        """流式回复完成 — 将 StreamingText 转为 MessageBubble"""
        # 移除 StreamingText widget
        if self._current_stream:
            try:
                self._current_stream.remove()
            except Exception:
                pass

        # 替换为永久消息气泡
        if full_text:
            bubble = MessageBubble(
                role="assistant",
                content=full_text,
                role_label="MIA",
            )
            self._append_to_chat(bubble)

        self._current_stream = None

    def _show_toast(self, level: str, message: str) -> None:
        """显示 Toast 通知"""
        try:
            self.notify(
                message,
                severity=level if level in ("information", "warning", "error") else "information",
                timeout=5,
            )
        except Exception:
            pass

    def _update_status(self, key: str, value: str) -> None:
        """更新状态栏"""
        try:
            # 使用 Footer 的 highlight 区域显示状态
            # 简单方案：直接在 chat 中不显示，用 Header subtitle
            subtitle_parts = []
            if key == "memory":
                subtitle_parts.append(f"记忆: {value}")
            elif key == "model":
                subtitle_parts.append(f"模型: {value}")
            else:
                subtitle_parts.append(f"{key}: {value}")
            header = self.query_one(Header)
            if subtitle_parts:
                header.sub_title = " │ ".join(subtitle_parts)
        except Exception:
            pass

    def _append_to_chat(self, widget) -> None:
        """向聊天面板追加一个 widget，自动滚动"""
        try:
            chat = self.query_one("#chat-history", ScrollableContainer)
            chat.mount(widget)
            self._message_count += 1

            # 限制消息数量，移除旧消息
            if self._message_count > self.MAX_CHAT_MESSAGES:
                children = list(chat.children)
                if len(children) > self.MAX_CHAT_MESSAGES:
                    # 移除最旧的 20 条
                    for old in children[:20]:
                        try:
                            old.remove()
                        except Exception:
                            pass
                    self._message_count = len(list(chat.children))

            # 自动滚动到底部
            chat.scroll_end(animate=False)
        except Exception as e:
            logger.debug("[TUI] append_to_chat 失败: {}", e)

    # ─── Actions ────────────────────────────────────────

    def action_focus_input(self) -> None:
        """聚焦到输入框"""
        try:
            input_area = self.query_one("#input-area", InputArea)
            input_area.focus_input()
        except Exception:
            pass

    async def action_quit_app(self) -> None:
        """退出 TUI 应用"""
        self._add_system_message("正在退出...")
        await asyncio.sleep(0.3)
        await self._stop_agent_system()
        self.exit()
