/**
 * MessageBus — 异步消息总线
 *
 * 所有 Agent 通过 MessageBus 进行通信，不直接相互调用。
 * 基于 AsyncQueue 实现发布-订阅模式。
 *
 * 特性:
 *   - 支持定向消息 (target 指定) 和广播 (target='broadcast')
 *   - 每个 Agent 有独立的订阅队列
 *   - 非阻塞发送，队列满时丢弃旧消息
 *   - 支持优雅关闭
 *   - 镜像投递: 指定类型的消息自动额外投递一份给观察者
 */

import { Message, MessageType } from './message.js';

// ─── AsyncQueue — 简单的异步队列 ──────────────────────────

/**
 * 基于 Promise 的异步队列实现
 * 替代 Python 的 asyncio.Queue
 */
class AsyncQueue<T> {
  private _items: T[] = [];
  private _waiters: Array<{
    resolve: (value: T | null) => void;
    reject: (reason?: unknown) => void;
  }> = [];
  private _maxSize: number;

  constructor(maxSize = 100) {
    this._maxSize = maxSize;
  }

  /** 获取当前队列长度 */
  get size(): number {
    return this._items.length;
  }

  /** 非阻塞放入 */
  putNowait(item: T): boolean {
    // 如果有等待者，直接交付
    if (this._waiters.length > 0) {
      const waiter = this._waiters.shift()!;
      waiter.resolve(item);
      return true;
    }

    // 队列满时失败
    if (this._items.length >= this._maxSize) {
      return false;
    }

    this._items.push(item);
    return true;
  }

  /** 异步获取（支持超时） */
  async get(timeoutMs?: number): Promise<T | null> {
    // 如果队列中有元素，直接返回
    if (this._items.length > 0) {
      return this._items.shift()!;
    }

    // 否则等待
    return new Promise<T | null>((resolve, reject) => {
      const waiter = {
        resolve: (value: T | null) => resolve(value),
        reject,
      };

      if (timeoutMs !== undefined && timeoutMs > 0) {
        const timer = setTimeout(() => {
          // 从等待列表中移除自己
          const idx = this._waiters.indexOf(waiter);
          if (idx !== -1) {
            this._waiters.splice(idx, 1);
          }
          resolve(null); // 超时返回 null
        }, timeoutMs);

        // 包装 resolve 以清除计时器
        waiter.resolve = (value: T | null) => {
          clearTimeout(timer);
          resolve(value);
        };
      }

      this._waiters.push(waiter);
    });
  }

  /** 非阻塞获取 */
  getNowait(): T | null {
    if (this._items.length > 0) {
      return this._items.shift()!;
    }
    return null;
  }

  /** 清空队列 */
  clear(): void {
    this._items = [];
    // 通知所有等待者队列已关闭
    for (const waiter of this._waiters) {
      waiter.resolve(null as unknown as T);
    }
    this._waiters = [];
  }

  /** 队列是否为空 */
  get empty(): boolean {
    return this._items.length === 0;
  }
}

// ─── MessageBus ───────────────────────────────────────────

/**
 * 异步消息总线
 *
 * 用法:
 *   const bus = new MessageBus(100);
 *   await bus.subscribe("scheduler");
 *   await bus.publish(message);
 *   const msg = await bus.receive("scheduler", 30000);
 */
export class MessageBus {
  private _queues: Map<string, AsyncQueue<Message>> = new Map();
  private _subscribers: Set<string> = new Set();
  private _maxQueueSize: number;
  private _running = false;

  /**
   * 镜像投递注册表: { MessageType → Set<mirror_target_names> }
   * 指定类型的消息自动额外投递一份给 mirror target
   */
  private _mirrors: Map<MessageType, Set<string>> = new Map();

  constructor(maxQueueSize = 100) {
    this._maxQueueSize = maxQueueSize;
  }

  /** 订阅消息 */
  async subscribe(name: string): Promise<void> {
    if (!this._queues.has(name)) {
      this._queues.set(name, new AsyncQueue<Message>(this._maxQueueSize));
      this._subscribers.add(name);
    }
  }

  /**
   * 注册镜像订阅 — 指定类型的消息自动额外投递一份给 target
   *
   * 用于 MemoryAgent 等需要感知全总线消息的 Agent。
   * 不重复投递给 source 自己（避免死循环）。
   *
   * @param msgType - 要镜像的消息类型
   * @param target - 镜像目标名称
   */
  subscribeMirror(msgType: MessageType, target: string): void {
    // 确保目标已订阅
    if (!this._queues.has(target)) {
      this._queues.set(target, new AsyncQueue<Message>(this._maxQueueSize));
      this._subscribers.add(target);
    }

    if (!this._mirrors.has(msgType)) {
      this._mirrors.set(msgType, new Set());
    }
    this._mirrors.get(msgType)!.add(target);
  }

  /** 取消订阅 */
  async unsubscribe(name: string): Promise<void> {
    this._queues.delete(name);
    this._subscribers.delete(name);

    // 清理该 name 的所有镜像订阅
    for (const mirrors of this._mirrors.values()) {
      mirrors.delete(name);
    }
  }

  /**
   * 发布消息到总线
   *
   * 定向消息 (target != 'broadcast'): 只投递到目标订阅者
   * 广播消息 (target == 'broadcast'): 投递到所有订阅者
   *
   * @returns true 如果至少投递到一个订阅者
   */
  async publish(msg: Message): Promise<boolean> {
    if (!this._running) {
      return false;
    }

    let delivered = false;

    if (msg.target === 'broadcast') {
      // 广播到所有订阅者（不发给发送方自己）
      for (const [name, queue] of this._queues) {
        if (name !== msg.source) {
          delivered = this._putSafe(queue, msg) || delivered;
        }
      }
    } else {
      // 定向投递
      const queue = this._queues.get(msg.target);
      if (queue) {
        delivered = this._putSafe(queue, msg);
      }
    }

    // ─── 镜像投递 ────────────────────────────────
    // 某些 Agent (如 MemoryAgent) 需要感知特定类型的消息，
    // 通过 subscribeMirror() 注册后在 publish() 时自动额外投递一份
    const mirrors = this._mirrors.get(msg.msg_type);
    if (mirrors) {
      for (const mirror of mirrors) {
        if (mirror !== msg.source && mirror !== msg.target) {
          const mirrorQueue = this._queues.get(mirror);
          if (mirrorQueue) {
            this._putSafe(mirrorQueue, msg);
          }
        }
      }
    }

    return delivered;
  }

  /**
   * 接收消息 (阻塞等待)
   *
   * @param name - 接收者名称
   * @param timeoutMs - 超时毫秒数 (undefined 表示无限等待)
   * @returns 收到的消息，超时返回 null
   */
  async receive(
    name: string,
    timeoutMs?: number,
  ): Promise<Message | null> {
    const queue = this._queues.get(name);
    if (!queue) return null;

    return queue.get(timeoutMs);
  }

  /**
   * 接收消息 — 只接收指定类型的消息，其他消息放回队列
   *
   * @param name - 接收者名称
   * @param msgTypes - 要接收的消息类型集合
   * @param timeoutMs - 超时毫秒数
   * @returns 匹配的消息，超时返回 null
   */
  async receiveFilter(
    name: string,
    msgTypes: Set<MessageType>,
    timeoutMs?: number,
  ): Promise<Message | null> {
    const queue = this._queues.get(name);
    if (!queue) return null;

    const deadline = timeoutMs !== undefined ? Date.now() + timeoutMs : null;
    const skipped: Message[] = [];

    while (true) {
      // 检查超时
      if (deadline !== null && Date.now() >= deadline) {
        // 把跳过的消息放回
        for (const m of skipped) {
          queue.putNowait(m);
        }
        return null;
      }

      const remainingMs = deadline !== null ? deadline - Date.now() : undefined;
      const msg = await queue.get(remainingMs);

      if (msg === null) {
        // 超时
        for (const m of skipped) {
          queue.putNowait(m);
        }
        return null;
      }

      if (msgTypes.has(msg.msg_type)) {
        // 找到匹配的消息，先放回跳过的消息
        for (const m of skipped) {
          queue.putNowait(m);
        }
        return msg;
      } else {
        // 不匹配，暂存
        skipped.push(msg);
      }
    }
  }

  /** 启动总线 */
  async start(): Promise<void> {
    this._running = true;
  }

  /** 停止总线 — 清空所有队列 */
  async stop(): Promise<void> {
    this._running = false;
    for (const [, queue] of this._queues) {
      queue.clear();
    }
  }

  /**
   * 安全投递 — 队列满时丢弃旧消息
   *
   * @returns true 如果投递成功
   */
  private _putSafe(
    queue: AsyncQueue<Message>,
    msg: Message,
  ): boolean {
    const ok = queue.putNowait(msg);
    if (!ok) {
      // 丢弃最旧的消息，放入新消息
      const old = queue.getNowait();
      if (old !== null) {
        // 旧消息已丢弃，重新尝试放入
        return queue.putNowait(msg);
      }
    }
    return ok;
  }
}
