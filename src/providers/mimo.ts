/**
 * MiMo Provider — 封装 Xiaomi MiMo 平台的 API
 *
 * 支持的模型:
 *   - mimo-v2.5-pro  : 旗舰文本模型 (1M context, reasoning)
 *   - mimo-v2.5      : 多模态模型 (图片/音频/视频理解)
 *   - mimo-v2.5-asr  : 语音识别 (audio → text)
 *   - mimo-v2.5-tts  : 语音合成 (text → audio)
 *
 * 认证方式: api-key header (OpenAI 兼容)
 *
 * 与 Python 版 providers/mimo.py 保持 1:1 语义映射。
 */

import fs from 'node:fs/promises';
import OpenAI from 'openai';
import type { ChatCompletionMessageParam } from 'openai/resources/chat/completions';
import { BaseProvider } from './base.js';

/** 默认模型常量 */
const CHAT_MODEL = 'mimo-v2.5-pro';       // 文本推理
const VISION_MODEL = 'mimo-v2.5';          // 多模态 (图片/音频/视频)
const ASR_MODEL = 'mimo-v2.5-asr';         // 语音识别
const TTS_MODEL = 'mimo-v2.5-tts';         // 语音合成

/** 默认 TTS 音色 */
const DEFAULT_VOICE = '冰糖';

/**
 * MiMoProvider — Xiaomi MiMo Platform API 封装
 *
 * 自动识别 API Key 类型:
 *   - tp- 开头 → Token Plan 网关 (token-plan-cn.xiaomimimo.com)
 *   - sk- 开头 → 按量付费网关 (api.xiaomimimo.com)
 */
export class MiMoProvider extends BaseProvider {
  private client: OpenAI;

  constructor(
    apiKey: string,
    baseUrl?: string,
  ) {
    super();

    // 自动识别 base URL
    if (!baseUrl) {
      if (apiKey.startsWith('tp-')) {
        baseUrl = 'https://token-plan-cn.xiaomimimo.com/v1';
      } else {
        baseUrl = 'https://api.xiaomimimo.com/v1';
      }
    }

    this.client = new OpenAI({
      apiKey,
      baseURL: baseUrl,
      timeout: 120_000,     // 120s total
      maxRetries: 2,
    });
  }

  // ─── 对话 (Chat) — 实现 BaseProvider 接口 ──────────────────

  /**
   * 流式/非流式对话 — OpenAI 兼容
   *
   * @returns stream=true → Stream; stream=false → ChatCompletion
   */
  async chat(
    messages: Array<{ role: string; content: unknown }>,
    model?: string,
    stream = true,
    tools?: Array<Record<string, unknown>>,
    maxTokens = 4096,
    temperature = 0.7,
  ): Promise<unknown> {
    const params: OpenAI.Chat.Completions.ChatCompletionCreateParams = {
      model: model || CHAT_MODEL,
      messages: messages as ChatCompletionMessageParam[],
      stream,
      max_tokens: maxTokens,
      temperature,
    };

    if (tools) {
      params.tools = tools as unknown as OpenAI.Chat.Completions.ChatCompletionTool[];
      params.tool_choice = 'auto';
    }

    // 注意: 不再传 thinking: disabled
    // Anthropic 格式 {"thinking": {"type": "disabled"}} 在 MiMo OpenAI 兼容端点下
    // 可能被忽略或触发兼容性问题 (400 Param Incorrect)

    return this.client.chat.completions.create(params, {
      ...(stream ? { stream: true as const } : { stream: false as const }),
    });
  }

  /** 非流式对话 — 返回完整文本 */
  async chatSync(
    messages: Array<{ role: string; content: unknown }>,
    model?: string,
    tools?: Array<Record<string, unknown>>,
    maxTokens = 4096,
    temperature = 0.7,
  ): Promise<string> {
    const response = (await this.chat(
      messages, model, false, tools, maxTokens, temperature,
    )) as OpenAI.Chat.Completions.ChatCompletion;

    return response.choices[0]?.message?.content || '';
  }

  /** 流式对话 — 逐 token 返回文本增量 */
  async *chatStream(
    messages: Array<{ role: string; content: unknown }>,
    model?: string,
    maxTokens = 4096,
    temperature = 0.7,
  ): AsyncGenerator<string, void, unknown> {
    const stream = (await this.chat(
      messages, model, true, undefined, maxTokens, temperature,
    )) as AsyncIterable<OpenAI.Chat.Completions.ChatCompletionChunk>;

    for await (const chunk of stream) {
      const delta = chunk.choices?.[0]?.delta?.content;
      if (delta) {
        yield delta;
      }
    }
  }

  // ─── 图片理解 (Vision) ───────────────────────────────────────

  /**
   * 图片理解 — 支持 URL 或 Base64 图片
   *
   * @param imageData - 图片 URL 或 data:image/xxx;base64,... 格式
   * @param prompt - 理解问题/指令
   * @param model - 模型名 (默认 mimo-v2.5)
   * @returns 模型对图片的描述文本
   */
  async understandImage(
    imageData: string,
    prompt = '请详细描述这张图片的内容',
    model?: string,
  ): Promise<string> {
    const messages: ChatCompletionMessageParam[] = [
      {
        role: 'user',
        content: [
          { type: 'image_url', image_url: { url: imageData } },
          { type: 'text', text: prompt },
        ],
      },
    ];

    const response = await this.client.chat.completions.create({
      model: model || VISION_MODEL,
      messages,
      max_tokens: 1024,
      stream: false,
    });

    return response.choices[0]?.message?.content || '';
  }

  /**
   * 将本地图片文件编码为 base64 data URL
   *
   * @param filePath - 图片文件路径
   * @param mimeType - MIME 类型 (默认 image/png)
   * @returns data:image/xxx;base64,... 格式字符串
   */
  static async encodeImageFile(
    filePath: string,
    mimeType = 'image/png',
  ): Promise<string> {
    const data = await fs.readFile(filePath);
    const b64 = Buffer.from(data).toString('base64');
    return `data:${mimeType};base64,${b64}`;
  }

  // ─── 语音识别 (ASR) — 单独 ASR 模型 ──────────────────────────

  /**
   * 语音识别 — 使用专用 ASR 模型将音频转为纯文本
   *
   * 注意: 此方法使用 mimo-v2.5-asr 模型，只做文字转写。
   * 如需理解语气/情感/意图，请使用 understandAudio()。
   *
   * @param audioData - data:audio/xxx;base64,... 格式的音频
   * @param language - 语种 (auto/zh/en)
   * @returns 识别出的文本
   */
  async transcribe(
    audioData: string,
    language = 'auto',
  ): Promise<string> {
    // input_audio 是 MiMo 特有扩展类型，OpenAI SDK 类型定义不包含
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const messages = [
      {
        role: 'user' as const,
        content: [{
          type: 'input_audio',
          input_audio: { data: audioData },
        }],
      },
    ] as any[];

    const response = await this.client.chat.completions.create(
      {
        model: ASR_MODEL,
        messages,
        stream: false,
      } as OpenAI.Chat.Completions.ChatCompletionCreateParams,
      {
        body: { asr_options: { language } },
      },
    );

    return (response as OpenAI.Chat.Completions.ChatCompletion)
      .choices[0]?.message?.content || '';
  }

  // ─── 多模态音频理解 (MiMo-V2.5 原生) ──────────────────────

  /**
   * 多模态音频理解 — 使用 MiMo-V2.5 原生理解音频内容、语气、情感和意图
   *
   * 与 transcribe() 的区别:
   *   - transcribe() 用专用 ASR 模型 (mimo-v2.5-asr)，只做文字转写
   *   - understandAudio() 用多模态模型 (mimo-v2.5)，可以同时理解:
   *     · 文字内容 (转写)
   *     · 说话人情绪
   *     · 语气/语调
   *     · 意图/目的
   *     · 背景信息
   *
   * MiMo-V2.5 有 261M 参数的 Audio Transformer，可以原生理解音频，
   * 不需要先转文字再分析的两步流程。
   *
   * @param audioData - data:audio/xxx;base64,... 格式的音频
   * @param prompt - 理解指令 (可以要求转写、总结、分析情绪等)
   * @param model - 模型名 (默认 mimo-v2.5 多模态)
   * @returns 模型对音频的理解文本 (包含转写内容和分析)
   */
  async understandAudio(
    audioData: string,
    prompt = '请转写这段语音的内容，并分析说话人的情绪和意图。',
    model?: string,
  ): Promise<string> {
    // input_audio 是 MiMo 特有扩展类型，OpenAI SDK 类型定义不包含
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const messages = [
      {
        role: 'user' as const,
        content: [
          { type: 'input_audio', input_audio: { data: audioData } },
          { type: 'text', text: prompt },
        ],
      },
    ] as any[];

    const response = await this.client.chat.completions.create({
      model: model || VISION_MODEL,  // mimo-v2.5 多模态
      messages,
      max_tokens: 1024,
      stream: false,
    } as OpenAI.Chat.Completions.ChatCompletionCreateParams);

    return (response as OpenAI.Chat.Completions.ChatCompletion)
      .choices[0]?.message?.content || '';
  }

  /**
   * 将本地音频文件编码为 base64 data URL
   *
   * @param filePath - 音频文件路径
   * @param mimeType - MIME 类型 (默认 audio/wav)
   * @returns data:audio/xxx;base64,... 格式字符串
   */
  static async encodeAudioFile(
    filePath: string,
    mimeType = 'audio/wav',
  ): Promise<string> {
    const data = await fs.readFile(filePath);
    const b64 = Buffer.from(data).toString('base64');
    return `data:${mimeType};base64,${b64}`;
  }

  // ─── 语音合成 (TTS) ──────────────────────────────────────────

  /**
   * 语音合成 — 将文本转为语音音频 (非流式)
   *
   * @param text - 要合成的文本 (放在 assistant 消息中)
   * @param voice - 音色 ID (冰糖/茉莉/苏打/白桦/Mia/Chloe/Milo/Dean)
   * @param audioFormat - 输出格式 (wav/pcm16)
   * @param instructions - 风格控制指令 (放在 user 消息中)
   * @returns 音频文件的二进制数据 (WAV 或 PCM16)
   */
  async synthesize(
    text: string,
    voice?: string,
    audioFormat = 'wav',
    instructions?: string,
  ): Promise<Buffer> {
    const messages: ChatCompletionMessageParam[] = [
      { role: 'user', content: instructions || '' },
      { role: 'assistant', content: text },
    ];

    const response = await this.client.chat.completions.create(
      {
        model: TTS_MODEL,
        messages,
        stream: false,
      },
      {
        body: {
          audio: {
            format: audioFormat,
            voice: voice || DEFAULT_VOICE,
          },
        },
      },
    );

    // TTS 返回的 audio data 是 base64 编码的
    const chatResponse = response as OpenAI.Chat.Completions.ChatCompletion & {
      choices: Array<{
        message: { audio?: { data?: string } };
      }>;
    };
    const audioB64 = chatResponse.choices[0]?.message?.audio?.data;
    if (!audioB64) {
      throw new Error('TTS 响应中未找到音频数据');
    }
    return Buffer.from(audioB64, 'base64');
  }

  /**
   * 流式语音合成 — 边生成边返回 PCM16 音频片段
   *
   * @param text - 要合成的文本
   * @param voice - 音色 ID
   * @param instructions - 风格控制指令
   * @yields 逐块 PCM16 音频数据
   */
  async *synthesizeStream(
    text: string,
    voice?: string,
    instructions?: string,
  ): AsyncGenerator<Buffer, void, unknown> {
    const messages: ChatCompletionMessageParam[] = [
      { role: 'user', content: instructions || '' },
      { role: 'assistant', content: text },
    ];

    const stream = (await this.client.chat.completions.create(
      {
        model: TTS_MODEL,
        messages,
        stream: true,
      },
      {
        body: {
          audio: {
            format: 'pcm16',
            voice: voice || DEFAULT_VOICE,
          },
        },
      },
    )) as AsyncIterable<OpenAI.Chat.Completions.ChatCompletionChunk & {
      choices: Array<{
        delta: { audio?: { data?: string } };
      }>;
    }>;

    for await (const chunk of stream) {
      const audio = chunk.choices?.[0]?.delta?.audio;
      if (audio?.data) {
        yield Buffer.from(audio.data, 'base64');
      }
    }
  }
}
