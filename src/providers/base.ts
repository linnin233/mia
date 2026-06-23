/**
 * BaseProvider 抽象类 — 所有 LLM Provider 的统一接口
 *
 * 所有 Provider 都走 OpenAI 兼容协议，使用 openai npm SDK。
 *
 * 与 Python 版 providers/base.py 保持 1:1 语义映射。
 */

/** LLM Provider 抽象基类 */
export abstract class BaseProvider {
  /**
   * 发起对话请求 (OpenAI 兼容 streaming)
   *
   * @param messages - OpenAI 格式消息列表
   * @param model - 模型名 (undefined 则用默认)
   * @param stream - 是否流式输出
   * @param tools - function calling 工具定义
   * @param maxTokens - 最大输出 token 数
   * @param temperature - 温度参数 (0-2)
   * @returns stream=true → AsyncIterable; stream=false → ChatCompletion
   */
  abstract chat(
    messages: Array<{ role: string; content: unknown }>,
    model?: string,
    stream?: boolean,
    tools?: Array<Record<string, unknown>>,
    maxTokens?: number,
    temperature?: number,
  ): Promise<unknown>;

  /**
   * 非流式对话 — 返回完整文本内容
   *
   * 用于 Scheduler 决策等需要完整 JSON 解析的场景。
   */
  abstract chatSync(
    messages: Array<{ role: string; content: unknown }>,
    model?: string,
    tools?: Array<Record<string, unknown>>,
    maxTokens?: number,
    temperature?: number,
  ): Promise<string>;

  /**
   * 流式对话 — 返回文本 token 的异步迭代器
   *
   * 用于用户可见的回复生成，实现逐字输出的流式效果。
   * 每个 yield 返回一个文本增量 (delta)，调用方负责拼接和展示。
   */
  abstract chatStream(
    messages: Array<{ role: string; content: unknown }>,
    model?: string,
    maxTokens?: number,
    temperature?: number,
  ): AsyncGenerator<string, void, unknown>;
}
