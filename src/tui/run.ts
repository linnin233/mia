/**
 * MIA Ink TUI — 启动入口
 *
 * npm run tui              # 启动 TUI 模式
 *
 * 关键设计：TUI 模式下压制所有 console.log，
 * Agent 输出通过 MessageBus → dispatch → React state → Ink 渲染。
 */

import React from 'react';
import { render } from 'ink';
import crypto from 'node:crypto';
import { App } from './app.js';
import { MessageBus } from '../bus/bus.js';
import {
  MessageType,
  makeRawInput,
} from '../bus/message.js';
import { getConfig } from '../config.js';
import { MiMoProvider } from '../providers/mimo.js';
import { DeepSeekProvider } from '../providers/deepseek.js';
import { ReceiverAgent } from '../agents/receiver.js';
import { SchedulerAgent } from '../agents/scheduler.js';
import { SenderAgent } from '../agents/sender.js';
import { TaskAgent } from '../agents/task.js';
import { MemoryAgent } from '../agents/memory.js';
import { MemoryStore } from '../memory/store.js';
import { BaseAgent } from '../agents/base.js';

// ─── Console 抑制 ───────────────────────────────────────

/** 备份原始 console 方法 */
const _origLog = console.log;
const _origWarn = console.warn;
const _origError = console.error;

/** 在 TUI 模式下压制 console 输出（避免冲破 Ink 布局） */
function suppressConsole() {
  const noop = () => {};
  console.log = noop;
  console.warn = noop;
  // error 保留给真正的崩溃信息
}

function restoreConsole() {
  console.log = _origLog;
  console.warn = _origWarn;
  console.error = _origError;
}

// ─── 消息监听（Agent 输出 → TUI 状态） ──────────────────

/**
 * 监听 Agent 管道的所有输出消息，通过 bridge 回调传给 TUI
 */
interface TuiBridge {
  onUserMessage: (text: string) => void;
  onStreamStart: () => void;
  onStreamChunk: (delta: string) => void;
  onStreamEnd: (fullText: string) => void;
  onSendText: (text: string) => void;
  onThought: (agent: string, title: string, detail: string) => void;
}

function setupTuiBridge(bus: MessageBus, bridge: TuiBridge) {
  // 在后台轮询 tui 队列
  const poll = async () => {
    while (true) {
      const msg = await bus.receive('tui', 200);
      if (!msg) continue;

      switch (msg.msg_type) {
        case MessageType.STREAM_START:
          bridge.onStreamStart();
          break;

        case MessageType.STREAM_CHUNK: {
          const delta = (msg.payload['delta'] as string) || '';
          if (delta) bridge.onStreamChunk(delta);
          break;
        }

        case MessageType.STREAM_END: {
          const fullText = (msg.payload['message'] as string) || '';
          bridge.onStreamEnd(fullText);
          break;
        }

        case MessageType.SEND_TEXT: {
          const text = (msg.payload['message'] as string) || '';
          bridge.onSendText(text);
          break;
        }

        case MessageType.TUI_THOUGHT: {
          bridge.onThought(
            (msg.payload['agent'] as string) || '',
            (msg.payload['title'] as string) || '',
            (msg.payload['detail'] as string) || '',
          );
          break;
        }
      }
    }
  };

  // 启动后台轮询（不阻塞）
  poll();
}

// 不需要特殊子类，直接使用普通 SenderAgent 即可
// 所有消息通过 MessageBus 的 mirror 投递给 'tui'，由 setupTuiBridge 处理

// ─── 启动函数 ───────────────────────────────────────────

export async function runTui(_enableWechat = false): Promise<void> {
  suppressConsole(); // 关键：压制 Agent 的 console.log 输出

  const config = getConfig();
  const rt = config.runtime;

  // 创建 Provider
  const mimoKey = rt.provider_api_keys['mimo'] || config.mimo.api_key;
  const deepseekKey = rt.provider_api_keys['deepseek'] || config.deepseek.api_key;

  if (!mimoKey) {
    restoreConsole();
    console.error('MIMO_API_KEY 未配置。请在 .env 文件中设置。');
    process.exit(1);
  }

  const mimo = new MiMoProvider(mimoKey);
  const deepseek = deepseekKey ? new DeepSeekProvider(deepseekKey) : null;

  // 创建 MessageBus
  const bus = new MessageBus(100);
  await bus.start();

  // ─── 设置全部镜像 ────────────────────────────
  const mirrorTypes = [
    MessageType.USER_INTENT,
    MessageType.SEND_TEXT,
    MessageType.STREAM_START,
    MessageType.STREAM_CHUNK,
    MessageType.STREAM_END,
    MessageType.EXECUTE_TASK,
    MessageType.TASK_RESULT,
    MessageType.TASK_ERROR,
    MessageType.CONVERSATION_DONE,
    MessageType.TUI_THOUGHT,
    MessageType.TUI_TOOL,
    MessageType.TUI_STATUS,
  ];
  for (const mt of mirrorTypes) {
    bus.subscribeMirror(mt, 'memory_agent');
    bus.subscribeMirror(mt, 'tui'); // 所有消息也镜像给 tui
  }

  // 确保 tui 订阅了 MessageBus（接收消息）
  await bus.subscribe('tui');

  // 创建 Agent
  const receiver = new ReceiverAgent(bus, mimo);
  const scheduler = new SchedulerAgent(
    bus, mimo, rt.scheduler_model,
    deepseek || undefined, rt.scheduler_fallback || undefined,
    config.agent.enable_streaming,
  );
  const sender = new SenderAgent(
    bus,
    rt.sender_tts_enabled ? mimo : null,
    config.agent.workspace_dir,
  );
  const taskAgent = new TaskAgent(
    bus, mimo, undefined, rt.task_model,
    deepseek || undefined, rt.task_fallback || undefined,
  );
  const memoryAgent = new MemoryAgent(
    bus, mimo, new MemoryStore(), rt.memory_model,
    deepseek || undefined, rt.memory_fallback || undefined,
  );

  const allAgents: BaseAgent[] = [
    receiver, memoryAgent, scheduler, sender, taskAgent,
  ];

  // 启动所有 Agent
  for (const agent of allAgents) {
    await agent.start();
  }
  await new Promise((r) => setTimeout(r, 300));

  // ─── TUI Bridge ──────────────────────────────
  // 这些回调会在 App 组件挂载后被赋值
  const bridge: TuiBridge = {
    onUserMessage: () => {},
    onStreamStart: () => {},
    onStreamChunk: () => {},
    onStreamEnd: () => {},
    onSendText: () => {},
    onThought: () => {},
  };

  setupTuiBridge(bus, bridge);

  // 处理用户输入 → Agent 管道
  const handleSubmit = (text: string) => {
    bridge.onUserMessage(text);
    const sessionId = crypto.randomBytes(6).toString('hex');
    const rawMsg = makeRawInput(text, [], sessionId);
    bus.publish(rawMsg);
  };

  // ─── 渲染 Ink App ────────────────────────────
  let quitResolve: (() => void) | null = null;

  const { unmount, waitUntilExit } = render(
    React.createElement(App, {
      memoryAgent,
      bridge,
      onSubmit: handleSubmit,
      onQuit: () => { if (quitResolve) quitResolve(); },
    }),
  );

  // 等待退出
  await new Promise<void>((resolve) => { quitResolve = resolve; });

  // 清理
  unmount();
  try { await waitUntilExit(); } catch { /* ignore */ }
  for (const agent of allAgents) {
    await agent.stop();
  }
  await bus.stop();
  restoreConsole();
}
