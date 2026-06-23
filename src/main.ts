#!/usr/bin/env node
/**
 * MIA 主入口 — CLI 交互 + Fastify HTTP 服务
 *
 * 完整 Agent 链路:
 *   User Input → ReceiverAgent → MemoryAgent → SchedulerAgent → (TaskAgent) → SenderAgent → Output
 *
 * 用法:
 *   npm run dev                        # 交互模式
 *   npm run dev -- --query "你好"      # 单次对话
 *   npm run dev -- --server 8080       # HTTP API 服务
 *   npm run dev -- --wechat            # 交互 + 微信渠道
 *
 * 与 Python 版 main.py 保持 1:1 语义映射。
 */

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { WebSocketServer, type WebSocket } from 'ws';
import { MessageBus } from './bus/bus.js';
import {
  MessageType,
  makeRawInput,
} from './bus/message.js';
import { getConfig, type Config } from './config.js';
import { ReceiverAgent } from './agents/receiver.js';
import { SchedulerAgent } from './agents/scheduler.js';
import { SenderAgent } from './agents/sender.js';
import { TaskAgent } from './agents/task.js';
import { MemoryAgent } from './agents/memory.js';
import { BaseAgent } from './agents/base.js';
import { MemoryStore } from './memory/store.js';
import { MiMoProvider } from './providers/mimo.js';
import { DeepSeekProvider } from './providers/deepseek.js';
import {
  handleModelCommand,
  handleAgentCommand,
  handleChannelCommand,
} from './cli/commands.js';
import type { PipelineEventCallbacks } from './server/ws-relay.js';
import { WsRelay } from './server/ws-relay.js';

// ─── 类型定义 ────────────────────────────────────────────

interface ParsedArgs {
  server: boolean;
  port: number;
  query?: string;
  image?: string;
  voice?: string;
  wechat: boolean;
}

// ─── 参数解析 ────────────────────────────────────────────

/** 解析命令行参数 */
function parseArgs(): ParsedArgs {
  const args = process.argv.slice(2);
  const result: ParsedArgs = {
    server: false,
    port: 8080,
    wechat: false,
  };

  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--server':
        result.server = true;
        break;
      case '--port':
        result.port = parseInt(args[++i] || '8080', 10);
        break;
      case '--query':
      case '-q':
        result.query = args[++i];
        break;
      case '--image':
      case '-i':
        result.image = args[++i];
        break;
      case '--voice':
      case '-v':
        result.voice = args[++i];
        break;
      case '--wechat':
      case '-w':
        result.wechat = true;
        break;
    }
  }

  return result;
}

// ─── Provider 工厂 ───────────────────────────────────────

interface ProviderSet {
  mimo: MiMoProvider | null;
  deepseek: DeepSeekProvider | null;
}

/** 创建 MiMo + DeepSeek provider 实例 */
function createProviders(config: Config): ProviderSet {
  const rt = config.runtime;
  const mimoKey = rt.provider_api_keys['mimo'] || config.mimo.api_key;
  const deepseekKey = rt.provider_api_keys['deepseek'] || config.deepseek.api_key;

  return {
    mimo: mimoKey ? new MiMoProvider(mimoKey) : null,
    deepseek: deepseekKey ? new DeepSeekProvider(deepseekKey) : null,
  };
}

// ─── Agent 工厂 ──────────────────────────────────────────

interface AgentSet {
  receiver: ReceiverAgent;
  scheduler: SchedulerAgent;
  sender: SenderAgent;
  taskAgent: TaskAgent;
  memoryAgent: MemoryAgent;
}

/** 创建所有核心 Agent */
function createAgents(
  bus: MessageBus,
  providers: ProviderSet,
  config: Config,
): AgentSet {
  const rt = config.runtime;

  if (!providers.mimo) {
    throw new Error('MiMo API Key 未配置。请在 .env 文件中设置 MIMO_API_KEY。');
  }

  return {
    receiver: new ReceiverAgent(bus, providers.mimo),
    scheduler: new SchedulerAgent(
      bus,
      providers.mimo,
      rt.scheduler_model,
      providers.deepseek || undefined,
      rt.scheduler_fallback || undefined,
      config.agent.enable_streaming,
    ),
    sender: new SenderAgent(
      bus,
      rt.sender_tts_enabled ? providers.mimo : null,
      config.agent.workspace_dir,
    ),
    taskAgent: new TaskAgent(
      bus,
      providers.mimo,
      undefined, // default tools
      rt.task_model,
      providers.deepseek || undefined,
      rt.task_fallback || undefined,
    ),
    memoryAgent: new MemoryAgent(
      bus,
      providers.mimo,
      new MemoryStore(),
      rt.memory_model,
      providers.deepseek || undefined,
      rt.memory_fallback || undefined,
    ),
  };
}

/** 设置消息总线镜像 — MemoryAgent 观察特定消息类型 */
function setupMirrors(bus: MessageBus): void {
  const mirrorTypes = [
    MessageType.USER_INTENT,
    MessageType.SEND_TEXT,
    MessageType.STREAM_END,
    MessageType.EXECUTE_TASK,
    MessageType.TASK_RESULT,
    MessageType.TASK_ERROR,
    MessageType.CONVERSATION_DONE,
  ];
  for (const mt of mirrorTypes) {
    bus.subscribeMirror(mt, 'memory_agent');
  }
}

/** 启动所有 Agent */
async function startAllAgents(agents: BaseAgent[]): Promise<void> {
  for (const agent of agents) {
    await agent.start();
  }
}

/** 停止所有 Agent */
async function stopAllAgents(agents: BaseAgent[]): Promise<void> {
  for (const agent of agents) {
    await agent.stop();
  }
}

// ─── 打印系统信息 ────────────────────────────────────────

/** 打印系统启动横幅 */
function printBanner(config: Config, enableWechat: boolean): void {
  const rt = config.runtime;
  console.log(`\x1b[1m${'='.repeat(50)}\x1b[0m`);
  console.log(`\x1b[1mMIA v0.2.0 — MiMo Intelligent Agent (TypeScript)\x1b[0m`);
  console.log(`  Scheduler: ${rt.scheduler_model} (主) / ${rt.scheduler_fallback || '无'} (备)`);
  console.log(`  Receiver: 视觉=${rt.receiver_vision_enabled ? 'on' : 'off'} 语音=${rt.receiver_audio_enabled ? 'on' : 'off'}`);
  console.log(`  Sender: TTS=${rt.sender_tts_enabled ? 'on' : 'off'}`);
  if (enableWechat) console.log(`  WeChat: 已启用 (iLink Bot)`);
  console.log(`\x1b[1m${'='.repeat(50)}\x1b[0m`);
  console.log();
}

// ─── 单次对话 Pipeline ──────────────────────────────────

/**
 * 运行完整的 Agent 链路（单次对话）
 *
 * @param query - 用户输入文本
 * @param imagePath - 可选图片路径
 * @param voicePath - 可选语音路径
 * @param timeoutSec - 超时秒数
 * @param events - 可选事件回调（用于 WebSocket/TUI 实时推送）
 * @param signal - 可选 AbortSignal（用于外部中止管线）
 * @param externalBus - 可选外部 MessageBus（用于 WebSocket 共享总线）
 * @returns 最终回复文本
 */
async function runAgentPipeline(
  query: string,
  imagePath?: string,
  voicePath?: string,
  timeoutSec = 180,
  events?: PipelineEventCallbacks,
  signal?: AbortSignal,
  externalBus?: MessageBus,
): Promise<string | null> {
  const config = getConfig();
  const providers = createProviders(config);
  const sessionId = crypto.randomBytes(6).toString('hex');

  // 使用外部传入的 bus 或创建新的
  const bus = externalBus || new MessageBus(100);
  const ownBus = !externalBus; // 标记是否自己拥有 bus（需要 stop）

  await bus.start();
  setupMirrors(bus);

  // ─── 事件回调镜像 ──────────────────────────────
  // 如果提供了 events 回调，将相关消息类型镜像投递到 main 队列，
  // 这样主循环可以同时处理 CONVERSATION_DONE 和其他实时事件
  if (events) {
    const eventMirrorTypes = [
      MessageType.STREAM_START,
      MessageType.STREAM_CHUNK,
      MessageType.STREAM_END,
      MessageType.EXECUTE_TASK,
      MessageType.TASK_RESULT,
      MessageType.TASK_ERROR,
      MessageType.TUI_THOUGHT,
    ];
    for (const mt of eventMirrorTypes) {
      bus.subscribeMirror(mt, 'main');
    }
  }

  // 创建 Agent
  const agents = createAgents(bus, providers, config);
  const allAgents: BaseAgent[] = [
    agents.receiver,
    agents.memoryAgent,
    agents.scheduler,
    agents.sender,
    agents.taskAgent,
  ];

  // 启动并开始后台循环
  await startAllAgents(allAgents);
  await new Promise((r) => setTimeout(r, 300)); // 等待就绪

  let finalResponse: string | null = null;

  try {
    // 注入用户输入
    const rawMsg = makeRawInput(query, [], sessionId);
    if (imagePath) rawMsg.payload['image'] = imagePath;
    if (voicePath) rawMsg.payload['voice'] = voicePath;
    await bus.publish(rawMsg);

    if (!events) {
      console.log(`\x1b[36m[Main]\x1b[0m 用户输入已注入: ${query.slice(0, 100)}`);
    }

    // 等待 CONVERSATION_DONE
    await bus.subscribe('main');
    const deadline = Date.now() + timeoutSec * 1000;

    while (Date.now() < deadline) {
      // 检查外部中止信号
      if (signal?.aborted) {
        console.log(`\x1b[33m[Main]\x1b[0m 管线被外部中止`);
        break;
      }

      const msg = await bus.receive('main', 1000);
      if (!msg) continue;

      // ─── 处理事件回调 ──────────────────────────
      if (events) {
        switch (msg.msg_type) {
          case MessageType.STREAM_START:
            events.onStreamStart?.();
            break;
          case MessageType.STREAM_CHUNK:
            events.onStreamChunk?.((msg.payload['delta'] as string) || '');
            break;
          case MessageType.STREAM_END:
            events.onStreamEnd?.((msg.payload['message'] as string) || '');
            break;
          case MessageType.TUI_THOUGHT:
            events.onThought?.(
              (msg.payload['agent'] as string) || 'scheduler',
              (msg.payload['title'] as string) || '',
              (msg.payload['detail'] as string) || '',
            );
            break;
          case MessageType.EXECUTE_TASK:
            events.onTool?.(
              'task',
              'running',
              (msg.payload['task'] as string) || '',
              '',
            );
            break;
          case MessageType.TASK_RESULT:
            events.onTool?.(
              'task',
              'success',
              '',
              (msg.payload['result'] as string)?.slice(0, 500) || '',
            );
            break;
          case MessageType.TASK_ERROR:
            events.onTool?.(
              'task',
              'error',
              '',
              (msg.payload['error'] as string) || 'Unknown error',
            );
            break;
        }
      }

      if (msg.msg_type === MessageType.CONVERSATION_DONE) {
        finalResponse = (msg.payload['message'] as string) || '';
        events?.onDone?.();
        break;
      }

      if (msg.msg_type === MessageType.TASK_ERROR) {
        if (!events) {
          console.log(
            `\x1b[31m[Main] 检测到 TASK_ERROR: ${msg.payload['error']}\x1b[0m`,
          );
        } else {
          events.onError?.((msg.payload['error'] as string) || 'Task error');
        }
      }
    }

    if (finalResponse === null) {
      if (!events) {
        console.log(`\n\x1b[31m[Main] 超时 (${timeoutSec}s)，未收到回复\x1b[0m`);
      } else {
        events.onError?.('处理超时，未收到回复');
      }
    }
  } finally {
    // 清理 — 只有自己创建的 bus 才 stop
    await stopAllAgents(allAgents);
    if (ownBus) {
      await bus.stop();
    }
  }

  return finalResponse;
}

// ─── 单次 CLI 对话 ──────────────────────────────────────

async function runCliQuery(
  query: string,
  imagePath?: string,
  voicePath?: string,
): Promise<void> {
  const result = await runAgentPipeline(query, imagePath, voicePath);

  if (result) {
    console.log();
    console.log(`\x1b[1m${'='.repeat(50)}\x1b[0m`);
    console.log(`\x1b[1m[完成] 对话结束\x1b[0m`);
  } else {
    console.log(`\x1b[31m[失败] 未收到回复\x1b[0m`);
    process.exit(1);
  }
}

// ─── CLI 交互模式 ───────────────────────────────────────

async function runCliInteractive(enableWechat = false): Promise<void> {
  console.log(`\x1b[1mMIA v0.2.0 — 交互模式 (TypeScript)\x1b[0m`);
  console.log(`  输入 '/quit' 退出, '/help' 查看帮助`);
  console.log(`  直接输入问题开始对话`);
  console.log();

  const config = getConfig();
  const providers = createProviders(config);
  const bus = new MessageBus(100);

  await bus.start();
  setupMirrors(bus);

  // 创建 Agent
  const agents = createAgents(bus, providers, config);
  const allAgents: BaseAgent[] = [
    agents.receiver,
    agents.memoryAgent,
    agents.scheduler,
    agents.sender,
    agents.taskAgent,
  ];

  // 打印横幅
  printBanner(config, enableWechat);

  // 启动所有 Agent
  await startAllAgents(allAgents);
  await new Promise((r) => setTimeout(r, 300));

  // 创建 readline 接口
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  /** 异步获取用户输入 */
  const question = (prompt: string): Promise<string> =>
    new Promise((resolve) => rl.question(prompt, resolve));

  try {
    // 用户输入循环
    while (true) {
      let userInput: string;
      try {
        userInput = (await question('\x1b[32mYou > \x1b[0m')).trim();
      } catch {
        console.log('\n再见~');
        break;
      }

      if (!userInput) continue;

      // /quit, /exit, /q
      if (['/quit', '/exit', '/q'].includes(userInput.toLowerCase())) {
        console.log('再见~');
        break;
      }

      // /help
      if (['/help', '/h'].includes(userInput.toLowerCase())) {
        console.log(`
命令:
  /quit, /exit, /q  — 退出
  /help, /h         — 显示帮助
  /model            — 查看模型平台配置
  /agent            — 查看 Agent 模型分配
  /channel          — 查看通信渠道配置
  /compact          — 压缩对话历史
  /verbose          — 切换详细日志
  /memory           — 显示当前记忆状态
  直接输入文本       — 开始对话
`);
        continue;
      }

      // /verbose
      if (userInput.toLowerCase() === '/verbose') {
        const cfg = getConfig();
        cfg.agent.verbose = !cfg.agent.verbose;
        console.log(`  \x1b[90m详细日志: ${cfg.agent.verbose ? '开启' : '关闭'}\x1b[0m`);
        console.log();
        continue;
      }

      // /compact
      if (userInput.toLowerCase() === '/compact') {
        const mem = agents.memoryAgent;
        if (mem) {
          console.log(`  \x1b[90m正在压缩对话历史...\x1b[0m`);
          try {
            const summary = await mem.compact();
            console.log(`  \x1b[32m[OK] 对话历史已压缩\x1b[0m`);
            console.log(`  \x1b[90m摘要: ${summary.slice(0, 100)}...\x1b[0m`);
          } catch (err) {
            console.log(`  \x1b[31m[FAIL] 压缩失败: ${err}\x1b[0m`);
          }
        }
        console.log();
        continue;
      }

      // /memory
      if (userInput.toLowerCase() === '/memory') {
        const store = agents.memoryAgent['store'] as MemoryStore;
        console.log(`\n  持久知识: ${store.count} 条`);
        console.log(`  临时记忆: ${(agents.memoryAgent as unknown as { _workingMemory?: unknown[] })._workingMemory?.length || 0} 条`);
        for (const entry of store.get_recent(5)) {
          console.log(`  [${entry.category_label}] ${entry.content}`);
        }
        console.log();
        continue;
      }

      // /model
      if (userInput.toLowerCase() === '/model') {
        await handleModelCommand(config.runtime);
        continue;
      }

      // /agent
      if (userInput.toLowerCase() === '/agent') {
        await handleAgentCommand(config.runtime);
        continue;
      }

      // /channel
      if (userInput.toLowerCase() === '/channel') {
        await handleChannelCommand(config.runtime);
        continue;
      }

      // 拦截未知 / 命令
      if (userInput.startsWith('/')) {
        console.log(`  \x1b[33m未知命令 '${userInput}'，输入 /help 查看可用命令。\x1b[0m`);
        console.log();
        continue;
      }

      // ─── 本轮对话 ────────────────────────────
      const sessionId = crypto.randomBytes(6).toString('hex');

      // 注入 RAW_INPUT
      const rawMsg = makeRawInput(userInput, [], sessionId);
      await bus.publish(rawMsg);

      console.log(`\x1b[36m[Main]\x1b[0m 用户输入已注入: ${userInput}`);

      // 等待 CONVERSATION_DONE
      await bus.subscribe('main');
      const deadline = Date.now() + 180_000; // 180s timeout
      let finalResponse: string | null = null;

      while (Date.now() < deadline) {
        const msg = await bus.receive('main', 1000);
        if (!msg) continue;

        if (msg.msg_type === MessageType.CONVERSATION_DONE) {
          finalResponse = (msg.payload['message'] as string) || '';
          break;
        }

        if (msg.msg_type === MessageType.TASK_ERROR) {
          console.log(
            `\x1b[31m[Main] 检测到 TASK_ERROR: ${msg.payload['error']}\x1b[0m`,
          );
        }
      }

      await bus.unsubscribe('main');

      if (finalResponse === null) {
        console.log(`\n\x1b[31m[Main] 超时 (180s)，未收到回复\x1b[0m`);
      } else {
        console.log();
        console.log(`\x1b[1m${'='.repeat(50)}\x1b[0m`);
        console.log(`\x1b[1m[完成] 对话结束\x1b[0m`);
      }
    }
  } finally {
    // 清理
    rl.close();
    console.log('\n\x1b[90m正在关闭 Agent 系统...\x1b[0m');
    await stopAllAgents(allAgents);
    await bus.stop();
    console.log('\x1b[90m已关闭。\x1b[0m');
  }
}

// ─── HTTP API 服务模式 ──────────────────────────────────

/** 存储最近 N 条日志的环形缓冲区 */
const logRingBuffer: string[] = [];
const LOG_RING_MAX = 200;

/** 所有已连接的 WebSocket 客户端 */
const wsClients: Set<WebSocket> = new Set();

/** 广播日志到所有 WebSocket 客户端 */
function broadcastLog(text: string): void {
  const payload = JSON.stringify({ type: 'log', text, ts: Date.now() });
  for (const ws of wsClients) {
    if (ws.readyState === 1) { // ws.OPEN
      try { ws.send(payload); } catch { /* ignore */ }
    }
  }
}

/** 发送历史日志给新连接的客户端 */
function sendRecentLogs(ws: WebSocket): void {
  const recent = logRingBuffer.slice(-50);
  for (const text of recent) {
    if (ws.readyState === 1) {
      try {
        ws.send(JSON.stringify({ type: 'log', text, ts: Date.now() }));
      } catch { return; }
    }
  }
}

async function runServer(port: number): Promise<void> {
  // 动态导入 fastify（仅在 server 模式需要）
  try {
    const { default: Fastify } = await import('fastify');

    // ─── 拦截 console.log → WebSocket 日志 ──────────
    const originalLog = console.log.bind(console);
    const originalError = console.error.bind(console);
    const originalWarn = console.warn.bind(console);

    function addLog(text: string): void {
      // 存入环形缓冲区
      logRingBuffer.push(text);
      if (logRingBuffer.length > LOG_RING_MAX) {
        logRingBuffer.shift();
      }
      // 广播到所有 WebSocket 客户端
      broadcastLog(text);
    }

    console.log = (...args: unknown[]) => {
      originalLog(...args);
      addLog(args.map(String).join(' '));
    };
    console.error = (...args: unknown[]) => {
      originalError(...args);
      addLog('[ERR] ' + args.map(String).join(' '));
    };
    console.warn = (...args: unknown[]) => {
      originalWarn(...args);
      addLog('[WARN] ' + args.map(String).join(' '));
    };

    // ─── 创建共享 HTTP Server ───────────────────────
    // 先创建原生 HTTP server，让 Fastify 和 ws 共享同一个 server
    const httpServer = http.createServer();

    const app = Fastify({
      logger: false,
      // 使用 serverFactory 让 Fastify 使用我们预先创建的 HTTP server
      serverFactory: (handler) => {
        httpServer.on('request', handler);
        return httpServer;
      },
    });

    // ─── WebSocket Server ──────────────────────────
    const wss = new WebSocketServer({
      server: httpServer,
      path: '/ws',
    });

    // ─── 静态文件: GET / (index.html) ──────────────
    const __filename = fileURLToPath(import.meta.url);
    const __dirname = path.dirname(__filename);
    const publicDir = path.resolve(__dirname, '..', 'public');
    const indexPath = path.join(publicDir, 'index.html');

    app.get('/', async (_request, reply) => {
      try {
        const html = fs.readFileSync(indexPath, 'utf-8');
        return reply.type('text/html; charset=utf-8').send(html);
      } catch {
        return reply.status(404).send('index.html not found');
      }
    });

    // ─── API 端点 ──────────────────────────────────
    // GET /health
    app.get('/health', async () => ({
      status: 'ok',
      version: '0.2.0',
    }));

    // POST /chat
    app.post('/chat', async (request, reply) => {
      const body = request.body as {
        query?: string;
        image?: string;
        voice?: string;
      };

      const query = body.query || '';
      if (!query) {
        return reply.status(400).send({ error: 'query 不能为空' });
      }

      const result = await runAgentPipeline(
        query,
        body.image,
        body.voice,
      );

      if (result === null) {
        return reply.status(500).send({ error: '处理超时' });
      }

      return { response: result };
    });

    // ─── 启动服务器 ────────────────────────────────
    await app.listen({ port, host: '127.0.0.1' });

    console.log(`  ╔══════════════════════════════════════════╗`);
    console.log(`  ║  MIA Server v0.2.0                       ║`);
    console.log(`  ║  HTTP API:  http://127.0.0.1:${port}          ║`);
    console.log(`  ║  WebSocket: ws://127.0.0.1:${port}/ws        ║`);
    console.log(`  ║  Web GUI:   http://127.0.0.1:${port}          ║`);
    console.log(`  ╚══════════════════════════════════════════╝`);

    // ─── WebSocket 连接处理 ────────────────────────
    wss.on('connection', (ws: WebSocket) => {
      handleWsConnection(ws);
    });

    console.log(`  MIA HTTP API 已启动: http://127.0.0.1:${port}`);
    console.log(`  API 端点: GET /health, POST /chat, WS /ws`);
  } catch (err) {
    console.error('Fastify 启动失败:', err);
    console.error('请确保已安装 fastify: npm install fastify');
    process.exit(1);
  }
}

/**
 * 处理 WebSocket 连接 — 为每个客户端创建独立的中继器
 *
 * 每个浏览器 tab 对应一个 WebSocket 连接，
 * 拥有独立的 MessageBus 订阅和 Agent 管线会话。
 */
function handleWsConnection(ws: WebSocket): void {
  const sessionId = `ws_${crypto.randomBytes(6).toString('hex')}`;

  // 注册到客户端列表
  wsClients.add(ws);
  // 发送最近日志给新客户端
  sendRecentLogs(ws);

  console.log(`[WS] 新连接: ${sessionId}`);

  // 为每个连接创建独立的 MessageBus（WsRelay 和 pipeline 共享）
  const bus = new MessageBus(100);

  // 创建 WsRelay — 它将:
  //   1. 订阅 MessageBus 的 Agent 事件
  //   2. 转发到 WebSocket 客户端
  //   3. 接收客户端 chat/stop 消息
  //   4. 调用 runAgentPipeline 执行管线（传入共享的 bus）
  const relay = new WsRelay(ws, sessionId, async (query, imagePath, voicePath, events, signal) => {
    return runAgentPipeline(query, imagePath, voicePath, 180, events, signal, bus);
  });

  // 启动中继器（异步，不阻塞）
  relay.start().catch((err) => {
    console.error(`[WS] 中继器启动失败 (${sessionId}):`, err);
  });

  // 断线时清理
  ws.on('close', () => {
    console.log(`[WS] 连接断开: ${sessionId}`);
    wsClients.delete(ws);
    // 清理共享的 bus
    bus.stop().catch(() => {});
  });

  ws.on('error', (err: Error) => {
    console.error(`[WS] 错误 (${sessionId}):`, err.message);
  });
}

// ─── 主入口 ──────────────────────────────────────────────

async function main(): Promise<void> {
  const args = parseArgs();

  if (args.server) {
    await runServer(args.port);
  } else if (args.query) {
    await runCliQuery(args.query, args.image, args.voice);
  } else {
    await runCliInteractive(args.wechat);
  }
}

// 启动
main().catch((err) => {
  console.error('MIA 启动失败:', err);
  process.exit(1);
});
