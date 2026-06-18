"""
测试 MemoryAgent 知识提取 — 验证两级记忆梯度

测试目标:
  1. Level 1: CONVERSATION_DONE 后实时提取临时知识
  2. Level 2: 换日触发合并去重 → 持久化
  3. 合并检索: working + persistent
  4. MemoryStore CRUD: KnowledgeEntry 存储/加载
  5. 降级: LLM 不可用时降级持久化
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

import json

from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.agents.memory import MemoryAgent
from mia.memory.store import (
    KnowledgeEntry,
    MemoryStore,
    CATEGORY_FACT,
    CATEGORY_PREFERENCE,
    CATEGORY_DECISION,
)


# ─── Mock Provider — 返回固定 JSON ──────────────────

class MockProvider:
    """模拟 LLM Provider，避免真实 API 调用"""

    def __init__(self, responses: list[str] = None):
        """
        Args:
            responses: 预设的响应队列，按顺序返回。
                      None 则使用默认响应。
        """
        self.responses = responses or []
        self.call_count = 0

    async def chat_sync(self, messages, model=None, max_tokens=None, temperature=None):
        """返回预设响应或默认响应"""
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        # 默认: 返回临时知识提取 JSON
        self.call_count += 1
        return json.dumps([
            {
                "content": "用户偏好使用中文交流",
                "category": "preference",
                "keywords": ["中文", "偏好"],
                "importance": 0.7,
            },
            {
                "content": "用户正在开发 MIA 多 Agent 系统",
                "category": "fact",
                "keywords": ["MIA", "Agent"],
                "importance": 0.8,
            },
        ])


# ─── 辅助: 创建测试用 MemoryAgent ──────────────────

async def _create_agent(
    tmpdir: Path,
    provider: MockProvider = None,
    enable_auto_store: bool = True,
) -> tuple[MemoryAgent, MessageBus, asyncio.Task]:
    """创建并启动一个测试用 MemoryAgent"""
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    store = MemoryStore(data_dir=tmpdir)
    store.load()

    agent = MemoryAgent(
        bus=bus,
        provider=provider or MockProvider(),
        store=store,
        enable_auto_store=enable_auto_store,
    )

    await agent.start()
    task = asyncio.create_task(agent.run())
    await asyncio.sleep(0.1)

    return agent, bus, task


async def _cleanup(agent: MemoryAgent, bus: MessageBus, task: asyncio.Task, tmpdir: Path):
    """清理测试资源"""
    await agent.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bus.stop()
    shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 测试 1: Level 1 临时知识提取
# ═══════════════════════════════════════════════════════════

async def test_level1_working_memory_extraction():
    """验证: CONVERSATION_DONE 后实时提取临时知识到 _working_memory"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_l1_"))
    agent = bus = task = None
    try:
        agent, bus, task = await _create_agent(tmpdir)

        # Step 1: USER_INTENT
        await bus.publish(Message(
            msg_type=MessageType.USER_INTENT,
            source="receiver",
            target="memory_agent",
            payload={"intent": "用户偏好中文交流，开发MIA系统", "original": "我在开发MIA"},
            session_id="s1",
        ))
        await asyncio.sleep(0.1)

        # Step 2: CONVERSATION_DONE
        await bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="sender",
            target="memory_agent",
            payload={"message": "好的，MIA 系统进展如何？"},
            session_id="s1",
        ))
        await asyncio.sleep(0.5)  # 等待 LLM 调用完成

        # ─── 断言 ────────────────────────────────────

        # 临时记忆应该被提取
        assert len(agent._working_memory) >= 1, \
            f"Level 1 临时记忆应至少有 1 条，实际: {len(agent._working_memory)}"

        # 持久存储应该还是空的 (还没换日)
        assert agent.store.count == 0, \
            f"Level 2 持久存储应为空 (未触发合并)，实际: {agent.store.count}"

        # 缓冲应该有 1 轮对话
        assert len(agent._daily_buffer) == 1, \
            f"缓冲应有 1 轮对话，实际: {len(agent._daily_buffer)}"

        # 临时记忆应包含正确的知识
        contents = " ".join(e.content for e in agent._working_memory)
        assert "中文" in contents, f"临时记忆应包含用户偏好: {contents}"

        print(f"  Level 1 临时记忆: {len(agent._working_memory)} 条")
        for e in agent._working_memory:
            print(f"    [{e.category_label}] {e.content} (confidence={e.confidence})")
        print("  [OK] test_level1_working_memory_extraction 通过")

    finally:
        if agent and bus and task:
            await _cleanup(agent, bus, task, tmpdir)


# ═══════════════════════════════════════════════════════════
# 测试 2: Level 2 合并去重持久化
# ═══════════════════════════════════════════════════════════

async def test_level2_consolidate_daily():
    """验证: 调用 _consolidate_daily() 后临时记忆合并持久化到 store"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_l2_"))
    agent = bus = task = None
    try:
        # 预设响应:
        #   第1次: 临时知识提取 (返回 2 条)
        #   第2次: Level 2 合并 (返回 3 条合并后的知识)
        provider = MockProvider(responses=[
            # Level 1 响应 (临时提取)
            json.dumps([
                {"content": "用户偏好中文", "category": "preference", "keywords": ["中文"], "importance": 0.7},
                {"content": "开发MIA Agent系统", "category": "fact", "keywords": ["MIA"], "importance": 0.8},
            ]),
            # Level 2 响应 (合并去重)
            json.dumps([
                {
                    "content": "用户偏好使用中文进行技术交流",
                    "category": "preference",
                    "confidence": 0.8,
                    "keywords": ["中文", "偏好", "技术交流"],
                    "importance": 0.8,
                    "source_sessions": ["s1"],
                },
                {
                    "content": "用户正在开发 MIA 多 Agent 智能系统",
                    "category": "fact",
                    "confidence": 0.9,
                    "keywords": ["MIA", "Agent", "开发"],
                    "importance": 0.9,
                    "source_sessions": ["s1"],
                },
                {
                    "content": "需要实现记忆系统重写",
                    "category": "task",
                    "confidence": 0.85,
                    "keywords": ["记忆", "重写"],
                    "importance": 0.7,
                    "source_sessions": ["s1"],
                },
            ]),
        ])

        agent, bus, task = await _create_agent(tmpdir, provider)

        # 模拟一轮对话
        await bus.publish(Message(
            msg_type=MessageType.USER_INTENT,
            source="receiver", target="memory_agent",
            payload={"intent": "用户偏好中文，开发MIA", "original": "我在用中文开发MIA"},
            session_id="s1",
        ))
        await asyncio.sleep(0.1)
        await bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="sender", target="memory_agent",
            payload={"message": "好的，继续开发MIA的记忆系统"},
            session_id="s1",
        ))
        await asyncio.sleep(0.5)

        # 验证 Level 1
        assert len(agent._working_memory) == 2, \
            f"Level 1 应有 2 条临时记忆: {len(agent._working_memory)}"

        # ─── 手动触发 Level 2 合并 ──────────────────
        await agent._consolidate_daily()
        await asyncio.sleep(0.1)

        # ─── 断言 ────────────────────────────────────

        # 临时记忆应被清空
        assert len(agent._working_memory) == 0, \
            f"合并后 _working_memory 应为空: {len(agent._working_memory)}"

        # 缓冲应被清空
        assert len(agent._daily_buffer) == 0, \
            f"合并后 _daily_buffer 应为空: {len(agent._daily_buffer)}"

        # 持久存储应有 3 条知识
        assert agent.store.count == 3, \
            f"Level 2 持久化应有 3 条知识: {agent.store.count}"

        # 验证知识类别
        entries = agent.store.get_all()
        categories = {e.category for e in entries}
        assert "preference" in categories, f"应有 preference: {categories}"
        assert "fact" in categories, f"应有 fact: {categories}"
        assert "task" in categories, f"应有 task: {categories}"

        # 验证置信度 >= 0.7
        for e in entries:
            assert e.confidence >= 0.7, \
                f"持久化知识置信度应 >= 0.7: {e.content[:30]} confidence={e.confidence}"

        print(f"  Level 2 持久知识: {agent.store.count} 条")
        for e in entries:
            print(f"    [{e.category_label}] {e.content[:50]}... (confidence={e.confidence})")
        print("  [OK] test_level2_consolidate_daily 通过")

    finally:
        if agent and bus and task:
            await _cleanup(agent, bus, task, tmpdir)


# ═══════════════════════════════════════════════════════════
# 测试 3: 合并检索
# ═══════════════════════════════════════════════════════════

async def test_retrieve_merged():
    """验证: 检索同时返回 working + persistent 结果"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_rm_"))
    agent = bus = task = None
    try:
        agent, bus, task = await _create_agent(tmpdir)

        # 手动填充临时记忆 (模拟已提取)
        agent._working_memory = [
            KnowledgeEntry(
                content="用户今天询问了天气预报",
                category=CATEGORY_FACT,
                confidence=0.5,
                keywords=["天气", "预报", "查询"],
                importance=0.4,
                source_sessions=["s_new"],
            ),
        ]

        # 手动填充持久存储 (模拟之前的知识)
        agent.store.add(KnowledgeEntry(
            content="用户偏好使用中文交流，喜欢详细注释",
            category=CATEGORY_PREFERENCE,
            confidence=0.8,
            keywords=["中文", "偏好", "注释", "交流"],
            importance=0.7,
            source_sessions=["s_old"],
        ))
        agent.store.add(KnowledgeEntry(
            content="用户正在开发 MIA 多 Agent 智能系统",
            category=CATEGORY_FACT,
            confidence=0.9,
            keywords=["MIA", "开发", "Agent", "系统"],
            importance=0.8,
            source_sessions=["s_old"],
        ))

        # ─── 执行合并检索 (用 "MIA" 关键词匹配持久条目) ──
        results = await agent._retrieve_merged(
            intent="MIA 系统开发进度",
            top_k=5,
        )

        # 注意: MockProvider 返回的默认 JSON 用于关键词提取，
        # 但这里关键词是通过 simple_tokenize("MIA 系统开发进度") → ["MIA", "系统开发进度"]
        # "MIA" 匹配 store entry 2, "系统" 匹配 store entry 2 的 keywords
        # 所以至少持久存储中的 MIA 条目应该被检索到

        # ─── 断言 ────────────────────────────────────
        # 至少检索到持久存储中的 MIA 条目 (关键词精确匹配)
        assert len(results) >= 1, \
            f"合并检索应至少返回 1 条: {len(results)}"

        contents_all = " ".join(e.content for e in results)
        assert "MIA" in contents_all, f"应包含 MIA 相关知识: {contents_all}"

        print(f"  合并检索: {len(results)} 条")
        for e in results:
            source = "working" if e.confidence <= 0.5 else "persistent"
            print(f"    [{e.category_label}] {e.content[:50]}... ({source})")
        print("  [OK] test_retrieve_merged 通过")

    finally:
        if agent and bus and task:
            await _cleanup(agent, bus, task, tmpdir)


# ═══════════════════════════════════════════════════════════
# 测试 4: MemoryStore CRUD
# ═══════════════════════════════════════════════════════════

async def test_store_knowledge_crud():
    """验证: MemoryStore 正确存储/加载/删除 KnowledgeEntry"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_crud_"))
    try:
        store = MemoryStore(data_dir=tmpdir)
        store.load()

        # ─── Create ──────────────────────────────────
        entry1 = KnowledgeEntry(
            content="用户偏好 Python 作为主要开发语言",
            category=CATEGORY_PREFERENCE,
            confidence=0.7,
            keywords=["Python", "偏好"],
            importance=0.6,
            source_sessions=["s1"],
        )
        entry2 = KnowledgeEntry(
            content="MIA 使用 MiMo Provider 作为主 LLM",
            category=CATEGORY_FACT,
            confidence=0.9,
            keywords=["MIA", "MiMo", "LLM"],
            importance=0.8,
            source_sessions=["s1", "s2"],
        )
        store.add(entry1)
        store.add(entry2)

        assert store.count == 2, f"应有 2 条知识: {store.count}"

        # ─── Read ────────────────────────────────────
        all_entries = store.get_all()
        assert len(all_entries) == 2

        # 验证字段完整性
        e1 = [e for e in all_entries if e.content == entry1.content][0]
        assert e1.category == CATEGORY_PREFERENCE
        assert e1.confidence == 0.7
        assert "Python" in e1.keywords
        assert e1.source_sessions == ["s1"]

        # 验证索引
        index = store.get_index_summaries()
        assert len(index) == 1  # 同一天
        ds = list(index.values())[0]
        assert ds.entry_count == 2
        assert ds.category_distribution.get("preference", 0) == 1
        assert ds.category_distribution.get("fact", 0) == 1

        # 验证日文件可加载
        date = list(index.keys())[0]
        loaded = store.load_day(date)
        assert len(loaded) == 2

        # ─── Delete ──────────────────────────────────
        store.delete(entry1.id)
        assert store.count == 1, f"删除后应有 1 条: {store.count}"
        remaining = store.get_all()
        assert remaining[0].id == entry2.id

        # ─── Clear ───────────────────────────────────
        store.clear()
        assert store.count == 0, f"清空后应为 0: {store.count}"

        print(f"  CRUD: create={2}, read=ok, delete=ok, clear=ok")
        print("  [OK] test_store_knowledge_crud 通过")

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════
# 测试 5: 降级持久化
# ═══════════════════════════════════════════════════════════

async def test_fallback_persist():
    """验证: LLM 不可用时，降级直接将临时记忆持久化"""
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_fb_"))
    agent = bus = task = None
    try:
        agent, bus, task = await _create_agent(tmpdir)

        # 手动填充临时记忆
        agent._working_memory = [
            KnowledgeEntry(
                content="测试知识 1",
                category=CATEGORY_DECISION,
                confidence=0.5,
                keywords=["test"],
                importance=0.5,
                source_sessions=["s_test"],
            ),
            KnowledgeEntry(
                content="测试知识 2",
                category=CATEGORY_FACT,
                confidence=0.5,
                keywords=["test"],
                importance=0.5,
                source_sessions=["s_test"],
            ),
        ]
        agent._daily_buffer = [{"user": "test", "assistant": "ok", "session_id": "s_test", "timestamp": "2026-01-01T00:00:00"}]

        # 执行降级持久化
        await agent._fallback_persist()

        # ─── 断言 ────────────────────────────────────
        assert agent.store.count == 2, \
            f"降级持久化应有 2 条: {agent.store.count}"
        assert len(agent._working_memory) == 0, \
            f"_working_memory 应被清空: {len(agent._working_memory)}"
        assert len(agent._daily_buffer) == 0, \
            f"_daily_buffer 应被清空: {len(agent._daily_buffer)}"

        # 置信度应被提升
        entries = agent.store.get_all()
        for e in entries:
            assert e.confidence >= 0.6, \
                f"降级持久化后置信度 >= 0.6: {e.confidence}"

        print(f"  降级持久化: {agent.store.count} 条")
        print("  [OK] test_fallback_persist 通过")

    finally:
        if agent and bus and task:
            await _cleanup(agent, bus, task, tmpdir)


# ═══════════════════════════════════════════════════════════
# 运行所有测试
# ═══════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("MemoryAgent 两级记忆梯度测试")
    print("=" * 60)
    print()

    tests = [
        ("Level 1 临时知识提取", test_level1_working_memory_extraction),
        ("Level 2 合并去重持久化", test_level2_consolidate_daily),
        ("合并检索 working+persistent", test_retrieve_merged),
        ("MemoryStore CRUD", test_store_knowledge_crud),
        ("降级持久化", test_fallback_persist),
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
