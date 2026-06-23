/**
 * MIA Ink TUI — 启动入口
 *
 * npm run tui                     # 启动 TUI 模式
 * npm run tui -- --wechat         # TUI + 微信通道
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

/**
 * 启动 MIA Ink TUI
 */
export async function runTui(_enableWechat = false): Promise<void> {
  const config = getConfig();
  const rt = config.runtime;

  // 创建 Provider
  const mimoKey = rt.provider_api_keys['mimo'] || config.mimo.api_key;
  const deepseekKey = rt.provider_api_keys['deepseek'] || config.deepseek.api_key;

  if (!mimoKey) {
    console.error('MIMO_API_KEY 未配置。请在 .env 文件中设置。');
    process.exit(1);
  }

  const mimo = new MiMoProvider(mimoKey);
  const deepseek = deepseekKey ? new DeepSeekProvider(deepseekKey) : null;

  // 创建 MessageBus
  const bus = new MessageBus(100);
  await bus.start();

  // 设置镜像订阅
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

  // 确保 tui 订阅了 MessageBus（接收 TUI 消息）
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

  // 处理用户输入
  let quitResolve: (() => void) | null = null;

  const handleSubmit = (text: string) => {
    const sessionId = crypto.randomBytes(6).toString('hex');
    const rawMsg = makeRawInput(text, [], sessionId);
    bus.publish(rawMsg);
  };

  const handleQuit = () => {
    if (quitResolve) quitResolve();
  };

  // 渲染 Ink App
  const { unmount } = render(
    React.createElement(App, {
      bus,
      memoryAgent,
      onSubmit: handleSubmit,
      onQuit: handleQuit,
    }),
  );

  // 等待退出
  await new Promise<void>((resolve) => {
    quitResolve = resolve;
  });

  // 清理
  unmount();
  for (const agent of allAgents) {
    await agent.stop();
  }
  await bus.stop();
}
