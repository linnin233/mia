/**
 * MemoryAgent — 知识记忆管理 Agent
 *
 * 两级记忆设计:
 *   - Level 1 (Working Memory): 每轮对话后实时提取原子知识，存内存
 *   - Level 2 (Persistent Knowledge): 换日或 /compact 时 LLM 合并去重 → 持久化
 *
 * 消息流:
 *   Receiver → USER_INTENT (target="memory_agent") → MemoryAgent
 *     → 检索 (working + persistent) → 注入 → USER_INTENT (target="scheduler")
 *
 * 与 Python 版 agents/memory.py 保持 1:1 语义映射。
 */

import type { BaseProvider } from '../providers/base.js';
import { BaseAgent } from './base.js';
import { MessageBus } from '../bus/bus.js';
import { Message, MessageType } from '../bus/message.js';
import { MemoryStore, KnowledgeEntry, todayStr, CATEGORY_FACT } from '../memory/store.js';
import { MemoryRetriever } from '../memory/retriever.js';
import { getConfig } from '../config.js';

// ─── Level 1: 临时知识提取 prompt ──────────────────────────

const WORKING_KNOWLEDGE_PROMPT =
  '从以下单轮对话中提取 1-3 条原子知识。每条知识应该是一个独立的事实、偏好、决策、任务或洞察。\n\n' +
  '规则:\n' +
  '- 不要复述对话内容，要提炼出可复用的知识\n' +
  '- 每条知识是自包含的，不依赖上下文就能理解\n' +
  '- category 从以下选择: fact(事实), preference(偏好), decision(决策), task(任务), insight(洞察)\n' +
  '- importance 0.0-1.0: 临时闲聊 0.2, 用户偏好 0.7, 重要决策 0.9\n' +
  '- confidence 固定 0.5 (临时知识需要后续验证)\n\n' +
  '只返回 JSON 数组:\n' +
  '[{"content": "用户偏好使用中文进行技术交流", "category": "preference", "keywords": ["中文", "偏好"], "importance": 0.7}]\n\n' +
  '用户消息: {user_msg}\n\n' +
  '助手回复: {assistant_reply}';

// ─── Level 2: 知识合并去重 prompt ──────────────────────────

const CONSOLIDATION_PROMPT =
  '你是一个知识管理助手。将以下临时记忆和原始对话合并提炼为持久知识条目。\n\n' +
  '合并规则:\n' +
  '1. 合并: 相同主题的多条临时知识合并为一条，保留所有细节，不丢失信息\n' +
  '2. 去重: 重复的事实/偏好只保留最新的版本\n' +
  '3. 更新: 如果新信息与旧知识冲突，用新信息覆盖 (用户偏好可能改变)\n' +
  '4. 废弃: 过时的问候道别丢弃，但有信息量的对话必须保留\n' +
  '5. 置信度: 多次出现的知识置信度更高 (临时默认 0.5, 合并后至少 0.7)\n' +
  '6. 完整性: 关键信息必须完整保留——人名、项目名、技术栈、偏好、决策不能省略\n' +
  '7. 不要为了"简洁"而删除重要细节，宁可多保留\n\n' +
  '输入:\n' +
  '- 临时记忆 (已从每轮对话中初步提取):\n{temporary_knowledge}\n\n' +
  '- 原始对话 (保留上下文用于判断):\n{raw_conversations}\n\n' +
  '只返回 JSON 数组 (5-15 条):\n' +
  '[{"content": "...", "category": "fact|preference|decision|task|insight", "confidence": 0.0-1.0, "keywords": [...], "importance": 0.0-1.0, "source_sessions": ["session_id1"]}]';

// ─── /compact 压缩 prompt ──────────────────────────────────

const COMPACT_PROMPT =
  '将以下知识条目压缩为一段简短摘要（200字以内），' +
  '保留关键信息：用户偏好、重要决策、未完成任务、核心事实等。' +
  '丢弃不重要的细节。\n\n{knowledge_text}\n\n请直接输出摘要文本:';

/** 对话轮次缓冲条目 */
interface ConversationTurn {
  user: string;
  assistant: string;
  session_id?: string;
  timestamp?: string;
}

/**
 * MemoryAgent — 两级记忆 + 对话历史管理
 */
export class MemoryAgent extends BaseAgent {
  // 常量
  static readonly MAX_RETRIEVED = 5;
  static readonly MAX_WORKING_ENTRIES = 30;
  static readonly EXTRACTION_TIMEOUT_MS = 8000;
  static readonly CONSOLIDATION_TIMEOUT_MS = 30000;
  static readonly DEFAULT_HISTORY_TURNS = 5;

  // 依赖
  private provider: BaseProvider;
  private model?: string;
  private fallbackProvider?: BaseProvider;
  private fallbackModel?: string;
  private enableAutoStore: boolean;

  // 记忆子系统
  private store: MemoryStore;
  private retriever: MemoryRetriever;

  // Level 1: 临时记忆 (内存)
  private _workingMemory: KnowledgeEntry[] = [];

  // 对话历史缓冲 (用于 Scheduler 上下文注入)
  private _conversationHistory: ConversationTurn[] = [];

  // 原始对话缓冲 (用于合并时 LLM 有完整上下文)
  private _dailyBuffer: ConversationTurn[] = [];

  // 当前日期 (用于换日检测)
  private _currentDate: string = todayStr();

  // 当前轮用户意图暂存
  private _pendingIntent: string | null = null;
  private _pendingSessionId: string | null = null;
  private _pendingOriginal: string | null = null;

  // 配置
  private maxHistoryTurns: number;

  constructor(
    bus: MessageBus,
    provider: BaseProvider,
    store?: MemoryStore,
    model?: string,
    fallbackProvider?: BaseProvider,
    fallbackModel?: string,
    enableAutoStore = true,
  ) {
    super('memory_agent', bus);
    this.provider = provider;
    this.model = model;
    this.fallbackProvider = fallbackProvider;
    this.fallbackModel = fallbackModel;
    this.enableAutoStore = enableAutoStore;

    // 读取配置
    try {
      this.maxHistoryTurns = getConfig().agent.memory_history_turns;
    } catch {
      this.maxHistoryTurns = MemoryAgent.DEFAULT_HISTORY_TURNS;
    }

    // 初始化存储和检索器
    this.store = store || new MemoryStore();
    this.retriever = new MemoryRetriever({
      provider,
      fallbackProvider,
      enableLlmRerank: true,
    });
  }

  // ─── 生命周期 ─────────────────────────────────

  protected async onStart(): Promise<void> {
    this.store.load();
    this._currentDate = todayStr();
  }

  protected async onStop(): Promise<void> {
    // 关闭时强制落盘 — 防止 Level 1 临时记忆丢失
    if (this._workingMemory.length > 0 || this._dailyBuffer.length > 0) {
      console.log(
        `\x1b[34m[MemoryAgent]\x1b[0m ` +
        `正在持久化记忆 (${this._workingMemory.length}条临时` +
        `+${this._dailyBuffer.length}轮对话)...`,
      );
      await this._consolidateDaily();
      console.log(
        `\x1b[34m[MemoryAgent]\x1b[0m ` +
        `记忆已落盘 (共${this.store.count}条)`,
      );
    }
  }

  /** 消息分发 */
  protected async handle(msg: Message): Promise<void> {
    if (msg.msg_type === MessageType.USER_INTENT) {
      await this._onUserIntent(msg);
    } else if (msg.msg_type === MessageType.CONVERSATION_DONE) {
      await this._onConversationDone(msg);
    }
  }

  // ─── USER_INTENT 处理 ──────────────────────────

  /**
   * 处理用户意图 — 对话历史 + 知识检索 → 注入 Scheduler 上下文 → 转发
   */
  private async _onUserIntent(msg: Message): Promise<void> {
    const intent = (msg.payload['intent'] as string) || '';
    const original = (msg.payload['original'] as string) || '';
    const sessionId = msg.session_id;

    // 暂存当前意图
    this._pendingIntent = intent;
    this._pendingSessionId = sessionId ?? null;
    this._pendingOriginal = original;

    // 换日检测
    const today = todayStr();
    if (today !== this._currentDate) {
      await this._consolidateDaily();
      this._currentDate = today;
    }

    // 构造完整记忆上下文
    const contextParts: string[] = [];

    // Part 1: 对话历史
    const historyText = this._buildHistoryContext();
    if (historyText) contextParts.push(historyText);

    // Part 2: 知识记忆检索 (working + persistent 合并)
    let knowledgeText = '';
    const totalAvailable = this.store.count + this._workingMemory.length;
    if (totalAvailable > 0) {
      try {
        const retrieved = await this._retrieveMerged(intent, MemoryAgent.MAX_RETRIEVED);
        if (retrieved.length > 0) {
          knowledgeText = await this.retriever.summarizeForContext(intent, retrieved);
        }
      } catch {
        const recent = this._getRecentMerged(3);
        if (recent.length > 0) {
          knowledgeText = this.retriever._simpleSummary(recent);
        }
      }
    }
    if (knowledgeText) contextParts.push(knowledgeText);

    const memoryContext = contextParts.join('\n\n');

    // 结构化展示
    const verbose = getConfig().agent.verbose;
    if (verbose) {
      console.log(`\x1b[34m[MemoryAgent]\x1b[0m 检索记忆`);
      console.log(`   \x1b[90m├─\x1b[0m 意图: ${intent.slice(0, 80)}`);
      console.log(
        `   \x1b[90m├─\x1b[0m 对话历史: ${this._conversationHistory.length} 轮可用`,
      );
      console.log(`   \x1b[90m├─\x1b[0m 持久知识: ${this.store.count} 条`);
      console.log(`   \x1b[90m├─\x1b[0m 临时记忆: ${this._workingMemory.length} 条`);
      if (knowledgeText) {
        console.log(`   \x1b[90m├─\x1b[0m 知识注入: ${knowledgeText.slice(0, 80)}...`);
      }
    } else {
      console.log(
        `\x1b[34m[MemoryAgent]\x1b[0m 检索: 持久${this.store.count}条 ` +
        `临时${this._workingMemory.length}条 历史${this._conversationHistory.length}轮`,
      );
    }

    // 构造转发消息
    const payload = { ...msg.payload, memory_context: memoryContext };
    const forwardMsg: Message = {
      msg_type: MessageType.USER_INTENT,
      source: this.name,
      target: 'scheduler',
      payload,
      msg_id: Date.now().toString(16),
      timestamp: Date.now(),
      session_id: sessionId,
    };

    await this.send(forwardMsg);
  }

  /** 构造对话历史上下文 */
  private _buildHistoryContext(): string {
    if (this._conversationHistory.length === 0) return '';

    const recent = this._conversationHistory.slice(-this.maxHistoryTurns);
    const lines = ['## 对话历史'];
    for (const turn of recent) {
      lines.push(`用户: ${turn.user.slice(0, 200)}`);
      lines.push(`助手: ${turn.assistant.slice(0, 200)}`);
      lines.push('');
    }

    return lines.join('\n');
  }

  // ─── CONVERSATION_DONE 处理 ─────────────────────

  /** 对话完成 — 缓冲对话 + 实时提取临时知识 (Level 1) */
  private async _onConversationDone(msg: Message): Promise<void> {
    if (!this.enableAutoStore) return;

    const reply = (msg.payload['message'] as string) || '';
    if (!reply || !this._pendingIntent) return;

    const sessionId = this._pendingSessionId;

    // 1. 追加原始对话到缓冲
    this._dailyBuffer.push({
      user: this._pendingOriginal || this._pendingIntent,
      assistant: reply,
      session_id: sessionId || undefined,
      timestamp: new Date().toISOString(),
    });

    // 2. 追加到对话历史
    this._conversationHistory.push({
      user: this._pendingOriginal || this._pendingIntent,
      assistant: reply,
      session_id: sessionId || undefined,
    });
    if (this._conversationHistory.length > this.maxHistoryTurns * 2) {
      this._conversationHistory = this._conversationHistory.slice(-this.maxHistoryTurns);
    }

    // 3. 实时提取临时知识 (Level 1) — 带超时
    try {
      const extracted = await this._withTimeout(
        this._extractWorkingKnowledge(
          this._pendingIntent,
          reply,
          sessionId || 'unknown',
        ),
        MemoryAgent.EXTRACTION_TIMEOUT_MS,
      );
      if (extracted && extracted.length > 0) {
        this._workingMemory.push(...extracted);
        console.log(
          `\x1b[34m[MemoryAgent]\x1b[0m L1 临时知识已提取: ` +
          `${extracted.length} 条, 共 ${this._workingMemory.length} 条`,
        );
      }
    } catch {
      console.log(
        `\x1b[34m[MemoryAgent]\x1b[0m L1 提取超时，降级为本地提取`,
      );
      // 降级: 本地提取
      const fallback = this._localExtractKnowledge(
        this._pendingOriginal || this._pendingIntent,
        reply,
        sessionId || 'unknown',
      );
      if (fallback) {
        this._workingMemory.push(fallback);
      }
    }

    // 4. 检查是否需要强制合并
    if (this._workingMemory.length >= MemoryAgent.MAX_WORKING_ENTRIES) {
      console.log(
        `\x1b[34m[MemoryAgent]\x1b[0m 临时记忆达上限 ` +
        `(${this._workingMemory.length}/${MemoryAgent.MAX_WORKING_ENTRIES})，触发 L2 合并`,
      );
      await this._consolidateDaily();
    }

    // 5. 清理暂存
    this._pendingIntent = null;
    this._pendingSessionId = null;
    this._pendingOriginal = null;
  }

  // ─── Level 1: 临时知识提取 ─────────────────────

  private async _extractWorkingKnowledge(
    userMsg: string,
    assistantReply: string,
    sessionId: string,
  ): Promise<KnowledgeEntry[]> {
    const prompt = WORKING_KNOWLEDGE_PROMPT
      .replace('{user_msg}', userMsg.slice(0, 500))
      .replace('{assistant_reply}', assistantReply.slice(0, 500));
    const messages = [{ role: 'user' as const, content: prompt }];

    const response = await this._callLlmWithFallback(messages, 384, 0.3);
    if (!response) return [];

    // 解析 JSON 数组
    const jsonMatch = response.match(/\[.*\]/s);
    if (!jsonMatch) return [];

    let data: unknown;
    try {
      data = JSON.parse(jsonMatch[0]);
    } catch {
      return [];
    }

    if (!Array.isArray(data)) return [];

    const entries: KnowledgeEntry[] = [];
    for (const item of data.slice(0, 5)) {
      if (!item || typeof item !== 'object') continue;
      const content = String((item as Record<string, unknown>)['content'] || '').trim();
      if (!content || content.length < 3) continue;

      entries.push(
        new KnowledgeEntry({
          content,
          category: String((item as Record<string, unknown>)['category'] || CATEGORY_FACT),
          confidence: 0.5,
          keywords: (item as Record<string, unknown>)['keywords'] as string[] || [],
          importance: Number((item as Record<string, unknown>)['importance']) || 0.5,
          source_sessions: [sessionId],
        }),
      );
    }

    return entries;
  }

  // ─── 本地降级提取 ──────────────────────────────

  private _localExtractKnowledge(
    userMsg: string,
    _assistantReply: string,
    sessionId: string,
  ): KnowledgeEntry | null {
    const source = userMsg.trim();
    if (source.length < 4) return null;

    const content = source.slice(0, 200) + (source.length > 200 ? '...' : '');

    // 简单分词
    const tokens: string[] = [];
    const asciiTokens = source.match(/[a-zA-Z_][a-zA-Z0-9_]{2,}/g) || [];
    tokens.push(...asciiTokens);

    const chineseChars = source.match(/[\u4e00-\u9fff]/g) || [];
    const seen = new Set<string>();
    for (let i = 0; i < chineseChars.length - 1; i++) {
      const bigram = chineseChars[i]! + chineseChars[i + 1]!;
      if (!seen.has(bigram)) {
        seen.add(bigram);
        tokens.push(bigram);
      }
    }

    const stopwords = new Set([
      '请问', '帮我', '我想', '可以', '什么', '怎么', '如何',
      '这个', '那个', '这是', '查询', '一下', '用户说',
    ]);
    const keywords = tokens.filter((t) => !stopwords.has(t)).slice(0, 5);

    return new KnowledgeEntry({
      content,
      category: CATEGORY_FACT,
      confidence: 0.3,
      keywords,
      importance: 0.3,
      source_sessions: [sessionId],
    });
  }

  // ─── Level 2: 知识合并去重 ──────────────────────

  private async _consolidateDaily(): Promise<void> {
    if (this._workingMemory.length === 0 && this._dailyBuffer.length === 0) return;

    // 构建临时知识文本
    let tempKnowledgeText: string;
    if (this._workingMemory.length > 0) {
      const parts = this._workingMemory.map(
        (e, i) =>
          `${i + 1}. [${e.category_label}] ${e.content} ` +
          `(importance=${e.importance.toFixed(1)})`,
      );
      tempKnowledgeText = parts.join('\n');
    } else {
      tempKnowledgeText = '(无临时记忆)';
    }

    // 构建原始对话文本
    let rawText: string;
    if (this._dailyBuffer.length > 0) {
      const parts = this._dailyBuffer.map(
        (turn, i) =>
          `--- 对话 ${i + 1} (session: ${turn.session_id || '?'}) ---\n` +
          `用户: ${turn.user.slice(0, 300)}\n` +
          `助手: ${turn.assistant.slice(0, 300)}`,
      );
      rawText = parts.join('\n\n');
    } else {
      rawText = '(无原始对话)';
    }

    // 调用 LLM 合并去重
    const prompt = CONSOLIDATION_PROMPT
      .replace('{temporary_knowledge}', tempKnowledgeText)
      .replace('{raw_conversations}', rawText);
    const messages = [{ role: 'user' as const, content: prompt }];

    let response: string | null = null;
    try {
      response = await this._withTimeout(
        this._callLlmWithFallback(messages, 2048, 0.3),
        MemoryAgent.CONSOLIDATION_TIMEOUT_MS,
      );
    } catch {
      await this._fallbackPersist();
      return;
    }

    if (!response) {
      await this._fallbackPersist();
      return;
    }

    // 解析合并后的 JSON
    const jsonMatch = response.match(/\[.*\]/s);
    if (!jsonMatch) {
      await this._fallbackPersist();
      return;
    }

    let data: unknown;
    try {
      data = JSON.parse(jsonMatch[0]);
    } catch {
      await this._fallbackPersist();
      return;
    }

    if (!Array.isArray(data)) {
      await this._fallbackPersist();
      return;
    }

    // 持久化
    let count = 0;
    for (const item of data) {
      if (!item || typeof item !== 'object') continue;
      const obj = item as Record<string, unknown>;
      const content = String(obj['content'] || '').trim();
      if (!content || content.length < 3) continue;

      this.store.add(
        new KnowledgeEntry({
          content,
          category: String(obj['category'] || CATEGORY_FACT),
          confidence: Math.max(0.7, Number(obj['confidence']) || 0.7),
          keywords: (obj['keywords'] as string[]) || [],
          importance: Number(obj['importance']) || 0.6,
          source_sessions: (obj['source_sessions'] as string[]) || [],
        }),
      );
      count++;
    }

    console.log(
      `\x1b[34m[MemoryAgent]\x1b[0m L2 合并完成: ` +
      `${this._workingMemory.length} 临时 + ${this._dailyBuffer.length} 对话 → ` +
      `${count} 条持久 (store共${this.store.count}条)`,
    );

    // 清空
    this._workingMemory = [];
    this._dailyBuffer = [];
  }

  /** 降级方案: 直接将临时记忆持久化 */
  private async _fallbackPersist(): Promise<void> {
    for (const entry of this._workingMemory) {
      entry.confidence = Math.max(0.6, entry.confidence);
      this.store.add(entry);
    }
    this._workingMemory = [];
    this._dailyBuffer = [];
  }

  // ─── 合并检索 ─────────────────────────────────

  private async _retrieveMerged(
    intent: string,
    topK: number,
  ): Promise<KnowledgeEntry[]> {
    const results: KnowledgeEntry[] = [];

    // 搜索持久知识
    if (this.store.count > 0) {
      try {
        const persistent = await this.retriever.retrieve(intent, this.store, topK);
        results.push(...persistent);
      } catch {
        // 忽略
      }
    }

    // 搜索临时记忆
    if (this._workingMemory.length > 0) {
      try {
        const keywords = await this.retriever._extractKeywords(intent);
        let workingResults = this.retriever._keywordMatch(keywords, this._workingMemory);
        if (workingResults.length === 0) {
          workingResults = this._workingMemory.slice(-topK);
        }
        for (const entry of workingResults) {
          entry.importance = Math.min(1.0, entry.importance + 0.1);
        }
        results.push(...workingResults.slice(0, topK));
      } catch {
        results.push(...this._workingMemory.slice(-topK));
      }
    }

    // 去重 + 排序
    const seen = new Set<string>();
    const unique = results.filter((e) => {
      if (seen.has(e.id)) return false;
      seen.add(e.id);
      return true;
    });

    unique.sort(
      (a, b) =>
        b.importance * 0.6 + b.confidence * 0.4 -
        (a.importance * 0.6 + a.confidence * 0.4),
    );

    return unique.slice(0, topK);
  }

  private _getRecentMerged(n: number): KnowledgeEntry[] {
    const results = [...this._workingMemory.slice(-n)];
    if (this.store.count > 0) {
      results.push(...this.store.get_recent(n));
    }
    const seen = new Set<string>();
    return results
      .filter((e) => {
        if (seen.has(e.id)) return false;
        seen.add(e.id);
        return true;
      })
      .sort(
        (a, b) =>
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      )
      .slice(0, n);
  }

  // ─── /compact 压缩 ────────────────────────────

  /** 压缩持久知识 */
  async compact(): Promise<string> {
    // 先合并临时记忆
    if (this._workingMemory.length > 0 || this._dailyBuffer.length > 0) {
      await this._consolidateDaily();
    }

    const entries = this.store.get_all();
    if (entries.length === 0) return '知识库为空，无需压缩。';

    const parts = entries.map(
      (e) =>
        `[${e.category_label}] ${e.content} ` +
        `(confidence=${e.confidence.toFixed(1)}, importance=${e.importance.toFixed(1)})`,
    );
    const knowledgeText = parts.join('\n');

    const prompt = COMPACT_PROMPT.replace('{knowledge_text}', knowledgeText);
    const messages = [{ role: 'user' as const, content: prompt }];

    const summary = await this._callLlmWithFallback(messages, 512, 0.3);
    if (!summary) {
      return `LLM 摘要生成失败，知识已持久化 (共 ${this.store.count} 条)`;
    }

    this.store.compact(summary.trim());
    return summary.trim();
  }

  // ─── LLM 调用辅助 ─────────────────────────────

  private async _callLlmWithFallback(
    messages: Array<{ role: string; content: string }>,
    maxTokens: number,
    temperature: number,
  ): Promise<string | null> {
    try {
      return await this.provider.chatSync(
        messages, this.model, undefined, maxTokens, temperature,
      );
    } catch (err) {
      console.warn(`[MemoryAgent] 主 Provider 失败: ${err}`);
    }

    if (this.fallbackProvider) {
      try {
        return await this.fallbackProvider.chatSync(
          messages, this.fallbackModel, undefined, maxTokens, temperature,
        );
      } catch (err) {
        console.error(`[MemoryAgent] 备选 Provider 也失败:`, err);
      }
    }

    return null;
  }

  /** 带超时的异步调用 */
  private async _withTimeout<T>(
    promise: Promise<T>,
    timeoutMs: number,
  ): Promise<T> {
    let timer: NodeJS.Timeout;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error('timeout')), timeoutMs);
    });
    try {
      return await Promise.race([promise, timeout]);
    } finally {
      clearTimeout(timer!);
    }
  }
}
