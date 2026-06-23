"""
SchedulerAgent — LLM 循环决策中心

整个系统的核心 Agent。持续运行 LLM 循环:
  1. 接收 USER_INTENT / TASK_RESULT / TASK_ERROR
  2. 调用 LLM 分析 → 生成 JSON 决策
  3. 决策 reply → 发送 SEND_TEXT/SEND_VOICE
  4. 决策 execute_task → 发送 EXECUTE_TASK → 等待结果
  5. 收到结果 → 回到步骤 2
  6. 决策 done / 达到上限 → 循环结束

安全保护:
  - MAX_ITERATIONS: 最多循环轮数
  - TASK_TIMEOUT: 单个任务超时
  - MAX_CONSECUTIVE_TASKS: 连续派发上限
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from mia.agents.base import BaseAgent
from mia.bus.bus import MessageBus
from mia.bus.message import (
    Message,
    MessageType,
    make_execute_task,
    make_send_text,
    make_send_voice,
    make_stream_start,
    make_stream_chunk,
    make_stream_end,
)
from mia.providers.base import BaseProvider


# ─── Prompt 加载 — 从 prompts/ 目录读取，支持用户自定义 ──

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    """从 prompts/ 目录加载提示词文件

    Args:
        filename: 文件名 (如 'scheduler.md')

    Returns:
        文件内容，文件不存在时返回空字符串
    """
    path = _PROMPTS_DIR / filename
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning("[Scheduler] 加载 prompt 文件失败 {}: {}", filename, e)
    return ""


def _load_agent_identity() -> str:
    """加载 AGENTS.md 作为 MIA 身份定义

    AGENTS.md 位于项目根目录，定义 MIA 的名字、性格、行为准则。
    所有 system prompt 都会注入此身份，确保 Scheduler 和 Reply
    生成器都清楚自己是 MIA，而不是底层组件名。
    """
    identity_path = Path(__file__).parent.parent.parent.parent / "AGENTS.md"
    try:
        if identity_path.exists():
            content = identity_path.read_text(encoding="utf-8").strip()
            logger.info("[Scheduler] 已加载 AGENTS.md ({:.0f} 字符)", len(content))
            return content
    except Exception as e:
        logger.warning("[Scheduler] AGENTS.md 加载失败: {}，使用默认身份", e)

    return """# MIA
你是 MIA（Modular Intelligent Agent），一个基于 LLM 决策循环的 AI 助手。
名字: MIA。口气: 温暖干练，像靠谱的朋友。语言: 中英混用自然。"""


def _get_scheduler_system_prompt() -> str:
    """获取 Scheduler 决策用的 system prompt — AGENTS.md 身份 + 决策指令

    身份在前，决策指令在后。这样 Scheduler 在做决策时知道自己是 MIA，
    语音回复的 message 文本会自然使用 MIA 的身份和语气。
    """
    identity = _load_agent_identity()
    instructions = _load_prompt("scheduler.md")
    if not instructions:
        # Fallback: 硬编码的默认决策指令 (prompts/scheduler.md 不存在时)
        instructions = (
            "你是一个智能调度员。你的职责是分析用户意图并做出决策。\n\n"
            "## 决策格式\n"
            "严格返回 JSON: {\"reasoning\":\"...\",\"action\":\"reply\"|\"execute_task\"|\"done\",\"action_detail\":{}}\n"
            "action=reply 时: action_detail 包含 message(仅voice时必填) 和 use_voice(bool)\n"
            "action=execute_task 时: action_detail 包含 task 和 tools_hint\n"
            "直接回复时 use_voice=false 且无需 message(系统会流式生成)\n"
        )
    return identity + "\n\n---\n\n" + instructions


# ─── Reply System Prompt — AGENTS.md + prompts/reply.md ─────

def _get_reply_system_prompt() -> str:
    """获取回复生成用的 system prompt — AGENTS.md 身份 + 回复指令"""
    identity = _load_agent_identity()
    instructions = _load_prompt("reply.md")
    if not instructions:
        instructions = (
            "## 当前任务\n根据对话上下文生成自然、有帮助的回复。\n\n"
            "## 要求\n- 简洁明了，控制在 300 字以内\n"
            "- 直接输出回复文本，不要加任何前缀、标签或格式标记\n"
            "- 不要输出 JSON、代码块或其他结构化格式\n"
        )
    return identity + "\n\n---\n\n" + instructions


# ─── SchedulerAgent ───────────────────────────────────────

class SchedulerAgent(BaseAgent):
    """调度 Agent — LLM 循环决策中心

    每个对话会话创建一个独立实例，
    该实例持续运行直到做出 reply/done 决策或达到限制。
    """

    # 安全保护常量
    MAX_ITERATIONS = 10
    TASK_TIMEOUT = 60              # 单任务超时 (秒)
    MAX_CONSECUTIVE_TASKS = 3      # 连续派发任务上限

    def __init__(
        self,
        bus: MessageBus,
        provider: BaseProvider,
        model: Optional[str] = None,
        fallback_provider: Optional[BaseProvider] = None,
        fallback_model: Optional[str] = None,
        enable_streaming: bool = True,
    ):
        """
        Args:
            bus: 消息总线
            provider: LLM Provider (主)
            model: 模型名 (None 则用 Provider 默认)
            fallback_provider: 备选 Provider (主 Provider 失败时使用)
            fallback_model: 备选模型名
            enable_streaming: 是否启用流式输出 (False 时降级为非流式)
        """
        super().__init__(name="scheduler", bus=bus)
        self.provider = provider
        self.model = model
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model
        self.enable_streaming = enable_streaming

        # 当前会话状态 (每轮对话重置)
        self._session_id: Optional[str] = None
        self._iteration: int = 0
        self._consecutive_tasks: int = 0
        self._task_history: list[str] = []  # 已执行的任务描述
        self._decision_history: list[dict] = []  # 当前会话的历史决策

    # ─── 生命周期 ────────────────────────────────────────

    async def on_start(self) -> None:
        """初始化 Scheduler"""
        logger.info("[Scheduler] LLM Loop 就绪, provider={}", self.provider.__class__.__name__)

    async def handle(self, msg: Message) -> None:
        """
        处理消息 — Scheduler 的 LLM 循环入口

        接收 USER_INTENT / TASK_RESULT / TASK_ERROR，
        开始新一轮 LLM 决策循环。
        """
        if msg.msg_type == MessageType.USER_INTENT:
            await self._process_user_intent(msg)
        elif msg.msg_type in (MessageType.TASK_RESULT, MessageType.TASK_ERROR):
            await self._process_task_response(msg)
        else:
            logger.debug("[Scheduler] 忽略消息类型: {}", msg.msg_type)

    # ─── 核心循环 ────────────────────────────────────────

    async def _process_user_intent(self, msg: Message) -> None:
        """处理用户意图 — 开始新一轮对话循环"""
        self._session_id = msg.session_id
        self._iteration = 0
        self._consecutive_tasks = 0
        self._task_history.clear()
        self._decision_history.clear()

        logger.info("[Scheduler] 收到用户意图: {}", msg.payload.get("intent", ""))

        # 打印思考前缀
        self._print_thought("分析用户意图", msg.payload.get("intent", ""))

        # 进入 LLM 循环
        await self._run_loop(msg)

    async def _process_task_response(self, msg: Message) -> None:
        """处理任务返回结果"""
        is_error = msg.msg_type == MessageType.TASK_ERROR

        if is_error:
            logger.warning("[Scheduler] 收到任务错误: {}", msg.payload.get("error", ""))
            self._print_thought("收到任务错误", msg.payload.get("error", ""))
        else:
            result = msg.payload.get("result", "")
            logger.info("[Scheduler] 收到任务结果: {}", result)
            self._print_thought("收到任务结果", result)

        # 继续 LLM 循环
        self._iteration += 1
        await self._run_loop(msg)

    async def _run_loop(self, trigger_msg: Message) -> None:
        """
        LLM 决策循环

        不断调用 LLM 分析当前状态 → 执行决策 → 等待结果，
        直到做出 reply/done 决策或达到限制。
        """

        # ─── 安全保护检查 ──────────────────────────────
        if self._iteration >= self.MAX_ITERATIONS:
            logger.warning("[Scheduler] 达到最大迭代次数 {}, 强制回复", self.MAX_ITERATIONS)
            await self._force_reply("已达到最大处理轮数，我先给你当前的结果。")
            return

        if self._consecutive_tasks >= self.MAX_CONSECUTIVE_TASKS:
            logger.warning("[Scheduler] 连续派发任务 {} 次，强制回复", self._consecutive_tasks)
            await self._force_reply("已经连续执行了多个任务，我先汇总一下结果。")
            return

        # ─── 构建 LLM Context ──────────────────────────
        messages = self._build_context(trigger_msg)

        # ─── 调用 LLM 决策 (主 Provider + 备选 fallback) ──
        response = None
        last_error = None

        # 尝试主 Provider
        try:
            response = await self.provider.chat_sync(
                messages=messages,
                model=self.model,
                max_tokens=4096,  # 长回复+reasoning 需要更多 token 避免 JSON 截断
                temperature=0.3,
            )
        except Exception as e:
            last_error = e
            logger.warning("[Scheduler] 主 Provider 失败: {}. 尝试备选...", e)

        # 主 Provider 失败，尝试备选
        if response is None and self.fallback_provider:
            try:
                logger.info("[Scheduler] 使用备选 Provider: {}", self.fallback_provider.__class__.__name__)
                response = await self.fallback_provider.chat_sync(
                    messages=messages,
                    model=self.fallback_model,
                    max_tokens=4096,  # 长回复+reasoning 需要更多 token 避免 JSON 截断
                    temperature=0.3,
                )
            except Exception as e:
                last_error = e
                logger.error("[Scheduler] 备选 Provider 也失败: {}", e)

        if response is None:
            await self._force_reply(f"抱歉，系统处理遇到问题：{last_error}")
            return

        # ─── 解析决策 JSON ─────────────────────────────
        decision = self._parse_decision(response)
        if not decision:
            # JSON 解析失败，重试一次（主 Provider + fallback）
            logger.warning("[Scheduler] JSON 解析失败，重试...")
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": (
                    "你上次的回复不是有效的 JSON（可能被截断或格式错误）。"
                    "请重新生成一个完整的 JSON 决策。"
                    "确保 message 字段简短（100字以内），"
                    "不要在文本中使用未转义的双引号。"
                    "只输出 JSON，不要有其他文字。"
                ),
            })
            retry_response = None
            try:
                retry_response = await self.provider.chat_sync(
                    messages=messages,
                    model=self.model,
                    max_tokens=4096,
                    temperature=0.1,
                )
            except Exception as e:
                logger.warning("[Scheduler] 主 Provider 重试失败: {}", e)

            # 主 Provider 重试失败，尝试备选
            if retry_response is None and self.fallback_provider:
                try:
                    logger.info("[Scheduler] 重试使用备选 Provider")
                    retry_response = await self.fallback_provider.chat_sync(
                        messages=messages,
                        model=self.fallback_model,
                        max_tokens=4096,
                        temperature=0.1,
                    )
                except Exception as e:
                    logger.error("[Scheduler] 备选 Provider 重试也失败: {}", e)

            if retry_response:
                decision = self._parse_decision(retry_response)

        if not decision:
            await self._force_reply("抱歉，我暂时无法做出决策，请重新描述你的需求。")
            return

        # ─── 执行决策 ─────────────────────────────────
        self._decision_history.append(decision)
        await self._execute_decision(decision, trigger_msg)

    def _build_context(self, trigger_msg: Message) -> list[dict]:
        """构建 LLM 上下文消息列表 (包含由 MemoryAgent 注入的记忆上下文)"""
        messages = [
            {"role": "system", "content": _get_scheduler_system_prompt()},
        ]

        # ─── 注入 MemoryAgent 提供的记忆上下文 ──────────
        # MemoryAgent 已经在 USER_INTENT payload 中注入了 memory_context 字段
        # 这是经过检索+总结的精炼记忆，直接注入到 LLM context 最前面
        memory_context = trigger_msg.payload.get("memory_context", "")
        if memory_context:
            messages.append({
                "role": "user",
                "content": f"## 跨对话记忆上下文 (来自 MemoryAgent)\n{memory_context}",
            })
            messages.append({
                "role": "assistant",
                "content": "已了解之前的对话历史，我会基于这些上下文理解用户的指代和新问题。",
            })

        # 添加历史决策摘要
        if self._decision_history:
            history_text = "## 之前的决策历史\n"
            for i, d in enumerate(self._decision_history[-3:]):  # 只保留最近3条
                history_text += f"第{i+1}轮: {json.dumps(d, ensure_ascii=False)}\n"
            messages.append({"role": "user", "content": history_text})
            messages.append({
                "role": "assistant",
                "content": f"已了解。我收到了{len(self._decision_history)}轮历史。请给我最新的消息。",
            })

        # 添加当前消息
        msg_type_name = trigger_msg.msg_type.value
        payload_str = json.dumps(trigger_msg.payload, ensure_ascii=False, indent=2)
        messages.append({
            "role": "user",
            "content": f"[{msg_type_name}] 当前消息:\n{payload_str}\n\n请做出决策（只返回 JSON）。",
        })

        return messages

    async def _execute_decision(
        self,
        decision: dict,
        trigger_msg: Message,
    ) -> None:
        """
        执行 LLM 决策

        Args:
            decision: 解析后的决策 dict
            trigger_msg: 触发的原始消息
        """
        action = decision.get("action", "reply")
        reasoning = decision.get("reasoning", "")
        detail = decision.get("action_detail", {})

        logger.info("[Scheduler] 决策: action={}, reasoning={}", action, reasoning)

        if action == "reply":
            # 发送回复
            self._consecutive_tasks = 0  # 重置连续任务计数
            message = detail.get("message", "")
            use_voice = detail.get("use_voice", False)

            if use_voice:
                # 语音回复：需要完整文本 → 非流式路径
                # 如果 decision JSON 中没有 message (不太可能但做 fallback)
                if not message:
                    message = await self._generate_fallback_reply(trigger_msg)
                self._print_thought("决策: 语音回复用户", reasoning)
                voice = detail.get("voice", "冰糖")
                target = self._resolve_output_target()
                meta = self._channel_meta(trigger_msg)
                await self.bus.publish(make_send_voice(
                    message=message,
                    voice=voice,
                    session_id=self._session_id,
                    target=target,
                    **meta,
                ))
            elif self.enable_streaming:
                # 文字回复：流式输出！
                self._print_thought("决策: 流式回复用户", reasoning)
                await self._stream_reply(trigger_msg)
            else:
                # 文字回复：流式关闭 → 非流式 fallback
                if not message:
                    message = await self._generate_fallback_reply(trigger_msg)
                self._print_thought("决策: 回复用户", reasoning)
                target = self._resolve_output_target()
                meta = self._channel_meta(trigger_msg)
                await self.bus.publish(make_send_text(
                    message=message,
                    session_id=self._session_id,
                    target=target,
                    **meta,
                ))
            logger.info("[Scheduler] 对话完成, action=reply")

        elif action == "execute_task":
            # 派发任务
            task = detail.get("task", "")
            tools_hint = detail.get("tools_hint", [])

            # 检查重复任务
            if task in self._task_history:
                logger.warning("[Scheduler] 检测到重复任务: {}", task)
                self._print_thought("检测到重复任务，跳过", f"任务: {task}\n理由: {reasoning}")
                # 不真正执行，而是模拟一个结果继续循环
                fake_result = Message(
                    msg_type=MessageType.TASK_RESULT,
                    source="scheduler",
                    target="scheduler",
                    payload={
                        "task_id": "duplicate",
                        "result": "任务与之前重复，已跳过。请基于已有结果做出决策。",
                    },
                    session_id=self._session_id,
                )
                await self._process_task_response(fake_result)
                return

            self._task_history.append(task)
            self._consecutive_tasks += 1

            self._print_thought(
                f"决策: 执行任务 (第{self._consecutive_tasks}次)",
                f"理由: {reasoning}\n任务: {task}",
            )

            task_msg = make_execute_task(
                task=task,
                tools_hint=tools_hint,
                parent_id=trigger_msg.msg_id,
                session_id=self._session_id,
            )
            await self.send(task_msg)
            # 不继续循环 — 等待 TASK_RESULT 通过 handle() 触发下一轮

        elif action == "done":
            # 标记完成
            logger.info("[Scheduler] 对话完成, action=done")
            self._print_thought("任务完成", reasoning)

        else:
            logger.warning("[Scheduler] 未知 action: {}, 降级为 reply", action)
            await self._force_reply("处理完成。")

    # ─── 流式回复 ──────────────────────────────────────

    async def _stream_reply(self, trigger_msg: Message) -> None:
        """流式生成回复 — 逐 token 推送给 SenderAgent

        调用 Provider.chat_stream() 获取文本 token 流，
        通过 MessageBus 的 STREAM_START/CHUNK/END 消息
        实时推送给 SenderAgent 进行逐字输出。

        包含完整的 fallback 链：主 Provider → 备选 Provider → 错误降级。
        """
        # 1. 构建流式回复的 LLM 上下文
        reply_messages = self._build_reply_context(trigger_msg)
        meta = self._channel_meta(trigger_msg)

        # 2. 通知输出目标准备接收流式文本
        target = self._resolve_output_target()
        await self.bus.publish(make_stream_start(
            session_id=self._session_id,
            target=target,
            **meta,
        ))

        # 3. 流式生成 — 主 Provider + 备选 fallback
        full_text = ""
        stream_error = None

        try:
            # 尝试主 Provider
            async for chunk in self.provider.chat_stream(
                messages=reply_messages,
                model=self.model,
                max_tokens=2048,
                temperature=0.7,
            ):
                full_text += chunk
                target = self._resolve_output_target()
                await self.bus.publish(make_stream_chunk(
                    delta=chunk,
                    session_id=self._session_id,
                    target=target,
                    **meta,
                ))
        except Exception as e:
            stream_error = e
            logger.warning(
                "[Scheduler] 主 Provider 流式失败: {}，尝试 fallback", e,
            )

        # 主 Provider 失败 → 尝试备选
        if stream_error and self.fallback_provider:
            full_text = ""  # 重置，重新生成
            try:
                logger.info(
                    "[Scheduler] 流式使用备选 Provider: {}",
                    self.fallback_provider.__class__.__name__,
                )
                async for chunk in self.fallback_provider.chat_stream(
                    messages=reply_messages,
                    model=self.fallback_model,
                    max_tokens=2048,
                    temperature=0.7,
                ):
                    full_text += chunk
                    target = self._resolve_output_target()
                    await self.bus.publish(make_stream_chunk(
                        delta=chunk,
                        session_id=self._session_id,
                        target=target,
                        **meta,
                    ))
                stream_error = None  # fallback 成功
            except Exception as e2:
                stream_error = e2
                logger.error("[Scheduler] 备选 Provider 流式也失败: {}", e2)

        # 两个 Provider 都失败 → 最终降级
        if stream_error and not full_text:
            full_text = f"抱歉，系统处理遇到问题：{stream_error}"
            target = self._resolve_output_target()
            await self.bus.publish(make_stream_chunk(
                delta=full_text,
                session_id=self._session_id,
                target=target,
                **meta,
            ))

        # 4. 通知输出目标流结束 (携带完整文本供 MemoryAgent 存储)
        target = self._resolve_output_target()
        print(f"\033[36m[Scheduler]\033[0m 流式完成: target={target} len={len(full_text)} chat_id={meta.get('chat_id','N/A')}")
        await self.bus.publish(make_stream_end(
            full_message=full_text,
            session_id=self._session_id,
            target=target,
            **meta,
        ))
        logger.info("[Scheduler] 流式回复完成, len={}", len(full_text))

    def _build_reply_context(self, trigger_msg: Message) -> list[dict]:
        """构建流式回复的 LLM 上下文消息列表

        包含:
          - _get_reply_system_prompt() (AGENTS.md 身份 + 回复指令)
          - 跨对话记忆上下文 (来自 MemoryAgent)
          - 决策历史 (最近 3 轮)
          - 当前触发消息
        """
        messages = [
            {"role": "system", "content": _get_reply_system_prompt()},
        ]

        # 注入 MemoryAgent 提供的记忆上下文
        memory_context = trigger_msg.payload.get("memory_context", "")
        if memory_context:
            messages.append({
                "role": "user",
                "content": f"## 跨对话记忆上下文 (来自 MemoryAgent)\n{memory_context}",
            })
            messages.append({
                "role": "assistant",
                "content": "已了解之前的对话历史和用户偏好，我会基于这些上下文生成回复。",
            })

        # 注入决策历史 (最近 3 轮，精简版)
        if self._decision_history:
            history_parts = ["## 之前的决策历史"]
            for i, d in enumerate(self._decision_history[-3:]):
                action = d.get("action", "?")
                reasoning = d.get("reasoning", "")[:150]
                history_parts.append(
                    f"第{i+1}轮: action={action}, reasoning={reasoning}"
                )
            messages.append({
                "role": "user",
                "content": "\n".join(history_parts),
            })
            messages.append({
                "role": "assistant",
                "content": "已了解。请给我最新的消息，我来生成回复。",
            })

        # 当前触发消息
        import json as _json
        payload_str = _json.dumps(
            trigger_msg.payload, ensure_ascii=False, indent=2,
        )
        messages.append({
            "role": "user",
            "content": (
                f"当前消息:\n{payload_str}\n\n"
                f"请生成回复（直接输出文本，不要 JSON 或其他格式）："
            ),
        })

        return messages

    async def _generate_fallback_reply(
        self, trigger_msg: Message,
    ) -> str:
        """非流式生成回复 — 用于流式关闭或语音需要完整文本时的降级

        直接调用 chat_sync 拿到完整文本，不经过流式管道。
        """
        reply_messages = self._build_reply_context(trigger_msg)
        try:
            response = await self.provider.chat_sync(
                messages=reply_messages,
                model=self.model,
                max_tokens=2048,
                temperature=0.7,
            )
            return response
        except Exception as e:
            logger.warning("[Scheduler] fallback 回复生成失败: {}", e)
            if self.fallback_provider:
                try:
                    return await self.fallback_provider.chat_sync(
                        messages=reply_messages,
                        model=self.fallback_model,
                        max_tokens=2048,
                        temperature=0.7,
                    )
                except Exception as e2:
                    logger.error("[Scheduler] fallback 备选也失败: {}", e2)
            return f"抱歉，系统处理遇到问题：{e}"

    # ─── 辅助方法 ──────────────────────────────────────

    def _channel_meta(self, trigger_msg: Message) -> dict:
        """从 trigger_msg.payload 提取渠道元数据（透传给 Sender）

        这些字段由渠道 ReceiverAgent 注入 RAW_INPUT → ReceiverAgent 透传 → 到达此处。
        WeChat: context_token + to_user_id
        Telegram: chat_id
        """
        payload = trigger_msg.payload if trigger_msg else {}
        meta: dict = {}
        for key in ("context_token", "to_user_id", "chat_id"):
            val = payload.get(key, "")
            if val:
                meta[key] = val
        return meta

    def _resolve_output_target(self) -> str:
        """根据 session_id 前缀确定回复目标 Agent

        session_id 编码了消息来源渠道:
          - "wechat:<user_id>" → 回复到微信 (WeChatAgent)
          - "<uuid_hex>" (无前缀) → 回复到终端 (SenderAgent)

        未来扩展: "telegram:*" → "telegram", "discord:*" → "discord"

        Returns:
            目标 Agent 名称
        """
        sid = self._session_id or ""
        if ":" in sid:
            channel = sid.split(":")[0]
            # 已知渠道白名单 — 防止伪造
            if channel == "wechat":
                return "wechat_sender"  # 微信收发分离: receiver 收, sender 发
            if channel == "telegram":
                return "telegram_sender"  # Telegram 收发分离
        # 默认: 无前缀 uuid = CLI/API → SenderAgent
        return "sender"

    def _parse_decision(self, text: str) -> Optional[dict]:
        """
        从 LLM 输出中解析 JSON 决策

        处理 LLM 可能输出的各种格式:
          - 纯 JSON
          - ```json ... ``` 代码块
          - 前后有其他文字
        """
        text = text.strip()

        # 尝试提取 ```json ... ``` 代码块
        code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if code_block:
            text = code_block.group(1).strip()

        # 尝试找到 JSON 对象
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("[Scheduler] JSON 解析失败: {}\n原始文本: {}", e, text)
            return None

    async def _force_reply(self, message: str) -> None:
        """强制回复 — 当循环无法正常完成时使用"""
        logger.info("[Scheduler] 强制回复: {}", message)
        target = self._resolve_output_target()
        await self.bus.publish(make_send_text(
            message=message,
            session_id=self._session_id,
            target=target,
            ))

    # ─── 跨对话记忆已迁至 MemoryAgent ──────────────
    # conversation_memory 和 compact_memory() 现在由 MemoryAgent 管理
    # SchedulerAgent 通过 payload["memory_context"] 消费记忆上下文

    def _print_thought(self, title: str, detail: str) -> None:
        """结构化输出思考过程"""
        from mia.config import get_config
        verbose = get_config().agent.verbose
        indent = "   "
        print(f"\033[36m[Scheduler]\033[0m {title}")
        if verbose and detail:
            for line in detail.split("\n"):
                print(f"{indent}\033[90m├─\033[0m {line}")
        if verbose:
            print()
