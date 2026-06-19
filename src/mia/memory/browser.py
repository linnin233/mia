"""
MemoryBrowser — 交互式 TUI 知识浏览器

3 级钻取式浏览:
  Level 1: questionary.select 展示日期列表 (方向键选择)
  Level 2: questionary.select 展示知识条目列表 (类别 + 内容预览)
  Level 3: rich.Table 展示完整知识详情 (9 个字段)

支持降级: 当终端不支持 prompt_toolkit (如 Git Bash) 时自动降级为 flat print 模式。

用法:
    from mia.memory.browser import MemoryBrowser
    browser = MemoryBrowser(store)
    await browser.browse()
"""

import asyncio
import sys
from typing import Optional

from loguru import logger

from mia.memory.store import KnowledgeEntry, MemoryStore, CATEGORY_LABELS


# ─── 检测终端是否支持交互输入 ──────────────────────

def _is_interactive() -> bool:
    """检测当前终端是否支持交互式输入 (非管道/非 pytest 捕获)"""
    return sys.stdin.isatty()


# ─── 退出信号异常 — 用于穿透多层 TUI 循环 ──────────

class _BrowserExit(Exception):
    """内部信号: 用户请求退出浏览器"""
    pass


class MemoryBrowser:
    """3 级钻取式知识浏览器 TUI

    Level 1: 日期列表 (条目数 + 日摘要预览)
    Level 2: 知识条目列表 (类别图标 + 内容预览)
    Level 3: 详情表格 (全部 9 个字段)

    同时展示临时记忆 (working memory) 和持久知识 (persistent store)。
    纯只读，不修改 MemoryStore。
    """

    PAGE_SIZE = 10  # 每页显示条目数

    def __init__(
        self,
        store: MemoryStore,
        working_entries: list[KnowledgeEntry] | None = None,
    ):
        """
        Args:
            store: MemoryStore 实例 (持久知识，只读)
            working_entries: 临时记忆列表 (Level 1 working memory, 只读)
        """
        self.store = store
        self.working_entries: list[KnowledgeEntry] = working_entries or []
        self._use_tui = True
        self._exit_requested = False  # 退出信号 (跨多层循环)
        self._all_entries: list[KnowledgeEntry] = []  # 全量加载的条目
        self._id_map: dict[str, KnowledgeEntry] = {}   # id → entry

    # ═══════════════════════════════════════════════════════
    # 公开 API
    # ═══════════════════════════════════════════════════════

    async def browse(self) -> None:
        """主入口 — 启动交互式知识浏览

        同时展示临时记忆 (working) 和持久知识 (persistent)。
        两者均为空时才打印提示。
        """
        total = self.store.count + len(self.working_entries)
        if total == 0:
            print("  \033[90m知识库为空 (无临时记忆 + 无持久知识)。\033[0m")
            print("  \033[90m提示: 先进行一轮对话让 Agent 提取知识。\033[0m")
            return

        # 尝试导入 questionary
        try:
            import questionary
            self._use_tui = True
        except ImportError:
            logger.info("[MemoryBrowser] questionary 未安装，降级为 flat 模式")
            self._use_tui = False
        except Exception:
            logger.info("[MemoryBrowser] 终端不支持 prompt_toolkit，降级为 flat 模式")
            self._use_tui = False

        if not self._use_tui:
            await self._browse_flat()
            return

        # ─── TUI 模式: 3 级钻取 ─────────────────────
        self._exit_requested = False
        try:
            await self._browse_tui()
        except KeyboardInterrupt:
            pass  # 用户 Ctrl+C，静默返回
        except Exception as e:
            logger.warning("[MemoryBrowser] TUI 失败: {}，降级为 flat", e)
            self._use_tui = False
            await self._browse_flat()

    # ═══════════════════════════════════════════════════════
    # TUI 模式 — 3 级钻取
    # ═══════════════════════════════════════════════════════

    async def _browse_tui(self) -> None:
        """TUI 模式: 全量加载 → 分页展示 → 详情

        1. 一次性加载所有日期的知识条目
        2. 打印临时记忆列表
        3. 分页展示 (10条/页)，支持翻页
        4. 选择条目查看完整详情
        """
        import questionary

        # ─── 1. 全量加载所有持久知识 ─────────────────
        self._all_entries = self.store.get_all()
        self._id_map = {e.id: e for e in self._all_entries}
        total_persistent = len(self._all_entries)
        total_working = len(self.working_entries)

        # ─── 2. 展示临时记忆 ─────────────────────────
        if self.working_entries:
            print(f"\n  \033[33m临时记忆\033[0m ({total_working}条) \033[90m— 待持久化，低置信度\033[0m")
            for i, entry in enumerate(self.working_entries):
                cat = entry.category_label
                preview = entry.content.replace("\n", " ")[:120]
                if len(entry.content) > 120:
                    preview += "..."
                print(f"    \033[90m[T{i+1}]\033[0m {cat} {preview} \033[90m({entry.confidence:.1f})\033[0m \033[93m[临时]\033[0m")
            print()

        if total_persistent == 0 and total_working == 0:
            print("  \033[90m知识库为空。\033[0m")
            return
        if total_persistent == 0:
            print("  \033[90m持久知识为空。按 Enter 退出。\033[0m")
            return

        # ─── 3. 分页浏览持久知识 ─────────────────────
        total_pages = (total_persistent + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        current_page = 0  # 0-indexed

        while not self._exit_requested:
            # 当前页的条目
            start = current_page * self.PAGE_SIZE
            end = min(start + self.PAGE_SIZE, total_persistent)
            page_entries = self._all_entries[start:end]

            choices = []
            for i, entry in enumerate(page_entries):
                cat = entry.category_label
                preview = entry.content.replace("\n", " ")[:120]
                if len(entry.content) > 120:
                    preview += "..."
                label = f"{i+1:2d}. {cat} {preview}"
                choices.append(questionary.Choice(title=label, value=entry.id))

            # 翻页选项
            nav_choices = []
            if current_page > 0:
                nav_choices.append(questionary.Choice(
                    title="← 上一页", value="__PREV__",
                ))
            if current_page < total_pages - 1:
                nav_choices.append(questionary.Choice(
                    title="→ 下一页", value="__NEXT__",
                ))
            if nav_choices:
                choices.append(questionary.Separator("─" * 40))
                choices.extend(nav_choices)

            choices.append(questionary.Separator())
            choices.append(questionary.Choice(
                title="[退出] 关闭浏览器",
                value="__EXIT__",
            ))

            message = (
                f"持久知识 — 第{current_page+1}/{total_pages}页 "
                f"({start+1}-{end}条, 共{total_persistent}条)  选择:"
            )

            try:
                result = await questionary.select(
                    message,
                    choices=choices,
                    use_indicator=True,
                    qmark=">",
                    instruction="(↑↓ 移动, Enter 选择, ←→ 翻页, Esc 退出)",
                ).ask_async()
            except (KeyboardInterrupt, EOFError, Exception) as e:
                logger.debug("[MemoryBrowser] 选择退出: {}", type(e).__name__)
                self._exit_requested = True
                break

            if result is None or result == "__EXIT__":
                self._exit_requested = True
                break
            elif result == "__PREV__":
                current_page = max(0, current_page - 1)
            elif result == "__NEXT__":
                current_page = min(total_pages - 1, current_page + 1)
            else:
                # 选中了条目 → 查看详情
                entry = self._id_map.get(result)
                if entry:
                    await self._show_detail_tui(entry)

    async def _show_detail_tui(self, entry: KnowledgeEntry) -> None:
        """Level 3 — rich.Table 展示完整知识详情 (9 个字段)"""
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.box import ROUNDED
            console = Console()
            table = Table(
                title=f"知识详情 [{entry.id[:8]}...]",
                box=ROUNDED,
                show_header=True,
                title_style="bold cyan",
            )
            table.add_column("字段", style="bold cyan", no_wrap=True, width=14)
            table.add_column("值", style="")

            # 置信度可视化
            confidence_bar = "█" * int(entry.confidence * 10) + "░" * (10 - int(entry.confidence * 10))
            confidence_str = f"{entry.confidence:.2f}  {confidence_bar}"

            # 来源会话
            source_str = ", ".join(entry.source_sessions[:3]) if entry.source_sessions else "(无)"
            if len(entry.source_sessions) > 3:
                source_str += f" ... (+{len(entry.source_sessions) - 3})"

            table.add_row("ID", entry.id)
            table.add_row("类别", entry.category_label)
            table.add_row("内容", entry.content)
            table.add_row("置信度", confidence_str)
            table.add_row("重要性", f"{entry.importance:.2f}")
            table.add_row("关键词", ", ".join(entry.keywords) if entry.keywords else "(无)")
            table.add_row("来源会话", source_str)
            table.add_row("创建时间", entry.created_at)
            table.add_row("更新时间", entry.updated_at)

            console.print()
            console.print(table)
            console.print()

            if _is_interactive():
                loop = asyncio.get_event_loop()
                try:
                    await loop.run_in_executor(None, input, "  按 Enter 返回...")
                except (EOFError, OSError):
                    pass  # 非交互环境 (pytest/Git Bash)，直接跳过

        except ImportError:
            self._show_detail_plain(entry)

    # ═══════════════════════════════════════════════════════
    # Flat 模式 — 降级方案 (不支持 TUI 的终端)
    # ═══════════════════════════════════════════════════════

    async def _browse_flat(self) -> None:
        """Flat 模式: 全量加载 → 分页打印 → 交互查看详情"""
        interactive = _is_interactive()
        loop = asyncio.get_event_loop()

        # 全量加载
        all_persistent = self.store.get_all()
        persistent_total = len(all_persistent)
        working_total = len(self.working_entries)
        total = persistent_total + working_total

        print(f"\n  \033[90m知识库: {working_total}临时 + {persistent_total}持久, 共{total}条\033[0m")
        print(f"  \033[90m输入序号查看详情 | n=下一页 p=上一页 | q=退出\033[0m\n")

        if total == 0:
            print("  \033[90m知识库为空。\033[0m\n")
            return

        all_entries: list[KnowledgeEntry] = (
            list(self.working_entries) + all_persistent
        )
        total_pages = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        page = 0

        while page < total_pages:
            start = page * self.PAGE_SIZE
            end = min(start + self.PAGE_SIZE, total)
            print(f"  \033[1m── 第{page+1}/{total_pages}页 ({start+1}-{end}条) ──\033[0m")

            for i in range(start, end):
                entry = all_entries[i]
                idx = i + 1
                cat = entry.category_label
                preview = entry.content.replace("\n", " ")[:150]
                if len(entry.content) > 150:
                    preview += "..."
                is_temp = i < working_total
                tag = " \033[93m[临时]\033[0m" if is_temp else ""
                print(f"  \033[90m[{idx}]\033[0m {cat} {preview} \033[90m({entry.confidence:.1f})\033[0m{tag}")

            print()
            if not interactive:
                page += 1
                continue

            try:
                user_input = await loop.run_in_executor(
                    None, input,
                    f"  \033[36m序号/n下一页/p上一页/q退出 > \033[0m",
                )
            except (EOFError, OSError):
                break

            user_input = user_input.strip().lower()
            if user_input in ("", "q"):
                break
            if user_input == "n":
                page = min(page + 1, total_pages - 1)
                continue
            if user_input == "p":
                page = max(page - 1, 0)
                continue

            try:
                idx = int(user_input)
                if 1 <= idx <= total:
                    self._show_detail_plain(all_entries[idx - 1])
                else:
                    print(f"  \033[90m无效序号: {idx}，范围 1-{total}\033[0m")
            except ValueError:
                print(f"  \033[90m无效输入: '{user_input}'\033[0m")

        print()

    # ═══════════════════════════════════════════════════════
    # 纯文本详情 (无 rich 时的降级)
    # ═══════════════════════════════════════════════════════

    def _show_detail_plain(self, entry: KnowledgeEntry) -> None:
        """纯文本打印 KnowledgeEntry 全部字段"""
        print()
        print(f"  \033[1m{'─' * 50}\033[0m")
        print(f"  \033[36mID:\033[0m       {entry.id}")
        print(f"  \033[36m类别:\033[0m     {entry.category_label}")
        print(f"  \033[36m置信度:\033[0m   {entry.confidence:.2f}")
        print(f"  \033[36m重要性:\033[0m   {entry.importance:.2f}")
        print(f"  \033[36m关键词:\033[0m   {', '.join(entry.keywords) if entry.keywords else '(无)'}")
        print(f"  \033[36m来源会话:\033[0m {', '.join(entry.source_sessions[:3]) if entry.source_sessions else '(无)'}")
        print(f"  \033[36m创建时间:\033[0m {entry.created_at}")
        print(f"  \033[36m更新时间:\033[0m {entry.updated_at}")
        print(f"  \033[1m{'─' * 50}\033[0m")
        print(f"  \033[36m内容:\033[0m")
        print(f"  {entry.content}")
        print(f"  \033[1m{'─' * 50}\033[0m")
        print()
