"""
MIA TUI Application — Static Widget + ScrollableContainer 聊天界面

基于 ChatUI (github.com/ApollonGT/chatui) 的 Static mount 模式:
  - 每条消息是一个 Static widget，mount 到 ScrollableContainer
  - 不用 RichLog.write()，避免文本拼接问题
  - 流式输出通过 update() 更新 Static 文本

用法:
    from mia.tui.app import MiaTuiApp
    app = MiaTuiApp()
    await app.run_async()
"""

import asyncio
import uuid
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Header, Static, Input, Button

from loguru import logger

from mia.config import get_config
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.providers.mimo import MiMoProvider
from mia.providers.deepseek import DeepSeekProvider
from mia.agents.receiver import ReceiverAgent
from mia.agents.scheduler import SchedulerAgent
from mia.agents.sender import SenderAgent
from mia.agents.task import TaskAgent
from mia.agents.memory import MemoryAgent


class MiaTuiApp(App):
    """MIA 主 TUI — Static widget 聊天界面"""

    CSS = """
    Screen { background: #1a1b26; color: #c0caf5; }

    Header { dock: top; }

    #chat-history {
        height: 1fr;
        padding: 1 2;
    }

    #input-container {
        dock: bottom;
        height: 3;
        background: #16161e;
        padding: 0 1;
        border-top: solid #3b4261;
    }
    #user-input {
        width: 1fr;
        background: #1a1b26;
        color: #c0caf5;
        border: solid #3b4261;
        margin: 0 1 0 0;
    }
    #user-input:focus { border: solid #7aa2f7; }
    #send-button { width: 8; background: #7aa2f7; color: #1a1b26; text-style: bold; }
    #send-button:hover { background: #9ece6a; }

    /* 消息样式 */
    .msg-user { color: #7aa2f7; margin: 0 0 1 0; }
    .msg-mia { color: #9ece6a; margin: 0 0 1 0; }
    .msg-thought { color: #7dcfff; margin: 0 0 0 2; }
    .msg-tool { color: #e0af68; margin: 0 0 0 2; }
    .msg-system { color: #565f89; margin: 0 0 1 0; }
    .msg-error { color: #f7768e; margin: 0 0 1 0; }
    """

    BINDINGS = [
        ("ctrl+c", "quit_app", "退出"),
        ("ctrl+q", "quit_app", "退出"),
        ("escape", "focus_input", "聚焦输入"),
    ]

    MAX_MESSAGES = 200  # 最多保留消息 widget 数

    def __init__(self) -> None:
        super().__init__()
        self.config = get_config()

        # Agent 系统
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

        # 流式状态
        self._stream_widget: Static | None = None
        self._stream_text: str = ""
        self._msg_count = 0

    # ─── Compose ────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield ScrollableContainer(id="chat-history")
        with Horizontal(id="input-container"):
            yield Input(
                placeholder="输入消息...  /help 查看命令",
                id="user-input",
            )
            yield Button("发送", id="send-button", variant="primary")

    # ─── 生命周期 ──────────────────────────────────────

    async def on_mount(self) -> None:
        # 抑制 loguru stderr
        logger.remove()
        log_dir = Path(__file__).parent.parent.parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "mia-tui.log",
            rotation="10 MB", retention="3 days", level="DEBUG",
            format="{time} | {level} | {name}:{function}:{line} - {message}",
        )

        try:
            self.query_one(Header).title = "MIA — Modular Intelligent Agent"
        except Exception:
            pass

        await self._start_agent_system()

        await self.bus.subscribe("tui")
        await self.bus.subscribe("sender")

        self._running = True
        self.run_worker(self._process_bus_messages(), exclusive=False)

        self._mount_text(
            f"MIA v0.2.0 已就绪  |  模型: {self.config.mimo.chat_model}  "
            f"|  /help 命令  |  /quit 退出",
            "msg-system",
        )

        self._focus_input()

    async def on_unmount(self) -> None:
        self._running = False
        await self._stop_agent_system()

    # ─── 输入处理 ──────────────────────────────────────

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-button":
            await self._submit()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._submit()

    async def _submit(self) -> None:
        try:
            inp = self.query_one("#user-input", Input)
            text = inp.value.strip()
            if not text:
                return
            inp.value = ""

            if text.startswith("/"):
                self._handle_command(text.lower())
            else:
                self._send_message(text)
        except Exception:
            pass

    def _send_message(self, text: str) -> None:
        session_id = uuid.uuid4().hex[:12]

        # 显示用户消息
        self._mount_text(f"You: {text}", "msg-user")

        # 发布到 MessageBus
        if self.bus:
            asyncio.create_task(self.bus.publish(Message(
                msg_type=MessageType.RAW_INPUT,
                source="tui", target="receiver",
                payload={"text": text, "image": None, "voice": None},
                session_id=session_id,
            )))

    # ─── 命令 ──────────────────────────────────────────

    def _handle_command(self, command: str) -> None:
        if command in ("/quit", "/exit", "/q"):
            self._mount_text("再见~", "msg-system")
            asyncio.create_task(self._delayed_quit())

        elif command in ("/help", "/h"):
            self._mount_text(
                "/quit /exit /q — 退出\n"
                "/help /h — 帮助\n"
                "/compact — 压缩对话历史\n"
                "/memory — 查看记忆状态\n"
                "Ctrl+C — 退出  Esc — 聚焦输入",
                "msg-system",
            )

        elif command == "/compact":
            if self.memory_agent:
                self._mount_text("正在压缩对话历史...", "msg-system")
                asyncio.create_task(self._do_compact())
            else:
                self._mount_text("记忆系统未就绪", "msg-error")

        elif command == "/memory":
            if self.memory_agent:
                w = len(self.memory_agent._working_memory)
                p = self.memory_agent.store.count
                h = len(self.memory_agent._conversation_history)
                lines = [f"记忆状态  临时:{w}条  持久:{p}条  历史:{h}轮"]
                for e in self.memory_agent._working_memory:
                    lines.append(
                        f"  [{e.category_label}] {e.content[:80]}"
                    )
                self._mount_text("\n".join(lines), "msg-system")
            else:
                self._mount_text("记忆系统未就绪", "msg-error")

        else:
            self._mount_text(f"未知命令 '{command}'，/help 查看", "msg-system")

    async def _do_compact(self) -> None:
        try:
            summary = await self.memory_agent.compact()
            new_count = self.memory_agent.store.count
            self._mount_text(
                f"对话历史已压缩  持久知识:{new_count}条\n  {summary[:200]}...",
                "msg-system",
            )
        except Exception as e:
            self._mount_text(f"压缩失败: {e}", "msg-error")

    async def _delayed_quit(self) -> None:
        await asyncio.sleep(0.5)
        await self.action_quit_app()

    # ─── Agent 系统 ────────────────────────────────────

    async def _start_agent_system(self) -> None:
        config = self.config
        self.bus = MessageBus(max_queue_size=100)
        await self.bus.start()

        self.mimo = MiMoProvider(api_key=config.mimo.api_key)
        self.deepseek = DeepSeekProvider(api_key=config.deepseek.api_key)

        self.receiver = ReceiverAgent(bus=self.bus, mimo=self.mimo)
        self.scheduler = SchedulerAgent(
            bus=self.bus, provider=self.mimo,
            model=config.mimo.chat_model,
            fallback_provider=self.deepseek,
            fallback_model=config.deepseek.chat_model,
            enable_streaming=config.agent.enable_streaming,
        )
        self.sender = SenderAgent(
            bus=self.bus, mimo=self.mimo,
            output_dir=config.agent.workspace_dir,
        )
        self.task_agent = TaskAgent(
            bus=self.bus, provider=self.mimo,
            model=config.mimo.chat_model,
            fallback_provider=self.deepseek,
            fallback_model=config.deepseek.chat_model,
        )
        self.memory_agent = MemoryAgent(
            bus=self.bus, provider=self.mimo,
            model=config.mimo.chat_model,
            fallback_provider=self.deepseek,
            fallback_model=config.deepseek.chat_model,
        )

        for agent in [self.receiver, self.memory_agent,
                       self.scheduler, self.sender, self.task_agent]:
            await agent.start()

        for agent in [self.receiver, self.memory_agent,
                       self.scheduler, self.sender, self.task_agent]:
            self._agent_tasks.append(asyncio.create_task(agent.run()))

        await asyncio.sleep(0.3)
        logger.info("[TUI] Agent 启动完成")

    async def _stop_agent_system(self) -> None:
        logger.info("[TUI] 正在关闭 Agent 系统...")
        for agent in [self.receiver, self.memory_agent,
                       self.scheduler, self.sender, self.task_agent]:
            if agent:
                try:
                    await agent.stop()
                except Exception:
                    pass
        for task in self._agent_tasks:
            task.cancel()
        if self._agent_tasks:
            await asyncio.gather(*self._agent_tasks, return_exceptions=True)
        if self.bus:
            await self.bus.stop()
        logger.info("[TUI] Agent 已关闭")

    # ─── 消息处理 ──────────────────────────────────────

    async def _process_bus_messages(self) -> None:
        while self._running:
            try:
                msg = await self.bus.receive("tui", timeout=0.05)
                if msg:
                    self._handle_tui_message(msg)

                msg = await self.bus.receive("sender", timeout=0.05)
                if msg:
                    self._handle_sender_message(msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[TUI] 消息异常: {}", e)

    def _handle_tui_message(self, msg: Message) -> None:
        mt = msg.msg_type

        if mt == MessageType.TUI_THOUGHT:
            agent = msg.payload.get("agent", "?")
            title = msg.payload.get("title", "")
            detail = msg.payload.get("detail", "")
            text = f"  [{agent}] {title}"
            if detail:
                # 截断过长的 detail
                d = detail.replace("\n", " ")[:200]
                text += f"\n    {d}"
            self._mount_text(text, "msg-thought")

        elif mt == MessageType.TUI_TOOL:
            tool_name = msg.payload.get("tool_name", "?")
            tool_args = msg.payload.get("tool_args", "")
            result = msg.payload.get("result", "")
            status = msg.payload.get("status", "running")
            if status == "running":
                self._mount_text(f"  🔧 {tool_name}({tool_args})", "msg-tool")
            elif status == "success":
                r = result[:200] if result else ""
                self._mount_text(f"    ✓ {tool_name} {r}", "msg-tool")
            else:
                r = result[:200] if result else ""
                self._mount_text(f"    ✖ {tool_name} {r}", "msg-error")

        elif mt == MessageType.TUI_TOAST:
            try:
                self.notify(
                    msg.payload.get("message", ""),
                    severity="information",
                    timeout=5,
                )
            except Exception:
                pass

        elif mt == MessageType.TUI_STATUS:
            key = msg.payload.get("key", "")
            value = msg.payload.get("value", "")
            if key == "memory":
                try:
                    self.query_one(Header).sub_title = f"记忆: {value}"
                except Exception:
                    pass

    def _handle_sender_message(self, msg: Message) -> None:
        mt = msg.msg_type

        if mt == MessageType.STREAM_START:
            # 创建新的流式 widget
            self._stream_text = ""
            self._stream_widget = Static("MIA: ", classes="msg-mia")
            self._mount_widget(self._stream_widget)

        elif mt == MessageType.STREAM_CHUNK:
            delta = msg.payload.get("delta", "")
            if delta and self._stream_widget:
                self._stream_text += delta
                try:
                    self._stream_widget.update(f"MIA: {self._stream_text}")
                    self._scroll_end()
                except Exception:
                    pass

        elif mt == MessageType.STREAM_END:
            # 流结束，widget 保留为永久消息
            self._stream_widget = None
            self._stream_text = ""

        elif mt == MessageType.SEND_TEXT:
            # 非流式文本回复
            message = msg.payload.get("message", "")
            self._mount_text(f"MIA: {message}", "msg-mia")

        elif mt == MessageType.CONVERSATION_DONE:
            pass

        elif mt == MessageType.TASK_ERROR:
            error = msg.payload.get("error", "")
            self._mount_text(f"✖ {error}", "msg-error")

    # ─── Widget 操作 ───────────────────────────────────

    def _mount_text(self, text: str, css_class: str) -> None:
        """向聊天面板挂载一个带 CSS class 的 Static 文本 widget"""
        widget = Static(text, classes=css_class)
        self._mount_widget(widget)

    def _mount_widget(self, widget: Static) -> None:
        """挂载 widget 到聊天面板，自动滚动到底部"""
        try:
            chat = self.query_one("#chat-history", ScrollableContainer)
            chat.mount(widget)
            self._msg_count += 1

            # 限制消息数量
            if self._msg_count > self.MAX_MESSAGES:
                children = list(chat.children)
                for old in children[:20]:
                    try:
                        old.remove()
                    except Exception:
                        pass
                self._msg_count = len(list(chat.children))

            self._scroll_end()
        except Exception as e:
            logger.debug("[TUI] mount 失败: {}", e)

    def _scroll_end(self) -> None:
        """滚动到底部"""
        try:
            self.query_one("#chat-history", ScrollableContainer).scroll_end(
                animate=False,
            )
        except Exception:
            pass

    def _focus_input(self) -> None:
        try:
            self.query_one("#user-input", Input).focus()
        except Exception:
            pass

    async def action_quit_app(self) -> None:
        self._mount_text("正在退出...", "msg-system")
        await asyncio.sleep(0.3)
        await self._stop_agent_system()
        self.exit()
