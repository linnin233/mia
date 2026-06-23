/**
 * TaskAgent — 任务执行 Agent
 *
 * 职责:
 *   1. 接收 Scheduler 的 EXECUTE_TASK 指令
 *   2. 通过自己的 LLM 循环分析任务 → 决定调用工具 → 执行 → 检查结果
 *   3. 返回 TASK_RESULT 或 TASK_ERROR 给 Scheduler
 *
 * 与 Python 版 agents/task.py 保持 1:1 语义映射。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BaseAgent } from './base.js';
import { MessageBus } from '../bus/bus.js';
import { Message, MessageType, makeTaskResult, makeTaskError } from '../bus/message.js';
import { BaseProvider } from '../providers/base.js';
import { Tool, type ToolResult } from '../tools/base.js';
import { ShellTool } from '../tools/shell.js';
import { WebSearchTool } from '../tools/web-search.js';
import { WeatherTool } from '../tools/weather.js';
import { FileTool } from '../tools/file.js';
import { getConfig } from '../config.js';

// ─── Prompt 加载 ─────────────────────────────────────────

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROMPTS_DIR = path.resolve(__dirname, '..', '..', 'prompts');

/** 从 prompts/task_agent.md 加载 TaskAgent 的 system prompt */
function getTaskAgentSystemPrompt(): string {
  const promptPath = path.join(PROMPTS_DIR, 'task_agent.md');
  try {
    if (fs.existsSync(promptPath)) {
      return fs.readFileSync(promptPath, 'utf-8').trim();
    }
  } catch {
    // 忽略加载错误
  }

  // Fallback: 精简的默认提示词
  return (
    '你是一个任务执行器(TaskAgent)。使用可用工具完成任务。\n' +
    '尽早完成，最多5次工具调用，同类工具最多2次。\n' +
    '每次迭代返回 JSON: {"reasoning":"...","action":"call_tool"|"finish",...}\n' +
    '任务完成时返回 finish。\n'
  );
}

// ─── TaskAgent ───────────────────────────────────────────

/**
 * TaskAgent — LLM + Tools 循环执行器
 *
 * 内部最多 5 次迭代，同类工具最多 2 次调用。
 */
export class TaskAgent extends BaseAgent {
  static readonly MAX_ITERATIONS = 5;

  private provider: BaseProvider;
  private model?: string;
  private fallbackProvider?: BaseProvider;
  private fallbackModel?: string;

  /** 已注册的工具表 */
  private tools: Map<string, Tool> = new Map();

  constructor(
    bus: MessageBus,
    provider: BaseProvider,
    tools?: Tool[],
    model?: string,
    fallbackProvider?: BaseProvider,
    fallbackModel?: string,
  ) {
    super('task_agent', bus);
    this.provider = provider;
    this.model = model;
    this.fallbackProvider = fallbackProvider;
    this.fallbackModel = fallbackModel;

    // 注册工具
    if (tools && tools.length > 0) {
      for (const tool of tools) {
        this.tools.set(tool.name, tool);
      }
    } else {
      // 默认注册全部内置工具
      for (const tool of [
        new ShellTool(),
        new WebSearchTool(),
        new WeatherTool(),
        new FileTool(),
      ]) {
        this.tools.set(tool.name, tool);
      }
    }
  }

  /** 处理 EXECUTE_TASK 消息 */
  protected async handle(msg: Message): Promise<void> {
    if (msg.msg_type !== MessageType.EXECUTE_TASK) return;

    const task = (msg.payload['task'] as string) || '';
    const toolsHint = (msg.payload['tools_hint'] as string[]) || [];
    const taskId = msg.msg_id;

    const verbose = getConfig().agent.verbose;
    if (verbose) {
      console.log(`\x1b[33m[TaskAgent]\x1b[0m 收到任务`);
      console.log(`   \x1b[90m├─\x1b[0m 任务: ${task}`);
      if (toolsHint.length > 0) {
        console.log(`   \x1b[90m├─\x1b[0m 建议工具: ${toolsHint.join(', ')}`);
      }
    } else {
      console.log(`\x1b[33m[TaskAgent]\x1b[0m 执行: ${task.slice(0, 60)}`);
    }

    try {
      const [resultText, toolCalls] = await this._executeTask(task, toolsHint);

      if (verbose) {
        console.log(`   \x1b[90m└─\x1b[0m 完成, 工具调用: ${toolCalls.length}次`);
      }

      await this.send(
        makeTaskResult(taskId, resultText, toolCalls, msg.session_id),
      );
    } catch (err) {
      console.error(`[TaskAgent] 任务执行异常:`, err);
      console.log(`   \x1b[90m└─\x1b[0m \x1b[31m失败: ${err}\x1b[0m`);

      await this.send(
        makeTaskError(taskId, String(err), msg.session_id),
      );
    }
  }

  // ─── 核心任务执行循环 ──────────────────────────

  /**
   * 执行任务的主循环
   *
   * @returns [resultText, toolCalls] — 结果文本和工具调用记录
   */
  private async _executeTask(
    task: string,
    toolsHint?: string[],
  ): Promise<[string, Array<Record<string, unknown>>]> {
    const toolCalls: Array<Record<string, unknown>> = [];
    const verbose = getConfig().agent.verbose;

    // 构建工具描述
    const toolsDesc = this._buildToolsDescription(toolsHint);

    // 注入当前北京时间
    const now = new Date();
    const bjTime = new Date(now.getTime() + 8 * 60 * 60 * 1000);
    const weekDays = ['日', '一', '二', '三', '四', '五', '六'];
    const dateContext =
      `当前北京时间: ${bjTime.getFullYear()}年` +
      `${String(bjTime.getMonth() + 1).padStart(2, '0')}月` +
      `${String(bjTime.getDate()).padStart(2, '0')}日 ` +
      `${String(bjTime.getHours()).padStart(2, '0')}:` +
      `${String(bjTime.getMinutes()).padStart(2, '0')}` +
      ` (星期${weekDays[bjTime.getDay()]})`;

    const messages: Array<{ role: string; content: string }> = [
      { role: 'system', content: getTaskAgentSystemPrompt() },
      {
        role: 'user',
        content:
          `${dateContext}\n\n## 可用工具\n${toolsDesc}\n\n` +
          `## 任务\n${task}\n\n请开始执行。只返回 JSON。`,
      },
    ];

    // 工具类型调用次数跟踪（同类最多 2 次）
    const toolCallCounts = new Map<string, number>();

    for (let iteration = 0; iteration < TaskAgent.MAX_ITERATIONS; iteration++) {
      // 调用 LLM (主 Provider + 备选 fallback)
      const response = await this._callLlm(messages);
      if (response === null) {
        return ['任务执行中 LLM 调用失败 (主+备选均不可用)', toolCalls];
      }

      // 解析决策
      const decision = this._parseDecision(response);
      if (!decision) {
        messages.push({ role: 'assistant', content: response });
        messages.push({
          role: 'user',
          content: '请返回有效的 JSON 格式。action 必须是 call_tool 或 finish。',
        });
        continue;
      }

      const action = (decision['action'] as string) || 'finish';
      const reasoning = (decision['reasoning'] as string) || '';

      if (action === 'finish') {
        const result = (decision['result'] as string) || reasoning;
        return [result, toolCalls];
      }

      if (action === 'call_tool') {
        const toolName = (decision['tool_name'] as string) || '';
        const toolArgs = (decision['tool_args'] as Record<string, unknown>) || {};

        // 检查同类工具调用次数
        const count = toolCallCounts.get(toolName) || 0;
        if (count >= 2) {
          messages.push({ role: 'assistant', content: response });
          messages.push({
            role: 'user',
            content: `工具 '${toolName}' 已调用 ${count} 次 (上限 2 次)。请选择其他方式或 finish。`,
          });
          continue;
        }

        if (!this.tools.has(toolName)) {
          messages.push({ role: 'assistant', content: response });
          messages.push({
            role: 'user',
            content: `工具 '${toolName}' 不存在。可用工具: ${[...this.tools.keys()].join(', ')}`,
          });
          continue;
        }

        // 执行工具
        if (verbose) {
          console.log(
            `   \x1b[90m├─\x1b[0m 调用工具: ${toolName}(${JSON.stringify(toolArgs)})`,
          );
        }

        let result: ToolResult;
        try {
          const tool = this.tools.get(toolName)!;
          result = await tool.execute(toolArgs);
        } catch (err) {
          result = { success: false, data: '', error: String(err) };
        }

        // 记录
        toolCallCounts.set(toolName, count + 1);
        toolCalls.push({
          tool: toolName,
          args: toolArgs,
          success: result.success,
          output: result.success ? result.data : result.error,
        });

        // 反馈给 LLM
        const resultText = result.success
          ? `工具 ${toolName} 执行成功。输出:\n${result.data}`
          : `工具 ${toolName} 执行失败。错误:\n${result.error}`;

        messages.push({ role: 'assistant', content: response });
        messages.push({ role: 'user', content: resultText });
      } else {
        messages.push({ role: 'assistant', content: response });
        messages.push({
          role: 'user',
          content: `未知的 action '${action}'。请使用 call_tool 或 finish。`,
        });
      }
    }

    // 达到最大迭代
    return [
      `任务达到最大执行轮数 (${TaskAgent.MAX_ITERATIONS})，以下是已获得的工具调用结果。`,
      toolCalls,
    ];
  }

  // ─── 辅助方法 ─────────────────────────────────

  /** 构建工具描述文本 */
  private _buildToolsDescription(toolsHint?: string[]): string {
    const lines: string[] = [];
    const targetNames = toolsHint && toolsHint.length > 0
      ? toolsHint
      : [...this.tools.keys()];

    for (const name of targetNames) {
      const tool = this.tools.get(name);
      if (tool) {
        lines.push(`### ${tool.name}`);
        lines.push(`描述: ${tool.description}`);
        lines.push(`参数: ${JSON.stringify(tool.parameters)}`);
        lines.push('');
      }
    }

    return lines.length > 0 ? lines.join('\n') : '无可用工具';
  }

  /** 调用 LLM (主 Provider + 备选 fallback) */
  private async _callLlm(
    messages: Array<{ role: string; content: string }>,
  ): Promise<string | null> {
    // 尝试主 Provider
    try {
      return await this.provider.chatSync(messages, this.model, undefined, 2048, 0.3);
    } catch (err) {
      console.warn(`[TaskAgent] 主 Provider 失败: ${err}. 尝试备选...`);
    }

    // 尝试备选
    if (this.fallbackProvider) {
      try {
        return await this.fallbackProvider.chatSync(
          messages, this.fallbackModel, undefined, 2048, 0.3,
        );
      } catch (err) {
        console.error(`[TaskAgent] 备选 Provider 也失败:`, err);
      }
    }

    return null;
  }

  /** 从 LLM 输出中解析 JSON 决策 */
  private _parseDecision(text: string): Record<string, unknown> | null {
    text = text.trim();

    // 提取代码块
    const codeBlock = text.match(/```(?:json)?\s*\n?(.*?)\n?```/s);
    if (codeBlock) {
      text = codeBlock[1]!.trim();
    }

    // 提取 JSON 对象
    const jsonMatch = text.match(/\{.*\}/s);
    if (jsonMatch) {
      text = jsonMatch[0];
    }

    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      return null;
    }
  }
}
