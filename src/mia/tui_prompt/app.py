"""
MIA TUI Application — 基于 prompt_toolkit 的聊天界面

借鉴 spica-cli 的 Scroll Region + Fixed Input 思路，用 prompt_toolkit 实现:
  - FormattedTextControl 做可滚动输出区 (每条消息 = 一个 style_tagged 文本行)
  - TextArea 做固定底部输入框
  - 流式输出通过更新输出列表 + app.invalidate() 实现即时刷新

用法:
    from mia.tui_prompt.app import MiaTuiApp
    app = MiaTuiApp(bus=bus, config=config)
    await app.run()
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    HSplit,
    Layout,
    VSplit,
    Window,
    WindowAlign,
)
from prompt_toolkit.layout.controls import (
    BufferControl,
    FormattedTextControl,
)
from prompt_toolkit.styles import Style

from loguru import logger

from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.config import Config


# ─── ANSI Color → prompt_toolkit Style 映射 ─────────────

# prompt_toolkit style 类名, 用于 FormattedText 的 style 参数
STYLE_CLASSES = {
    "user": "class:user",       # 蓝色 — 用户消息
    "mia": "class:mia",         # 绿色 — MIA 回复
    "thought": "class:thought", # 青色 — 思考过程
    "tool": "class:tool",       # 黄色 — 工具调用
    "system": "class:system",   # 灰色 — 系统消息
    "error": "class:error",     # 红色 — 错误
}

# prompt_toolkit Style 定义 — 映射 class 到颜色
PROMPT_TOOLKIT_STYLE = Style.from_dict({
    # 主界面
    "header":     "bold #7aa2f7 bg:#1a1b26",
    "separator":  "#3b4261",
    "background": "#1a1b26",
    "frame":      "bg:#1a1b26",

    # 消息类
    "user":       "#7aa2f7",  # 蓝
    "mia":        "#9ece6a",  # 绿
    "thought":    "#7dcfff",  # 青
    "tool":       "#e0af68",  # 黄
    "system":     "#565f89",  # 灰
    "error":      "#f7768e",  # 红

    # 输入区
    "input":      "bg:#16161e #c0caf5",
    "placeholder": "#484d6b",
    "button":     "bg:#7aa2f7 #1a1b26 bold",
    "button.hover": "bg:#9ece6a #1a1b26 bold",

    # 窗口
    "window":     "bg:#1a1b26",
})


class MiaTuiApp:
    """MIA 主 TUI — prompt_toolkit 聊天界面

    在 TUI 模式下取代 SenderAgent，直接订阅 bus 的 "sender"
    通道接收流式输出并在 prompt_toolkit 界面中渲染。
    """

    MAX_MESSAGES = 200  # 最多保留消息条数

    def __init__(self, bus: MessageBus, config: Config) -> None:
        self.bus = bus
        self.config = config

        # ─── 消息列表 [(style_key, text), ...] ────────
        # style_key: "user" | "mia" | "thought" | "tool" | "system" | "error"
        self._messages: list[tuple[str, str]] = []

        # ─── 流式状态 ──────────────────────────────────
        self._stream_buffer: str = ""
        self._stream_index: Optional[int] = None  # 流式消息在列表中的索引
        self._running = False

        # ─── Agent 引用 (外部注入) ──────────────────────
        self.memory_agent = None

        # ─── 输入框引用 ─────────────────────────────────
        self._input_buffer: Optional[Buffer] = None

        # ─── 构建 Application ────────────────────────────
        self._app = self._build_app()

    # ══════════════════════════════════════════════════════
    # 构建 prompt_toolkit UI
    # ══════════════════════════════════════════════════════

    def _build_app(self) -> Application:
        """构建 prompt_toolkit Application 实例"""
        kb = self._create_keybindings()

        # ─── 输出区域 (可滚动) ─────────────────────────────
        self._output_control = FormattedTextControl(
            text=self._render_messages,
            focusable=False,
        )
        output_window = Window(
            content=self._output_control,
            wrap_lines=True,
            always_hide_cursor=True,
        )

        # ─── 分隔线 ────────────────────────────────────────
        separator = Window(
            height=1,
            char="─",
            style="class:separator",
        )

        # ─── 输入区域 ───────────────────────────────────────
        # multiline=False → Enter 触发 accept_handler 直接发送
        self._input_buffer = Buffer(
            completer=WordCompleter([
                "/help", "/quit", "/exit", "/q",
                "/compact", "/memory",
            ]),
            complete_while_typing=False,
            multiline=False,
            accept_handler=self._on_buffer_accept,
        )
        input_control = BufferControl(
            buffer=self._input_buffer,
            input_processors=[],
        )
        input_window = Window(
            content=input_control,
            height=3,
            style="class:input",
            wrap_lines=True,
        )

        # ─── 快捷键提示 ────────────────────────────────────────
        hint_label = Window(
            content=FormattedTextControl(
                "Enter 发送\n"
                "Ctrl+C 退出\n"
                "Esc 聚焦输入"
            ),
            width=14,
            style="class:system",
            align=WindowAlign.CENTER,
        )

        # ─── 底部输入区 ──────────────────────────────────────
        input_container = VSplit([
            input_window,
            Window(width=1),  # spacer
            hint_label,
        ], height=3, padding=0)

        # ─── 整体布局 ──────────────────────────────────────
        root_container = HSplit([
            output_window,
            separator,
            input_container,
        ])

        layout = Layout(root_container)

        app = Application(
            layout=layout,
            key_bindings=kb,
            style=PROMPT_TOOLKIT_STYLE,
            full_screen=True,
            mouse_support=False,
        )

        return app

    def _create_keybindings(self) -> KeyBindings:
        """创建快捷键绑定"""
        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("c-q")
        def _(event):
            """退出 TUI"""
            self._add_message("system", "正在退出...")
            event.app.exit()

        @kb.add("escape")
        def _(event):
            """Esc 聚焦输入框"""
            try:
                event.app.layout.focus_last()
            except Exception:
                pass

        return kb

    # ══════════════════════════════════════════════════════
    # 渲染
    # ══════════════════════════════════════════════════════

    def _render_messages(self) -> FormattedText:
        """渲染消息列表为 FormattedText (prompt_toolkit 回调)

        每次 UI 刷新时调用，遍历消息列表生成带样式的格式化文本。
        """
        parts = []
        for style_key, text in self._messages:
            # 根据 style_key 获取对应的 CSS class
            style_class = STYLE_CLASSES.get(style_key, "")
            # 每行文本作为一个独立的格式化片段
            parts.append((style_class, text))
            parts.append(("", "\n"))
        return FormattedText(parts)

    # ══════════════════════════════════════════════════════
    # 消息管理
    # ══════════════════════════════════════════════════════

    def _add_message(self, style: str, text: str) -> None:
        """添加一条消息到列表"""
        self._messages.append((style, text))
        self._trim_messages()
        self._invalidate()

    def _add_stream_start(self) -> None:
        """开始流式消息 — 在列表末尾添加空占位"""
        self._stream_buffer = ""
        self._messages.append(("mia", "MIA: "))
        self._stream_index = len(self._messages) - 1
        self._invalidate()

    def _add_stream_chunk(self, delta: str) -> None:
        """追加流式文本块 — 更新最后一条消息"""
        if delta:
            self._stream_buffer += delta
            if self._stream_index is not None:
                self._messages[self._stream_index] = (
                    "mia",
                    f"MIA: {self._stream_buffer}",
                )
            self._invalidate()

    def _end_stream(self) -> None:
        """结束流式 — 消息保留，重置状态"""
        self._stream_buffer = ""
        self._stream_index = None

    def _trim_messages(self) -> None:
        """限制消息数量，超过上限时移除旧消息"""
        while len(self._messages) > self.MAX_MESSAGES:
            self._messages.pop(0)
            # 调整流式索引
            if self._stream_index is not None:
                self._stream_index -= 1

    def _invalidate(self) -> None:
        """触发 UI 刷新"""
        try:
            self._app.invalidate()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════
    # 输入处理
    # ══════════════════════════════════════════════════════

    def _on_buffer_accept(self, buffer: Buffer) -> bool:
        """Buffer accept_handler — Enter 键触发 (multiline=False)

        返回 True 让 prompt_toolkit 自动清空 Buffer。
        """
        text = buffer.text.strip()
        if not text:
            return True  # 空输入，清空

        if text.startswith("/"):
            self._handle_command(text.lower())
        else:
            self._send_message(text)

        return True  # 清空 Buffer

    def _send_message(self, text: str) -> None:
        """发送用户消息到 Agent 系统"""
        session_id = uuid.uuid4().hex[:12]

        # 显示用户消息
        self._add_message("user", f"You: {text}")

        # 通过 bus 注入 RAW_INPUT (线程安全)
        msg = Message(
            msg_type=MessageType.RAW_INPUT,
            source="tui",
            target="receiver",
            payload={"text": text, "image": None, "voice": None},
            session_id=session_id,
        )
        asyncio.ensure_future(self.bus.publish(msg))

    def _handle_command(self, command: str) -> None:
        """处理 / 命令"""
        if command in ("/quit", "/exit", "/q"):
            self._add_message("system", "再见~")
            # 延迟退出，让用户看到消息
            def _quit():
                self._app.exit()
            self._app.create_background_task(
                asyncio.get_event_loop().create_task(self._delayed_quit())
            )

        elif command in ("/help", "/h"):
            self._add_message("system",
                "命令列表:\n"
                "  /quit /exit /q — 退出\n"
                "  /help /h — 帮助\n"
                "  /compact — 压缩对话历史\n"
                "  /memory — 查看记忆状态\n"
                "  Ctrl+C — 退出  Esc — 聚焦输入"
            )

        elif command == "/compact":
            if self.memory_agent:
                self._add_message("system", "正在压缩对话历史...")
                asyncio.ensure_future(self._do_compact())
            else:
                self._add_message("error", "记忆系统未就绪")

        elif command == "/memory":
            if self.memory_agent:
                ma = self.memory_agent
                w = len(ma._working_memory)
                p = ma.store.count
                h = len(ma._conversation_history)
                d = len(ma._daily_buffer)
                lines = [
                    f"记忆状态  临时:{w}条  持久:{p}条  历史:{h}轮  日缓冲:{d}轮",
                    "",
                ]

                # ── 持久知识 ──
                if p > 0:
                    lines.append("── 持久知识 ──")
                    for e in ma.store.get_recent(10):
                        cat = getattr(e, 'category_label', e.category if hasattr(e, 'category') else '?')
                        lines.append(f"  [{cat}] {e.content[:100]}")
                    lines.append("")

                # ── 临时记忆 ──
                if w > 0:
                    lines.append("── 临时记忆 ──")
                    for e in ma._working_memory:
                        cat = getattr(e, 'category_label', e.category if hasattr(e, 'category') else '?')
                        lines.append(f"  [{cat}] {e.content[:100]}")
                    lines.append("")

                # ── 对话历史 ──
                if h > 0:
                    lines.append(f"── 对话历史 (最近{min(h, 3)}轮) ──")
                    for turn in ma._conversation_history[-3:]:
                        u = turn.get("user", "")[:60]
                        a = turn.get("assistant", "")[:60]
                        lines.append(f"  You: {u}")
                        lines.append(f"  MIA: {a}")
                        lines.append("")

                self._add_message("system", "\n".join(lines))
            else:
                self._add_message("error", "记忆系统未就绪")

        else:
            self._add_message("system",
                f"未知命令 '{command}'，输入 /help 查看帮助"
            )

    async def _do_compact(self) -> None:
        """执行对话压缩"""
        try:
            summary = await self.memory_agent.compact()
            new_count = self.memory_agent.store.count
            self._add_message("system",
                f"对话历史已压缩  持久知识:{new_count}条\n  {summary[:200]}..."
            )
        except Exception as e:
            self._add_message("error", f"压缩失败: {e}")

    async def _delayed_quit(self) -> None:
        """延迟退出"""
        await asyncio.sleep(0.3)
        self._app.exit()

    # ══════════════════════════════════════════════════════
    # Bus 消息处理
    # ══════════════════════════════════════════════════════

    async def _process_bus_messages(self) -> None:
        """后台任务: 处理 MessageBus 消息

        在 TUI 运行时持续轮询 bus 的 "sender" 和 "tui" 通道，
        将系统消息转换为 UI 更新。
        """
        # 订阅通道
        await self.bus.subscribe("sender")
        await self.bus.subscribe("tui")

        # Debug: 打开 trace 文件记录消息处理顺序
        from pathlib import Path as _Path
        _trace_dir = _Path(__file__).parent.parent.parent.parent / "logs"
        _trace_dir.mkdir(parents=True, exist_ok=True)
        self._trace_file = open(str(_trace_dir / "tui-trace.log"), "w", encoding="utf-8")
        self._trace_seq = 0

        self._running = True
        while self._running:
            try:
                # 排空 tui 队列 — 确保所有思考过程在流式输出之前渲染
                # 使用短超时非阻塞轮询，一次排空所有积压的 TUI 消息
                while True:
                    tui_msg = await self.bus.receive("tui", timeout=0.005)
                    if tui_msg is None:
                        break
                    self._trace_seq += 1
                    mt_val = tui_msg.msg_type.value
                    title = tui_msg.payload.get("title", "")
                    self._trace_file.write(
                        f"[{self._trace_seq:04d}] TUI_CHAN {mt_val} | {title}\n"
                    )
                    self._trace_file.flush()
                    self._handle_tui_message(tui_msg)

                # 只取一条 sender 消息 (保证流式输出及时渲染)
                sender_msg = await self.bus.receive("sender", timeout=0.005)
                if sender_msg:
                    self._trace_seq += 1
                    mt_val = sender_msg.msg_type.value
                    self._trace_file.write(
                        f"[{self._trace_seq:04d}] SENDER_CHAN {mt_val}\n"
                    )
                    self._trace_file.flush()
                    self._handle_sender_message(sender_msg)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[TUI] 消息处理异常: {}", e)

        await self.bus.unsubscribe("sender")
        await self.bus.unsubscribe("tui")
        # 关闭 trace 文件
        try:
            self._trace_file.close()
        except Exception:
            pass

    def _handle_sender_message(self, msg: Message) -> None:
        """处理 Sender 通道消息 (流式输出 / 文本回复)"""
        mt = msg.msg_type

        if mt == MessageType.STREAM_START:
            # SenderAgent 在 TUI 模式下不存在，TUI 补上 sender 头
            self._add_message("thought", "  [sender] 输出回复")
            self._add_stream_start()

        elif mt == MessageType.STREAM_CHUNK:
            delta = msg.payload.get("delta", "")
            self._add_stream_chunk(delta)

        elif mt == MessageType.STREAM_END:
            self._end_stream()
            # Sender 不会运行，所以 TUI 需要发布 CONVERSATION_DONE
            full_message = msg.payload.get("message", "")
            asyncio.ensure_future(self._publish_conversation_done(full_message, msg.session_id))

        elif mt == MessageType.SEND_TEXT:
            # 非流式 fallback: 补 sender 头
            self._add_message("thought", "  [sender] 输出回复")
            message = msg.payload.get("message", "")
            self._add_message("mia", f"MIA: {message}")
            asyncio.ensure_future(self._publish_conversation_done(message, msg.session_id))

        elif mt == MessageType.TASK_ERROR:
            error = msg.payload.get("error", "")
            self._add_message("error", f"✖ {error}")

    def _handle_tui_message(self, msg: Message) -> None:
        """处理 TUI 通道消息 (思考过程 / 工具调用 / 状态)"""
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
            self._add_message("thought", text)

        elif mt == MessageType.TUI_TOOL:
            tool_name = msg.payload.get("tool_name", "?")
            tool_args = msg.payload.get("tool_args", "")
            result = msg.payload.get("result", "")
            status = msg.payload.get("status", "running")
            if status == "running":
                self._add_message("tool", f"  🔧 {tool_name}({tool_args[:100]})")
            elif status == "success":
                r = result[:200] if result else ""
                self._add_message("tool", f"    ✓ {tool_name} {r}")
            else:
                r = result[:200] if result else ""
                self._add_message("error", f"    ✖ {tool_name} {r}")

        elif mt == MessageType.TUI_STATUS:
            key = msg.payload.get("key", "")
            value = msg.payload.get("value", "")
            if key == "memory":
                self._add_message("system", f"📊 记忆: {value}")

    async def _publish_conversation_done(
        self, message: str, session_id: Optional[str] = None
    ) -> None:
        """发布对话完成消息 (TUI 模式下 TUI 替换 Sender)"""
        # 通知 main (如果有的话)
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="tui",
            target="main",
            payload={"message": message},
            session_id=session_id,
        ))
        # 通知 MemoryAgent 存储
        await self.bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="tui",
            target="memory_agent",
            payload={"message": message},
            session_id=session_id,
        ))

    # ══════════════════════════════════════════════════════
    # 生命周期
    # ══════════════════════════════════════════════════════

    async def run(self) -> None:
        """启动 TUI 并进入主循环

        返回时 TUI 已关闭。
        (loguru 抑制已在 main.run_tui_mode() 中处理)
        """
        # 显示欢迎消息
        self._add_message("system",
            f"MIA v0.2.0 已就绪  |  模型: {self.config.mimo.chat_model}  "
            f"|  /help 命令  |  /quit 退出"
        )

        # 启动 bus 消息处理后端任务
        self._app.create_background_task(self._process_bus_messages())

        # 进入 prompt_toolkit 主循环 (阻塞直到 exit())
        await self._app.run_async()

        # 清理
        self._running = False
