/**
 * DeepSeek Provider — 备选 LLM Provider
 *
 * 当 MiMo API 不可用时作为 fallback。
 * 走标准 OpenAI 兼容协议。
 *
 * 与 Python 版 providers/deepseek.py 保持 1:1 语义映射。
 */

import OpenAI from 'openai';
import type { ChatCompletionMessageParam } from 'openai/resources/chat/completions';
import { BaseProvider } from './base.js';

/** DeepSeek API 默认配置 */
const BASE_URL = 'https://api.deepseek.com/v1';
const CHAT_MODEL = 'deepseek-chat';

/**
 * DeepSeekProvider — DeepSeek API 封装
 *
 * 用于 Scheduler 和 TaskAgent 的备选 Provider。
 */
export class DeepSeekProvider extends BaseProvider {
  private client: OpenAI;

  constructor(
    apiKey: string,
    baseUrl?: string,
  ) {
    super();
    this.client = new OpenAI({
      apiKey,
      baseURL: baseUrl || BASE_URL,
    });
  }

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
}
