/**
 * BaseAgent 抽象类 — 所有 Agent 的基类
 *
 * 每个 Agent 都是独立的异步协程，通过 MessageBus 进行通信。
 * Agent 的生命周期: start() → run() 消息处理循环 → stop()
 *
 * 与 Python 版 agents/base.py 保持 1:1 语义映射。
 */

import { MessageBus } from '../bus/bus.js';
import { Message, MessageType, makeSystemReady, makeSystemShutdown } from '../bus/message.js';

/** Agent 基类 — 抽象类，子类必须实现 handle() */
export abstract class BaseAgent {
  /** Agent 唯一名称 (如 'scheduler', 'receiver', 'sender', 'task_agent') */
  readonly name: string;

  /** 共享的消息总线实例 */
  protected readonly bus: MessageBus;

  /** 运行状态 */
  private _running = false;

  /** 后台处理循环的 AbortController */
  private _abortController: AbortController | null = null;

  constructor(name: string, bus: MessageBus) {
    this.name = name;
    this.bus = bus;
  }

  /** 是否正在运行 */
  get running(): boolean {
    return this._running;
  }

  /**
   * 启动 Agent
   *
   * 1. 订阅消息总线
   * 2. 调用 onStart() 钩子
   * 3. 发送 SYSTEM_READY 消息
   * 4. 进入消息处理循环
   */
  async start(): Promise<void> {
    await this.bus.subscribe(this.name);
    this._running = true;
    this._abortController = new AbortController();

    await this.onStart();

    // 通知其他 Agent 自己已就绪
    await this.bus.publish(makeSystemReady(this.name));

    // 启动后台消息处理循环
    this._runLoop();
  }

  /**
   * 停止 Agent
   *
   * 1. 发送 SYSTEM_SHUTDOWN 消息
   * 2. 调用 onStop() 钩子
   * 3. 取消订阅
   */
  async stop(): Promise<void> {
    if (!this._running) return;

    this._running = false;

    // 中止后台循环
    if (this._abortController) {
      this._abortController.abort();
      this._abortController = null;
    }

    // 通知其他 Agent
    await this.bus.publish(makeSystemShutdown(this.name));

    await this.onStop();
    await this.bus.unsubscribe(this.name);
  }

  /**
   * 发送消息到总线
   *
   * @param msg - 要发送的消息 (自动设置 source 为当前 Agent)
   * @returns true 如果发送成功
   */
  async send(msg: Message): Promise<boolean> {
    msg.source = this.name;
    return this.bus.publish(msg);
  }

  /**
   * 启动钩子 — 子类可覆盖，用于初始化资源（如加载模型、连接外部服务等）
   */
  protected async onStart(): Promise<void> {
    // 默认空实现
  }

  /**
   * 停止钩子 — 子类可覆盖，用于清理资源（如关闭连接、持久化内存等）
   */
  protected async onStop(): Promise<void> {
    // 默认空实现
  }

  /**
   * 处理收到的消息 — 子类必须实现
   *
   * @param msg - 收到的消息
   */
  protected abstract handle(msg: Message): Promise<void>;

  /**
   * 消息处理主循环 — 不断从总线接收消息并调用 handle()
   *
   * 在后台运行，通过 AbortController 控制生命周期。
   * 默认处理所有消息类型，子类可通过覆盖 receiveLoop 过滤类型。
   *
   * @param msgTypes - 只处理这些类型的消息 (undefined = 处理所有)
   * @param pollIntervalMs - 轮询间隔 (毫秒)
   */
  protected async runLoop(
    msgTypes?: Set<MessageType>,
    pollIntervalMs = 1000,
  ): Promise<void> {
    await this._runLoop(msgTypes, pollIntervalMs);
  }

  /** 内部消息循环实现 */
  private async _runLoop(
    msgTypes?: Set<MessageType>,
    pollIntervalMs = 1000,
  ): Promise<void> {
    const signal = this._abortController?.signal;

    while (this._running && !signal?.aborted) {
      try {
        let msg: Message | null;

        if (msgTypes) {
          msg = await this.bus.receiveFilter(
            this.name,
            msgTypes,
            pollIntervalMs,
          );
        } else {
          msg = await this.bus.receive(this.name, pollIntervalMs);
        }

        if (msg !== null) {
          await this.handle(msg);
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name === 'AbortError') {
          break;
        }
        // 其他异常记录后继续
        console.error(`[${this.name}] 消息处理异常:`, err);
        await sleep(500);
      }
    }
  }

  /**
   * 启动并在后台自动运行消息循环（便捷方法）
   *
   * 适用场景：Agent 只需订阅消息 + 处理消息，不需要自定义循环逻辑。
   *
   * @param msgTypes - 只处理这些类型的消息 (undefined = 所有类型)
   */
  startWithAutoLoop(msgTypes?: Set<MessageType>): void {
    // 启动后自动进入后台消息处理循环
    this.start().then(() => {
      setImmediate(() => {
        this._runLoop(msgTypes).catch((err) => {
          console.error(`[${this.name}] 后台循环异常:`, err);
        });
      });
    });
  }
}

/** 工具函数：异步 sleep */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
