"""
测试 MemoryAgent 记忆存储 — 验证 CONVERSATION_DONE 后记忆被正确持久化

复现 bug: 交互模式中 input() 阻塞事件循环导致 MemoryAgent 后台任务无法处理消息
修复: input() 改为线程池执行 (main.py run_cli_interactive)
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

from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.agents.memory import MemoryAgent
from mia.memory.store import MemoryStore


# ─── Mock Provider — 快速返回固定 JSON 不调真实 LLM ───

class MockProvider:
    """模拟 LLM Provider，避免真实 API 调用"""

    async def chat_sync(self, messages, model=None, max_tokens=None, temperature=None):
        # 返回摘要 JSON (用于 _generate_summary)
        return '{"summary": "用户查询天气，助手返回预报信息", "keywords": ["测试", "天气"]}'


# ─── 测试 1: MemoryAgent 在异步环境下正确存储记忆 ───

async def test_memory_agent_stores_on_conversation_done():
    """验证: CONVERSATION_DONE 消息到达后，MemoryAgent 存储 user+assistant 两条记忆"""
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    # 临时目录作为记忆存储
    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_"))
    try:
        store = MemoryStore(data_dir=tmpdir)
        store.load()

        provider = MockProvider()
        agent = MemoryAgent(
            bus=bus,
            provider=provider,
            store=store,
            enable_auto_store=True,
        )

        # 启动 MemoryAgent
        await agent.start()
        agent_task = asyncio.create_task(agent.run())
        await asyncio.sleep(0.1)

        # ─── 模拟对话流程 ─────────────────────────────

        # Step 1: USER_INTENT (Receiver → MemoryAgent)
        await bus.publish(Message(
            msg_type=MessageType.USER_INTENT,
            source="receiver",
            target="memory_agent",
            payload={
                "intent": "用户说: 测试天气查询",
                "original": "查询一下天气",
            },
            session_id="test_session_001",
        ))
        await asyncio.sleep(0.2)  # 让 MemoryAgent 处理

        # 验证 _pending_intent 已设置
        assert agent._pending_intent == "用户说: 测试天气查询", \
            f"_pending_intent 应为 '用户说: 测试天气查询'，实际: {agent._pending_intent}"

        # Step 2: CONVERSATION_DONE (Sender → MemoryAgent)
        await bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="sender",
            target="memory_agent",
            payload={
                "message": "今天天气晴朗，25°C，适合出行。",
            },
            session_id="test_session_001",
        ))
        await asyncio.sleep(0.3)  # 让 MemoryAgent 处理存储

        # ─── 断言 ────────────────────────────────────

        total = store.get_total_count()
        print(f"  记忆总数: {total}")
        assert total == 2, f"期望 2 条记忆 (user + assistant)，实际: {total}"

        entries = store.get_all()
        roles = {e.role for e in entries}
        assert "user" in roles, f"缺少 user 条目: {roles}"
        assert "assistant" in roles, f"缺少 assistant 条目: {roles}"

        # 验证内容
        user_entry = [e for e in entries if e.role == "user"][0]
        assert "查询一下天气" in user_entry.content, f"user content 不匹配: {user_entry.content}"

        assistant_entry = [e for e in entries if e.role == "assistant"][0]
        assert "25°C" in assistant_entry.content, f"assistant content 不匹配: {assistant_entry.content}"

        print(f"  ✅ test_memory_agent_stores_on_conversation_done 通过")

        # 清理 agent
        await agent.stop()
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

    finally:
        await bus.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 测试 2: 模拟交互模式的事件循环不阻塞场景 ───

async def test_input_blocking_no_longer_blocks_memory():
    """
    验证修复: 当主协程在等待用户输入时 (模拟为 asyncio.sleep)，
    MemoryAgent 的后台任务仍然可以处理消息并存储记忆。

    这是对 main.py run_cli_interactive() 中 run_in_executor 修复的验证。
    """
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_"))
    try:
        store = MemoryStore(data_dir=tmpdir)
        store.load()

        provider = MockProvider()
        agent = MemoryAgent(
            bus=bus,
            provider=provider,
            store=store,
            enable_auto_store=True,
        )

        await agent.start()
        agent_task = asyncio.create_task(agent.run())
        await asyncio.sleep(0.1)

        # ─── 模拟对话 ───────────────────────────────

        await bus.publish(Message(
            msg_type=MessageType.USER_INTENT,
            source="receiver",
            target="memory_agent",
            payload={"intent": "查询天气", "original": "今天天气怎么样"},
            session_id="s2",
        ))

        # 模拟事件循环中的一个 tick
        await asyncio.sleep(0.1)

        await bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="sender",
            target="memory_agent",
            payload={"message": "今天雷阵雨，记得带伞。"},
            session_id="s2",
        ))

        # 关键: 模拟 run_in_executor 的等待 ——
        # 主协程让出事件循环，MemoryAgent 在后台处理
        # (修复前: input() 阻塞导致 MemoryAgent 永远收不到消息)
        for _ in range(5):
            await asyncio.sleep(0.05)

        # ─── 断言 ───────────────────────────────────

        total = store.get_total_count()
        print(f"  记忆总数: {total}")
        assert total == 2, f"期望 2 条记忆，实际: {total}"

        entries = store.get_all()
        for e in entries:
            print(f"  [{e.role}] {e.summary}")

        print(f"  ✅ test_input_blocking_no_longer_blocks_memory 通过")

        await agent.stop()
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

    finally:
        await bus.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 测试 3: 多次对话后 index + daily 文件结构正确 ───

async def test_index_and_daily_files_after_multiple_conversations():
    """验证: 多次对话后，index.json 和 daily 文件结构正确"""
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    tmpdir = Path(tempfile.mkdtemp(prefix="mia_test_"))
    try:
        store = MemoryStore(data_dir=tmpdir)
        store.load()

        provider = MockProvider()
        agent = MemoryAgent(
            bus=bus,
            provider=provider,
            store=store,
            enable_auto_store=True,
        )

        await agent.start()
        agent_task = asyncio.create_task(agent.run())
        await asyncio.sleep(0.1)

        # ─── 模拟两轮对话 ────────────────────────────

        # 第 1 轮
        await bus.publish(Message(
            msg_type=MessageType.USER_INTENT,
            source="receiver",
            target="memory_agent",
            payload={"intent": "查询天气", "original": "天气"},
            session_id="s1",
        ))
        await asyncio.sleep(0.1)
        await bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="sender",
            target="memory_agent",
            payload={"message": "晴天"},
            session_id="s1",
        ))
        await asyncio.sleep(0.2)

        # 第 2 轮
        await bus.publish(Message(
            msg_type=MessageType.USER_INTENT,
            source="receiver",
            target="memory_agent",
            payload={"intent": "查询股票", "original": "股票"},
            session_id="s2",
        ))
        await asyncio.sleep(0.1)
        await bus.publish(Message(
            msg_type=MessageType.CONVERSATION_DONE,
            source="sender",
            target="memory_agent",
            payload={"message": "股价上涨"},
            session_id="s2",
        ))
        await asyncio.sleep(0.2)

        # ─── 验证文件结构 ─────────────────────────────

        assert (tmpdir / "index.json").exists(), "index.json 应存在"
        daily_files = list((tmpdir / "daily").glob("*.json"))
        print(f"  Daily 文件: {[f.name for f in daily_files]}")

        assert len(daily_files) >= 1, f"至少要有 1 个 daily 文件: {daily_files}"

        total = store.get_total_count()
        assert total == 4, f"期望 4 条记忆 (2轮 × 2条/轮)，实际: {total}"

        # 验证 index
        index = store.get_index_summaries()
        assert len(index) >= 1, f"index 至少要有 1 天: {index}"

        # 验证每条 daily 文件都能正常加载
        for date_str in index:
            entries = store.load_day(date_str)
            assert len(entries) == index[date_str].entry_count, \
                f"日期 {date_str}: load_day 返回 {len(entries)} 条, index 说 {index[date_str].entry_count} 条"

        print(f"  ✅ test_index_and_daily_files_after_multiple_conversations 通过")

        await agent.stop()
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass

    finally:
        await bus.stop()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 运行所有测试 ──────────────────────────────────

async def main():
    print("=" * 60)
    print("MemoryAgent 记忆存储测试")
    print("=" * 60)
    print()

    tests = [
        ("MemoryAgent CONVERSATION_DONE 存储", test_memory_agent_stores_on_conversation_done),
        ("交互模式事件循环不阻塞", test_input_blocking_no_longer_blocks_memory),
        ("多轮对话 index+daily 结构", test_index_and_daily_files_after_multiple_conversations),
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
            print(f"  ❌ FAIL: {e}")

    print()
    print("=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, {len(tests)} 总计")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
