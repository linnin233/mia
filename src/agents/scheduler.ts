/**
 * SchedulerAgent — LLM 循环决策中心
 *
 * 整个系统的核心 Agent。持续运行 LLM 循环:
 *   1. 接收 USER_INTENT / TASK_RESULT / TASK_ERROR
 *   2. 调用 LLM 分析 → 生成 JSON 决策
 *   3. 决策 reply → 发送流式/非流式回复
 *   4. 决策 execute_task → 发送 EXECUTE_TASK → 等待结果
 *   5. 收到结果 → 回到步骤 2
 *   6. 决策 done / 达到上限 → 循环结束
 *
 * 安全保护:
 *   - MAX_ITERATIONS: 最多循环轮数
 *   - TASK_TIMEOUT: 单个任务超时
 *   - MAX_CONSECUTIVE_TASKS: 连续派发上限
 *
 * 与 Python 版 agents/scheduler.py 保持 1:1 语义映射。
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { BaseAgent } from './base.js';
import { MessageBus } from '../bus/bus.js';
import {
  Message,
  MessageType,
  makeSendText,
  makeSendVoice,
  makeExecuteTask,
  makeStreamStart,
  makeStreamChunk,
  makeStreamEnd,
  makeTuiThought,
} from '../bus/message.js';
import { BaseProvider } from '../providers/base.js';
import { getConfig } from '../config.js';

// ─── Prompt 加载 ─────────────────────────────────────────

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROMPTS_DIR = path.resolve(__dirname, '..', '..', 'prompts');
const AGENTS_MD_PATH = path.resolve(__dirname, '..', '..', 'AGENTS.md');

/** 加载 prompts/ 目录下的文件 */
function loadPrompt(filename: string): string {
  const filePath = path.join(PROMPTS_DIR, filename);
  try {
    if (fs.existsSync(filePath)) {
      return fs.readFileSync(filePath, 'utf-8').trim();
    }
  } catch {
    // 忽略
  }
  return '';
}

/** 加载 AGENTS.md 作为 MIA 身份定义 */
function loadAgentIdentity(): string {
  try {
    if (fs.existsSync(AGENTS_MD_PATH)) {
      return fs.readFileSync(AGENTS_MD_PATH, 'utf-8').trim();
    }
  } catch {
    // 忽略
  }
  return `# MIA
你是 MIA（Modular Intelligent Agent），一个基于 LLM 决策循环的 AI 助手。
名字: MIA。口气: 温暖干练，像靠谱的朋友。语言: 中英混用自然。`;
}

/** 获取 Scheduler 决策用的 system prompt */
function getSchedulerSystemPrompt(): string {
  const identity = loadAgentIdentity();
  let instructions = loadPrompt('scheduler.md');
  if (!instructions) {
    instructions =
      '你是一个智能调度员。你的职责是分析用户意图并做出决策。\n\n' +
      '## 决策格式\n' +
      '严格返回 JSON: {"reasoning":"...","action":"reply"|"execute_task"|"done","action_detail":{}}\n' +
      'action=reply 时: action_detail 包含 message(仅voice时必填) 和 use_voice(bool)\n' +
      'action=execute_task 时: action_detail 包含 task 和 tools_hint\n' +
      '直接回复时 use_voice=false 且无需 message(系统会流式生成)\n';
  }
  return identity + '\n\n---\n\n' + instructions;
}

/** 获取回复生成用的 system prompt */
function getReplySystemPrompt(): string {
  const identity = loadAgentIdentity();
  let instructions = loadPrompt('reply.md');
  if (!instructions) {
    instructions =
      '## 当前任务\n根据对话上下文生成自然、有帮助的回复。\n\n' +
      '## 要求\n- 简洁明了，控制在 300 字以内\n' +
      '- 直接输出回复文本，不要加任何前缀、标签或格式标记\n' +
      '- 不要输出 JSON、代码块或其他结构化格式\n';
  }
  return identity + '\n\n---\n\n' + instructions;
}

// ─── SchedulerAgent ─────────────────────────────────────

/** 决策记录 */
interface Decision {
  action: string;
  reasoning?: string;
  action_detail?: Record<string, unknown>;
}

/**
 * SchedulerAgent — LLM 循环决策中心
 *
 * 每个对话会话独立运行，直到做出 reply/done 决策或达到限制。
 */
export class SchedulerAgent extends BaseAgent {
  // 安全保护常量
  static readonly MAX_ITERATIONS = 10;
  static readonly TASK_TIMEOUT_SEC = 60;
  static readonly MAX_CONSECUTIVE_TASKS = 3;

  private provider: BaseProvider;
  private model?: string;
  private fallbackProvider?: BaseProvider;
  private fallbackModel?: string;
  private enableStreaming: boolean;

  // 当前会话状态
  private _sessionId?: string;
  private _iteration = 0;
  private _consecutiveTasks = 0;
  private _taskHistory: string[] = [];
  private _decisionHistory: Decision[] = [];
  /** 缓存的 memory_context（来自初始 USER_INTENT，TASK_RESULT 后复用） */
  private _savedMemoryContext = '';

  constructor(
    bus: MessageBus,
    provider: BaseProvider,
    model?: string,
    fallbackProvider?: BaseProvider,
    fallbackModel?: string,
    enableStreaming = true,
  ) {
    super('scheduler', bus);
    this.provider = provider;
    this.model = model;
    this.fallbackProvider = fallbackProvider;
    this.fallbackModel = fallbackModel;
    this.enableStreaming = enableStreaming;
  }

  /** 消息处理入口 */
  protected async handle(msg: Message): Promise<void> {
    if (msg.msg_type === MessageType.USER_INTENT) {
      await this._processUserIntent(msg);
    } else if (
      msg.msg_type === MessageType.TASK_RESULT ||
      msg.msg_type === MessageType.TASK_ERROR
    ) {
      await this._processTaskResponse(msg);
    }
  }

  // ─── 核心循环入口 ─────────────────────────────

  /** 处理用户意图 — 开始新一轮对话循环 */
  private async _processUserIntent(msg: Message): Promise<void> {
    this._sessionId = msg.session_id;
    this._iteration = 0;
    this._consecutiveTasks = 0;
    this._taskHistory = [];
    this._decisionHistory = [];
    this._savedMemoryContext = (msg.payload['memory_context'] as string) || '';

    this._printThought('分析用户意图', (msg.payload['intent'] as string) || '');
    await this._decisionLoop(msg);
  }

  /** 处理任务返回结果 */
  private async _processTaskResponse(msg: Message): Promise<void> {
    const isError = msg.msg_type === MessageType.TASK_ERROR;
    if (isError) {
      this._printThought('收到任务错误', (msg.payload['error'] as string) || '');
    } else {
      this._printThought('收到任务结果', (msg.payload['result'] as string) || '');
    }
    this._iteration++;
    await this._decisionLoop(msg);
  }

  // ─── LLM 决策循环 ─────────────────────────────

  /** LLM 决策循环 (避免与 BaseAgent._runLoop 冲突) */
  private async _decisionLoop(triggerMsg: Message): Promise<void> {
    // 安全检查
    if (this._iteration >= SchedulerAgent.MAX_ITERATIONS) {
      await this._forceReply('已达到最大处理轮数，我先给你当前的结果。', triggerMsg);
      return;
    }
    if (this._consecutiveTasks >= SchedulerAgent.MAX_CONSECUTIVE_TASKS) {
      await this._forceReply('已经连续执行了多个任务，我先汇总一下结果。', triggerMsg);
      return;
    }

    // 构建 LLM Context
    const messages = this._buildContext(triggerMsg);

    // 调用 LLM 决策 (主 + fallback)
    let response = await this._tryCallLlm(messages, 4096, 0.3);
    if (response === null) {
      await this._forceReply('抱歉，系统处理遇到问题。', triggerMsg);
      return;
    }

    // 解析决策 JSON
    let decision = this._parseDecision(response);
    if (!decision) {
      // JSON 解析失败，重试一次
      messages.push({ role: 'assistant', content: response });
      messages.push({
        role: 'user',
        content:
          '你上次的回复不是有效的 JSON（可能被截断或格式错误）。' +
          '请重新生成一个完整的 JSON 决策。只输出 JSON。',
      });
      const retryResponse = await this._tryCallLlm(messages, 4096, 0.1);
      if (retryResponse) {
        decision = this._parseDecision(retryResponse);
      }
    }

    if (!decision) {
      await this._forceReply('抱歉，我暂时无法做出决策，请重新描述你的需求。', triggerMsg);
      return;
    }

    // 执行决策
    this._decisionHistory.push(decision);
    await this._executeDecision(decision, triggerMsg);
  }

  // ─── 决策执行 ─────────────────────────────────

  private async _executeDecision(
    decision: Decision,
    triggerMsg: Message,
  ): Promise<void> {
    const action = decision.action || 'reply';
    const reasoning = decision.reasoning || '';
    const detail = decision.action_detail || {};

    if (action === 'reply') {
      this._consecutiveTasks = 0;
      const message = (detail['message'] as string) || '';
      const useVoice = Boolean(detail['use_voice']);

      if (useVoice) {
        // 语音回复
        const finalMsg = message || (await this._generateFallbackReply(triggerMsg));
        this._printThought('决策: 语音回复用户', reasoning);
        const target = this._resolveOutputTarget();
        const meta = this._channelMeta(triggerMsg);
        await this.bus.publish(
          makeSendVoice(finalMsg, (detail['voice'] as string) || '冰糖', 'wav', this._sessionId, target, meta.context_token, meta.to_user_id),
        );
      } else if (this.enableStreaming) {
        // 流式文字回复
        this._printThought('决策: 流式回复用户', reasoning);
        await this._streamReply(triggerMsg);
      } else {
        // 非流式文字回复
        const finalMsg = message || (await this._generateFallbackReply(triggerMsg));
        this._printThought('决策: 回复用户', reasoning);
        const target = this._resolveOutputTarget();
        const meta = this._channelMeta(triggerMsg);
        await this.bus.publish(
          makeSendText(finalMsg, this._sessionId, target, meta.context_token, meta.to_user_id),
        );
      }
    } else if (action === 'execute_task') {
      const task = (detail['task'] as string) || '';
      const toolsHint = (detail['tools_hint'] as string[]) || [];

      // 检查重复任务
      if (this._taskHistory.includes(task)) {
        this._printThought('检测到重复任务，跳过', task);
        const fakeResult: Message = {
          msg_type: MessageType.TASK_RESULT,
          source: 'scheduler',
          target: 'scheduler',
          payload: {
            task_id: 'duplicate',
            result: '任务与之前重复，已跳过。请基于已有结果做出决策。',
          },
          msg_id: Date.now().toString(16),
          timestamp: Date.now(),
          session_id: this._sessionId,
        };
        await this._processTaskResponse(fakeResult);
        return;
      }

      this._taskHistory.push(task);
      this._consecutiveTasks++;
      this._printThought(
        `决策: 执行任务 (第${this._consecutiveTasks}次)`,
        task,
      );

      const taskMsg = makeExecuteTask(
        task, toolsHint, triggerMsg.msg_id, this._sessionId,
      );
      await this.send(taskMsg);
    } else if (action === 'done') {
      // LLM 明确结束 → 发送文本让 Sender 发出 CONVERSATION_DONE
      this._printThought('任务完成', reasoning);
      const target = this._resolveOutputTarget();
      const meta = this._channelMeta(triggerMsg);
      await this.bus.publish(makeSendText(
        reasoning || '处理完成。',
        this._sessionId,
        target,
        meta.context_token,
        meta.to_user_id,
      ));
    } else {
      await this._forceReply('处理完成。', triggerMsg);
    }
  }

  // ─── 流式回复 ─────────────────────────────────

  /** 流式生成回复 — 逐 token 推送给 SenderAgent */
  private async _streamReply(triggerMsg: Message): Promise<void> {
    const replyMessages = this._buildReplyContext(triggerMsg);
    const meta = this._channelMeta(triggerMsg);
    const target = this._resolveOutputTarget();

    // 通知输出目标准备接收
    await this.bus.publish(
      makeStreamStart(this._sessionId, target, meta.context_token, meta.to_user_id),
    );

    // 流式生成 (主 + fallback)
    let fullText = '';
    let streamError: unknown = null;

    try {
      for await (const chunk of this.provider.chatStream(
        replyMessages, this.model, 2048, 0.7,
      )) {
        fullText += chunk;
        await this.bus.publish(
          makeStreamChunk(chunk, this._sessionId, target, meta.context_token, meta.to_user_id),
        );
      }
    } catch (err) {
      streamError = err;
      console.warn(`[Scheduler] 主 Provider 流式失败: ${err}`);
    }

    // 主 Provider 失败 → 尝试备选
    if (streamError && this.fallbackProvider) {
      fullText = '';
      try {
        for await (const chunk of this.fallbackProvider.chatStream(
          replyMessages, this.fallbackModel, 2048, 0.7,
        )) {
          fullText += chunk;
          await this.bus.publish(
            makeStreamChunk(chunk, this._sessionId, target, meta.context_token, meta.to_user_id),
          );
        }
        streamError = null;
      } catch (err2) {
        streamError = err2;
        console.error(`[Scheduler] 备选 Provider 流式也失败:`, err2);
      }
    }

    // 最终降级
    if (streamError && !fullText) {
      fullText = `抱歉，系统处理遇到问题：${streamError}`;
      await this.bus.publish(
        makeStreamChunk(fullText, this._sessionId, target, meta.context_token, meta.to_user_id),
      );
    }

    // 通知流结束
    await this.bus.publish(
      makeStreamEnd(fullText, this._sessionId, target, meta.context_token, meta.to_user_id),
    );
  }

  // ─── 上下文构建 ───────────────────────────────

  /** 构建 LLM 决策上下文 */
  private _buildContext(triggerMsg: Message): Array<{ role: string; content: string }> {
    const messages: Array<{ role: string; content: string }> = [
      { role: 'system', content: getSchedulerSystemPrompt() },
    ];

    // 注入 MemoryAgent 提供的记忆上下文
    // 优先取当前消息的，fallback 到缓存的（TASK_RESULT 后可能丢失）
    const memoryContext =
      ((triggerMsg.payload['memory_context'] as string) || '') ||
      this._savedMemoryContext;
    if (memoryContext) {
      messages.push({
        role: 'user',
        content: `## 跨对话记忆上下文 (来自 MemoryAgent)\n${memoryContext}`,
      });
      messages.push({
        role: 'assistant',
        content: '已了解之前的对话历史，我会基于这些上下文理解用户的指代和新问题。',
      });
    }

    // 历史决策摘要 (最近 3 条)
    if (this._decisionHistory.length > 0) {
      const recent = this._decisionHistory.slice(-3);
      let historyText = '## 之前的决策历史\n';
      for (let i = 0; i < recent.length; i++) {
        historyText += `第${i + 1}轮: ${JSON.stringify(recent[i])}\n`;
      }
      messages.push({ role: 'user', content: historyText });
      messages.push({
        role: 'assistant',
        content: `已了解。我收到了${this._decisionHistory.length}轮历史。请给我最新的消息。`,
      });
    }

    // 当前消息
    const payloadStr = JSON.stringify(triggerMsg.payload, null, 2);
    messages.push({
      role: 'user',
      content: `[${triggerMsg.msg_type}] 当前消息:\n${payloadStr}\n\n请做出决策（只返回 JSON）。`,
    });

    return messages;
  }

  /** 构建流式回复的 LLM 上下文 */
  private _buildReplyContext(triggerMsg: Message): Array<{ role: string; content: string }> {
    const messages: Array<{ role: string; content: string }> = [
      { role: 'system', content: getReplySystemPrompt() },
    ];

    const memoryContext = (triggerMsg.payload['memory_context'] as string) || '';
    if (memoryContext) {
      messages.push({
        role: 'user',
        content: `## 跨对话记忆上下文 (来自 MemoryAgent)\n${memoryContext}`,
      });
      messages.push({
        role: 'assistant',
        content: '已了解之前的对话历史和用户偏好，我会基于这些上下文生成回复。',
      });
    }

    // 决策历史 (最近 3 轮，精简版)
    if (this._decisionHistory.length > 0) {
      const parts = ['## 之前的决策历史'];
      const recent = this._decisionHistory.slice(-3);
      for (let i = 0; i < recent.length; i++) {
        const d = recent[i]!;
        parts.push(
          `第${i + 1}轮: action=${d.action}, reasoning=${(d.reasoning || '').slice(0, 150)}`,
        );
      }
      messages.push({ role: 'user', content: parts.join('\n') });
      messages.push({ role: 'assistant', content: '已了解。请给我最新的消息，我来生成回复。' });
    }

    // 当前消息
    const payloadStr = JSON.stringify(triggerMsg.payload, null, 2);
    messages.push({
      role: 'user',
      content: `当前消息:\n${payloadStr}\n\n请生成回复（直接输出文本，不要 JSON 或其他格式）：`,
    });

    return messages;
  }

  // ─── 辅助方法 ─────────────────────────────────

  /** 非流式生成回复 (降级用) */
  private async _generateFallbackReply(triggerMsg: Message): Promise<string> {
    const replyMessages = this._buildReplyContext(triggerMsg);
    try {
      return await this.provider.chatSync(replyMessages, this.model, undefined, 2048, 0.7);
    } catch (err) {
      if (this.fallbackProvider) {
        try {
          return await this.fallbackProvider.chatSync(
            replyMessages, this.fallbackModel, undefined, 2048, 0.7,
          );
        } catch (err2) {
          return `抱歉，系统处理遇到问题：${err2}`;
        }
      }
      return `抱歉，系统处理遇到问题：${err}`;
    }
  }

  /** 尝试调用 LLM (主 + fallback) */
  private async _tryCallLlm(
    messages: Array<{ role: string; content: string }>,
    maxTokens: number,
    temperature: number,
  ): Promise<string | null> {
    try {
      return await this.provider.chatSync(messages, this.model, undefined, maxTokens, temperature);
    } catch (err) {
      console.warn(`[Scheduler] 主 Provider 失败: ${err}`);
    }
    if (this.fallbackProvider) {
      try {
        return await this.fallbackProvider.chatSync(
          messages, this.fallbackModel, undefined, maxTokens, temperature,
        );
      } catch (err) {
        console.error(`[Scheduler] 备选 Provider 也失败:`, err);
      }
    }
    return null;
  }

  /** 提取渠道元数据 */
  private _channelMeta(triggerMsg: Message): { context_token: string; to_user_id: string } {
    const payload = triggerMsg.payload;
    return {
      context_token: (payload['context_token'] as string) || '',
      to_user_id: (payload['to_user_id'] as string) || '',
    };
  }

  /** 根据 session_id 前缀确定回复目标 Agent */
  private _resolveOutputTarget(): string {
    const sid = this._sessionId || '';
    if (sid.includes(':')) {
      const channel = sid.split(':')[0]!;
      if (channel === 'wechat') return 'wechat_sender';
    }
    return 'sender';
  }

  /** 从 LLM 输出中解析 JSON 决策 */
  private _parseDecision(text: string): Decision | null {
    text = text.trim();

    // 提取代码块
    const codeBlock = text.match(/```(?:json)?\s*\n?(.*?)\n?```/s);
    if (codeBlock) text = codeBlock[1]!.trim();

    // 提取 JSON 对象
    const jsonMatch = text.match(/\{.*\}/s);
    if (jsonMatch) text = jsonMatch[0];

    try {
      return JSON.parse(text) as Decision;
    } catch {
      return null;
    }
  }

  /** 强制回复 — 当循环无法正常完成时，携带渠道元数据 */
  private async _forceReply(message: string, triggerMsg?: Message): Promise<void> {
    const target = this._resolveOutputTarget();
    const meta = triggerMsg ? this._channelMeta(triggerMsg) : { context_token: '', to_user_id: '' };
    await this.bus.publish(makeSendText(
      message, this._sessionId, target, meta.context_token, meta.to_user_id,
    ));
  }

  /** 结构化输出思考过程 — 同时发布到 MessageBus 供 TUI/WebSocket 使用 */
  private _printThought(title: string, detail: string): void {
    const verbose = getConfig().agent.verbose;
    console.log(`\x1b[36m[Scheduler]\x1b[0m ${title}`);
    if (verbose && detail) {
      for (const line of detail.split('\n')) {
        console.log(`   \x1b[90m├─\x1b[0m ${line}`);
      }
    }
    // 发布到 MessageBus 给 TUI / WebSocket 消费
    this.bus.publish(
      makeTuiThought('scheduler', title, detail, this._sessionId),
    ).catch(() => {
      // 忽略发布失败（总线可能已关闭）
    });
  }
}
