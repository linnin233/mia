/**
 * SenderAgent — 消息发送 Agent
 *
 * 职责:
 *   1. 接收 Scheduler 的 SEND_TEXT / SEND_VOICE 指令
 *   2. 生成最终回复文本 (SEND_TEXT)
 *   3. 可选调用 MiMo TTS 生成语音 (SEND_VOICE)
 *   4. 输出到 CLI 终端
 *
 * 与 Python 版 agents/sender.py 保持 1:1 语义映射。
 */

import fs from 'node:fs';
import path from 'node:path';
import { BaseAgent } from './base.js';
import { MessageBus } from '../bus/bus.js';
import { Message, MessageType } from '../bus/message.js';
import { MiMoProvider } from '../providers/mimo.js';
import { getConfig } from '../config.js';

/**
 * SenderAgent — 生成最终回复并输出到用户界面
 */
export class SenderAgent extends BaseAgent {
  private mimo: MiMoProvider | null;
  private outputDir: string;

  constructor(
    bus: MessageBus,
    mimo: MiMoProvider | null = null,
    outputDir = 'workspace',
  ) {
    super('sender', bus);
    this.mimo = mimo;
    this.outputDir = path.resolve(outputDir);
    fs.mkdirSync(this.outputDir, { recursive: true });
  }

  /** 消息分发 */
  protected async handle(msg: Message): Promise<void> {
    switch (msg.msg_type) {
      case MessageType.SEND_TEXT:
        await this._handleSendText(msg);
        break;
      case MessageType.SEND_VOICE:
        await this._handleSendVoice(msg);
        break;
      case MessageType.STREAM_START:
        await this._handleStreamStart(msg);
        break;
      case MessageType.STREAM_CHUNK:
        await this._handleStreamChunk(msg);
        break;
      case MessageType.STREAM_END:
        await this._handleStreamEnd(msg);
        break;
    }
  }

  /** 处理文本发送指令 */
  private async _handleSendText(msg: Message): Promise<void> {
    const message = (msg.payload['message'] as string) || '';

    // 结构化展示
    const verbose = getConfig().agent.verbose;
    if (verbose) {
      console.log();
      console.log(`\x1b[32m[Sender]\x1b[0m 输出回复:`);
      console.log(`   \x1b[90m└─\x1b[0m ${message}`);
      console.log();
      console.log(`\x1b[1m${'-'.repeat(50)}\x1b[0m`);
    }

    // 通知 main 对话已完成 + MemoryAgent 存储
    await this._emitConversationDone(message, msg.session_id);
  }

  // ─── 流式输出处理 ──────────────────────────────

  /** 流式输出开始 */
  private async _handleStreamStart(_msg: Message): Promise<void> {
    const verbose = getConfig().agent.verbose;
    if (verbose) {
      console.log();
      console.log(`\x1b[32m[Sender]\x1b[0m 输出回复:`);
      process.stdout.write(`   \x1b[90m└─\x1b[0m `);
    }
  }

  /** 流式输出文本块 — 立即打印增量文本 */
  private async _handleStreamChunk(msg: Message): Promise<void> {
    const delta = (msg.payload['delta'] as string) || '';
    if (delta) {
      process.stdout.write(delta);
    }
  }

  /** 流式输出结束 */
  private async _handleStreamEnd(msg: Message): Promise<void> {
    const message = (msg.payload['message'] as string) || '';
    console.log(); // 流式结束换行

    const verbose = getConfig().agent.verbose;
    if (verbose) {
      console.log();
      console.log(`\x1b[1m${'-'.repeat(50)}\x1b[0m`);
    }

    await this._emitConversationDone(message, msg.session_id);
  }

  /** 处理语音发送指令 */
  private async _handleSendVoice(msg: Message): Promise<void> {
    const message = (msg.payload['message'] as string) || '';
    const voice = (msg.payload['voice'] as string) || '冰糖';
    const audioFormat = (msg.payload['format'] as string) || 'wav';

    if (!this.mimo) {
      // 无 TTS Provider，降级为文本
      console.warn('[Sender] MiMo Provider 未配置，降级为文本输出');
      await this._handleSendText(msg);
      return;
    }

    console.log();
    console.log(`\x1b[32m[Sender]\x1b[0m 输出语音回复 (音色: ${voice}):`);
    console.log(`   \x1b[90m├─\x1b[0m 文本: ${message}`);

    try {
      const audioBytes = await this.mimo.synthesize(
        message,
        voice,
        audioFormat,
      );

      // 保存语音文件
      const filename = `reply_${msg.msg_id}.${audioFormat}`;
      const filepath = path.join(this.outputDir, filename);
      fs.writeFileSync(filepath, audioBytes);

      console.log(`   \x1b[90m└─\x1b[0m 语音文件: ${filepath}`);
      console.log();
      console.log(`\x1b[1m${'-'.repeat(50)}\x1b[0m`);
    } catch (err) {
      console.error(`[Sender] TTS 合成失败:`, err);
      console.log(`   \x1b[90m└─\x1b[0m \x1b[31m语音合成失败: ${err}\x1b[0m`);
      console.log(`   \x1b[90m└─\x1b[0m 降级为文本: ${message}`);
      console.log();
      console.log(`\x1b[1m${'-'.repeat(50)}\x1b[0m`);
    }

    await this._emitConversationDone(message, msg.session_id);
  }

  /**
   * 发送 CONVERSATION_DONE 给 main 和 memory_agent
   * 双目标投递确保主循环退出 + 记忆存储
   */
  private async _emitConversationDone(
    message: string,
    sessionId?: string,
  ): Promise<void> {
    const doneMsg: Message = {
      msg_type: MessageType.CONVERSATION_DONE,
      source: this.name,
      target: 'main',
      payload: { message },
      msg_id: Date.now().toString(16),
      timestamp: Date.now(),
      session_id: sessionId,
    };

    // 通知 main
    await this.bus.publish(doneMsg);

    // 通知 MemoryAgent (双目标)
    await this.bus.publish({
      ...doneMsg,
      target: 'memory_agent',
      msg_id: Date.now().toString(16) + 'm',
    });
  }
}
