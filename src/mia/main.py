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
from mia.providers.mimo import MiMoProvider
from mia.providers.deepseek import DeepSeekProvider
from mia.agents.receiver import ReceiverAgent
from mia.agents.scheduler import SchedulerAgent
from mia.agents.sender import SenderAgent
from mia.agents.task import TaskAgent
from mia.agents.memory import MemoryAgent
from mia.memory.store import MemoryStore


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
    parser.add_argument(
        "--no-tui", action="store_true",
        help="禁用 TUI 界面，使用纯文本模式",
    )
    return parser.parse_args()


def _check_tui_support() -> bool:
    """检测终端是否支持 Textual TUI

    条件:
      1. stdout 是 tty (非管道/重定向)
      2. 未设置 MIA_NO_TUI 环境变量
      3. textual 包已安装
      4. 不在 pytest 中运行
    """
    import os
    if not sys.stdout.isatty():
        return False
    if os.environ.get("MIA_NO_TUI"):
        return False
    # 检测 pytest
    if "pytest" in sys.modules:
        return False
    try:
        import textual  # noqa: F401
        return True
    except ImportError:
        return False


async def _run_tui_mode() -> None:
    """Textual TUI 模式 — 全屏终端界面"""
    from mia.tui.app import MiaTuiApp
    app = MiaTuiApp()
    await app.run_async()


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
    config = get_config()
    session_id = uuid.uuid4().hex[:12]

    # ─── 1. 创建 MessageBus ───────────────────────────
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    # ─── 2. 初始化 Provider ───────────────────────────
    mimo = MiMoProvider(api_key=config.mimo.api_key)
    deepseek = DeepSeekProvider(api_key=config.deepseek.api_key)

    # ─── 3. 创建所有 Agent ────────────────────────────
    receiver = ReceiverAgent(bus=bus, mimo=mimo)
    scheduler = SchedulerAgent(
        bus=bus,
        provider=mimo,            # 主: MiMo (已修复网关和参数问题)
        model=config.mimo.chat_model,
        fallback_provider=deepseek,  # 备选: DeepSeek
        fallback_model=config.deepseek.chat_model,
        enable_streaming=config.agent.enable_streaming,
    )
    sender = SenderAgent(
        bus=bus,
        mimo=mimo,              # Sender 用 MiMo TTS (可选)
        output_dir=config.agent.workspace_dir,
    )
    task_agent = TaskAgent(
        bus=bus,
        provider=mimo,            # TaskAgent 也用 MiMo
        model=config.mimo.chat_model,
        fallback_provider=deepseek,  # 备选: DeepSeek
        fallback_model=config.deepseek.chat_model,
    )

    # MemoryAgent — 记忆检索与存储
    memory_agent = MemoryAgent(
        bus=bus,
        provider=mimo,
        model=config.mimo.chat_model,
        fallback_provider=deepseek,
        fallback_model=config.deepseek.chat_model,
    )

    # ─── 4. 启动所有 Agent ───────────────────────────
    print(f"\033[1m{'='*50}\033[0m")
    print(f"\033[1mMIA v0.1.0 — MiMo Intelligent Agent\033[0m")
    print(f"  Session: {session_id}")
    print(f"  Scheduler: {config.mimo.chat_model} @ {config.mimo.get_base_url()}")
    print(f"  Fallback: deepseek-chat @ {config.deepseek.base_url}")
    print(f"\033[1m{'='*50}\033[0m")
    print()

    # 启动 Agent 的 start() 和 run() 循环
    await receiver.start()
    await memory_agent.start()
    await scheduler.start()
    await sender.start()
    await task_agent.start()

    # 为每个 Agent 启动消息处理循环 (后台任务)
    tasks: list[asyncio.Task] = []
    for agent in [receiver, memory_agent, scheduler, sender, task_agent]:
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
        for agent in [receiver, memory_agent, scheduler, sender, task_agent]:
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


async def run_cli_interactive(use_tui: bool = True) -> None:
    """CLI 交互模式 — 持久 Agent 系统 (启动一次，持续运行)

    Args:
        use_tui: 是否使用 Textual TUI 界面 (False 时降级为纯文本模式)
    """
    if use_tui and _check_tui_support():
        await _run_tui_mode()
        return

    # ─── 纯文本降级模式 ──────────────────────────────────
    print(f"\033[1mMIA v0.1.0 — 交互模式\033[0m")
    print(f"  输入 '/quit' 退出, '/help' 查看帮助, '/compact' 压缩对话历史")
    print(f"  直接输入问题开始对话")
    print()

    # ══════════════════════════════════════════════════════
    # 系统初始化 — 只执行一次，整个交互会话内持续运行
    # ══════════════════════════════════════════════════════

    config = get_config()
    bus = MessageBus(max_queue_size=100)
    await bus.start()

    mimo = MiMoProvider(api_key=config.mimo.api_key)
    deepseek = DeepSeekProvider(api_key=config.deepseek.api_key)

    receiver = ReceiverAgent(bus=bus, mimo=mimo)
    scheduler = SchedulerAgent(
        bus=bus,
        provider=mimo,
        model=config.mimo.chat_model,
        fallback_provider=deepseek,
        fallback_model=config.deepseek.chat_model,
        enable_streaming=config.agent.enable_streaming,
    )
    sender = SenderAgent(
        bus=bus,
        mimo=mimo,
        output_dir=config.agent.workspace_dir,
    )
    task_agent = TaskAgent(
        bus=bus,
        provider=mimo,
        model=config.mimo.chat_model,
        fallback_provider=deepseek,
        fallback_model=config.deepseek.chat_model,
    )
    memory_agent = MemoryAgent(
        bus=bus,
        provider=mimo,
        model=config.mimo.chat_model,
        fallback_provider=deepseek,
        fallback_model=config.deepseek.chat_model,
    )

    # 启动所有 Agent
    print(f"\033[1m{'='*50}\033[0m")
    print(f"\033[1mMIA v0.1.0 — MiMo Intelligent Agent (持久模式)\033[0m")
    print(f"  Scheduler: {config.mimo.chat_model} @ {config.mimo.get_base_url()}")
    print(f"  Fallback: deepseek-chat @ {config.deepseek.base_url}")
    print(f"  记忆: MemoryAgent @ {memory_agent.store.file_path}/ (index+daily)")
    print(f"\033[1m{'='*50}\033[0m")
    print()

    await receiver.start()
    await memory_agent.start()
    await scheduler.start()
    await sender.start()
    await task_agent.start()

    # 后台消息处理循环 (持久运行)
    tasks: list[asyncio.Task] = []
    for agent in [receiver, memory_agent, scheduler, sender, task_agent]:
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
  /compact          — 压缩对话历史 (将多轮对话总结为摘要，节省 token)
  /memory           — 显示当前对话记忆状态
  /image <path>     — 发送图片 (下一行输入)
  直接输入文本       — 开始对话

示例:
  You > 帮我搜索最新的 Python 新闻
  You > 嘉兴的天气怎么样
  You > /compact
  You > /image screenshot.png
  You > 分析这张截图
""")
                continue

            # /compact — 压缩对话历史
            if user_input.lower() == "/compact":
                await _handle_compact(memory_agent)
                continue

            # /memory — 交互式记忆浏览器 (临时 + 持久, 3级钻取)
            if user_input.lower() == "/memory":
                from mia.memory.browser import MemoryBrowser
                browser = MemoryBrowser(
                    memory_agent.store,
                    working_entries=memory_agent._working_memory,
                )
                await browser.browse()
                continue

            # /image — 图片输入
            image_path = None
            if user_input.lower().startswith("/image "):
                image_path = user_input[7:].strip()
                user_input = input("\033[32mYou (图片说明) > \033[0m").strip()
                if not user_input:
                    user_input = "请描述这张图片"

            # ─── 拦截所有以 / 开头的未知命令，不进入 Agent 链 ───
            if user_input.startswith("/"):
                # 尝试模糊匹配给出建议
                known_commands = ["/quit", "/exit", "/q", "/help", "/h", "/compact", "/memory", "/image"]
                cmd_lower = user_input.lower()
                suggestions = [c for c in known_commands if c.startswith(cmd_lower[:3])]
                if suggestions:
                    print(f"  \033[33m未知命令 '{user_input}'，你是想输入 {' 或 '.join(suggestions[:3])} 吗？\033[0m")
                else:
                    print(f"  \033[33m未知命令 '{user_input}'，输入 /help 查看可用命令。\033[0m")
                print()
                continue

            # ─── 本轮对话 ────────────────────────────
            session_id = uuid.uuid4().hex[:12]

            # 注入 RAW_INPUT 到持久系统
            raw_msg = Message(
                msg_type=MessageType.RAW_INPUT,
                source="main",
                target="receiver",
                payload={
                    "text": user_input,
                    "image": image_path,
                    "voice": None,
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
        for agent in [receiver, memory_agent, scheduler, sender, task_agent]:
            await agent.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.stop()
        print("\033[90m已关闭。\033[0m")


async def run_server(port: int) -> None:
    """HTTP API 服务模式"""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    import uvicorn

    app = FastAPI(
        title="MIA — MiMo Intelligent Agent",
        version="0.1.0",
        description="基于LLM循环的多Agent系统 HTTP API",
    )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/chat")
    async def chat(request: dict):
        """发送消息并获取回复"""
        query = request.get("query", "")
        image = request.get("image")
        voice = request.get("voice")

        if not query:
            return JSONResponse(
                status_code=400,
                content={"error": "query 不能为空"},
            )

        result = await run_agent_pipeline(
            query=query,
            image_path=image,
            voice_path=voice,
        )

        if result is None:
            return JSONResponse(
                status_code=500,
                content={"error": "处理超时"},
            )

        return {"response": result}

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    print(f"  MIA HTTP API 已启动: http://127.0.0.1:{port}")
    print(f"  API 文档: http://127.0.0.1:{port}/docs")
    await server.serve()


def main():
    """主入口"""
    args = parse_args()

    if args.server:
        asyncio.run(run_server(args.port))
    elif args.query:
        asyncio.run(run_cli_query(args.query, args.image, args.voice))
    else:
        asyncio.run(run_cli_interactive(use_tui=not args.no_tui))


if __name__ == "__main__":
    main()
