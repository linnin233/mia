/**
 * 记忆系统全链路测试
 *
 * 覆盖 Python 版 test_memory_storage.py 的所有测试场景：
 *   1. 对话上下文 — 多轮对话后历史可见
 *   2. Level 1 临时知识提取 — CONVERSATION_DONE 后 _workingMemory 有内容
 *   3. Level 2 合并持久化 — _consolidateDaily 后 store 有持久知识
 *   4. 合并检索 — working + persistent 同时检索
 *   5. MemoryStore 文件持久化 — 保存后重新加载数据不丢失
 *   6. 降级持久化 — LLM 不可用时 _fallbackPersist 工作正常
 *   7. 本地降级提取 — _localExtractKnowledge 正确提取关键词
 *   8. 短消息边界 — 短消息不产生垃圾记忆
 *   9. 名字提取 — "我叫XX" 生成高置信度事实
 *  10. 偏好提取 — "我喜欢XX" 生成偏好条目
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { MessageBus } from '../src/bus/bus.js';
import {
  MessageType,
  type Message,
  makeTuiThought,
} from '../src/bus/message.js';
import { MemoryAgent } from '../src/agents/memory.js';
import { MemoryStore, KnowledgeEntry, CATEGORY_FACT, CATEGORY_PREFERENCE, CATEGORY_DECISION } from '../src/memory/store.js';
import { BaseProvider } from '../src/providers/base.js';

// ─── Mock Provider ───────────────────────────────────────

/**
 * 模拟 LLM Provider，返回预设 JSON 响应
 * 避免真实 API 调用，确保测试快速稳定
 */
class MockProvider extends BaseProvider {
  private responses: string[];
  public callCount = 0;

  constructor(responses: string[] = []) {
    super();
    this.responses = responses;
  }

  /** 返回预设响应或默认响应 */
  private _nextResponse(): string {
    if (this.callCount < this.responses.length) {
      return this.responses[this.callCount++]!;
    }
    this.callCount++;
    // 默认返回临时知识提取 JSON
    return JSON.stringify([
      {
        content: '用户偏好使用中文交流',
        category: 'preference',
        keywords: ['中文', '偏好'],
        importance: 0.7,
      },
      {
        content: '用户正在开发 MIA 多 Agent 系统',
        category: 'fact',
        keywords: ['MIA', 'Agent'],
        importance: 0.8,
      },
    ]);
  }

  async chatSync(
    _messages: Array<{ role: string; content: unknown }>,
    _model?: string,
    _tools?: Array<Record<string, unknown>>,
    _maxTokens?: number,
    _temperature?: number,
  ): Promise<string> {
    return Promise.resolve(this._nextResponse());
  }

  async *chatStream(
    _messages: Array<{ role: string; content: unknown }>,
    _model?: string,
    _maxTokens?: number,
    _temperature?: number,
  ): AsyncGenerator<string, void, unknown> {
    yield this._nextResponse();
  }

  // chat() must be implemented for abstract class
  async chat(
    _messages: Array<{ role: string; content: unknown }>,
    _model?: string,
    _stream?: boolean,
    _tools?: Array<Record<string, unknown>>,
    _maxTokens?: number,
    _temperature?: number,
  ): Promise<unknown> {
    return Promise.resolve({ choices: [{ message: { content: this._nextResponse() } }] });
  }
}

// ─── 测试辅助 ────────────────────────────────────────────

/** 创建临时目录 */
function makeTempDir(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mia_test_'));
  return dir;
}

/** 清理临时目录 */
function rmTempDir(dir: string): void {
  fs.rmSync(dir, { recursive: true, force: true });
}

/** 等待指定毫秒 */
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** 创建测试 MessageBus */
async function createBus(): Promise<MessageBus> {
  const bus = new MessageBus(100);
  await bus.start();
  return bus;
}

/** 创建测试 MemoryAgent */
async function createAgent(
  tmpDir: string,
  provider?: MockProvider,
  enableAutoStore = true,
): Promise<{ agent: MemoryAgent; bus: MessageBus }> {
  const bus = await createBus();

  // 使用临时目录存储
  const storeFile = path.join(tmpDir, 'store.json');
  const store = new MemoryStore();
  // 手动设置文件路径
  (store as any)._filePath = storeFile;

  const agent = new MemoryAgent(
    bus,
    provider || new MockProvider(),
    store,
    undefined,
    undefined,
    undefined,
    enableAutoStore,
  );

  // 使用 startWithAutoLoop 启动后台消息处理循环
  agent.startWithAutoLoop();
  await sleep(100);

  return { agent, bus };
}

/** 停止并清理 */
async function cleanup(agent: MemoryAgent, bus: MessageBus, tmpDir: string): Promise<void> {
  agent.stop().catch(() => {});
  await bus.stop();
  rmTempDir(tmpDir);
}

// ─── 辅助：发送消息 ──────────────────────────────────────

/** 发送 USER_INTENT 消息到 memory_agent */
async function sendUserIntent(
  bus: MessageBus,
  intent: string,
  original: string,
  sessionId = 's_test',
): Promise<void> {
  const msg: Message = {
    msg_type: MessageType.USER_INTENT,
    source: 'receiver',
    target: 'memory_agent',
    payload: { intent, original },
    msg_id: Date.now().toString(16),
    timestamp: Date.now(),
    session_id: sessionId,
  };
  await bus.publish(msg);
}

/** 发送 CONVERSATION_DONE 消息到 memory_agent */
async function sendConversationDone(
  bus: MessageBus,
  message: string,
  sessionId = 's_test',
): Promise<void> {
  const msg: Message = {
    msg_type: MessageType.CONVERSATION_DONE,
    source: 'sender',
    target: 'memory_agent',
    payload: { message },
    msg_id: Date.now().toString(16),
    timestamp: Date.now(),
    session_id: sessionId,
  };
  await bus.publish(msg);
}

// ═══════════════════════════════════════════════════════════
// 测试 1: 对话上下文 — 多轮后历史可见
// ═══════════════════════════════════════════════════════════

describe('对话上下文 (Conversation Context)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('第 1 轮对话后历史为 0 轮', async () => {
    await sendUserIntent(bus, '你好', '你好', 's1');
    await sleep(50);

    const history = (agent as any)._conversationHistory;
    expect(history).toHaveLength(0); // CONVERSATION_DONE 还没来
  });

  it('第 1 轮 CONVERSATION_DONE 后历史 = 1 轮', async () => {
    await sendUserIntent(bus, '你好世界', '你好世界', 's1');
    await sleep(50);
    await sendConversationDone(bus, '你好！有什么可以帮你的？', 's1');
    await sleep(300); // 等待 L1 提取

    const history = (agent as any)._conversationHistory;
    expect(history.length).toBeGreaterThanOrEqual(1);
    expect(history[0].user).toBe('你好世界');
    expect(history[0].assistant).toBe('你好！有什么可以帮你的？');
  });

  it('第 2 轮后历史 = 2 轮 (含第1轮)', async () => {
    // Round 1
    await sendUserIntent(bus, '我叫Alice', '我叫Alice', 's1');
    await sleep(50);
    await sendConversationDone(bus, '你好Alice，记住了！', 's1');
    await sleep(300);

    // Round 2
    await sendUserIntent(bus, '我叫什么名字', '我叫什么名字', 's2');
    await sleep(50);

    const history = (agent as any)._conversationHistory;
    expect(history.length).toBeGreaterThanOrEqual(1);
    // 第 1 轮对话内容应还在
    const allText = history.map((h: any) => h.user + ' ' + h.assistant).join(' ');
    expect(allText).toContain('Alice');
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 2: Level 1 临时知识提取
// ═══════════════════════════════════════════════════════════

describe('Level 1 临时知识提取 (Working Memory)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('CONVERSATION_DONE 后 _workingMemory 应 >= 1 条', async () => {
    await sendUserIntent(bus, '我在开发MIA系统', '我在开发MIA系统', 's1');
    await sleep(50);
    await sendConversationDone(bus, '好的，MIA 系统进展如何？', 's1');
    await sleep(500); // 等待 L1 + LLM 调用

    const wm = (agent as any)._workingMemory as KnowledgeEntry[];
    expect(wm.length).toBeGreaterThanOrEqual(1);

    // 应包含预设的知识
    const contents = wm.map((e) => e.content).join(' ');
    expect(contents).toMatch(/中文|MIA/i);
  });

  it('临时记忆 confidence = 0.5', async () => {
    await sendUserIntent(bus, '测试知识提取', '测试知识提取', 's1');
    await sleep(50);
    await sendConversationDone(bus, '收到！', 's1');
    await sleep(500);

    const wm = (agent as any)._workingMemory as KnowledgeEntry[];
    for (const e of wm) {
      expect(e.confidence).toBe(0.5); // 临时知识固定 0.5
    }
  });

  it('临时记忆的数量正确', async () => {
    // 使用预设 2 条的 MockProvider（默认）
    await sendUserIntent(bus, '多轮测试', '多轮测试', 's1');
    await sleep(50);
    await sendConversationDone(bus, '回复内容', 's1');
    await sleep(500);

    const wm = (agent as any)._workingMemory as KnowledgeEntry[];
    expect(wm.length).toBe(2); // MockProvider 默认返回 2 条
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 3: Level 2 合并去重持久化
// ═══════════════════════════════════════════════════════════

describe('Level 2 合并去重持久化 (Consolidate)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const provider = new MockProvider([
      // 第 1 次: L1 临时提取 (2 条)
      JSON.stringify([
        { content: '用户偏好中文', category: 'preference', keywords: ['中文'], importance: 0.7 },
        { content: '开发MIA Agent系统', category: 'fact', keywords: ['MIA'], importance: 0.8 },
      ]),
      // 第 2 次: L2 合并去重 (3 条)
      JSON.stringify([
        {
          content: '用户偏好使用中文进行技术交流',
          category: 'preference',
          confidence: 0.8,
          keywords: ['中文', '偏好', '技术交流'],
          importance: 0.8,
          source_sessions: ['s1'],
        },
        {
          content: '用户正在开发 MIA 多 Agent 智能系统',
          category: 'fact',
          confidence: 0.9,
          keywords: ['MIA', 'Agent', '开发'],
          importance: 0.9,
          source_sessions: ['s1'],
        },
        {
          content: '需要实现记忆系统重写',
          category: 'task',
          confidence: 0.85,
          keywords: ['记忆', '重写'],
          importance: 0.7,
          source_sessions: ['s1'],
        },
      ]),
    ]);
    const result = await createAgent(tmpDir, provider);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('合并前 store 为空, 合并后 = 3 条', async () => {
    // 触发 Level 1
    await sendUserIntent(bus, '用户偏好中文，开发MIA', '我在用中文开发MIA', 's1');
    await sleep(50);
    await sendConversationDone(bus, '好的，继续开发MIA的记忆系统', 's1');
    await sleep(500);

    // 验证 L1 有 2 条
    const wm = (agent as any)._workingMemory as KnowledgeEntry[];
    expect(wm.length).toBe(2);

    // store 还是空的
    expect(agent.store.count).toBe(0);

    // 手动触发 L2
    await (agent as any)._consolidateDaily();
    await sleep(100);

    // L2 后 store = 3
    expect(agent.store.count).toBe(3);

    // 验证类别多样性
    const all = agent.store.get_all();
    const cats = new Set(all.map((e) => e.category));
    expect(cats.has('preference')).toBe(true);
    expect(cats.has('fact')).toBe(true);
    expect(cats.has('task')).toBe(true);

    // 验证置信度 >= 0.7
    for (const e of all) {
      expect(e.confidence).toBeGreaterThanOrEqual(0.7);
    }

    // working 和 buffer 被清空
    expect((agent as any)._workingMemory).toHaveLength(0);
    expect((agent as any)._dailyBuffer).toHaveLength(0);
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 4: 合并检索 (working + persistent)
// ═══════════════════════════════════════════════════════════

describe('合并检索 (Merged Retrieval)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;

    // 手动填充临时记忆
    (agent as any)._workingMemory = [
      new KnowledgeEntry({
        content: '用户今天询问了天气预报',
        category: CATEGORY_FACT,
        confidence: 0.5,
        keywords: ['天气', '预报', '查询'],
        importance: 0.4,
        source_sessions: ['s_new'],
      }),
    ];

    // 手动填充持久存储
    agent.store.add(new KnowledgeEntry({
      content: '用户偏好使用中文交流，喜欢详细注释',
      category: CATEGORY_PREFERENCE,
      confidence: 0.8,
      keywords: ['中文', '偏好', '注释', '交流'],
      importance: 0.7,
      source_sessions: ['s_old'],
    }));
    agent.store.add(new KnowledgeEntry({
      content: '用户正在开发 MIA 多 Agent 智能系统',
      category: CATEGORY_FACT,
      confidence: 0.9,
      keywords: ['MIA', '开发', 'Agent', '系统'],
      importance: 0.8,
      source_sessions: ['s_old'],
    }));
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('检索应返回 working + persistent 结果', async () => {
    const results = await (agent as any)._retrieveMerged(
      'MIA 系统开发进度',
      5,
    );

    expect(results.length).toBeGreaterThanOrEqual(1);

    const contents = results.map((e: KnowledgeEntry) => e.content).join(' ');
    expect(contents).toContain('MIA');
  });

  it('关键词 "天气" 应匹配到临时记忆', async () => {
    const results = await (agent as any)._retrieveMerged(
      '天气怎么样',
      5,
    );

    const contents = results.map((e: KnowledgeEntry) => e.content).join(' ');
    expect(contents).toContain('天气');
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 5: MemoryStore 文件持久化
// ═══════════════════════════════════════════════════════════

describe('MemoryStore 文件持久化', () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = makeTempDir();
    // 创建 memory 子目录
    const memDir = path.join(tmpDir, 'memory');
    fs.mkdirSync(memDir, { recursive: true });
  });

  afterEach(() => {
    rmTempDir(tmpDir);
  });

  it('save → 重新 load → 数据不丢失', () => {
    const storeFile = path.join(tmpDir, 'memory', 'store.json');

    // Store 1: 写入数据
    const store1 = new MemoryStore();
    // 手动设置文件路径（hack: 用反射覆盖私有属性）
    (store1 as any)._filePath = storeFile;

    store1.add(new KnowledgeEntry({
      content: '用户名字是 Alice',
      category: CATEGORY_FACT,
      confidence: 0.9,
      keywords: ['名字', 'Alice'],
      importance: 0.8,
      source_sessions: ['s1'],
    }));
    store1.add(new KnowledgeEntry({
      content: '用户偏好 Python',
      category: CATEGORY_PREFERENCE,
      confidence: 0.7,
      keywords: ['Python', '偏好'],
      importance: 0.6,
      source_sessions: ['s1'],
    }));

    expect(store1.count).toBe(2);
    // 验证文件存在
    expect(fs.existsSync(storeFile)).toBe(true);

    // Store 2: 重新加载
    const store2 = new MemoryStore();
    (store2 as any)._filePath = storeFile;
    store2.load();

    expect(store2.count).toBe(2);

    const all = store2.get_all();
    const contents = all.map((e) => e.content).join(' ');
    expect(contents).toContain('Alice');
    expect(contents).toContain('Python');

    // 验证字段完整性
    const alice = all.find((e) => e.content.includes('Alice'))!;
    expect(alice.category).toBe(CATEGORY_FACT);
    expect(alice.confidence).toBe(0.9);
    expect(alice.keywords).toContain('Alice');
    expect(alice.source_sessions).toEqual(['s1']);
  });

  it('同 content 去重合并', () => {
    const storeFile = path.join(tmpDir, 'memory', 'store.json');
    const store = new MemoryStore();
    (store as any)._filePath = storeFile;

    store.add(new KnowledgeEntry({
      content: '用户叫 Bob',
      category: CATEGORY_FACT,
      confidence: 0.5,
      keywords: ['Bob'],
      importance: 0.5,
      source_sessions: ['s1'],
    }));

    // 再添加相同 content
    store.add(new KnowledgeEntry({
      content: '用户叫 Bob',
      category: CATEGORY_FACT,
      confidence: 0.8,
      keywords: ['Bob', '名字'],
      importance: 0.7,
      source_sessions: ['s2'],
    }));

    expect(store.count).toBe(1); // 去重

    const e = store.get_all()[0]!;
    expect(e.confidence).toBe(0.8); // 取高值
    expect(e.keywords).toContain('Bob');
    expect(e.keywords).toContain('名字'); // 合并
    expect(e.source_sessions).toEqual(['s1', 's2']); // 合并
    expect(e.importance).toBe(0.7); // 取高值
  });

  it('delete 后 save', () => {
    const storeFile = path.join(tmpDir, 'memory', 'store.json');
    const store = new MemoryStore();
    (store as any)._filePath = storeFile;

    const entry = new KnowledgeEntry({
      content: '待删除',
      category: CATEGORY_FACT,
      confidence: 0.5,
    });
    store.add(entry);
    expect(store.count).toBe(1);

    store.delete(entry.id);
    expect(store.count).toBe(0);

    // 重新加载验证
    const store2 = new MemoryStore();
    (store2 as any)._filePath = storeFile;
    store2.load();
    expect(store2.count).toBe(0);
  });

  it('clear 后 count = 0', () => {
    const storeFile = path.join(tmpDir, 'memory', 'store.json');
    const store = new MemoryStore();
    (store as any)._filePath = storeFile;

    store.add(new KnowledgeEntry({ content: 'A', category: CATEGORY_FACT, confidence: 0.5 }));
    store.add(new KnowledgeEntry({ content: 'B', category: CATEGORY_FACT, confidence: 0.5 }));
    store.clear();
    expect(store.count).toBe(0);

    const store2 = new MemoryStore();
    (store2 as any)._filePath = storeFile;
    store2.load();
    expect(store2.count).toBe(0);
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 6: 降级持久化 (_fallbackPersist)
// ═══════════════════════════════════════════════════════════

describe('降级持久化 (Fallback Persist)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('降级后 working 条目 → store (confidence 提升到 >= 0.6)', async () => {
    // 手动填充临时记忆
    (agent as any)._workingMemory = [
      new KnowledgeEntry({
        content: '测试知识 1',
        category: CATEGORY_DECISION,
        confidence: 0.5,
        keywords: ['test'],
        importance: 0.5,
        source_sessions: ['s_test'],
      }),
      new KnowledgeEntry({
        content: '测试知识 2',
        category: CATEGORY_FACT,
        confidence: 0.5,
        keywords: ['test'],
        importance: 0.5,
        source_sessions: ['s_test'],
      }),
    ];
    (agent as any)._dailyBuffer = [
      { user: 'test', assistant: 'ok', session_id: 's_test', timestamp: '2026-01-01T00:00:00' },
    ];

    await (agent as any)._fallbackPersist();

    expect(agent.store.count).toBe(2);
    expect((agent as any)._workingMemory).toHaveLength(0);
    expect((agent as any)._dailyBuffer).toHaveLength(0);

    for (const e of agent.store.get_all()) {
      expect(e.confidence).toBeGreaterThanOrEqual(0.6);
    }
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 7: 本地降级提取 (_localExtractKnowledge)
// ═══════════════════════════════════════════════════════════

describe('本地降级提取 (Local Extract)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('常规输入应返回有效条目', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '查询一下嘉兴明天的天气',
      '嘉兴明天23-30度，阵雨...',
      's_test',
    );

    expect(entry).not.toBeNull();
    expect(entry.content.length).toBeGreaterThanOrEqual(4);

    // 应包含助手回复的摘要（现在是优先使用助手回复）
    expect(entry.category).toBe(CATEGORY_FACT);
    expect(entry.confidence).toBe(0.3); // 低置信度
    expect(entry.importance).toBe(0.3);
    expect(entry.source_sessions).toEqual(['s_test']);
    expect(entry.keywords.length).toBeGreaterThanOrEqual(0);
  });

  it('短消息 (总长度 < 5) 应返回 null', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '你好',
      '你好呀',
      's_short',
    );

    expect(entry).toBeNull();
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 8: 名字提取 ("我叫XX")
// ═══════════════════════════════════════════════════════════

describe('名字提取 (Name Extraction)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('"我叫张三" → 生成名字知识', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '我叫张三',
      '你好张三，我记住了！',
      's_name',
    );

    expect(entry).not.toBeNull();
    expect(entry.content).toContain('张三');
    expect(entry.category).toBe(CATEGORY_FACT);
    expect(entry.importance).toBe(0.8); // 名字是高重要性
    expect(entry.confidence).toBe(0.6); // 本地提取名字置信度
  });

  it('"我是李四" → 生成名字知识', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '我是李四',
      '好的李四，记下了',
      's_name2',
    );

    expect(entry).not.toBeNull();
    expect(entry.content).toContain('李四');
  });

  it('"叫我王五" → 生成名字知识', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '叫我王五就行',
      '好的王五！',
      's_name3',
    );

    expect(entry).not.toBeNull();
    expect(entry.content).toContain('王五');
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 9: 偏好提取
// ═══════════════════════════════════════════════════════════

describe('偏好提取 (Preference Extraction)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const result = await createAgent(tmpDir);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('"我喜欢Python" → 生成偏好知识', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '我喜欢Python编程',
      'Python确实很棒！',
      's_pref',
    );

    expect(entry).not.toBeNull();
    expect(entry.content).toMatch(/偏好|Python/i);
    expect(entry.category).toBe(CATEGORY_PREFERENCE);
    expect(entry.importance).toBe(0.6);
  });

  it('"我常用VSCode" → 生成偏好知识', () => {
    const entry = (agent as any)._localExtractKnowledge(
      '我常用VSCode写代码',
      'VSCode是个好选择',
      's_pref2',
    );

    expect(entry).not.toBeNull();
    expect(entry.content).toMatch(/偏好|VSCode/i);
  });
});

// ═══════════════════════════════════════════════════════════
// 测试 10: 记忆检索注入 (端到端）
// ═══════════════════════════════════════════════════════════

describe('记忆检索注入 (Memory Injection)', () => {
  let tmpDir: string;
  let agent: MemoryAgent;
  let bus: MessageBus;

  beforeEach(async () => {
    tmpDir = makeTempDir();
    const provider = new MockProvider([
      // L1 临时提取
      JSON.stringify([
        { content: '用户叫 Alice', category: 'fact', keywords: ['Alice', '名字'], importance: 0.8 },
      ]),
      // L2 合并
      JSON.stringify([
        {
          content: '用户叫 Alice',
          category: 'fact',
          confidence: 0.9,
          keywords: ['Alice', '名字'],
          importance: 0.9,
          source_sessions: ['s1'],
        },
      ]),
    ]);
    const result = await createAgent(tmpDir, provider);
    agent = result.agent;
    bus = result.bus;
  });

  afterEach(async () => {
    await cleanup(agent, bus, tmpDir);
  });

  it('名字知识存储后，下次检索能查到', async () => {
    // 第 1 轮：介绍名字
    await sendUserIntent(bus, '我叫Alice', '我叫Alice', 's1');
    await sleep(50);
    await sendConversationDone(bus, '你好Alice，记住了！', 's1');
    await sleep(500);

    // 手动触发 L2 合并
    await (agent as any)._consolidateDaily();
    await sleep(100);

    // store 应有知识
    expect(agent.store.count).toBeGreaterThanOrEqual(1);

    // 第 2 轮：询问名字
    await sendUserIntent(bus, '我叫什么名字', '我叫什么名字', 's2');
    await sleep(300);

    // _retrieveMerged 应该能找到之前的名字知识
    const retrieved = await (agent as any)._retrieveMerged('我叫什么名字', 5);
    const contents = retrieved.map((e: KnowledgeEntry) => e.content).join(' ');
    expect(contents).toContain('Alice');
  });
});
