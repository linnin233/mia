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
)
from mia.providers.base import BaseProvider


# ─── Scheduler System Prompt ─────────────────────────────

SCHEDULER_SYSTEM_PROMPT = """你是一个智能调度员(Scheduler)。你的职责是分析用户意图并做出决策。

## 你的工作方式
你会收到用户意图(USER_INTENT)或任务执行结果(TASK_RESULT)。
每次收到消息，你必须分析当前情况，返回一个 JSON 决策。

## 决策格式 (严格遵守)

```json
{
  "reasoning": "你的分析思考过程（中文，详细说明为什么做这个决定）",
  "action": "reply" | "execute_task" | "done",
  "action_detail": {}
}
```

### action = "reply" — 回复用户
```json
{
  "reasoning": "...",
  "action": "reply",
  "action_detail": {
    "message": "要发送给用户的完整回复文本",
    "use_voice": false
  }
}
```

### action = "execute_task" — 执行任务
```json
{
  "reasoning": "...",
  "action": "execute_task",
  "action_detail": {
    "task": "给 TaskAgent 的详细任务描述",
    "tools_hint": ["web_search", "shell", "file"]
  }
}
```

### action = "done" — 任务完成
```json
{
  "reasoning": "...",
  "action": "done",
  "action_detail": {}
}
```

## 决策规则
1. 收到 USER_INTENT → 分析用户意图，判断是否需要执行任务
   - 简单问答/闲聊 → 直接 reply
   - 需要搜索/计算/执行操作 → execute_task
2. 收到 TASK_RESULT → 检查结果是否满足用户需求
   - 满足 → reply 告知用户
   - 不满足 → 可以再次 execute_task（但说明哪里不够）
   - 部分满足 → reply 并说明哪些完成了哪些没有
3. 收到 TASK_ERROR → 判断是否重试
   - 可重试的错误（如网络超时）→ 重试一次
   - 不可重试的错误 → reply 告知用户失败原因
4. 不要重复执行完全相同的任务
5. 如果连续2次任务都没进展，改为 reply 告诉用户当前情况

## 回复格式要求
- message 字段中的文本应该简洁明了，控制在 200 字以内
- 不要在 message 中使用多级列表或复杂格式
- 如果信息较多，只提取最重要的部分
- 严禁在 message 中使用未转义的双引号

## 可用工具
TaskAgent 可以使用以下工具:
- web_search: 搜索互联网信息
- weather: 查询指定城市的天气信息
- shell: 执行Shell命令（代码、计算等）
- file: 读写文件

请严格返回 JSON 格式的决策，不要有任何其他文字。"""


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
    ):
        """
        Args:
            bus: 消息总线
            provider: LLM Provider (主)
            model: 模型名 (None 则用 Provider 默认)
            fallback_provider: 备选 Provider (主 Provider 失败时使用)
            fallback_model: 备选模型名
        """
        super().__init__(name="scheduler", bus=bus)
        self.provider = provider
        self.model = model
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model

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
            {"role": "system", "content": SCHEDULER_SYSTEM_PROMPT},
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

            self._print_thought(f"决策: 回复用户", message)

            if use_voice:
                voice = detail.get("voice", "冰糖")
                await self.send(make_send_voice(
                    message=message,
                    voice=voice,
                    session_id=self._session_id,
                ))
            else:
                await self.send(make_send_text(
                    message=message,
                    session_id=self._session_id,
                ))
            logger.info("[Scheduler] 对话完成, action=reply")

        elif action == "execute_task":
            # 派发任务
            task = detail.get("task", "")
            tools_hint = detail.get("tools_hint", [])

            # 检查重复任务
            if task in self._task_history:
                logger.warning("[Scheduler] 检测到重复任务: {}", task)
                self._print_thought("检测到重复任务，跳过", task)
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
                task,
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
            self._print_thought("任务完成", "")

        else:
            logger.warning("[Scheduler] 未知 action: {}, 降级为 reply", action)
            await self._force_reply("处理完成。")

    # ─── 辅助方法 ──────────────────────────────────────

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
        await self.send(make_send_text(
            message=message,
            session_id=self._session_id,
        ))

    # ─── 跨对话记忆已迁至 MemoryAgent ──────────────
    # conversation_memory 和 compact_memory() 现在由 MemoryAgent 管理
    # SchedulerAgent 通过 payload["memory_context"] 消费记忆上下文

    @staticmethod
    def _print_thought(title: str, detail: str) -> None:
        """结构化输出思考过程"""
        indent = "   "
        print(f"\033[36m[Scheduler]\033[0m {title}")
        if detail:
            for line in detail.split("\n"):
                print(f"{indent}\033[90m├─\033[0m {line}")
        print()
