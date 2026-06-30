"""
Sleep 工具 — 模拟长时间执行任务（用于测试异步调度）
"""

import asyncio
from mia.tools.base import Tool, ToolResult


class SleepTool(Tool):
    """长时间延迟工具 — 测试 Scheduler 异步任务调度"""

    @property
    def name(self) -> str:
        return "sleep"

    @property
    def description(self) -> str:
        return "模拟长时间执行的任务。调用后等待指定秒数再返回。用于测试后台任务进度查询。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "integer",
                    "description": "等待的秒数 (1-120)",
                },
                "message": {
                    "type": "string",
                    "description": "任务描述 (可选)",
                },
            },
            "required": ["seconds"],
        }

    async def execute(self, seconds: int = 5, message: str = "") -> ToolResult:
        seconds = max(1, min(seconds, 120))
        desc = message or f"等待 {seconds} 秒"
        print(f"  [SleepTool] 开始: {desc}")
        await asyncio.sleep(seconds)
        print(f"  [SleepTool] 完成: {desc}")
        return ToolResult(
            success=True,
            data=f"任务完成: {desc}（实际等待了 {seconds} 秒）",
        )
