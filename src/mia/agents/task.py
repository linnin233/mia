"""
TaskAgent — 任务执行 Agent

职责:
  1. 接收 Scheduler 的 EXECUTE_TASK 指令
  2. 通过自己的 LLM 循环分析任务 → 决定调用工具 → 执行 → 检查结果
  3. 返回 TASK_RESULT 或 TASK_ERROR 给 Scheduler

TaskAgent 自己也是一个小的 LLM 循环，但它只关心"怎么做"，
不像 Scheduler 关心"做什么"。
"""

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
    make_task_error,
    make_task_result,
)
from mia.providers.base import BaseProvider
from mia.tools.base import Tool, ToolResult
from mia.tools.shell import ShellTool
from mia.tools.web_search import WebSearchTool
from mia.tools.file import FileTool


# ─── TaskAgent System Prompt ─────────────────────────────

TASK_AGENT_SYSTEM_PROMPT = """你是一个任务执行器(TaskAgent)。你会收到一个任务描述，
你的目标是使用可用的工具完成任务并返回结果。

## 核心原则：尽早完成！
- 你最多只能执行 5 次工具调用。如果你用完了所有次数还未 finish，任务会失败。
- 因此，一旦你获得了足够回答用户的信息，立即 finish，不要无限优化搜索词。
- 最多调用 2 次同类工具。如果前 2 次搜索没有找到好结果，用已有的信息合成答案，不要继续搜。
- 搜索工具返回的结果可能不完美，这很正常 — 从已有结果中提取有用信息即可。

## 工作方式
每次迭代，你可以选择:
1. 调用一个工具来执行操作
2. 返回最终结果 (当任务完成时)

## 输出格式
严格返回 JSON:

```json
{
  "reasoning": "当前分析和计划",
  "action": "call_tool" | "finish",
  "tool_name": "web_search",  // 仅 call_tool 时需要
  "tool_args": {},             // 仅 call_tool 时需要
  "result": "任务执行结果"     // 仅 finish 时需要
}
```

## 规则
1. 每次只调用一个工具
2. 获得搜索结果后，优先 finish 并总结发现的信息，而不是反复调整搜索词
3. 任务完成或无法继续时返回 finish
4. 如果工具调用失败，分析原因后决定重试（最多1次）还是放弃
5. 结果要简洁但完整，包含用户需要的信息
6. 用中文组织最终结果
"""


# ─── TaskAgent ───────────────────────────────────────────

class TaskAgent(BaseAgent):
    """任务执行 Agent — LLM + Tools 循环"""

    MAX_ITERATIONS = 5  # TaskAgent 内部循环上限

    def __init__(
        self,
        bus: MessageBus,
        provider: BaseProvider,
        tools: Optional[list[Tool]] = None,
        model: Optional[str] = None,
        fallback_provider: Optional[BaseProvider] = None,
        fallback_model: Optional[str] = None,
    ):
        """
        Args:
            bus: 消息总线
            provider: LLM Provider (主)
            tools: 可用工具列表 (默认注册全部工具)
            model: 模型名
            fallback_provider: 备选 Provider (主 Provider 失败时使用)
            fallback_model: 备选模型名
        """
        super().__init__(name="task_agent", bus=bus)
        self.provider = provider
        self.model = model
        self.fallback_provider = fallback_provider
        self.fallback_model = fallback_model

        # 注册工具
        self.tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.tools[tool.name] = tool
        else:
            # 默认注册全部内置工具
            default_tools = [ShellTool(), WebSearchTool(), FileTool()]
            for tool in default_tools:
                self.tools[tool.name] = tool

        logger.info("[TaskAgent] 已注册工具: {}", list(self.tools.keys()))

    # ─── 生命周期 ────────────────────────────────────────

    async def handle(self, msg: Message) -> None:
        """处理 EXECUTE_TASK 消息"""
        if msg.msg_type != MessageType.EXECUTE_TASK:
            logger.debug("[TaskAgent] 忽略消息类型: {}", msg.msg_type)
            return

        task = msg.payload.get("task", "")
        tools_hint = msg.payload.get("tools_hint", [])
        task_id = msg.msg_id

        # 结构化展示
        print(f"\033[33m[TaskAgent]\033[0m 收到任务")
        print(f"   \033[90m├─\033[0m 任务: {task[:150]}")
        if tools_hint:
            print(f"   \033[90m├─\033[0m 建议工具: {', '.join(tools_hint)}")

        logger.info("[TaskAgent] 开始执行任务: {}", task[:100])

        try:
            result_text, tool_calls = await self._execute_task(
                task=task,
                tools_hint=tools_hint,
            )

            print(f"   \033[90m└─\033[0m 完成, 工具调用: {len(tool_calls)}次")

            await self.send(make_task_result(
                task_id=task_id,
                result=result_text,
                tool_calls=tool_calls,
                session_id=msg.session_id,
            ))

        except Exception as e:
            logger.error("[TaskAgent] 任务执行异常: {}", e)
            print(f"   \033[90m└─\033[0m \033[31m失败: {e}\033[0m")

            await self.send(make_task_error(
                task_id=task_id,
                error=str(e),
                session_id=msg.session_id,
            ))

    # ─── 核心任务执行循环 ────────────────────────────────

    async def _execute_task(
        self,
        task: str,
        tools_hint: Optional[list[str]] = None,
    ) -> tuple[str, list[dict]]:
        """
        执行任务的主循环

        Returns:
            (result_text, tool_calls) — 结果文本和工具调用记录
        """
        tool_calls: list[dict] = []

        # 构建工具描述
        tools_desc = self._build_tools_description(tools_hint)

        messages = [
            {"role": "system", "content": TASK_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": f"## 可用工具\n{tools_desc}\n\n## 任务\n{task}\n\n请开始执行。只返回 JSON。"},
        ]

        for iteration in range(self.MAX_ITERATIONS):
            # 调用 LLM (主 Provider + 备选 fallback)
            response = await self._call_llm(messages)
            if response is None:
                return "任务执行中 LLM 调用失败 (主+备选均不可用)", tool_calls

            # 解析决策
            decision = self._parse_decision(response)
            if not decision:
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "请返回有效的 JSON 格式。action 必须是 call_tool 或 finish。",
                })
                continue

            action = decision.get("action", "finish")
            reasoning = decision.get("reasoning", "")

            logger.info("[TaskAgent] 迭代 {}/{}, action={}",
                        iteration + 1, self.MAX_ITERATIONS, action)

            if action == "finish":
                result = decision.get("result", reasoning)
                return result, tool_calls

            elif action == "call_tool":
                tool_name = decision.get("tool_name", "")
                tool_args = decision.get("tool_args", {})

                if tool_name not in self.tools:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": f"工具 '{tool_name}' 不存在。可用工具: {list(self.tools.keys())}",
                    })
                    continue

                # 执行工具
                print(f"   \033[90m├─\033[0m 调用工具: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:80]})")

                try:
                    tool = self.tools[tool_name]
                    result: ToolResult = await tool.execute(**tool_args)
                except Exception as e:
                    logger.error("[TaskAgent] 工具执行异常: {}", e)
                    result = ToolResult(success=False, data=None, error=str(e))

                # 记录工具调用
                tool_calls.append({
                    "tool": tool_name,
                    "args": tool_args,
                    "success": result.success,
                    "output": str(result.data)[:500] if result.data else result.error,
                })

                # 将结果反馈给 LLM
                if result.success:
                    result_text = f"工具 {tool_name} 执行成功。输出:\n{result.data}"
                else:
                    result_text = f"工具 {tool_name} 执行失败。错误:\n{result.error}"

                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": result_text})

            else:
                logger.warning("[TaskAgent] 未知 action: {}", action)
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": f"未知的 action '{action}'。请使用 call_tool 或 finish。",
                })

        # 达到最大迭代
        logger.warning("[TaskAgent] 达到最大迭代次数 {}, 强制返回", self.MAX_ITERATIONS)
        return f"任务达到最大执行轮数 ({self.MAX_ITERATIONS})，以下是已获得的工具调用结果。", tool_calls

    # ─── 辅助方法 ──────────────────────────────────────

    def _build_tools_description(self, tools_hint: Optional[list[str]] = None) -> str:
        """构建工具描述文本"""
        lines = []
        target_tools = tools_hint if tools_hint else list(self.tools.keys())

        for name in target_tools:
            if name in self.tools:
                tool = self.tools[name]
                lines.append(f"### {tool.name}")
                lines.append(f"描述: {tool.description}")
                lines.append(f"参数: {json.dumps(tool.parameters, ensure_ascii=False)}")
                lines.append("")

        return "\n".join(lines) if lines else "无可用工具"

    async def _call_llm(self, messages: list[dict]) -> Optional[str]:
        """调用 LLM，支持主 Provider + 备选 fallback

        Returns:
            LLM 响应文本，主备都失败返回 None
        """
        last_error = None

        # 尝试主 Provider
        try:
            return await self.provider.chat_sync(
                messages=messages,
                model=self.model,
                max_tokens=2048,  # 1024 不够，搜索结果较长时回复被截断
                temperature=0.3,
            )
        except Exception as e:
            last_error = e
            logger.warning("[TaskAgent] 主 Provider 失败: {}. 尝试备选...", e)

        # 尝试备选 Provider
        if self.fallback_provider:
            try:
                logger.info("[TaskAgent] 使用备选 Provider: {}", self.fallback_provider.__class__.__name__)
                return await self.fallback_provider.chat_sync(
                    messages=messages,
                    model=self.fallback_model,
                    max_tokens=1024,
                    temperature=0.3,
                )
            except Exception as e:
                last_error = e
                logger.error("[TaskAgent] 备选 Provider 也失败: {}", e)

        return None

    def _parse_decision(self, text: str) -> Optional[dict]:
        """从 LLM 输出中解析 JSON 决策"""
        text = text.strip()

        # 提取代码块
        code_block = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if code_block:
            text = code_block.group(1).strip()

        # 提取 JSON 对象
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.warning("[TaskAgent] JSON 解析失败: {}\n文本: {}", e, text[:200])
            return None
