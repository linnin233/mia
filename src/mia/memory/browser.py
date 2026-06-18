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

    DISPLAY_DAYS = 30  # Level 1 最多展示的天数

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
        """TUI 模式: 临时记忆 → 日期列表 → 条目列表 → 详情 (循环)

        先打印临时记忆列表，再进入持久知识的日期选择。
        """
        import questionary

        # ─── 1. 展示临时记忆 (如有) ─────────────────
        if self.working_entries:
            print(f"  \033[33m临时记忆\033[0m ({len(self.working_entries)}条) \033[90m— 待持久化，低置信度\033[0m")
            for i, entry in enumerate(self.working_entries):
                cat_label = entry.category_label
                preview = entry.content.replace("\n", " ")[:70]
                if len(entry.content) > 70:
                    preview += "..."
                conf_str = f"\033[90m({entry.confidence:.1f})\033[0m"
                tag = " \033[93m[临时]\033[0m" if entry.confidence <= 0.5 else ""
                print(f"    \033[90m[{i+1}]\033[0m {cat_label} {preview} {conf_str}{tag}")
            print()

        # ─── 2. 持久知识 TUI 导航 ────────────────────
        index = self.store.get_index_summaries()
        if not index:
            print("  \033[90m持久知识为空。\033[0m")
            return

        # 只有 1 天: 跳过日期选择，直接进条目列表
        if len(index) == 1:
            date = list(index.keys())[0]
            await self._browse_entries_tui(date)
            return

        # 正常流程: 日期 → 条目 → 详情
        while not self._exit_requested:
            date = await self._select_date_tui(index)
            if self._exit_requested or date is None:
                break
            await self._browse_entries_tui(date)

    async def _select_date_tui(self, index: dict) -> Optional[str]:
        """Level 1 — questionary.select 日期候选框"""
        import questionary

        choices = []
        for date, ds in list(index.items())[:self.DISPLAY_DAYS]:
            # 构造选择项: "2026-06-19 (6条) — 日摘要..."
            label = f"{date}  ({ds.entry_count}条)"
            if ds.daily_summary:
                summary_preview = ds.daily_summary[:50]
                label += f" — {summary_preview}"
            # 类别分布
            if ds.category_distribution:
                cat_parts = []
                for cat, count in sorted(ds.category_distribution.items()):
                    cat_label = CATEGORY_LABELS.get(cat, f"[{cat}]")
                    cat_parts.append(f"{cat_label}×{count}")
                if cat_parts:
                    label += f"  \033[90m{' '.join(cat_parts)}\033[0m"
            choices.append(questionary.Choice(title=label, value=date))

        choices.append(questionary.Choice(
            title="[返回] 退出浏览器",
            value=None,
        ))

        total = self.store.get_total_count()
        message = f"知识浏览 — {len(index)} 天, {total} 条知识  选择日期:"

        try:
            result = await questionary.select(
                message,
                choices=choices,
                use_indicator=True,
                qmark=">",
                instruction="(↑↓ 移动, Enter 选择, Esc 退出)",
            ).ask_async()
        except KeyboardInterrupt:
            self._exit_requested = True
            return None

        if result is None:
            self._exit_requested = True

        return result

    async def _browse_entries_tui(self, date: str) -> None:
        """浏览某天的条目 — 条目列表 + 详情循环"""
        entries = self.store.load_day(date)
        if not entries:
            print(f"  \033[90m{date} 无知识条目。\033[0m")
            return

        # 构建 id → entry 映射 (修复 questionary value 序列化问题)
        entry_map: dict[str, KnowledgeEntry] = {e.id: e for e in entries}

        while not self._exit_requested:
            selected_id = await self._select_entry_tui(date, entries)
            if self._exit_requested or selected_id is None:
                break
            entry = entry_map.get(selected_id)
            if entry:
                await self._show_detail_tui(entry)

    async def _select_entry_tui(
        self, date: str, entries: list[KnowledgeEntry],
    ) -> Optional[str]:
        """Level 2 — questionary.select 知识条目候选框

        修复: 使用 entry.id (str) 作为 choice value，避免 questionary
        将 MemoryEntry/KnowledgeEntry 对象序列化为字符串导致的
        'str' object has no attribute 'id' 错误。

        Returns:
            选中的 entry.id (str)，或 None (返回上一级)
        """
        import questionary

        choices = []
        for entry in entries:
            cat_label = entry.category_label
            # 内容预览: 单行，截断到 70 字
            preview = entry.content.replace("\n", " ")[:70]
            if len(entry.content) > 70:
                preview += "..."
            label = f"{cat_label} {preview}"
            # 使用 entry.id (str) 作为 value，不是 entry 对象
            choices.append(questionary.Choice(title=label, value=entry.id))

        choices.append(questionary.Choice(
            title="[返回] 上一级",
            value=None,
        ))

        try:
            result = await questionary.select(
                f"{date} — {len(entries)} 条知识  选择条目:",
                choices=choices,
                use_indicator=True,
                qmark=">",
                instruction="(↑↓ 移动, Enter 查看详情, Esc 返回)",
            ).ask_async()
        except KeyboardInterrupt:
            self._exit_requested = True
            return None

        if result is None:
            self._exit_requested = True

        return result

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
        """Flat 模式: 先展示临时记忆，再按日期展示持久知识

        临时记忆 (working memory) 展示在最上方，标记为 [临时]。
        用户可输入序号查看完整详情（Level 3）。
        """
        interactive = _is_interactive()
        loop = asyncio.get_event_loop()
        persistent_total = self.store.get_total_count()
        working_total = len(self.working_entries)
        total = persistent_total + working_total

        print(f"\n  \033[90m知识库: {working_total} 条临时 + {persistent_total} 条持久, 共 {total} 条\033[0m")
        print(f"  \033[90m输入序号查看详情 | Enter 下一组 | q 退出\033[0m")
        print()

        # ─── 构建全局序号映射 (临时记忆 + 持久知识) ──
        # 临时记忆在前，持久知识在后
        all_entries: list[tuple[str, KnowledgeEntry]] = []

        # 临时记忆
        for entry in self.working_entries:
            all_entries.append(("working", entry))

        # 持久知识
        for entry in self.store.get_all():
            all_entries.append(("persistent", entry))

        if not all_entries:
            print("  \033[90m知识库为空。\033[0m")
            return

        # 构建序号 → entry 映射
        entry_by_index: dict[int, KnowledgeEntry] = {}
        for i, (source, entry) in enumerate(all_entries):
            entry_by_index[i + 1] = entry

        # ─── 展示临时记忆 ───────────────────────
        if self.working_entries:
            print(f"  \033[33m临时记忆\033[0m ({working_total}条) \033[90m— 待持久化，低置信度\033[0m")
            for i, entry in enumerate(self.working_entries):
                cat_label = entry.category_label
                preview = entry.content.replace("\n", " ")[:80]
                if len(entry.content) > 80:
                    preview += "..."
                conf_str = f"\033[90m({entry.confidence:.1f})\033[0m"
                global_idx = i + 1  # 临时记忆从 1 开始
                tag = " \033[93m[临时]\033[0m" if entry.confidence <= 0.5 else ""
                print(f"    \033[90m[{global_idx}]\033[0m {cat_label} {preview} {conf_str}{tag}")
            print()

        # ─── 展示持久知识 (按日期分组) ────────
        index = self.store.get_index_summaries()
        idx_offset = working_total  # 持久条目序号从 working_total+1 开始

        if index:
            for date, ds in list(index.items())[:self.DISPLAY_DAYS]:
                summary_hint = f" — {ds.daily_summary}" if ds.daily_summary else ""
                print(f"  \033[33m{date}\033[0m ({ds.entry_count}条){summary_hint}")

                entries = self.store.load_day(date)
                for i, entry in enumerate(entries):
                    global_idx = idx_offset + i + 1
                    cat_label = entry.category_label
                    preview = entry.content.replace("\n", " ")[:80]
                    if len(entry.content) > 80:
                        preview += "..."
                    conf_str = f"\033[90m({entry.confidence:.1f})\033[0m"
                    print(f"    \033[90m[{global_idx}]\033[0m {cat_label} {preview} {conf_str}")
                idx_offset += len(entries)
        elif not self.working_entries:
            print("  \033[90m持久知识为空。\033[0m")

        # ─── 交互循环: 查看详情 ─────────────────
        if not interactive:
            print()
            return

        while True:
            print()
            try:
                user_input = await loop.run_in_executor(
                    None, input,
                    f"  \033[36m序号 (1-{len(all_entries)}) / Enter 退出 / q 退出 > \033[0m",
                )
            except (EOFError, OSError):
                break

            user_input = user_input.strip().lower()
            if user_input == "" or user_input == "q":
                break

            try:
                idx = int(user_input)
                entry = entry_by_index.get(idx)
                if entry:
                    self._show_detail_plain(entry)
                else:
                    print(f"  \033[90m无效序号: {idx}，范围 1-{len(all_entries)}\033[0m")
            except ValueError:
                print(f"  \033[90m无效输入: '{user_input}'，输入数字、Enter 或 q\033[0m")

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
