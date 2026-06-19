"""
MIA 全流程测试用例

涵盖: CLI 文本/语音、记忆持久化、工具调用(天气/搜索)、流式/语音回复、
      微信 SILK 转码、AES 加解密、消息总线镜像

用法:
  python tests/test_full_pipeline.py              # 全部测试
  python tests/test_full_pipeline.py --quick      # 仅快速单元测试(不调LLM)
  python tests/test_full_pipeline.py --pipeline   # 仅 LLM 管线测试
"""

import asyncio
import sys
import uuid
from pathlib import Path

_project_root = Path(__file__).parent.parent
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

# ─── 工具函数 ──────────────────────────────────────────

results: list[dict] = []


def record(test_name: str, passed: bool, detail: str = ""):
    status = "\033[32mPASS\033[0m" if passed else "\033[31mFAIL\033[0m"
    print(f"  [{status}] {test_name}")
    if detail and not passed:
        print(f"         \033[90m{detail}\033[0m")
    results.append({"name": test_name, "passed": passed, "detail": detail})


def summary():
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    color = "\033[32m" if passed == total else "\033[31m"
    print(f"\n\033[1m  结果: {color}{passed}/{total} 通过\033[0m")
    if passed < total:
        print("\033[33m  失败用例:\033[0m")
        for r in results:
            if not r["passed"]:
                print(f"    - {r['name']}: {r['detail'][:100]}")
    return passed == total


async def run_pipeline_query(
    bus: MessageBus,
    query: str,
    image_path: str = "",
    voice_path: str = "",
    timeout: float = 120.0,
) -> str:
    """运行一次完整的 Agent 链路并返回回复文本"""
    session_id = uuid.uuid4().hex[:12]

    payload: dict = {"text": query}
    if image_path:
        payload["image"] = image_path
    if voice_path:
        payload["voice"] = voice_path

    raw_msg = Message(
        msg_type=MessageType.RAW_INPUT,
        source="test",
        target="receiver",
        payload=payload,
        session_id=session_id,
    )
    await bus.publish(raw_msg)

    await bus.subscribe("main")
    final_response = ""
    remaining = timeout

    while remaining > 0:
        msg = await bus.receive("main", timeout=1.0)
        remaining -= 1.0
        if msg is None:
            continue
        if msg.msg_type == MessageType.CONVERSATION_DONE:
            final_response = msg.payload.get("message", "")
            break
        if msg.msg_type == MessageType.TASK_ERROR:
            print(f"         \033[33m[TaskError]\033[0m {msg.payload.get('error', '')[:100]}")

    await bus.unsubscribe("main")
    return final_response


# ═══════════════════════════════════════════════════════════
# 测试 1: 微信组件单元测试 (无需 LLM)
# ═══════════════════════════════════════════════════════════

def test_wechat_components():
    """测试微信渠道的基础组件: AES 加解密、headers、SILK 检测"""
    print("\033[1;33m── 微信组件单元测试 ──\033[0m")

    # ── AES headers ──
    from mia.channels.wechat.utils import (
        make_headers, generate_aes_key_b64, aes_ecb_encrypt, aes_ecb_decrypt,
    )
    headers = make_headers("test_token")
    record("1.1 请求头包含 Authorization", headers.get("Authorization") == "Bearer test_token")
    record("1.2 请求头包含 X-WECHAT-UIN", "X-WECHAT-UIN" in headers)
    record("1.3 AuthorizationType=ilink_bot_token", headers.get("AuthorizationType") == "ilink_bot_token")

    # ── AES 加解密 ──
    key = generate_aes_key_b64()
    plaintext = b"Hello MIA test data 1234567890!!"  # 32 bytes
    ciphertext = aes_ecb_encrypt(plaintext, key)
    decrypted = aes_ecb_decrypt(ciphertext, key)
    record("1.4 AES 加密后长度 > 原文", len(ciphertext) > len(plaintext))
    record("1.5 AES 解密还原", decrypted == plaintext, f"expected={plaintext}, got={decrypted}")
    record("1.6 AES key 格式正确(24 chars base64)", len(key) == 24, f"len={len(key)}")

    # ── SILK 检测 ──
    silk_header = b"\x02#!SILK_V3\x0c\x00\xa7\x2b\x74\xf7" + b"\x00" * 100
    is_silk = b"SILK" in silk_header
    record("1.7 SILK 文件头检测", is_silk)

    # Has 0x02 prefix?
    has_prefix = silk_header[0:1] == b"\x02"
    record("1.8 WeChat 0x02 前缀检测", has_prefix)

    # ── ILinkClient 构造 ──
    from mia.channels.wechat.client import ILinkClient
    client = ILinkClient(bot_token="test", base_url="https://ilinkai.weixin.qq.com")
    record("1.9 ILinkClient 构造", client is not None)
    record("1.10 ILinkClient base_url", client.base_url == "https://ilinkai.weixin.qq.com")

    # ── Agent 导入 ──
    from mia.channels.wechat.receiver import WeChatReceiverAgent
    from mia.channels.wechat.sender import WeChatSenderAgent
    record("1.11 WeChatReceiverAgent 导入", WeChatReceiverAgent is not None)
    record("1.12 WeChatSenderAgent 导入", WeChatSenderAgent is not None)

    print()


# ═══════════════════════════════════════════════════════════
# 测试 2: 消息总线镜像 (无需 LLM)
# ═══════════════════════════════════════════════════════════

async def test_bus_mirror():
    """测试 MessageBus 镜像投递机制"""
    print("\033[1;33m── 消息总线镜像测试 ──\033[0m")

    bus = MessageBus(max_queue_size=10)
    await bus.start()

    # 注册 mirror
    bus.subscribe_mirror(MessageType.SEND_TEXT, "memory_agent")
    bus.subscribe_mirror(MessageType.STREAM_END, "memory_agent")

    # 正常订阅
    await bus.subscribe("sender")
    await bus.subscribe("memory_agent")

    # 发送一条 SEND_TEXT 到 sender — memory_agent 应该通过 mirror 也收到
    msg = Message(
        msg_type=MessageType.SEND_TEXT,
        source="scheduler",
        target="sender",
        payload={"message": "hello", "context_token": "tk123"},
        session_id="test_session",
    )
    await bus.publish(msg)

    # sender 收到
    m1 = await bus.receive("sender", timeout=1.0)
    record("2.1 sender 收到 SEND_TEXT", m1 is not None and m1.payload.get("message") == "hello")

    # memory_agent 通过 mirror 也收到
    m2 = await bus.receive("memory_agent", timeout=1.0)
    record("2.2 mirror 投递到 memory_agent", m2 is not None and m2.payload.get("message") == "hello")

    # 测试 mirror 不重复投递给 source
    msg2 = Message(
        msg_type=MessageType.SEND_TEXT,
        source="memory_agent",  # ← 和 mirror target 同名
        target="sender",
        payload={"message": "from memory"},
    )
    await bus.publish(msg2)
    m3 = await bus.receive("sender", timeout=1.0)
    # memory_agent 不应该通过 mirror 收到自己发的消息
    m4 = await bus.receive("memory_agent", timeout=1.0)
    record("2.3 mirror 不投递给 source 自己", m4 is None, f"got: {m4}")

    await bus.stop()
    print()


# ═══════════════════════════════════════════════════════════
# 测试 3: 消息 payload 透传 (无需 LLM)
# ═══════════════════════════════════════════════════════════

def test_message_passthrough():
    """测试 context_token/to_user_id 在 factory functions 中的透传"""
    print("\033[1;33m── 消息 payload 透传测试 ──\033[0m")

    from mia.bus.message import (
        make_user_intent, make_send_text, make_send_voice,
        make_stream_start, make_stream_chunk, make_stream_end,
    )

    m = make_user_intent("你好", "用户问好", context_token="tk123", to_user_id="wxid_abc")
    record("3.1 USER_INTENT context_token", m.payload.get("context_token") == "tk123")
    record("3.2 USER_INTENT to_user_id", m.payload.get("to_user_id") == "wxid_abc")

    m = make_send_text("回复", context_token="tk456", to_user_id="wxid_def")
    record("3.3 SEND_TEXT context_token", m.payload.get("context_token") == "tk456")
    record("3.4 SEND_TEXT to_user_id", m.payload.get("to_user_id") == "wxid_def")

    m = make_send_voice("语音", context_token="tk789", to_user_id="wxid_ghi")
    record("3.5 SEND_VOICE context_token", m.payload.get("context_token") == "tk789")

    m = make_stream_start(context_token="tk000")
    record("3.6 STREAM_START context_token", m.payload.get("context_token") == "tk000")

    m = make_stream_end("全文", context_token="tk111", to_user_id="wxid_jkl")
    record("3.7 STREAM_END context_token", m.payload.get("context_token") == "tk111")

    # 无 context_token 时不应出现在 payload 中
    m = make_send_text("plain")
    record("3.8 无 context_token 时不注入", "context_token" not in m.payload)

    print()


# ═══════════════════════════════════════════════════════════
# 测试 4: LLM 管线测试 (需 MIMO_API_KEY)
# ═══════════════════════════════════════════════════════════

async def test_llm_pipeline():
    """测试完整 LLM Agent 管线: 对话、记忆、工具调用"""
    print("\033[1;33m── LLM 管线测试 ──\033[0m")

    config = get_config()
    if not config.mimo.api_key:
        print("  \033[33m跳过: 未配置 MIMO_API_KEY\033[0m")
        return

    bus = MessageBus(max_queue_size=100)
    await bus.start()

    # 镜像
    for mt in [MessageType.USER_INTENT, MessageType.SEND_TEXT, MessageType.STREAM_END,
               MessageType.EXECUTE_TASK, MessageType.TASK_RESULT, MessageType.TASK_ERROR,
               MessageType.CONVERSATION_DONE]:
        bus.subscribe_mirror(mt, "memory_agent")

    mimo = MiMoProvider(api_key=config.mimo.api_key)
    deepseek = DeepSeekProvider(api_key=config.deepseek.api_key)

    agents = [
        ReceiverAgent(bus=bus, mimo=mimo),
        MemoryAgent(bus=bus, provider=mimo, model=config.mimo.chat_model,
                     fallback_provider=deepseek, fallback_model=config.deepseek.chat_model),
        SchedulerAgent(bus=bus, provider=mimo, model=config.mimo.chat_model,
                        fallback_provider=deepseek, fallback_model=config.deepseek.chat_model),
        SenderAgent(bus=bus, mimo=mimo, output_dir=config.agent.workspace_dir),
        TaskAgent(bus=bus, provider=mimo, model=config.mimo.chat_model,
                   fallback_provider=deepseek, fallback_model=config.deepseek.chat_model),
    ]
    for a in agents:
        await a.start()
    tasks = [asyncio.create_task(a.run()) for a in agents]
    await asyncio.sleep(0.3)

    try:
        # ── 4.1 基础对话 ──
        print("  \033[90m4.1 基础对话...\033[0m")
        resp = await run_pipeline_query(bus, "你好，我是linnin233，请记住我的名字")
        record("4.1 收到回复", len(resp) > 10, f"len={len(resp)}")
        record("4.2 包含用户名字", "linnin233" in resp or "linnin" in resp.lower(), resp[:100])

        # ── 4.2 跨轮记忆 ──
        print("  \033[90m4.2 跨轮记忆...\033[0m")
        resp2 = await run_pipeline_query(bus, "我叫什么名字？")
        record("4.3 回忆出用户名", "linnin233" in resp2 or "linnin" in resp2.lower(), resp2[:100])

        # ── 4.3 天气查询 ──
        print("  \033[90m4.3 天气查询...\033[0m")
        resp3 = await run_pipeline_query(bus, "查询一下北京明天的天气")
        has_weather = any(kw in resp3 for kw in ["温度", "天气", "度", "°", "风", "雨", "晴", "℃"])
        record("4.4 天气查询有回复", len(resp3) > 10, f"len={len(resp3)}")
        record("4.5 包含天气信息", has_weather, resp3[:120])

        # ── 4.4 对话历史 ──
        print("  \033[90m4.4 对话历史...\033[0m")
        resp4 = await run_pipeline_query(bus, "我刚才查过哪个城市的天气？")
        record("4.6 提及北京", "北京" in resp4, resp4[:120])

        await asyncio.sleep(2)  # 等待 MemoryAgent 处理

    finally:
        for a in agents:
            await a.stop()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.stop()

    print()


# ═══════════════════════════════════════════════════════════
# 测试 5: 微信端手动测试清单
# ═══════════════════════════════════════════════════════════

WECHAT_MANUAL_TESTS = """
\033[1;33m── 微信端手动测试清单 ──\033[0m
启动: python -m mia --wechat

\033[1m收发测试\033[0m
  \033[90m[ ]\033[0m 1.1  文本收发    微信发送 "你好"                    → 收到文字回复
  \033[90m[ ]\033[0m 1.2  流式文本    微信发送 "介绍一下你自己"           → 终端看到流式输出 + 微信收到完整文本
  \033[90m[ ]\033[0m 1.3  长文本      微信发送 "写一篇200字的文章"         → 回复分段发送，内容完整

\033[1m语音测试\033[0m
  \033[90m[ ]\033[0m 2.1  语音→文字   微信发送语音 "今天天气怎么样"         → 收到文字回复，提及天气
  \033[90m[ ]\033[0m 2.2  语音→语音   微信发送语音 "语音回复我一段话"       → 收到可播放的音频文件 + 🎤文字
  \033[90m[ ]\033[0m 2.3  语音情绪   微信发送语音(带情绪，如兴奋/沮丧)    → 回复体现对情绪的理解
  \033[90m[ ]\033[0m 2.4  SILK→WAV   发送语音后终端日志显示                 → "[WeChatAgent] SILK→WAV 转码成功"
  \033[90m[ ]\033[0m 2.5  TTS→CDN    请求语音回复后终端日志显示              → "CDN OK: rawsize=... filesize=..."
  \033[90m[ ]\033[0m 2.6  语音文件   微信端收到的音频文件                   → 可点击播放，时长正确

\033[1m工具调用\033[0m
  \033[90m[ ]\033[0m 3.1  天气查询    微信发送 "查询嘉兴明天天气"          → 返回温度/风力/降雨等信息
  \033[90m[ ]\033[0m 3.2  网络搜索    微信发送 "搜索Python最新新闻"        → 返回搜索结果(3-5条)
  \033[90m[ ]\033[0m 3.3  天气+语音   微信发送 "查询上海天气 语音回复"      → 语音文件包含天气信息

\033[1m记忆测试\033[0m
  \033[90m[ ]\033[0m 4.1  跨轮记忆   先问天气 → 再问 "刚才查过什么"          → 第二论回复提及第一轮内容
  \033[90m[ ]\033[0m 4.2  用户偏好    多轮对话后问 "记得我吗"                → 回复包含用户名 linnin233
  \033[90m[ ]\033[0m 4.3  对话历史   终端日志显示                               → "对话历史: N 轮可用, 临时记忆: M 条"
  \033[90m[ ]\033[0m 4.4  记忆落盘    Ctrl+C 退出 → 日志显示                    → "[MemoryAgent] 正在持久化记忆 (N条临时+M轮对话)"
  \033[90m[ ]\033[0m 4.5  重启恢复    退出后重启 → 微信问 "记得我吗"            → 回复包含之前对话中的用户名

\033[1m边界测试\033[0m
  \033[90m[ ]\033[0m 5.1  空消息      微信发送空消息(或纯空格)               → 不触发回复
  \033[90m[ ]\033[0m 5.2  图片消息    微信发送一张图片                       → MIA 描述图片内容
  \033[90m[ ]\033[0m 5.3  文件消息    微信发送一个文件                       → 提示 "[收到文件: xxx]"
  \033[90m[ ]\033[0m 5.4  快速连续    连续发送3条消息                          → 每条都正常回复，不丢失
  \033[90m[ ]\033[0m 5.5  渠道隔离    CLI 终端发消息                            → 微信端不收到 CLI 的回复

\033[1m架构验证\033[0m
  \033[90m[ ]\033[0m 6.1  收发分离    启动日志显示                               → WeChatReceiver + WeChatSender 各自就绪
  \033[90m[ ]\033[0m 6.2  总线镜像    MemoryAgent 日志                            → 显示通过 mirror 收到 STREAM_END 等消息
  \033[90m[ ]\033[0m 6.3  context_token  语音发送日志                             → payload 包含 context_token 透传
"""


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="仅快速单元测试")
    parser.add_argument("--pipeline", action="store_true", help="仅 LLM 管线测试")
    args = parser.parse_args()

    run_all = not args.quick and not args.pipeline

    print("\033[1m" + "=" * 60 + "\033[0m")
    print("\033[1m  MIA 全流程测试\033[0m")
    print("\033[1m" + "=" * 60 + "\033[0m")
    print()

    if run_all or args.quick:
        # ── 单元测试 (无需 LLM) ──
        test_wechat_components()
        await test_bus_mirror()
        test_message_passthrough()
        print("\033[90m  单元测试完成\033[0m")

    if run_all or args.pipeline:
        # ── LLM 管线测试 ──
        await test_llm_pipeline()
        print("\033[90m  管线测试完成\033[0m")

    if run_all:
        # ── 微信手动测试清单 ──
        print(WECHAT_MANUAL_TESTS)

    print("\033[1m" + "=" * 60 + "\033[0m")
    ok = summary()
    print("\033[1m" + "=" * 60 + "\033[0m")
    return ok


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
