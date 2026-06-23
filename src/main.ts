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
import readline from 'node:readline';
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
 * @returns 最终回复文本
 */
async function runAgentPipeline(
  query: string,
  imagePath?: string,
  voicePath?: string,
  timeoutSec = 180,
): Promise<string | null> {
  const config = getConfig();
  const providers = createProviders(config);
  const sessionId = crypto.randomBytes(6).toString('hex');
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

    console.log(`\x1b[36m[Main]\x1b[0m 用户输入已注入: ${query.slice(0, 100)}`);

    // 等待 CONVERSATION_DONE
    await bus.subscribe('main');
    const deadline = Date.now() + timeoutSec * 1000;

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

    if (finalResponse === null) {
      console.log(`\n\x1b[31m[Main] 超时 (${timeoutSec}s)，未收到回复\x1b[0m`);
    }
  } finally {
    // 清理
    await stopAllAgents(allAgents);
    await bus.stop();
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

async function runServer(port: number): Promise<void> {
  // 动态导入 fastify（仅在 server 模式需要）
  try {
    const { default: Fastify } = await import('fastify');

    const app = Fastify({
      logger: false, // pino 日志在后台，不干扰终端
    });

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

    console.log(`  MIA HTTP API 已启动: http://127.0.0.1:${port}`);
    console.log(`  API 端点: GET /health, POST /chat`);

    await app.listen({ port, host: '127.0.0.1' });
    console.log(`  HTTP 服务器已关闭`);
  } catch (err) {
    console.error('Fastify 启动失败:', err);
    console.error('请确保已安装 fastify: npm install fastify');
    process.exit(1);
  }
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
