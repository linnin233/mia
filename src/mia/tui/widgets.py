"""
MIA TUI 自定义 Widget 组件

提供聊天界面所需的所有自定义组件:
  - StatusBar: 顶部状态栏 (session/model/memory)
  - ChatHistory: 对话历史滚动面板
  - MessageBubble: 单条消息气泡 (User/MIA)
  - ThoughtSection: 可折叠的思考过程
  - ToolCallSection: 可折叠的工具调用详情
  - StreamingText: 实时流式文本追加
  - InputArea: 底部输入区域
"""

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.widgets import Static, Button, Input, RichLog, Label
from textual.widget import Widget
from textual.message import Message


# ─── 状态栏 ──────────────────────────────────────────────

class StatusBar(Widget):
    """顶部状态栏 — 显示 session/model/memory 等状态信息"""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._items: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static(" MIA v0.2.0", id="status-left"),
            Static("", id="status-center"),
            Static("", id="status-right"),
        )

    def update(self, key: str, value: str) -> None:
        """更新状态栏中的某个键值"""
        self._items[key] = value
        self._refresh_display()

    def _refresh_display(self) -> None:
        """刷新状态栏显示"""
        parts = []
        for key, value in self._items.items():
            parts.append(f"{key}: {value}")
        text = " │ ".join(parts)
        try:
            center = self.query_one("#status-center", Static)
            center.update(text)
        except Exception:
            pass


# ─── 消息气泡 ────────────────────────────────────────────

class MessageBubble(Widget):
    """单条消息气泡 — User 左蓝色边框, MIA 左绿色边框"""

    DEFAULT_CSS = """
    MessageBubble {
        height: auto;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, role: str, content: str, role_label: str = "") -> None:
        """
        Args:
            role: "user" 或 "assistant"
            content: 消息文本
            role_label: 角色标签文本 (如 "You" / "MIA")
        """
        super().__init__(classes=role)
        self.role = role
        self.content = content
        self.role_label = role_label or ("You" if role == "user" else "MIA")

    def compose(self) -> ComposeResult:
        yield Static(
            f"[bold]{self.role_label}[/bold]",
            classes="role-label",
        )
        yield Static(self.content, classes="content")


# ─── 可折叠区块 ──────────────────────────────────────────

class CollapsibleSection(Widget):
    """可折叠的区块 — 点击标题展开/收起详情"""

    DEFAULT_CSS = """
    CollapsibleSection {
        height: auto;
        margin: 0 0 1 0;
    }
    """

    def __init__(
        self,
        title: str,
        detail: str = "",
        section_class: str = "",
    ) -> None:
        super().__init__(classes=section_class)
        self._title = title
        self._detail = detail
        self._collapsed = True  # 默认折叠

    def compose(self) -> ComposeResult:
        # 标题行 (可点击)
        yield Static(
            f"▶ {self._title}",
            classes="title",
        )
        # 详情 (默认隐藏)
        detail_widget = Static(self._detail, classes="detail")
        if self._collapsed:
            detail_widget.display = False
        yield detail_widget

    def on_click(self) -> None:
        """点击切换折叠/展开"""
        self._collapsed = not self._collapsed
        try:
            title_widget = self.query_one(".title", Static)
            detail_widget = self.query_one(".detail", Static)
            if self._collapsed:
                title_widget.update(f"▶ {self._title}")
                detail_widget.display = False
            else:
                title_widget.update(f"▼ {self._title}")
                detail_widget.display = True
        except Exception:
            pass


class ThoughtSection(CollapsibleSection):
    """思考过程区块 — 青色虚线边框"""

    def __init__(self, agent: str, title: str, detail: str = "") -> None:
        full_title = f"[{agent}] {title}"
        super().__init__(
            title=full_title,
            detail=detail,
            section_class="thought",
        )


class ToolCallSection(CollapsibleSection):
    """工具调用区块 — 橙色虚线边框"""

    def __init__(
        self,
        tool_name: str,
        tool_args: str = "",
        result: str = "",
        status: str = "running",
    ) -> None:
        title = f"🔧 {tool_name}"
        if tool_args:
            title += f"({tool_args})"
        detail = result or "执行中..."
        classes = f"tool {status}" if status else "tool"
        super().__init__(
            title=title,
            detail=detail,
            section_class=classes,
        )


# ─── 流式文本 ────────────────────────────────────────────

class StreamingText(Widget):
    """实时流式文本 — 逐 chunk 追加内容"""

    DEFAULT_CSS = """
    StreamingText {
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
    }
    """

    def __init__(self) -> None:
        super().__init__(classes="streaming")
        self._buffer: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static(
            "[bold]MIA[/bold] ",
            classes="prefix",
        )
        yield Static("", classes="content")

    def append(self, delta: str) -> None:
        """追加文本增量"""
        self._buffer.append(delta)
        try:
            content = self.query_one(".content", Static)
            content.update("".join(self._buffer))
        except Exception:
            pass

    def get_full_text(self) -> str:
        """获取完整文本"""
        return "".join(self._buffer)


# ─── 输入区域 ────────────────────────────────────────────

class InputArea(Widget):
    """底部输入区域 — 文本输入框 + 发送按钮"""

    DEFAULT_CSS = """
    InputArea {
        height: 3;
    }
    """

    class Submitted(Message):
        """用户提交文本事件"""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Command(Message):
        """用户输入斜杠命令事件"""
        def __init__(self, command: str) -> None:
            super().__init__()
            self.command = command

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Input(
                placeholder="输入消息... (/help 查看命令)",
                id="user-input",
            ),
            Button("发送", id="send-button", variant="primary"),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """发送按钮点击 — 提交输入文本"""
        self._submit_input()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """回车键 — 提交输入文本"""
        self._submit_input()

    def _submit_input(self) -> None:
        """获取输入文本并发送事件"""
        try:
            input_widget = self.query_one("#user-input", Input)
            text = input_widget.value.strip()
            if not text:
                return
            input_widget.value = ""  # 清空输入框

            if text.startswith("/"):
                self.post_message(self.Command(text))
            else:
                self.post_message(self.Submitted(text))
        except Exception:
            pass

    def focus_input(self) -> None:
        """聚焦到输入框"""
        try:
            self.query_one("#user-input", Input).focus()
        except Exception:
            pass
