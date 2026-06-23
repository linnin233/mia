/**
 * ReceiverAgent — 消息接收 Agent
 *
 * 职责:
 *   1. 接收 CLI/API 传来的原始用户输入 (RAW_INPUT)
 *   2. 检测输入类型 (text / image / voice)
 *   3. 调用 MiMo VL/ASR 理解内容
 *   4. 产出标准化的 USER_INTENT 发送给 MemoryAgent → Scheduler
 *
 * 与 Python 版 agents/receiver.py 保持 1:1 语义映射。
 */

import fs from 'node:fs';
import path from 'node:path';
import { BaseAgent } from './base.js';
import { MessageBus } from '../bus/bus.js';
import { Message, MessageType, makeUserIntent } from '../bus/message.js';
import { MiMoProvider } from '../providers/mimo.js';
import { getConfig } from '../config.js';

/** 文件扩展名 → MIME 类型映射 */
const MIME_MAP: Record<string, string> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

/** 音频 MIME 映射 */
const AUDIO_MIME_MAP: Record<string, string> = {
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.m4a': 'audio/mp4',
  '.ogg': 'audio/ogg',
};

/**
 * ReceiverAgent — 理解用户输入并转为标准意图
 *
 * 支持的输入类型:
 *   - text: 纯文本，直接作为意图传递
 *   - image: 图片路径，调用 MiMo VL 理解图片内容
 *   - voice: 音频路径，调用 MiMo 多模态音频理解
 */
export class ReceiverAgent extends BaseAgent {
  private mimo: MiMoProvider;

  constructor(bus: MessageBus, mimo: MiMoProvider) {
    super('receiver', bus);
    this.mimo = mimo;
  }

  /** 处理 RAW_INPUT 消息 */
  protected async handle(msg: Message): Promise<void> {
    if (msg.msg_type !== MessageType.RAW_INPUT) return;

    const sessionId = msg.session_id;

    // 分析输入内容
    const text = (msg.payload['text'] as string) || '';
    const imagePath = msg.payload['image'] as string | undefined;
    const voicePath = msg.payload['voice'] as string | undefined;

    const intentParts: string[] = [];
    const mediaRefs: string[] = [];

    const hasText = Boolean(text && text.trim());

    // ─── 处理图片 ───────────────────────────────
    if (imagePath) {
      mediaRefs.push(imagePath);
      const imgDesc = await this._understandImage(imagePath, text);
      if (imgDesc) {
        intentParts.push(`图片内容: ${imgDesc}`);
      }
    }

    // ─── 处理语音 (多模态理解) ──────────────────
    if (voicePath) {
      mediaRefs.push(voicePath);
      const voiceUnderstanding = await this._understandAudio(voicePath, text);
      if (voiceUnderstanding) {
        if (hasText) {
          intentParts.push(`用户说: ${text}`);
          intentParts.push(`语音内容: ${voiceUnderstanding}`);
        } else {
          // 纯语音输入: 告诉 Scheduler 直接回应
          intentParts.push(
            `用户发送了一段语音消息，请直接基于以下理解回复用户` +
            `（把转写内容视为用户亲口说的话，不要分析语音本身）:\n` +
            voiceUnderstanding,
          );
        }
      }
    } else if (hasText) {
      // 无语音: 纯文本输入
      intentParts.push(`用户说: ${text}`);
    }

    // ─── 构建 USER_INTENT ────────────────────────
    if (intentParts.length === 0) {
      intentParts.push('用户发送了空消息');
    }

    const fullIntent = intentParts.join('\n');

    // 透传微信渠道的 context_token 和 to_user_id
    const contextToken = (msg.payload['context_token'] as string) || '';
    const toUserId = (msg.payload['to_user_id'] as string) || '';

    // 结构化展示 (verbose 模式)
    const verbose = getConfig().agent.verbose;
    if (verbose) {
      console.log(`\x1b[35m[Receiver]\x1b[0m 理解用户输入`);
      console.log(`   \x1b[90m├─\x1b[0m 原始输入: ${text || '(无文本)'}`);
      if (imagePath) console.log(`   \x1b[90m├─\x1b[0m 图片: ${imagePath}`);
      if (voicePath) console.log(`   \x1b[90m├─\x1b[0m 语音: ${voicePath}`);
      console.log(`   \x1b[90m└─\x1b[0m 意图: ${fullIntent}`);
    }

    // 发送到 MemoryAgent → Scheduler（透传渠道元数据）
    const intentMsg = makeUserIntent(
      text || '',
      fullIntent,
      mediaRefs,
      sessionId,
      contextToken,
      toUserId,
    );
    await this.send(intentMsg);
  }

  // ─── 私有方法 ─────────────────────────────────

  /**
   * 调用 MiMo VL 理解图片内容
   *
   * @param imagePath - 图片文件路径 或 URL
   * @param context - 用户同时发送的文本
   * @returns 图片描述文本，失败返回 null
   */
  private async _understandImage(
    imagePath: string,
    context = '',
  ): Promise<string | null> {
    try {
      let imageData: string;

      // 判断是 URL 还是本地文件
      if (imagePath.startsWith('http://') || imagePath.startsWith('https://')) {
        imageData = imagePath;
      } else {
        if (!fs.existsSync(imagePath)) {
          console.error(`[Receiver] 图片文件不存在: ${imagePath}`);
          return null;
        }
        const ext = path.extname(imagePath).toLowerCase();
        const mimeType = MIME_MAP[ext] || 'image/png';
        imageData = await MiMoProvider.encodeImageFile(imagePath, mimeType);
      }

      const prompt =
        `请详细描述这张图片的内容。${context ? '用户同时说: ' + context : ''}`;
      const description = await this.mimo.understandImage(imageData, prompt);
      return description;
    } catch (err) {
      console.error(`[Receiver] 图片理解失败:`, err);
      return `[图片理解失败: ${err}]`;
    }
  }

  /**
   * 多模态音频理解 — 使用 MiMo-V2.5 原生理解音频内容、情感和意图
   *
   * 与纯 ASR 的区别:
   *   - ASR: 只做文字转写
   *   - 多模态: 同时理解内容、情绪、语气、意图
   *
   * @param voicePath - 音频文件路径
   * @param context - 用户同时发送的文本
   * @returns 音频理解文本，失败返回 null
   */
  private async _understandAudio(
    voicePath: string,
    context = '',
  ): Promise<string | null> {
    try {
      if (!fs.existsSync(voicePath)) {
        console.error(`[Receiver] 音频文件不存在: ${voicePath}`);
        return null;
      }

      const ext = path.extname(voicePath).toLowerCase();
      const mimeType = AUDIO_MIME_MAP[ext] || 'audio/wav';
      const audioData = await MiMoProvider.encodeAudioFile(voicePath, mimeType);

      // 构建多模态理解 prompt
      const contextHint = context ? `用户同时输入了文字: ${context}` : '';
      const prompt =
        `请理解这段语音内容。${contextHint}\n` +
        `请完成以下任务:\n` +
        `1. 转写语音的文字内容\n` +
        `2. 分析说话人的情绪状态（如高兴、焦虑、愤怒、平静等）\n` +
        `3. 判断说话人的意图和目的\n` +
        `请简洁回复，直接给出分析结果。`;

      const understanding = await this.mimo.understandAudio(audioData, prompt);
      return understanding;
    } catch (err) {
      console.error(`[Receiver] 多模态音频理解失败: ${err}，降级为纯 ASR`);
      // 降级: 多模态失败时回退到纯 ASR 转写
      try {
        const audioData = await MiMoProvider.encodeAudioFile(voicePath, 'audio/wav');
        const text = await this.mimo.transcribe(audioData);
        return `[降级转写] ${text}`;
      } catch (err2) {
        console.error(`[Receiver] ASR 降级也失败:`, err2);
        return `[音频理解失败: ${err}]`;
      }
    }
  }
}
