/**
 * WsRelay — WebSocket 中继器
 *
 * 桥接 MIA Agent 管线与 WebSocket 客户端，实现实时双向通信。
 *
 * 职责:
 *   1. 接收 WebSocket 客户端的 chat/stop 消息
 *   2. 调用 runAgentPipeline 并通过 PipelineEventCallbacks 获取实时事件
 *   3. 将事件转发为 WebSocket JSON 消息给客户端
 *   4. 管理连接生命周期（连接、断线、超时清理）
 *
 * 注意: WsRelay 不直接订阅 MessageBus — 所有 Agent 事件通过
 * PipelineEventCallbacks 获取，避免重复投递。
 *
 * WebSocket 协议:
 *
 *   Server → Client:
 *     { type: "connected", sessionId: "...", ts: ... }
 *     { type: "stream_start", ts: ... }
 *     { type: "stream_chunk", delta: "...", ts: ... }
 *     { type: "stream_end", fullMessage: "...", ts: ... }
 *     { type: "thought", agent: "...", title: "...", detail: "...", ts: ... }
 *     { type: "tool", name: "...", status: "running"|"success"|"error", args: "...", result: "...", ts: ... }
 *     { type: "log", text: "...", ts: ... }
 *     { type: "done", ts: ... }
 *     { type: "error", message: "...", ts: ... }
 *
 *   Client → Server:
 *     { type: "chat", query: "...", image?: "...", voice?: "..." }
 *     { type: "stop" }
 */

import type { WebSocket } from 'ws';

// ─── 事件类型定义 ────────────────────────────────────────

/** Server → Client 事件 */
export type WsServerEvent =
  | { type: 'connected'; sessionId: string; ts: number }
  | { type: 'stream_start'; ts: number }
  | { type: 'stream_chunk'; delta: string; ts: number }
  | { type: 'stream_end'; fullMessage: string; ts: number }
  | { type: 'thought'; agent: string; title: string; detail: string; ts: number }
  | { type: 'tool'; name: string; status: 'running' | 'success' | 'error'; args: string; result: string; ts: number }
  | { type: 'log'; text: string; ts: number }
  | { type: 'done'; ts: number }
  | { type: 'error'; message: string; ts: number };

/** Client → Server 事件 */
export type WsClientEvent =
  | { type: 'chat'; query: string; image?: string; voice?: string }
  | { type: 'stop' };

/** WebSocket readyState 常量 (ws 库) */
const WS_OPEN = 1;

// ─── PipelineEventCallbacks ───────────────────────────────

/**
 * Agent 管线事件回调接口
 *
 * 由 runAgentPipeline 在关键事件发生时调用，
 * 调用方（如 WsRelay）通过这些回调获取实时事件。
 */
export interface PipelineEventCallbacks {
  onStreamStart?: () => void;
  onStreamChunk?: (delta: string) => void;
  onStreamEnd?: (fullMessage: string) => void;
  onThought?: (agent: string, title: string, detail: string) => void;
  onTool?: (name: string, status: 'running' | 'success' | 'error', args: string, result: string) => void;
  onDone?: () => void;
  onError?: (error: string) => void;
}

/** 管线执行函数签名 — 由外部注入 */
export type PipelineRunner = (
  query: string,
  imagePath: string | undefined,
  voicePath: string | undefined,
  events: PipelineEventCallbacks,
  signal: AbortSignal,
) => Promise<string | null>;

// ─── WsRelay ─────────────────────────────────────────────

/**
 * WebSocket 中继器 — 管理单个客户端连接的完整生命周期
 *
 * 每个 WebSocket 连接创建一个 WsRelay 实例。
 * 不直接订阅 MessageBus，所有 Agent 事件通过 PipelineEventCallbacks 获取。
 */
export class WsRelay {
  private ws: WebSocket;
  private sessionId: string;
  private alive = true;

  /** 用于取消当前 Agent 管线的 AbortController */
  private abortController: AbortController | null = null;

  /** Agent 管线执行函数（由外部注入） */
  private runPipeline: PipelineRunner;

  constructor(
    ws: WebSocket,
    sessionId: string,
    runPipeline: PipelineRunner,
  ) {
    this.ws = ws;
    this.sessionId = sessionId;
    this.runPipeline = runPipeline;
  }

  /**
   * 启动中继器 — 发送 connected 事件并开始监听客户端消息
   */
  async start(): Promise<void> {
    // 发送连接成功事件
    this._send({ type: 'connected', sessionId: this.sessionId });

    // 监听 WebSocket 客户端消息
    this.ws.on('message', (data: Buffer) => {
      this._handleClientMessage(data);
    });

    // 断线清理
    this.ws.on('close', () => {
      this._cleanup();
    });
  }

  // ─── 客户端消息处理 ────────────────────────────────

  /** 处理 WebSocket 客户端发来的消息 */
  private _handleClientMessage(data: Buffer): void {
    let parsed: WsClientEvent;
    try {
      parsed = JSON.parse(data.toString()) as WsClientEvent;
    } catch {
      this._send({ type: 'error', message: 'Invalid JSON' });
      return;
    }

    if (parsed.type === 'chat') {
      this._handleChat(parsed);
    } else if (parsed.type === 'stop') {
      this._handleStop();
    } else {
      this._send({ type: 'error', message: `Unknown message type: ${(parsed as { type: string }).type}` });
    }
  }

  /** 处理 chat 消息 — 启动 Agent 管线 */
  private async _handleChat(event: { query: string; image?: string; voice?: string }): Promise<void> {
    if (!this.alive) return;

    const query = event.query?.trim();
    if (!query) {
      this._send({ type: 'error', message: 'query 不能为空' });
      return;
    }

    // 如果已有管线在运行，先中止
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }

    // 创建新的 AbortController
    this.abortController = new AbortController();
    const signal = this.abortController.signal;

    try {
      const result = await this.runPipeline(
        query,
        event.image,
        event.voice,
        {
          // ─── 管线事件 → WebSocket JSON ──────────
          onStreamStart: () => this._send({ type: 'stream_start' }),
          onStreamChunk: (delta: string) => this._send({ type: 'stream_chunk', delta }),
          onStreamEnd: (fullMessage: string) => this._send({ type: 'stream_end', fullMessage }),
          onThought: (agent: string, title: string, detail: string) =>
            this._send({ type: 'thought', agent, title, detail }),
          onTool: (name: string, status: 'running' | 'success' | 'error', args: string, result: string) =>
            this._send({ type: 'tool', name, status, args, result }),
          onDone: () => this._send({ type: 'done' }),
          onError: (error: string) => this._send({ type: 'error', message: error }),
        },
        signal,
      );

      // 如果管线返回 null 且未明确 done，发送 done
      if (result === null && this.alive) {
        this._send({ type: 'done' });
      }
    } catch (err) {
      if (this.alive) {
        this._send({ type: 'error', message: `管线执行失败: ${err}` });
      }
    } finally {
      this.abortController = null;
    }
  }

  /** 处理 stop 消息 — 中止当前管线 */
  private _handleStop(): void {
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
      this._send({ type: 'error', message: '已停止' });
    }
  }

  // ─── 工具方法 ──────────────────────────────────────

  /** 发送 WebSocket 事件（JSON 序列化，自动附加时间戳） */
  private _send(event: Record<string, unknown>): void {
    if (!this.alive || this.ws.readyState !== WS_OPEN) return;
    try {
      // 自动附加毫秒级时间戳
      event.ts = Date.now();
      this.ws.send(JSON.stringify(event));
    } catch {
      // 发送失败，标记为断线
      this.alive = false;
    }
  }

  /** 清理资源 */
  private _cleanup(): void {
    if (!this.alive) return;
    this.alive = false;

    // 取消在途管线
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
  }
}
