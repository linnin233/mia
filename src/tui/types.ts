/**
 * MIA Ink TUI — 类型定义
 */

/** 一条对话消息 */
export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
}

/** 思考记录 */
export interface ThoughtEntry {
  id: string;
  agent: string;       // 'scheduler' | 'task_agent' | 'memory_agent'
  title: string;
  detail: string;
  timestamp: number;
}

/** 工具调用记录 */
export interface ToolCallEntry {
  id: string;
  toolName: string;
  toolArgs: string;
  result: string;
  status: 'running' | 'success' | 'error';
  timestamp: number;
}

/** 记忆条目（TUI 展示用） */
export interface MemoryEntry {
  id: string;
  content: string;
  category: string;     // fact/preference/decision/task/insight
  confidence: number;
  importance: number;
  categoryLabel: string;
}

/** 面板折叠状态 */
export interface PanelState {
  thinking: boolean;    // Thinking 面板是否展开
  tools: boolean;       // Tools 面板是否展开
  memory: boolean;      // Memory 面板是否展开
}

/** 全局 TUI 状态 */
export interface TuiState {
  /** 对话历史 */
  messages: ChatMessage[];
  /** 当前流式输出文本 */
  streamingText: string;
  /** 是否正在处理 */
  isProcessing: boolean;
  /** 思考记录 */
  thoughts: ThoughtEntry[];
  /** 工具调用记录 */
  toolCalls: ToolCallEntry[];
  /** 相关记忆 */
  memories: MemoryEntry[];
  /** 面板折叠状态 */
  panels: PanelState;
  /** 状态栏信息 */
  status: {
    model: string;
    memoryCount: number;
    sessionId: string;
  };
  /** 全屏模式 */
  fullscreenMode: 'chat' | 'memory';
  /** 记忆浏览器分页 */
  memoryPage: number;
}
