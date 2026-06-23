/**
 * 消息类型定义 — MessageBus 上流转的所有消息结构
 *
 * 定义了系统中所有 Agent 之间通信的消息格式。
 * 与 Python 版 message.py 保持 1:1 语义映射。
 */

import crypto from 'node:crypto';

// ─── 消息类型枚举 ────────────────────────────────────────

/** 消息类型枚举 — 定义所有可能的 Agent 间通信类型 */
export enum MessageType {
  // ─── Receiver → Scheduler ───────────────────────────
  /** 用户意图消息: Receiver 理解用户输入后发送给 Scheduler */
  USER_INTENT = 'user_intent',

  // ─── Scheduler → Sender ─────────────────────────────
  /** 文本回复指令: Scheduler 要求 Sender 发送文本消息 */
  SEND_TEXT = 'send_text',

  /** 语音回复指令: Scheduler 要求 Sender 发送语音消息 */
  SEND_VOICE = 'send_voice',

  // ─── Scheduler → Sender (流式) ──────────────────────
  /** 流式回复开始: Scheduler 通知 Sender 准备接收流式文本 */
  STREAM_START = 'stream_start',

  /** 流式文本块: Scheduler 推送一个文本增量 (delta) 给 Sender */
  STREAM_CHUNK = 'stream_chunk',

  /** 流式回复结束: Scheduler 通知 Sender 流式文本已完成 */
  STREAM_END = 'stream_end',

  // ─── Scheduler ⇄ TaskAgent ─────────────────────────
  /** 任务执行指令: Scheduler 要求 TaskAgent 执行任务 */
  EXECUTE_TASK = 'execute_task',

  /** 任务执行结果: TaskAgent 返回执行结果给 Scheduler */
  TASK_RESULT = 'task_result',

  /** 任务执行错误: TaskAgent 返回错误信息给 Scheduler */
  TASK_ERROR = 'task_error',

  // ─── 系统消息 ───────────────────────────────────────
  /** Agent 启动完成通知 */
  SYSTEM_READY = 'system_ready',

  /** Agent 停止通知 */
  SYSTEM_SHUTDOWN = 'system_shutdown',

  /** 对话完成通知 (Sender → Main) */
  CONVERSATION_DONE = 'conversation_done',

  // ─── TUI 显示消息 ──────────────────────────────────
  /** 思考过程: Agent 发送给 TUI 显示的思考内容 */
  TUI_THOUGHT = 'tui_thought',

  /** 工具调用: Agent 发送给 TUI 显示的工具执行状态 */
  TUI_TOOL = 'tui_tool',

  /** 状态更新: Agent 发送给 TUI 显示的状态信息 */
  TUI_STATUS = 'tui_status',

  // ─── 用户输入 (内部) ───────────────────────────────
  /** 原始用户输入: CLI/API 层发给 Receiver 的原始消息 */
  RAW_INPUT = 'raw_input',
}

// ─── Message 类型 ────────────────────────────────────────

/**
 * 消息 — MessageBus 上流转的标准数据单元
 *
 * 所有 Agent 之间通过 Message 通信，不直接调用对方的方法。
 * 每条消息有唯一 ID、明确的来源/目标，以及结构化的 payload。
 */
export interface Message {
  /** 消息类型 */
  msg_type: MessageType;

  /** 发送方名称 (如 'receiver', 'scheduler', 'task_agent') */
  source: string;

  /** 目标名称 (如 'scheduler', 'sender') 或 'broadcast' (广播) */
  target: string;

  /** 消息负载 — 具体数据，格式因 msg_type 而异 */
  payload: Record<string, unknown>;

  /** 消息唯一 ID (12 位 hex) */
  msg_id: string;

  /** 消息创建时间 (Unix timestamp ms) */
  timestamp: number;

  /** 父消息 ID — 用于追踪任务链 (EXECUTE_TASK → TASK_RESULT) */
  parent_id?: string;

  /** 会话 ID — 用于多轮对话关联 */
  session_id?: string;
}

// ─── 内部工具函数 ────────────────────────────────────────

/** 生成 12 位 hex 消息 ID */
function generateMsgId(): string {
  return crypto.randomBytes(6).toString('hex');
}

/** 创建基础 Message 对象 */
function createMessage(
  msg_type: MessageType,
  source: string,
  target: string,
  payload: Record<string, unknown> = {},
  overrides: Partial<Pick<Message, 'parent_id' | 'session_id'>> = {},
): Message {
  return {
    msg_type,
    source,
    target,
    payload,
    msg_id: generateMsgId(),
    timestamp: Date.now(),
    ...overrides,
  };
}

// ─── Payload 工厂函数 ───────────────────────────────────
// 提供类型安全的 payload 构建方式

/**
 * 构建 USER_INTENT 消息
 *
 * @param original - 用户原始输入文本
 * @param intent - Receiver 理解后的意图描述
 * @param mediaRefs - 关联的媒体文件路径列表 (图片/音频)
 * @param sessionId - 会话 ID
 * @param contextToken - 微信渠道的 iLink context_token (透传给回复端)
 * @param toUserId - 微信渠道的用户 ID (透传给回复端)
 */
export function makeUserIntent(
  original: string,
  intent: string,
  mediaRefs: string[] = [],
  sessionId?: string,
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = {
    original,
    intent,
    media_refs: mediaRefs,
  };
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.USER_INTENT,
    'receiver',
    'memory_agent',
    payload,
    { session_id: sessionId },
  );
}

/**
 * 构建 SEND_TEXT 消息
 *
 * @param message - 要发送给用户的文本内容
 * @param sessionId - 会话 ID
 * @param target - 目标 Agent 名称（默认 "sender"）
 * @param contextToken - 微信渠道 iLink context_token
 * @param toUserId - 微信渠道用户 ID
 */
export function makeSendText(
  message: string,
  sessionId?: string,
  target = 'sender',
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = { message };
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.SEND_TEXT,
    'scheduler',
    target,
    payload,
    { session_id: sessionId },
  );
}

/**
 * 构建 SEND_VOICE 消息
 */
export function makeSendVoice(
  message: string,
  voice = '冰糖',
  audioFormat = 'wav',
  sessionId?: string,
  target = 'sender',
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = {
    message,
    voice,
    format: audioFormat,
  };
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.SEND_VOICE,
    'scheduler',
    target,
    payload,
    { session_id: sessionId },
  );
}

/**
 * 构建 EXECUTE_TASK 消息
 *
 * @param task - 任务描述 (给 TaskAgent 看)
 * @param toolsHint - 建议使用的工具列表
 * @param parentId - 父消息 ID (关联的 USER_INTENT 或上一个 TASK_RESULT)
 * @param sessionId - 会话 ID
 */
export function makeExecuteTask(
  task: string,
  toolsHint: string[] = [],
  parentId?: string,
  sessionId?: string,
): Message {
  return createMessage(
    MessageType.EXECUTE_TASK,
    'scheduler',
    'task_agent',
    {
      task,
      tools_hint: toolsHint,
    },
    { parent_id: parentId, session_id: sessionId },
  );
}

/**
 * 构建 TASK_RESULT 消息
 *
 * @param taskId - 原 EXECUTE_TASK 的 msg_id
 * @param result - 任务执行结果文本
 * @param toolCalls - 工具调用记录列表
 * @param sessionId - 会话 ID
 */
export function makeTaskResult(
  taskId: string,
  result: string,
  toolCalls: Record<string, unknown>[] = [],
  sessionId?: string,
): Message {
  return createMessage(
    MessageType.TASK_RESULT,
    'task_agent',
    'scheduler',
    {
      task_id: taskId,
      result,
      tool_calls: toolCalls,
    },
    { parent_id: taskId, session_id: sessionId },
  );
}

/**
 * 构建 TASK_ERROR 消息
 *
 * @param taskId - 原 EXECUTE_TASK 的 msg_id
 * @param error - 错误描述
 * @param sessionId - 会话 ID
 */
export function makeTaskError(
  taskId: string,
  error: string,
  sessionId?: string,
): Message {
  return createMessage(
    MessageType.TASK_ERROR,
    'task_agent',
    'scheduler',
    {
      task_id: taskId,
      error,
    },
    { parent_id: taskId, session_id: sessionId },
  );
}

// ─── 流式消息工厂函数 ─────────────────────────────────

/**
 * 构建 STREAM_START 消息
 */
export function makeStreamStart(
  sessionId?: string,
  target = 'sender',
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = {};
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.STREAM_START,
    'scheduler',
    target,
    payload,
    { session_id: sessionId },
  );
}

/**
 * 构建 STREAM_CHUNK 消息
 */
export function makeStreamChunk(
  delta: string,
  sessionId?: string,
  target = 'sender',
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = { delta };
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.STREAM_CHUNK,
    'scheduler',
    target,
    payload,
    { session_id: sessionId },
  );
}

/**
 * 构建 STREAM_END 消息
 */
export function makeStreamEnd(
  fullMessage: string,
  sessionId?: string,
  target = 'sender',
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = { message: fullMessage };
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.STREAM_END,
    'scheduler',
    target,
    payload,
    { session_id: sessionId },
  );
}

// ─── TUI 消息工厂函数 ─────────────────────────────────

/**
 * 构建 TUI_THOUGHT 消息 — Agent 思考过程显示
 *
 * @param agent - Agent 名称 (如 'scheduler', 'task_agent')
 * @param title - 思考标题
 * @param detail - 详细描述 (可选)
 * @param sessionId - 会话 ID
 */
export function makeTuiThought(
  agent: string,
  title: string,
  detail = '',
  sessionId?: string,
): Message {
  return createMessage(
    MessageType.TUI_THOUGHT,
    agent,
    'tui',
    { agent, title, detail },
    { session_id: sessionId },
  );
}

/**
 * 构建 TUI_TOOL 消息 — 工具调用状态显示
 *
 * @param toolName - 工具名称
 * @param toolArgs - 工具参数 (简要描述)
 * @param result - 执行结果 (success/error 时)
 * @param status - 状态 ('running', 'success', 'error')
 * @param sessionId - 会话 ID
 */
export function makeTuiTool(
  toolName: string,
  toolArgs = '',
  result = '',
  status: 'running' | 'success' | 'error' = 'running',
  sessionId?: string,
): Message {
  return createMessage(
    MessageType.TUI_TOOL,
    'task_agent',
    'tui',
    {
      tool_name: toolName,
      tool_args: toolArgs,
      result,
      status,
    },
    { session_id: sessionId },
  );
}

/**
 * 构建 TUI_STATUS 消息 — 状态信息显示
 *
 * @param key - 状态键 (如 'memory')
 * @param value - 状态值
 * @param sessionId - 会话 ID
 */
export function makeTuiStatus(
  key: string,
  value: string,
  sessionId?: string,
): Message {
  return createMessage(
    MessageType.TUI_STATUS,
    'memory_agent',
    'tui',
    { key, value },
    { session_id: sessionId },
  );
}

/** 构建 CONVERSATION_DONE 消息 */
export function makeConversationDone(sessionId?: string): Message {
  return createMessage(
    MessageType.CONVERSATION_DONE,
    'sender',
    'main',
    {},
    { session_id: sessionId },
  );
}

/** 构建 RAW_INPUT 消息 */
export function makeRawInput(
  content: string,
  mediaRefs: string[] = [],
  sessionId?: string,
  contextToken = '',
  toUserId = '',
): Message {
  const payload: Record<string, unknown> = {
    content,
    media_refs: mediaRefs,
  };
  if (contextToken) payload['context_token'] = contextToken;
  if (toUserId) payload['to_user_id'] = toUserId;

  return createMessage(
    MessageType.RAW_INPUT,
    'cli',
    'receiver',
    payload,
    { session_id: sessionId },
  );
}

/** 构建 SYSTEM_READY 消息 */
export function makeSystemReady(agentName: string): Message {
  return createMessage(
    MessageType.SYSTEM_READY,
    agentName,
    'broadcast',
    { agent: agentName },
  );
}

/** 构建 SYSTEM_SHUTDOWN 消息 */
export function makeSystemShutdown(agentName: string): Message {
  return createMessage(
    MessageType.SYSTEM_SHUTDOWN,
    agentName,
    'broadcast',
    { agent: agentName },
  );
}
