"""
Test MemoryBrowser — 交互式 TUI 知识浏览器

测试目标:
  1. 空知识库 — browse() 立即返回不报错
  2. 单日 — 跳过日期选择，直接进条目
  3. Flat 降级模式 — 无 questionary 时正常降级
  4. 边界 — 某天无记录时处理
  5. 只读验证 — browse() 不修改 store 数据
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

# 确保 mia 在 sys.path 中
_project_root = Path(__file__).parent.parent
if str(_project_root / "src") not in sys.path:
    sys.path.insert(0, str(_project_root / "src"))

from mia.memory.store import (
    KnowledgeEntry,
    MemoryStore,
    DaySummary,
    CATEGORY_FACT,
    CATEGORY_PREFERENCE,
)
from mia.memory.browser import MemoryBrowser


# ═══════════════════════════════════════════════════════════
# 辅助: 构造测试用的 MemoryStore
# ═══════════════════════════════════════════════════════════

def _make_store(data_dir: Path, entries: list[KnowledgeEntry]) -> MemoryStore:
    """构造 MemoryStore 并预填入指定知识条目"""
    store = MemoryStore(data_dir=data_dir)
    store.load()
    for entry in entries:
        store.add(entry)
    return store


# ═══════════════════════════════════════════════════════════
# 测试 1: 空知识库
# ═══════════════════════════════════════════════════════════

async def test_browse_empty_store():
    """空知识库 — browse() 应该立即返回不报错"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_browser_test_"))
    try:
        store = MemoryStore(data_dir=tmpdir)
        store.load()
        assert store.count == 0

        browser = MemoryBrowser(store)
        await browser.browse()

        print("  [OK] test_browse_empty_store 通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 测试 2: 单日 (边界 — 自动跳过日期选择)
# ═══════════════════════════════════════════════════════════

async def test_browse_single_day():
    """只有 1 天时，跳过 Level 1 日期选择"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_browser_test_"))
    try:
        entry = KnowledgeEntry(
            content="用户偏好使用中文进行技术交流",
            category=CATEGORY_PREFERENCE,
            confidence=0.8,
            keywords=["中文", "偏好", "技术交流"],
            importance=0.7,
            source_sessions=["test_s1"],
        )
        store = _make_store(tmpdir, [entry])
        assert store.day_count == 1
        assert store.count == 1

        browser = MemoryBrowser(store)
        await browser.browse()

        print("  [OK] test_browse_single_day 通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 测试 3: Flat 模式降级
# ═══════════════════════════════════════════════════════════

async def test_browse_flat_mode():
    """强制 flat 模式 — 验证降级路径不抛异常"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_browser_test_"))
    try:
        entries = [
            KnowledgeEntry(
                content=f"测试知识 {i}",
                category=CATEGORY_FACT,
                confidence=0.5 + i * 0.1,
                keywords=["test"],
                importance=0.5,
                source_sessions=[f"s{i}"],
            )
            for i in range(3)
        ]
        store = _make_store(tmpdir, entries)
        assert store.count == 3

        browser = MemoryBrowser(store)
        browser._use_tui = False
        await browser.browse()

        print("  [OK] test_browse_flat_mode 通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 测试 4: 某天有记录但 load_day 返回空 (数据不一致边界)
# ═══════════════════════════════════════════════════════════

async def test_date_without_file():
    """index 中有日期但 daily 文件不存在 — load_day 返回空列表"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_browser_test_"))
    try:
        store = MemoryStore(data_dir=tmpdir)
        store.load()

        # 手动注入一个 index 条目 (不创建 daily 文件)
        store._index["2026-01-15"] = DaySummary(
            date="2026-01-15",
            file="daily/2026-01-15.json",
            entry_count=3,
            daily_summary="测试日期",
            keywords=["test"],
            importance=0.8,
            category_distribution={"fact": 2, "preference": 1},
        )
        store._save_index()

        # load_day 应该返回空列表 (文件不存在)
        entries = store.load_day("2026-01-15")
        assert entries == [], f"不存在的文件应返回空列表: {entries}"

        # browser 应该正常处理
        browser = MemoryBrowser(store)
        browser._use_tui = False
        await browser.browse()

        print("  [OK] test_date_without_file 通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 测试 5: 浏览器不修改 MemoryStore (纯只读验证)
# ═══════════════════════════════════════════════════════════

async def test_browser_is_readonly():
    """验证 browse() 后 store 数据不变"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_browser_test_"))
    try:
        entries = [
            KnowledgeEntry(
                content="知识 1", category=CATEGORY_FACT,
                confidence=0.8, keywords=["k1"], importance=0.5,
                source_sessions=["s1"],
            ),
            KnowledgeEntry(
                content="知识 2", category=CATEGORY_PREFERENCE,
                confidence=0.9, keywords=["k2"], importance=0.5,
                source_sessions=["s1"],
            ),
        ]
        store = _make_store(tmpdir, entries)
        expected_count = store.count
        expected_days = store.day_count

        browser = MemoryBrowser(store)
        browser._use_tui = False
        await browser.browse()

        # 验证数据未被修改
        assert store.count == expected_count, \
            f"count 不应改变: {expected_count} → {store.count}"
        assert store.day_count == expected_days, \
            f"day_count 不应改变: {expected_days} → {store.day_count}"

        # 验证条目内容不变
        all_entries = store.get_all()
        contents = {e.content for e in all_entries}
        assert "知识 1" in contents
        assert "知识 2" in contents

        print("  [OK] test_browser_is_readonly 通过")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 运行所有测试
# ═══════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("MemoryBrowser 知识浏览器测试")
    print("=" * 60)

    tests = [
        ("空知识库不报错", test_browse_empty_store),
        ("单日跳过日期选择", test_browse_single_day),
        ("Flat 降级模式", test_browse_flat_mode),
        ("日期无文件边界", test_date_without_file),
        ("纯只读验证", test_browser_is_readonly),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        print(f"\n--- {name} ---")
        try:
            await test_func()
            passed += 1
        except Exception as e:
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] FAIL: {e}")

    print()
    print("=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, {len(tests)} 总计")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
