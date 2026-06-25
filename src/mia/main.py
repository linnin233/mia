"""
MIA 主入口 — CLI 交互 + FastAPI HTTP 服务 (可选)

完整 Agent 链路:
  User Input → ReceiverAgent → SchedulerAgent → (TaskAgent) → SenderAgent → Output

用法:
  # 单次对话模式
  python -m mia --query "你好，帮我搜索今天的新闻"

  # 交互模式
  python -m mia

  # HTTP API 服务模式
  python -m mia --server --port 8080
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Optional

from loguru import logger

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root / "src"))

from mia.config import get_config
from mia.bus.bus import MessageBus
from mia.bus.message import Message, MessageType
from mia.model_registry import create_provider
from mia.session import SessionManager
from mia.agents.receiver import ReceiverAgent
from mia.agents.scheduler import SchedulerAgent
from mia.agents.sender import SenderAgent
from mia.agents.task import TaskAgent
from mia.agents.memory import MemoryAgent
from mia.memory.store import MemoryStore
from mia.channels.wechat.receiver import WeChatReceiverAgent
from mia.channels.wechat.sender import WeChatSenderAgent
from mia.channels.telegram.receiver import TelegramReceiverAgent
from mia.channels.telegram.sender import TelegramSenderAgent
from mia.cli.commands import (
    handle_model_command,
    handle_agent_command,
    handle_channel_command,
    handle_interface_command,
    handle_session_command,
    CommandAction,
)


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="MIA — MiMo Intelligent Agent 多Agent系统",
    )
    parser.add_argument(
        "--server", action="store_true",
        help="以 HTTP API 服务模式启动",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="HTTP 服务端口 (默认: 8080)",
    )
    parser.add_argument(
        "--query", "-q", type=str,
        help="单次对话模式，直接输入问题",
    )
    parser.add_argument(
        "--image", "-i", type=str,
        help="图片路径 (配合 --query 使用)",
    )
    parser.add_argument(
        "--voice", "-v", type=str,
        help="语音文件路径 (配合 --query 使用)",
    )
    return parser.parse_args()


async def run_agent_pipeline(
    query: str,
    image_path: Optional[str] = None,
    voice_path: Optional[str] = None,
    timeout: float = 180.0,
) -> Optional[str]:
    """
    运行完整的 Agent 链路

    Args:
        query: 用户文本输入
        image_path: 可选的图片路径
        voice_path: 可选的语音文件路径
        timeout: 整体超时秒数

    Returns:
        最终回复文本，超时返回 None
    """
    # ─── 1. 创建 MessageBus ───────────────────────────
    config = get_config()
    rt = config.runtime
    session_id = uuid.uuid4().hex[:12]
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    # ─── 总线记忆镜像 ──────────────────────────────
    _mirror_types = [
        MessageType.USER_INTENT,
        MessageType.SEND_TEXT,
        MessageType.STREAM_END,
        MessageType.EXECUTE_TASK,
        MessageType.TASK_RESULT,
        MessageType.TASK_ERROR,
        MessageType.CONVERSATION_DONE,
    ]
    for mt in _mirror_types:
        bus.subscribe_mirror(mt, "memory_agent")

    # ─── 2. 初始化 Provider（从 RuntimeConfig 读取平台 Key）───
    mimo_key = rt.provider_api_keys.get("mimo", config.mimo.api_key)
    deepseek_key = rt.provider_api_keys.get("deepseek", config.deepseek.api_key)

    mimo = None
    deepseek = None
    if mimo_key:
        mimo = create_provider("mimo", mimo_key)
    if deepseek_key:
        deepseek = create_provider("deepseek", deepseek_key)

    # ─── 3. 创建所有 Agent（从 RuntimeConfig 读取模型分配）────
    receiver = ReceiverAgent(bus=bus, mimo=mimo)
    scheduler = SchedulerAgent(
        bus=bus,
        provider=mimo,
        model=rt.scheduler_model,
        fallback_provider=deepseek if rt.scheduler_fallback else None,
        fallback_model=rt.scheduler_fallback if rt.scheduler_fallback else None,
        enable_streaming=config.agent.enable_streaming,
    )
    sender = SenderAgent(
        bus=bus,
        mimo=mimo if rt.sender_tts_enabled else None,
        output_dir=config.agent.workspace_dir,
    )
    task_agent = TaskAgent(
        bus=bus,
        provider=mimo,
        model=rt.task_model,
        fallback_provider=deepseek if rt.task_fallback else None,
        fallback_model=rt.task_fallback if rt.task_fallback else None,
    )

    # MemoryAgent — 记忆检索与存储
    memory_agent = MemoryAgent(
        bus=bus,
        provider=mimo,
        model=rt.memory_model,
        fallback_provider=deepseek if rt.memory_fallback else None,
        fallback_model=rt.memory_fallback if rt.memory_fallback else None,
    )

    # WeChat 通信渠道 (可选) — 收发分离
    if rt.wechat_enabled:
        wechat_receiver = WeChatReceiverAgent(
            bus=bus,
            bot_token=config.wechat.bot_token,
            bot_token_file=config.wechat.bot_token_file,
            base_url=config.wechat.base_url,
            enabled=config.wechat.enabled or rt.wechat_enabled,
            media_dir=config.wechat.media_dir,
        )
        wechat_sender = WeChatSenderAgent(
            bus=bus,
            bot_token=config.wechat.bot_token,
            bot_token_file=config.wechat.bot_token_file,
            base_url=config.wechat.base_url,
            enabled=True,
            mimo=mimo if rt.wechat_sender_tts_enabled else None,
            workspace_dir=config.agent.workspace_dir,
        )

    # Telegram 通信渠道 (可选) — 收发分离
    if rt.telegram_enabled:
        telegram_receiver = TelegramReceiverAgent(
            bus=bus,
            bot_token=config.telegram.bot_token,
            bot_token_file=config.telegram.bot_token_file,
            enabled=True,
        )
        telegram_sender = TelegramSenderAgent(
            bus=bus,
            bot_token=config.telegram.bot_token,
            bot_token_file=config.telegram.bot_token_file,
            enabled=True,
            mimo=mimo if rt.telegram_sender_tts_enabled else None,
            workspace_dir=config.agent.workspace_dir,
        )

    # ─── 5. 启动所有 Agent ───────────────────────────
    print(f"\033[1m{'='*50}\033[0m")
    print(f"\033[1mMIA v0.1.0 — MiMo Intelligent Agent\033[0m")
    print(f"  Session: {session_id}")
    print(f"  Scheduler: {rt.scheduler_model} (主) / {rt.scheduler_fallback or '无'} (备)")
    print(f"  Receiver: 视觉={'on' if rt.receiver_vision_enabled else 'off'} 语音={'on' if rt.receiver_audio_enabled else 'off'}")
    print(f"  Sender: TTS={'on' if rt.sender_tts_enabled else 'off'}")
    if rt.wechat_enabled:
        print(f"  WeChat: 已启用 (iLink Bot)")
    if rt.telegram_enabled:
        print(f"  Telegram: 已启用 (Bot API)")
    print(f"\033[1m{'='*50}\033[0m")
    print()

    # 启动 Agent 的 start() 和 run() 循环
    await receiver.start()
    await memory_agent.start()
    await scheduler.start()
    await sender.start()
    await task_agent.start()
    if rt.wechat_enabled:
        await wechat_receiver.start()
        await wechat_sender.start()
    if rt.telegram_enabled:
        await telegram_receiver.start()
        await telegram_sender.start()

    # 为每个 Agent 启动消息处理循环 (后台任务)
    agents = [receiver, memory_agent, scheduler, sender, task_agent]
    if rt.wechat_enabled:
        agents.extend([wechat_receiver, wechat_sender])
    if rt.telegram_enabled:
        agents.extend([telegram_receiver, telegram_sender])
    tasks: list[asyncio.Task] = []
    for agent in agents:
        t = asyncio.create_task(agent.run())
        tasks.append(t)

    # 等待 Agent 全部就绪
    await asyncio.sleep(0.3)

    final_response: Optional[str] = None

    try:
        # ─── 5. 注入用户输入 ──────────────────────────
        raw_msg = Message(
            msg_type=MessageType.RAW_INPUT,
            source="main",
            target="receiver",
            payload={
                "text": query,
                "image": image_path,
                "voice": voice_path,
            },
            session_id=session_id,
        )
        await bus.publish(raw_msg)

        print(f"\033[36m[Main]\033[0m 用户输入已注入: {query[:100]}")

        # ─── 6. 等待 Sender 输出 ──────────────────────
        # Main 也订阅总线，监听 SEND_TEXT
        await bus.subscribe("main")
        main_timeout = timeout

        while main_timeout > 0:
            msg = await bus.receive("main", timeout=1.0)
            main_timeout -= 1.0

            if msg is None:
                continue

            if msg.msg_type == MessageType.CONVERSATION_DONE:
                final_response = msg.payload.get("message", "")
                break

            # 也检查 ERROR 等系统消息
            if msg.msg_type == MessageType.TASK_ERROR:
                print(f"\033[31m[Main] 检测到 TASK_ERROR: {msg.payload.get('error', '')}\033[0m")

        if final_response is None:
            print(f"\n\033[31m[Main] 超时 ({timeout}s)，未收到回复\033[0m")

        # ─── 给 MemoryAgent 时间存储记忆 ────────────
        # Sender 同时向 "main" 和 "memory_agent" 发送 CONVERSATION_DONE
        # 需要给 event loop 一个调度周期让 memory_agent 处理消息
        await asyncio.sleep(0.5)

    except Exception as e:
        logger.error("[Main] Agent 链路异常: {}", e)
        print(f"\033[31m[Main] 错误: {e}\033[0m")

    finally:
        # ─── 7. 清理 ──────────────────────────────────
        # 停止所有 Agent
        for agent in agents:
            await agent.stop()

        # 取消后台任务
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        await bus.stop()

    return final_response


async def run_cli_query(
    query: str,
    image_path: Optional[str] = None,
    voice_path: Optional[str] = None,
) -> None:
    """运行单次 CLI 对话"""
    result = await run_agent_pipeline(
        query=query,
        image_path=image_path,
        voice_path=voice_path,
    )

    if result:
        print()
        print(f"\033[1m{'='*50}\033[0m")
        print(f"\033[1m[完成] 对话结束\033[0m")
    else:
        print(f"\033[31m[失败] 未收到回复\033[0m")
        sys.exit(1)


def _find_agent(agent_list: list, agent_type):
    """从 Agent 列表中查找指定类型的实例"""
    for agent in agent_list:
        if isinstance(agent, agent_type):
            return agent
    return None


async def _reconfigure_agents(
    agent_list: list,
    tasks: list,
    bus: MessageBus,
    config,
    session_manager: Optional[SessionManager] = None,
) -> tuple[list, list]:
    """根据当前 RuntimeConfig 重建所有 Agent

    会话状态保护:
      1. 重建前保存 MemoryAgent 的会话状态
      2. 重建后将状态恢复到新的 MemoryAgent 实例
      3. session_manager=None 时跳过（兼容 run_agent_pipeline）
    """
    rt = config.runtime

    # 0. 保存旧 MemoryAgent 的会话状态（避免重建时丢失）
    old_memory = _find_agent(agent_list, MemoryAgent)
    if old_memory and session_manager:
        await old_memory.save_state()
    current_session_id = session_manager.get_current_session_id() if session_manager else None

    # 1. 停止旧 Agent
    for agent in agent_list:
        await agent.stop()
    # 2. 取消旧任务
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    tasks.clear()
    agent_list.clear()

    # 3. 重新创建 Provider
    mimo_key = rt.provider_api_keys.get("mimo", config.mimo.api_key)
    deepseek_key = rt.provider_api_keys.get("deepseek", config.deepseek.api_key)

    mimo = None
    deepseek = None
    if mimo_key:
        mimo = create_provider("mimo", mimo_key)
    if deepseek_key:
        deepseek = create_provider("deepseek", deepseek_key)

    # 4. 重建核心 Agent
    receiver = ReceiverAgent(bus=bus, mimo=mimo)
    scheduler = SchedulerAgent(
        bus=bus,
        provider=mimo,
        model=rt.scheduler_model,
        fallback_provider=deepseek if rt.scheduler_fallback else None,
        fallback_model=rt.scheduler_fallback if rt.scheduler_fallback else None,
        enable_streaming=config.agent.enable_streaming,
    )
    sender = SenderAgent(
        bus=bus,
        mimo=mimo if rt.sender_tts_enabled else None,
        output_dir=config.agent.workspace_dir,
    )
    task_agent = TaskAgent(
        bus=bus,
        provider=mimo,
        model=rt.task_model,
        fallback_provider=deepseek if rt.task_fallback else None,
        fallback_model=rt.task_fallback if rt.task_fallback else None,
    )
    memory_agent = MemoryAgent(
        bus=bus,
        provider=mimo,
        model=rt.memory_model,
        fallback_provider=deepseek if rt.memory_fallback else None,
        fallback_model=rt.memory_fallback if rt.memory_fallback else None,
        session_manager=session_manager,
    )

    agent_list.extend([receiver, memory_agent, scheduler, sender, task_agent])

    # 5. 微信渠道
    wechat_receiver = None
    wechat_sender = None
    if rt.wechat_enabled:
        wechat_receiver = WeChatReceiverAgent(
            bus=bus,
            bot_token=config.wechat.bot_token,
            bot_token_file=config.wechat.bot_token_file,
            base_url=config.wechat.base_url,
            enabled=True,
            media_dir=config.wechat.media_dir,
        )
        wechat_sender = WeChatSenderAgent(
            bus=bus,
            bot_token=config.wechat.bot_token,
            bot_token_file=config.wechat.bot_token_file,
            base_url=config.wechat.base_url,
            enabled=True,
            mimo=mimo if rt.wechat_sender_tts_enabled else None,
            workspace_dir=config.agent.workspace_dir,
        )
        agent_list.extend([wechat_receiver, wechat_sender])

    # 5b. Telegram 渠道
    telegram_receiver = None
    telegram_sender = None
    if rt.telegram_enabled:
        telegram_receiver = TelegramReceiverAgent(
            bus=bus,
            bot_token=config.telegram.bot_token,
            bot_token_file=config.telegram.bot_token_file,
            enabled=True,
        )
        telegram_sender = TelegramSenderAgent(
            bus=bus,
            bot_token=config.telegram.bot_token,
            bot_token_file=config.telegram.bot_token_file,
            enabled=True,
            mimo=mimo if rt.telegram_sender_tts_enabled else None,
            workspace_dir=config.agent.workspace_dir,
        )
        agent_list.extend([telegram_receiver, telegram_sender])

    # 6. 启动新 Agent
    for agent in agent_list:
        await agent.start()
    await asyncio.sleep(0.3)
    # 恢复会话状态（在 Agent 启动后、run() 前）
    if session_manager and current_session_id:
        new_memory = _find_agent(agent_list, MemoryAgent)
        if new_memory:
            await new_memory.load_state(current_session_id)
            session_manager.set_current(current_session_id)
    for agent in agent_list:
        tasks.append(asyncio.create_task(agent.run()))

    print(f"  \033[32m[OK]\033[0m Agent 已重建 "
          f"(Scheduler: {rt.scheduler_model}, "
          f"Receiver语音={'on' if rt.receiver_audio_enabled else 'off'}, "
          f"SenderTTS={'on' if rt.sender_tts_enabled else 'off'})")

    return agent_list, tasks


async def _handle_compact(memory_agent: MemoryAgent) -> None:
    """处理 /compact 命令 — 压缩跨对话历史 (临时记忆 + 持久知识)"""
    # 检查临时记忆 + 持久知识 (临时记忆在 working_memory 中，不在 store)
    has_working = (
        len(memory_agent._working_memory) > 0
        or len(memory_agent._daily_buffer) > 0
    )
    if memory_agent.store.count == 0 and not has_working:
        print("  \033[90m对话历史为空，无需压缩。\033[0m")
        print()
        return

    memory_count = memory_agent.store.count + len(memory_agent._working_memory)
    print(f"  \033[90m正在压缩对话历史 ({memory_count} 条记录)...\033[0m")
    try:
        summary = await memory_agent.compact()
        new_count = memory_agent.store.count
        print(f"  \033[32m[OK] 对话历史已压缩 ({memory_count} 条 → {new_count} 条摘要)\033[0m")
        print(f"  \033[90m摘要: {summary[:100]}...\033[0m")
    except Exception as e:
        print(f"  \033[31m[FAIL] 压缩失败: {e}\033[0m")
    print()


async def run_cli_interactive() -> None:
    """CLI 交互模式 — 持久 Agent 系统 (启动一次，持续运行)

    通信渠道开关通过 /channel 命令控制，启动时从 RuntimeConfig 读取。
    """
    print(f"\033[1mMIA v0.1.0 — 交互模式\033[0m")
    print(f"  输入 '/quit' 退出, '/help' 查看帮助, '/compact' 压缩对话历史")
    print(f"  直接输入问题开始对话")
    print()

    # ══════════════════════════════════════════════════════
    # 系统初始化 — 只执行一次，整个交互会话内持续运行
    # ══════════════════════════════════════════════════════

    # 交互模式下抑制 loguru 终端输出 — MemoryAgent 后台提取等日志
    # 不应出现在 You> 提示符附近，只写文件
    logger.remove()
    from pathlib import Path as _Path
    _log_dir = _Path(__file__).parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        _log_dir / "mia.log",
        rotation="10 MB",
        retention="3 days",
        level="DEBUG",
        format="{time} | {level} | {name}:{function}:{line} - {message}",
    )

    config = get_config()
    rt = config.runtime

    # ─── 会话管理 ──────────────────────────────
    session_manager = SessionManager()
    session_manager.load_index()

    # 每次启动创建新的 CLI 会话（不恢复旧会话）
    # 旧会话仍可通过 /session 切换回去
    new_session = session_manager.create_session("新对话", source="cli")
    session_manager.set_current(new_session.session_id)
    print(f"  \033[90m会话: {new_session.name} ({new_session.session_id})\033[0m")

    bus = MessageBus(max_queue_size=100)
    await bus.start()

    # ─── 总线记忆镜像 ──────────────────────────────
    _mirror_types = [
        MessageType.USER_INTENT,
        MessageType.SEND_TEXT,
        MessageType.STREAM_END,
        MessageType.EXECUTE_TASK,
        MessageType.TASK_RESULT,
        MessageType.TASK_ERROR,
        MessageType.CONVERSATION_DONE,
    ]
    for mt in _mirror_types:
        bus.subscribe_mirror(mt, "memory_agent")

    # ─── 初始化 Provider（从 RuntimeConfig 读取平台 Key）───
    mimo_key = rt.provider_api_keys.get("mimo", config.mimo.api_key)
    deepseek_key = rt.provider_api_keys.get("deepseek", config.deepseek.api_key)

    mimo = None
    deepseek = None
    if mimo_key:
        mimo = create_provider("mimo", mimo_key)
    if deepseek_key:
        deepseek = create_provider("deepseek", deepseek_key)

    receiver = ReceiverAgent(bus=bus, mimo=mimo)
    scheduler = SchedulerAgent(
        bus=bus,
        provider=mimo,
        model=rt.scheduler_model,
        fallback_provider=deepseek if rt.scheduler_fallback else None,
        fallback_model=rt.scheduler_fallback if rt.scheduler_fallback else None,
        enable_streaming=config.agent.enable_streaming,
    )
    sender = SenderAgent(
        bus=bus,
        mimo=mimo if rt.sender_tts_enabled else None,
        output_dir=config.agent.workspace_dir,
    )
    task_agent = TaskAgent(
        bus=bus,
        provider=mimo,
        model=rt.task_model,
        fallback_provider=deepseek if rt.task_fallback else None,
        fallback_model=rt.task_fallback if rt.task_fallback else None,
    )
    memory_agent = MemoryAgent(
        bus=bus,
        provider=mimo,
        model=rt.memory_model,
        fallback_provider=deepseek if rt.memory_fallback else None,
        fallback_model=rt.memory_fallback if rt.memory_fallback else None,
        session_manager=session_manager,
    )

    # WeChat 通信渠道 (可选) — 收发分离
    wechat_receiver = None
    wechat_sender = None
    if rt.wechat_enabled:
        wechat_receiver = WeChatReceiverAgent(
            bus=bus,
            bot_token=config.wechat.bot_token,
            bot_token_file=config.wechat.bot_token_file,
            base_url=config.wechat.base_url,
            enabled=True,
            media_dir=config.wechat.media_dir,
        )
        wechat_sender = WeChatSenderAgent(
            bus=bus,
            bot_token=config.wechat.bot_token,
            bot_token_file=config.wechat.bot_token_file,
            base_url=config.wechat.base_url,
            enabled=True,
            mimo=mimo if rt.wechat_sender_tts_enabled else None,
            workspace_dir=config.agent.workspace_dir,
        )

    # Telegram 通信渠道 (可选) — 收发分离
    telegram_receiver = None
    telegram_sender = None
    if rt.telegram_enabled:
        telegram_receiver = TelegramReceiverAgent(
            bus=bus,
            bot_token=config.telegram.bot_token,
            bot_token_file=config.telegram.bot_token_file,
            enabled=True,
        )
        telegram_sender = TelegramSenderAgent(
            bus=bus,
            bot_token=config.telegram.bot_token,
            bot_token_file=config.telegram.bot_token_file,
            enabled=True,
            mimo=mimo if rt.telegram_sender_tts_enabled else None,
            workspace_dir=config.agent.workspace_dir,
        )

    # 启动所有 Agent
    print(f"\033[1m{'='*50}\033[0m")
    print(f"\033[1mMIA v0.1.0 — MiMo Intelligent Agent (持久模式)\033[0m")
    print(f"  Scheduler: {rt.scheduler_model} (主) / {rt.scheduler_fallback or '无'} (备)")
    print(f"  Receiver: 视觉={'on' if rt.receiver_vision_enabled else 'off'} 语音={'on' if rt.receiver_audio_enabled else 'off'}")
    print(f"  Sender: TTS={'on' if rt.sender_tts_enabled else 'off'}")
    print(f"  记忆: MemoryAgent @ {memory_agent.store.file_path}/ (index+daily)")
    if rt.wechat_enabled:
        print(f"  微信: 已启用 (iLink Bot 长轮询) {'(有 token)' if config.wechat.bot_token else '(需 QR 码登录)'}")
    if rt.telegram_enabled:
        print(f"  Telegram: 已启用 (Bot API @{config.telegram.bot_token[:10]}...{' 有 token' if config.telegram.bot_token else ' 需配置'})")
    print(f"\033[1m{'='*50}\033[0m")
    print()

    await receiver.start()
    await memory_agent.start()
    await scheduler.start()
    await sender.start()
    await task_agent.start()
    if rt.wechat_enabled:
        await wechat_receiver.start()
        await wechat_sender.start()
    if rt.telegram_enabled:
        await telegram_receiver.start()
        await telegram_sender.start()

    # 后台消息处理循环 (持久运行)
    agent_list = [receiver, memory_agent, scheduler, sender, task_agent]
    if rt.wechat_enabled:
        agent_list.extend([wechat_receiver, wechat_sender])
    if rt.telegram_enabled:
        agent_list.extend([telegram_receiver, telegram_sender])
    tasks: list[asyncio.Task] = []
    for agent in agent_list:
        tasks.append(asyncio.create_task(agent.run()))

    await asyncio.sleep(0.3)

    try:
        # ══════════════════════════════════════════════════
        # 用户输入循环 — 每轮对话在持久系统中处理
        # ══════════════════════════════════════════════════
        # 注意: input() 必须在线程池中执行，否则会阻塞事件循环，
        # 导致 MemoryAgent 等后台任务无法处理消息（记忆无法存储）。
        while True:
            try:
                # 使用线程池执行 input()，让事件循环在等待用户输入时保持自由
                # 这样 MemoryAgent 可以在后台完成 LLM 摘要生成和记忆持久化
                loop = asyncio.get_event_loop()
                user_input = (await loop.run_in_executor(
                    None, input, "\033[32mYou > \033[0m"
                )).strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见~")
                break

            if not user_input:
                continue

            if user_input.lower() in ("/quit", "/exit", "/q"):
                print("再见~")
                break

            if user_input.lower() in ("/help", "/h"):
                print("""
命令:
  /quit, /exit, /q  — 退出
  /help, /h         — 显示帮助
  /model            — 模型平台配置 (API Key + 模型开关)
  /agent            — Agent 模型分配 (每个Agent独立选模型)
  /channel          — 通信渠道开关 (启用/禁用微信)
  /interface        — 消息接口绑定管理 (查看token/重新扫码/删除绑定)
  /session          — 会话管理 (列表/切换/新建/重命名/删除)
  /compact          — 压缩对话历史 (将多轮对话总结为摘要，节省 token)
  /verbose          — 切换详细日志 (默认开启，关闭后只显示概要)
  /memory           — 显示当前对话记忆状态
  /image <path>     — 发送图片 (下一行输入)
  /voice <path>     — 发送语音/音频文件 (下一行可选输入文字)
  /record           — 从麦克风录音并发送
  直接输入文本       — 开始对话

示例:
  You > 帮我搜索最新的 Python 新闻
  You > /model          (配置 API Key 和模型)
  You > /agent          (给每个 Agent 分配模型)
  You > /channel        (开关微信渠道)
  You > /interface      (查看微信绑定状态)
  You > /compact
""")
                continue

            # /verbose — 切换详细日志
            if user_input.lower() == "/verbose":
                config = get_config()
                config.agent.verbose = not config.agent.verbose
                status = "开启" if config.agent.verbose else "关闭"
                print(f"  \033[90m详细日志: {status}\033[0m")
                print(f"  \033[90m(Agent 思考过程、工具调用详情等)\033[0m")
                print()
                continue

            # /compact — 压缩对话历史
            if user_input.lower() == "/compact":
                mem = _find_agent(agent_list, MemoryAgent) or memory_agent
                if mem:
                    await _handle_compact(mem)
                else:
                    print("  \033[33mMemoryAgent 未就绪\033[0m")
                continue

            # /memory — 交互式记忆浏览器 (临时 + 持久, 3级钻取)
            if user_input.lower() == "/memory":
                mem = _find_agent(agent_list, MemoryAgent) or memory_agent
                if not mem:
                    print("  \033[33mMemoryAgent 未就绪\033[0m")
                    continue
                from mia.memory.browser import MemoryBrowser
                browser = MemoryBrowser(
                    mem.store,
                    working_entries=mem._working_memory,
                )
                await browser.browse()
                continue

            # /image — 图片输入
            image_path = None
            voice_path = None
            if user_input.lower().startswith("/image "):
                image_path = user_input[7:].strip()
                user_input = input("\033[32mYou (图片说明) > \033[0m").strip()
                if not user_input:
                    user_input = "请描述这张图片"

            # /voice — 语音/音频输入 (多模态理解)
            if user_input.lower().startswith("/voice "):
                voice_path = user_input[7:].strip()
                # 使用 run_in_executor 避免 input() 阻塞事件循环
                user_input = (await loop.run_in_executor(
                    None, input, "\033[32mYou (语音说明/可选) > \033[0m"
                )).strip()
                # 不再注入虚拟的"请理解..."提示词
                # 如果用户没输文字，Receiver 会将音频理解直接作为用户意图

            # /record — 麦克风录音输入
            if user_input.lower() == "/record":
                try:
                    from mia.audio.recorder import record_until_keypress

                    # 等待用户准备好 (run_in_executor 避免阻塞事件循环)
                    await loop.run_in_executor(
                        None, input,
                        "  \033[33m[录音]\033[0m 准备好后按 Enter 开始录音...\n",
                    )

                    print("  \033[33m[录音]\033[0m \033[91m● 正在录音... 按 Enter 停止\033[0m")

                    # 在 executor 线程中录音 (内部 input() 等待用户停止)
                    voice_path = await loop.run_in_executor(
                        None, record_until_keypress,
                    )

                    if voice_path:
                        print(f"  \033[32m[OK]\033[0m 录音完成 ({voice_path})")
                        user_input = (await loop.run_in_executor(
                            None, input, "\033[32mYou (语音说明/可选) > \033[0m"
                        )).strip()
                        # 不再注入虚拟提示词 — Receiver 会直接用音频理解作为意图
                    else:
                        print("  \033[31m[FAIL]\033[0m 录音失败或为空，请重试")
                        print()
                        continue
                except ImportError as e:
                    print(f"  \033[31m[FAIL]\033[0m 缺少录音依赖: {e}")
                    print(f"  \033[90m请运行: pip install sounddevice soundfile\033[0m")
                    print()
                    continue
                except Exception as e:
                    print(f"  \033[31m[FAIL]\033[0m 录音异常: {e}")
                    print()
                    continue

            # /model — 模型平台配置 (API Key + 模型开关)
            if user_input.lower() == "/model":
                action = await handle_model_command(rt)
                if action == CommandAction.RECONFIGURE_AGENTS:
                    agent_list, tasks = await _reconfigure_agents(
                        agent_list, tasks, bus, config,
                        session_manager=session_manager,
                    )
                    memory_agent = _find_agent(agent_list, MemoryAgent)
                continue

            # /agent — Agent 模型分配
            if user_input.lower() == "/agent":
                action = await handle_agent_command(rt)
                if action == CommandAction.RECONFIGURE_AGENTS:
                    agent_list, tasks = await _reconfigure_agents(
                        agent_list, tasks, bus, config,
                        session_manager=session_manager,
                    )
                    memory_agent = _find_agent(agent_list, MemoryAgent)
                continue

            # /channel — 通信渠道配置 (微信等)
            if user_input.lower() == "/channel":
                action = await handle_channel_command(rt)
                if action == CommandAction.RECONFIGURE_WECHAT:
                    agent_list, tasks = await _reconfigure_agents(
                        agent_list, tasks, bus, config,
                        session_manager=session_manager,
                    )
                    memory_agent = _find_agent(agent_list, MemoryAgent)
                continue

            # /interface — 消息接口绑定管理 (查看token/重新绑定/删除绑定)
            if user_input.lower() == "/interface":
                action = await handle_interface_command(rt)
                if action == CommandAction.RECONFIGURE_WECHAT:
                    agent_list, tasks = await _reconfigure_agents(
                        agent_list, tasks, bus, config,
                        session_manager=session_manager,
                    )
                    memory_agent = _find_agent(agent_list, MemoryAgent)
                continue

            # /session — 会话管理 (列表/切换/新建/重命名/删除)
            if user_input.lower() == "/session":
                mem = _find_agent(agent_list, MemoryAgent) or memory_agent
                if mem:
                    await handle_session_command(rt, session_manager, mem)
                continue

            # ─── 拦截所有以 / 开头的未知命令，不进入 Agent 链 ───
            if user_input.startswith("/"):
                # 尝试模糊匹配给出建议
                known_commands = ["/quit", "/exit", "/q", "/help", "/h", "/compact",
                                  "/verbose", "/memory", "/image", "/voice", "/record",
                                  "/model", "/agent", "/channel", "/interface", "/session"]
                cmd_lower = user_input.lower()
                suggestions = [c for c in known_commands if c.startswith(cmd_lower[:3])]
                if suggestions:
                    print(f"  \033[33m未知命令 '{user_input}'，你是想输入 {' 或 '.join(suggestions[:3])} 吗？\033[0m")
                else:
                    print(f"  \033[33m未知命令 '{user_input}'，输入 /help 查看可用命令。\033[0m")
                print()
                continue

            # ─── 本轮对话 ────────────────────────────
            session_id = session_manager.get_current_session_id() or uuid.uuid4().hex[:12]

            # 注入 RAW_INPUT 到持久系统
            raw_msg = Message(
                msg_type=MessageType.RAW_INPUT,
                source="main",
                target="receiver",
                payload={
                    "text": user_input,
                    "image": image_path,
                    "voice": voice_path,
                },
                session_id=session_id,
            )
            await bus.publish(raw_msg)

            print(f"\033[36m[Main]\033[0m 用户输入已注入: {user_input}")

            # 等待 CONVERSATION_DONE
            await bus.subscribe("main")
            main_timeout = 180.0
            final_response: Optional[str] = None

            while main_timeout > 0:
                msg = await bus.receive("main", timeout=1.0)
                main_timeout -= 1.0

                if msg is None:
                    continue

                if msg.msg_type == MessageType.CONVERSATION_DONE:
                    final_response = msg.payload.get("message", "")
                    break

                if msg.msg_type == MessageType.TASK_ERROR:
                    print(f"\033[31m[Main] 检测到 TASK_ERROR: {msg.payload.get('error', '')}\033[0m")

            # 清理本次订阅，准备下一轮对话
            await bus.unsubscribe("main")

            if final_response is None:
                print(f"\n\033[31m[Main] 超时 (180s)，未收到回复\033[0m")
            else:
                print()
                print(f"\033[1m{'='*50}\033[0m")
                print(f"\033[1m[完成] 对话结束\033[0m")

    except Exception as e:
        logger.error("[Main] Agent 链路异常: {}", e)
        print(f"\033[31m[Main] 错误: {e}\033[0m")

    finally:
        # ─── 清理 — 退出时执行一次 ─────────────────
        print("\n\033[90m正在关闭 Agent 系统...\033[0m")
        for agent in agent_list:
            await agent.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.stop()
        print("\033[90m已关闭。\033[0m")


def _session_to_dict(s) -> dict:
    """SessionInfo 转 dict（API 响应用）"""
    return {
        "session_id": s.session_id,
        "name": s.name,
        "source": s.source,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "turn_count": s.turn_count,
        "is_active": s.is_active,
    }


async def run_server(port: int) -> None:
    """HTTP API 服务模式 — 完整 REST API + CORS"""
    from fastapi import FastAPI, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, StreamingResponse
    import uvicorn

    config = get_config()
    config.runtime.load_runtime_state()
    rt = config.runtime

    session_manager = SessionManager()
    session_manager.load_index()

    app = FastAPI(
        title="MIA — MiMo Intelligent Agent",
        version="0.1.0",
        description="基于LLM循环的多Agent系统 HTTP API",
    )

    # CORS — 允许前端跨域访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    # ─── 会话管理 ────────────────────────────────────

    @app.get("/api/sessions")
    async def list_sessions():
        sessions = session_manager.list_sessions()
        return {
            "sessions": [
                {
                    "session_id": s.session_id,
                    "name": s.name,
                    "source": s.source,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                    "turn_count": s.turn_count,
                    "is_active": s.is_active,
                }
                for s in sessions
            ],
            "current_id": session_manager.get_current_session_id(),
        }

    @app.post("/api/sessions")
    async def create_session(data: dict):
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse(status_code=400, content={"error": "name 不能为空"})
        if ":" in name:
            return JSONResponse(status_code=400, content={"error": "名称不能包含冒号"})
        s = session_manager.create_session(name, source="cli")
        return _session_to_dict(s)

    @app.put("/api/sessions/{session_id}")
    async def rename_session(session_id: str, data: dict):
        name = data.get("name", "").strip()
        if not name:
            return JSONResponse(status_code=400, content={"error": "name 不能为空"})
        ok = session_manager.rename_session(session_id, name)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str):
        ok = session_manager.delete_session(session_id)
        if not ok:
            return JSONResponse(status_code=400, content={"error": "删除失败"})
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/activate")
    async def activate_session(session_id: str):
        s = session_manager.get_session(session_id)
        if not s:
            return JSONResponse(status_code=404, content={"error": "会话不存在"})
        session_manager.set_current(session_id)
        return _session_to_dict(s)

    @app.get("/api/sessions/current")
    async def current_session():
        s = session_manager.get_current()
        if not s:
            return JSONResponse(status_code=404, content={"error": "无活跃会话"})
        return _session_to_dict(s)

    @app.get("/api/sessions/{session_id}/history")
    async def session_history(session_id: str):
        state = session_manager.load_state(session_id)
        if not state:
            return {"session_id": session_id, "messages": []}
        # 将 conversation_history 转为前端 ChatMessage 格式
        messages = []
        for turn in state.conversation_history:
            user_text = turn.get("user", "")
            assistant_text = turn.get("assistant", "")
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})
        return {"session_id": session_id, "messages": messages}

    # ─── 渠道管理 ────────────────────────────────────

    @app.get("/api/channels")
    async def channel_status():
        return {
            "wechat": {
                "enabled": rt.wechat_enabled,
                "has_token": bool(config.wechat.bot_token),
            },
            "telegram": {
                "enabled": rt.telegram_enabled,
                "has_token": bool(config.telegram.bot_token),
            },
        }

    @app.put("/api/channels/{name}")
    async def toggle_channel(name: str, data: dict):
        enabled = data.get("enabled", False)
        if name == "wechat":
            rt.wechat_enabled = enabled
        elif name == "telegram":
            rt.telegram_enabled = enabled
        else:
            return JSONResponse(status_code=400, content={"error": f"未知渠道: {name}"})
        rt.save_runtime_state()
        return {"ok": True, "name": name, "enabled": enabled}

    # ─── 运行时配置 ──────────────────────────────────

    @app.get("/api/config")
    async def get_config_summary():
        return {
            "scheduler": {"model": rt.scheduler_model, "fallback": rt.scheduler_fallback},
            "task": {"model": rt.task_model, "fallback": rt.task_fallback},
            "memory": {"model": rt.memory_model, "fallback": rt.memory_fallback},
            "receiver": {
                "text_model": rt.receiver_text_model,
                "vision_enabled": rt.receiver_vision_enabled,
                "audio_enabled": rt.receiver_audio_enabled,
            },
            "sender": {"tts_enabled": rt.sender_tts_enabled, "tts_model": rt.sender_tts_model},
            "streaming": config.agent.enable_streaming,
        }

    # ─── 记忆管理 ────────────────────────────────────

    @app.get("/api/memory")
    async def browse_memory(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        store = MemoryStore()
        store.load()
        all_entries = store.get_all()
        total = len(all_entries)
        start = (page - 1) * page_size
        end = start + page_size
        entries = all_entries[start:end]
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "entries": [e.to_dict() for e in entries],
        }

    @app.post("/api/compact")
    async def compact_memory():
        store = MemoryStore()
        store.load()
        entries = store.get_all()
        before = len(entries)
        if before == 0:
            return {"ok": True, "before": 0, "after": 0, "summary": "无知识条目"}
        # 简单的降级 compact — 保留最新 50 条
        if before > 50:
            keep = sorted(entries, key=lambda e: e.updated_at or e.created_at, reverse=True)[:50]
            store.compact("最近对话摘要", source_session_ids=None)
            store.load()
            after = len(store.get_all())
        else:
            after = before
        return {"ok": True, "before": before, "after": after}

    # ─── 接口绑定 ────────────────────────────────────

    @app.get("/api/interface/{name}")
    async def interface_status(name: str):
        if name == "wechat":
            token_file = Path.home() / ".mia" / "wechat_bot_token"
            has_token = token_file.exists() or bool(config.wechat.bot_token)
            return {"name": "wechat", "has_token": has_token, "enabled": rt.wechat_enabled}
        elif name == "telegram":
            token_file = Path.home() / ".mia" / "telegram_bot_token"
            has_token = token_file.exists() or bool(config.telegram.bot_token)
            return {"name": "telegram", "has_token": has_token, "enabled": rt.telegram_enabled}
        return JSONResponse(status_code=404, content={"error": f"未知接口: {name}"})

    # ─── 对话 ────────────────────────────────────────

    @app.post("/api/chat")
    async def chat(request: dict):
        query = request.get("query", "")
        image = request.get("image")
        voice = request.get("voice")

        if not query:
            return JSONResponse(status_code=400, content={"error": "query 不能为空"})

        result = await run_agent_pipeline(query=query, image_path=image, voice_path=voice)

        if result is None:
            return JSONResponse(status_code=500, content={"error": "处理超时"})
        return {"response": result}

    @app.post("/api/chat/stream")
    async def chat_stream(request: dict):
        query = request.get("query", "")
        if not query:
            return JSONResponse(status_code=400, content={"error": "query 不能为空"})

        async def generate():
            result = await run_agent_pipeline(
                query=query,
                image_path=request.get("image"),
                voice_path=request.get("voice"),
                timeout=180.0,
            )
            if result:
                yield f"data: {json.dumps({'text': result, 'done': True})}\n\n"
            else:
                yield f"data: {json.dumps({'error': '处理超时', 'done': True})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    print(f"  MIA HTTP API: http://127.0.0.1:{port}")
    print(f"  API 文档:     http://127.0.0.1:{port}/docs")
    if rt.wechat_enabled:
        print(f"  WeChat:       已启用")
    if rt.telegram_enabled:
        print(f"  Telegram:     已启用")
    config_uv = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config_uv)
    await server.serve()


def main():
    """主入口"""
    args = parse_args()

    if args.server:
        asyncio.run(run_server(args.port))
    elif args.query:
        asyncio.run(run_cli_query(
            args.query, args.image, args.voice,
        ))
    else:
        asyncio.run(run_cli_interactive())


if __name__ == "__main__":
    main()
